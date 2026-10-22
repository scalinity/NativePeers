"""Fixture-only readiness and admission coverage; no native inference is used."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nativepeers.antigravity_install import desired, verify_tool_gate
from nativepeers.errors import PeerError
from nativepeers.install import Installer, readiness
from nativepeers.worker_process import Worker


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.home=self.root/'home';self.project=self.root/'source'
        self.cwd=self.root/'workspace';self.cwd.mkdir();(self.cwd/'.git').mkdir()
        self.codex=self.root/'selected-codex';self.claude=self.root/'selected-claude'
        self.journal=SimpleNamespace(root=self.root/'state')
        for provider in ('claude','codex'):
            skill=self.project/'integrations'/provider/'skills/nativepeers/SKILL.md'
            skill.parent.mkdir(parents=True);skill.write_text('fixture '+provider+' skill\n')
        (self.project/'bin').mkdir()
        for name in ('npeer','npeer-hook','npeer-antigravity-hook'):
            path=self.project/'bin'/name;path.write_text('#!/bin/sh\n');path.chmod(0o755)
        for item in (patch('nativepeers.install.ROOT',self.project),
                     patch('nativepeers.antigravity_install.ROOT',self.project),
                     patch('nativepeers.antigravity_install.PYTHON',Path(sys.executable)),
                     patch('pathlib.Path.home',return_value=self.home),
                     patch('pathlib.Path.cwd',return_value=self.cwd),
                     patch.dict(os.environ,CODEX_HOME=str(self.codex),CLAUDE_CONFIG_DIR=str(self.claude))):
            item.start();self.addCleanup(item.stop)
        self.installer=Installer(self.journal,native=False)

    def write_json(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))

    def configured(self):
        for provider,home in (('claude',self.claude),('codex',self.codex)):
            path=home/('settings.json' if provider=='claude' else 'hooks.json')
            self.write_json(path,{'hooks':{event:[group] for event,group in self.installer.desired_hooks(provider).items()}})
            skill=home/'skills/nativepeers/SKILL.md' if provider=='claude' else self.installer.skills[provider]
            skill.parent.mkdir(parents=True,exist_ok=True)
            skill.write_bytes((self.project/'integrations'/provider/'skills/nativepeers/SKILL.md').read_bytes())
        self.write_json(self.installer.manifest,{'trusted_hooks':{'start':'one','end':'two'}})
        (self.codex/'config.toml').write_text('[hooks.state.start]\ntrusted_hash="one"\n[hooks.state.end]\ntrusted_hash="two"\n')
        for path,(kind,key,value) in desired(self.home).items():
            if kind=='file':path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value)
            else:self.write_json(path,{'mcpServers':{key:value}} if kind=='mcp' else {key:value})
        self.installer.command.parent.mkdir(parents=True);self.installer.command.symlink_to(self.project/'bin/npeer')

    def snapshot(self):
        return {str(path):(path.read_bytes(),path.stat().st_mode,path.stat().st_mtime_ns)
                for path in self.root.rglob('*') if path.is_file()}

    def states(self,report,provider,kind):
        return [item['state'] for item in report['providers'][provider]['checks'] if item['kind']==kind]

    def test_missing_installation_stays_passive_and_does_not_create_state(self):
        before=self.snapshot()
        report=readiness(self.journal)
        self.assertEqual(self.states(report,'claude','hook'),['MISSING']*3)
        self.assertEqual(self.states(report,'codex','hook'),['MISSING']*2)
        self.assertEqual(self.states(report,'antigravity','profile'),['MISSING']*2)
        self.assertEqual(self.states(report,'antigravity','mcp_facade'),['MISSING'])
        self.assertEqual(report['entrypoint']['state'],'MISSING')
        self.assertFalse(self.journal.root.exists());self.assertEqual(self.snapshot(),before)

    def test_selected_homes_and_matching_owned_files_without_launch_write_or_repair(self):
        self.configured();before=self.snapshot()
        with (patch('subprocess.Popen',side_effect=AssertionError('provider launch')),
             patch('asyncio.create_subprocess_exec',side_effect=AssertionError('provider launch')),
             patch('nativepeers.install.AppServer',side_effect=AssertionError('app-server')),
             patch('pathlib.Path.mkdir',side_effect=AssertionError('write')),
             patch('pathlib.Path.write_text',side_effect=AssertionError('write')),
             patch('pathlib.Path.write_bytes',side_effect=AssertionError('write'))):
            report=readiness(self.journal)
        self.assertTrue(report['passive'])
        self.assertEqual(report['selected_homes'],{'claude':str(self.claude),'codex':str(self.codex),
                                                  'antigravity':str((self.home/'.gemini/antigravity-cli').resolve())})
        self.assertEqual(report['native_effective_precedence'],'unknown')
        for provider,data in report['providers'].items():
            self.assertEqual({item['state'] for item in data['checks']},{'PRESENT'},provider)
        self.assertEqual(report['entrypoint']['state'],'PRESENT')
        self.assertEqual(self.snapshot(),before)
        json.dumps(report)

    def test_modified_disabled_and_malformed_items_are_reported_without_config_values(self):
        self.configured()
        settings=self.claude/'settings.json';data=json.loads(settings.read_text());data['disableAllHooks']=True
        data['apiKey']='fixture-secret-must-not-appear';self.write_json(settings,data)
        hooks=self.codex/'hooks.json';data=json.loads(hooks.read_text())
        data['hooks']['SessionStart'][0]['hooks'][0]['timeout']=99;self.write_json(hooks,data)
        (self.codex/'config.toml').write_text('[hooks.state.start]\ntrusted_hash="one"\nenabled=false\n[hooks.state.end]\ntrusted_hash="two"\n')
        (self.home/'.gemini/config/agents/nativepeers-analysis.md').write_text('modified profile')
        path=self.home/'.gemini/config/mcp_config.json';data=json.loads(path.read_text())
        data['mcpServers']['nativepeers']['disabled']=True;self.write_json(path,data)
        (self.project/'bin/npeer').chmod(0o600)
        before=self.snapshot();report=readiness(self.journal)
        self.assertEqual(self.states(report,'claude','hook'),['DISABLED']*3)
        self.assertEqual(self.states(report,'codex','hook'),['MODIFIED','PRESENT'])
        self.assertEqual(self.states(report,'codex','hook_trust'),['DISABLED'])
        self.assertEqual(self.states(report,'antigravity','profile'),['MODIFIED','PRESENT'])
        self.assertEqual(self.states(report,'antigravity','mcp_facade'),['DISABLED'])
        self.assertEqual(report['entrypoint']['state'],'DISABLED')
        self.assertNotIn('fixture-secret',json.dumps(report));self.assertEqual(self.snapshot(),before)
        self.write_json(hooks,{'hooks':[]});path.write_text('{invalid')
        report=readiness(self.journal)
        self.assertEqual(self.states(report,'codex','hooks'),['UNKNOWN'])
        self.assertEqual(self.states(report,'antigravity','mcp_facade'),['UNKNOWN'])

    def test_antigravity_mcp_gate_refuses_missing_disabled_modified_and_workspace_conflicts(self):
        self.configured();path=self.home/'.gemini/config/mcp_config.json'
        expected=desired(self.home)[path][2]
        for scenario in ('present','explicit-enabled','missing','disabled','modified','tools','malformed','workspace-matching','workspace','workspace-disabled','workspace-null','ancestor'):
            with self.subTest(scenario=scenario):
                workspace=self.cwd/'.agents/mcp_config.json';workspace.unlink(missing_ok=True)
                local=self.cwd/'subdir';local.mkdir(exist_ok=True)
                current=dict(expected);cwd=self.cwd
                if scenario=='explicit-enabled':current['disabled']=False
                if scenario=='disabled':current['disabled']=True
                if scenario=='modified':current['command']='/different-fixture-provider'
                if scenario=='tools':current['disabledTools']=['worker_spawn']
                self.write_json(path,{'mcpServers':{} if scenario=='missing' else {'nativepeers':current}})
                if scenario=='malformed':path.write_text('not json')
                if scenario in ('workspace-matching','workspace','workspace-disabled','workspace-null','ancestor'):
                    override=(None if scenario=='workspace-null' else expected if scenario=='workspace-matching' else dict(expected,disabled=True)
                              if scenario=='workspace-disabled' else dict(expected,command='/workspace-fixture'))
                    self.write_json(workspace,{'mcpServers':{'nativepeers':override}})
                    if scenario=='ancestor':cwd=local
                before=self.snapshot()
                if scenario in ('present','explicit-enabled','workspace-matching'):verify_tool_gate({'cwd':str(cwd)})
                else:
                    with self.assertRaises(PeerError) as error:verify_tool_gate({'cwd':str(cwd)})
                    self.assertEqual(error.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
                    self.assertTrue(error.exception.details)
                    self.assertTrue(all(item['native_effective_precedence']=='unknown' for item in error.exception.details))
                self.assertEqual(self.snapshot(),before)
        self.write_json(workspace,{'mcpServers':{'unrelated':{'disabled':True}}})
        self.write_json(path,{'mcpServers':{'nativepeers':expected}})
        verify_tool_gate({'cwd':str(self.cwd)})

    def test_malformed_antigravity_hooks_do_not_hide_profile_and_facade_checks(self):
        self.configured();(self.home/'.gemini/config/hooks.json').write_text('not json')
        report=readiness(self.journal)
        self.assertEqual(self.states(report,'antigravity','hooks'),['UNKNOWN'])
        self.assertEqual(self.states(report,'antigravity','profile'),['PRESENT']*2)
        self.assertEqual(self.states(report,'antigravity','mcp_facade'),['PRESENT'])

    def test_antigravity_workspace_and_native_settings_hook_overrides_are_visible(self):
        self.configured()
        self.write_json(self.cwd/'.agents/hooks.json',{'nativepeers':{}})
        self.write_json(self.home/'.gemini/antigravity-cli/settings.json',{'hooks':{'nativepeers':{}}})
        report=readiness(self.journal)
        self.assertEqual(self.states(report,'antigravity','hooks'),['PRESENT','MODIFIED','MODIFIED'])

    def test_fake_worker_cannot_announce_ready_when_mcp_is_disabled(self):
        self.configured();path=self.home/'.gemini/config/mcp_config.json'
        expected=desired(self.home)[path][2]
        self.write_json(path,{'mcpServers':{'nativepeers':dict(expected,disabled=True)}})
        worker=Worker.__new__(Worker)
        worker.row={'provider':'antigravity','worker_id':'fixture'}
        worker.spec={'cwd':str(self.cwd),'permission_profile':'analysis'}
        worker.lease={'executable':str(self.project/'bin/npeer')}
        worker.journal=SimpleNamespace(db=SimpleNamespace(execute=lambda *args: self.fail('worker became READY')))
        worker.bootstrap=lambda:None;worker.emit=lambda *args,**kwargs:self.fail('worker announced READY')
        with patch('nativepeers.worker_process.verify'),self.assertRaises(PeerError) as error:
            asyncio.run(worker.prepare())
        self.assertEqual(error.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
