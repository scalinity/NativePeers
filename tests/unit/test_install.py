from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.compatibility import fingerprint, verify
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, private_directory
from nativepeers.install import Installer
from nativepeers.journal import Journal


class InstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_noop_install_refreshes_only_verified_receipts(self):
        for scenario in ('missing', 'updated', 'unchanged', 'unverified','superseded','historical-removed'):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);home=private_directory(root/'home');project=private_directory(root/'project')
                journal=Journal(root/'state');self.addCleanup(journal.close)
                providers={}
                for provider in ('claude','codex'):
                    skill=project/'integrations'/provider/'skills/nativepeers/SKILL.md'
                    skill.parent.mkdir(parents=True);skill.write_text('fixture skill\n')
                    executable=root/provider;executable.write_text('verified '+provider)
                    providers[provider]={'executable':str(executable),'executable_sha256':fingerprint(executable)['sha256'],'version':'fixture'}
                    launcher=home/('.local/bin/claude' if provider=='claude' else '.local/share/codex-mcp-manager/current/bin/codex')
                    launcher.parent.mkdir(parents=True,exist_ok=True);launcher.symlink_to(executable)
                (project/'bin').mkdir()
                for name in ('npeer','npeer-hook'):(project/'bin'/name).write_text('#!/bin/sh\n')
                evidence=project/'evidence/capabilities.json';atomic_json(evidence,{'providers':providers})
                with patch('nativepeers.install.ROOT',project),patch('nativepeers.install.Path.home',return_value=home):
                    installer=Installer(journal,home=home,native=False);await installer.install()
                    trusted={'fixture-start':'start-hash','fixture-end':'end-hash'}
                    manifest=json.loads(installer.manifest.read_text());manifest['trusted_hooks']=trusted
                    atomic_json(installer.manifest,manifest)
                    installer.config.write_text(''.join('[hooks.state.'+key+']\ntrusted_hash="'+value+'"\n' for key,value in trusted.items()))
                    expected={provider:dict(fingerprint(info['executable']),version=info['version']) for provider,info in providers.items()}
                    third={'path':'fixture-agy','sha256':'retained','scope':'fixture'}
                    receipt=journal.root/'compat/runtime.json'
                    if scenario!='missing':atomic_json(receipt,{'schema_version':1,'providers':dict(expected,antigravity=third)})
                    if scenario in ('updated','unverified'):
                        Path(providers['claude']['executable']).write_text('updated claude executable')
                        if scenario=='updated':
                            providers['claude']['executable_sha256']=fingerprint(providers['claude']['executable'])['sha256']
                            atomic_json(evidence,{'providers':providers})
                    if scenario in ('superseded','historical-removed'):
                        newer=root/'newer-claude';newer.write_text('independently verified new runtime')
                        current=json.loads(receipt.read_text());current['providers']['claude']=dict(fingerprint(newer),version='newer')
                        atomic_json(receipt,current)
                        if scenario=='historical-removed':Path(providers['claude']['executable']).unlink()
                    before={p:p.read_bytes() for p in [installer.manifest,installer.config,*installer.targets.values(),*installer.skills.values()]}
                    receipt_before=receipt.read_bytes() if receipt.exists() else None
                    backups=list((project/'backups').iterdir())
                    installer.native=True
                    with patch.object(installer,'lock',side_effect=nullcontext),patch.object(installer,'trust',new_callable=AsyncMock) as trust,\
                         patch('nativepeers.install.AppServer',side_effect=AssertionError('must remain offline')):
                        if scenario in ('superseded','historical-removed'):
                            self.assertEqual((await installer.install(dry_run=True))['operations'],[])
                            self.assertFalse((await installer.install())['changed'])
                            self.assertEqual(receipt.read_bytes(),receipt_before)
                            verify(journal,'claude',newer)
                        elif scenario in ('unverified','updated'):
                            self.assertFalse((await installer.install())['changed'])
                            self.assertEqual(receipt.read_bytes(),receipt_before)
                            with self.assertRaises(PeerError):verify(journal,'claude',providers['claude']['executable'])
                            self.assertTrue(installer.plan(remove=True)['operations'])
                            if scenario=='updated':
                                from nativepeers.compatibility import record_verified
                                record_verified(journal,evidence)
                                verify(journal,'claude',providers['claude']['executable'])
                        else:
                            plan=await installer.install(dry_run=True)
                            self.assertEqual([op['operation'] for op in plan['operations']],
                                             [] if scenario=='unchanged' else ['refresh-verified-compatibility-receipt'])
                            self.assertEqual(receipt.read_bytes() if receipt.exists() else None,receipt_before)
                            result=await installer.install()
                            self.assertEqual(result['changed'],scenario!='unchanged')
                            for provider,info in providers.items():verify(journal,provider,info['executable'])
                            if scenario!='missing':self.assertEqual(json.loads(receipt.read_text())['providers']['antigravity'],third)
                            stable=receipt.read_bytes();modified=receipt.stat().st_mtime_ns
                            self.assertFalse((await installer.install())['changed'])
                            self.assertEqual(receipt.read_bytes(),stable)
                            self.assertEqual(receipt.stat().st_mtime_ns,modified)
                        trust.assert_not_awaited()
                    self.assertEqual({p:p.read_bytes() for p in before},before)
                    self.assertEqual(list((project/'backups').iterdir()),backups)

    async def test_reinstall_preserves_local_skill_edits_and_updates_unchanged_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=private_directory(root/'home');journal=Journal(root/'state');self.addCleanup(journal.close)
            project=private_directory(root/'project')
            for provider in ('claude','codex'):
                source=project/'integrations'/provider/'skills/nativepeers/SKILL.md';source.parent.mkdir(parents=True);source.write_text('installed revision 1\n')
            (project/'bin').mkdir()
            for name in ('npeer','npeer-hook'):(project/'bin'/name).write_text('#!/bin/sh\n')
            with patch('nativepeers.install.ROOT',project):
                installer=Installer(journal,home=home,native=False);await installer.install()
                skill=installer.skills['claude'];skill.write_text('local edit\n')
                with self.assertRaises(PeerError) as error:await installer.install()
                self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT');self.assertEqual(skill.read_text(),'local edit\n')
                skill.write_text('installed revision 1\n')
                for provider in ('claude','codex'):(project/'integrations'/provider/'skills/nativepeers/SKILL.md').write_text('installed revision 2\n')
                await installer.install()
                self.assertTrue(all(p.read_text()=='installed revision 2\n' for p in installer.skills.values()))
                self.assertFalse((await installer.install())['changed'])

    async def test_concurrent_hook_edit_is_preserved_during_install_and_uninstall(self):
        for remove in (False,True):
            with self.subTest(remove=remove),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);home=private_directory(root/'home');journal=Journal(root/'state')
                self.addCleanup(journal.close)
                settings=home/'.claude/settings.json';settings.parent.mkdir();settings.write_text('{"unrelated":false}')
                installer=Installer(journal,home=home,native=False)
                if remove:await installer.install()
                merged=installer.merged;newer=None
                def concurrent_merge(path,provider,remove=False):
                    nonlocal newer
                    before,after=merged(path,provider,remove)
                    if provider=='claude':
                        newer=json.dumps(dict(before,laterEdit={'enabled':False})).encode()
                        path.write_bytes(newer)
                    return before,after
                plan={'operations':[{'operation':'fixture'}],'command_collision':False}
                with patch.object(installer,'plan',return_value=plan),patch.object(installer,'merged',side_effect=concurrent_merge):
                    with self.assertRaises(PeerError) as error:
                        await (installer.uninstall() if remove else installer.install())
                self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT')
                self.assertEqual(settings.read_bytes(),newer)

    async def test_idempotent_install_and_owned_uninstall_preserve_later_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=private_directory(root/'home');journal=Journal(private_directory(root/'state'))
            (home/'.claude').mkdir();(home/'.codex').mkdir()
            original={'model':'unchanged','featureDefault':False,'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':'existing-hook'}]}]}}
            settings=home/'.claude/settings.json';settings.write_text(json.dumps(original))
            (home/'.codex/hooks.json').write_text(json.dumps({'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':'existing-codex'}]}]}}))
            (home/'.codex/config.toml').write_text('[features]\ndaemon_auto_start = false\n')
            installer=Installer(journal,home=home,native=False)
            before=installer.plan();self.assertTrue(before['operations'])
            await installer.install();manifest=installer.manifest.read_bytes();settings_bytes=settings.read_bytes()
            again=await installer.install();self.assertFalse(again['changed']);self.assertEqual(manifest,installer.manifest.read_bytes());self.assertEqual(settings_bytes,settings.read_bytes())
            changed=json.loads(settings.read_text());changed['laterUnrelated']={'enabled':False};changed['hooks']['SessionStart'].append({'hooks':[{'type':'command','command':'later-hook'}]});settings.write_text(json.dumps(changed))
            result=await installer.uninstall();self.assertTrue(result['uninstalled'])
            after=json.loads(settings.read_text());self.assertEqual(after['laterUnrelated'],{'enabled':False});self.assertEqual(after['featureDefault'],False)
            self.assertEqual([h['command'] for g in after['hooks']['SessionStart'] for h in g['hooks']],['existing-hook','later-hook'])
            self.assertFalse(installer.command.exists());self.assertTrue(journal.root.exists());journal.close()

    async def test_command_collision_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=private_directory(root/'home');journal=Journal(private_directory(root/'state'))
            command=home/'.local/bin/npeer';command.parent.mkdir(parents=True);command.write_text('unrelated command')
            installer=Installer(journal,home=home,native=False);self.assertTrue(installer.plan()['command_collision'])
            await installer.install();self.assertEqual(command.read_text(),'unrelated command');await installer.uninstall();self.assertEqual(command.read_text(),'unrelated command');journal.close()
