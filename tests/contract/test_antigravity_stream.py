import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

from nativepeers.antigravity_install import worker_profile,desired
from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker

ROOT=Path(__file__).resolve().parents[2]


class AntigravityContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_stream_partial_lines_and_exact_history_followup(self):
        await self.run_scenario('success',followup=True)

    async def test_each_fresh_process_rechecks_compatibility_profile_hooks_and_mcp(self):
        for change in ('binary-drift','hooks-disabled','profile-modified','mcp-disabled'):
            for before_first in (True,False):
                with self.subTest(change=change,before_first=before_first):
                    await self.run_scenario('success',followup_change=change,before_first=before_first)

    async def test_hookless_second_process_cannot_reuse_previous_or_mismatched_evidence(self):
        for change in ('no-hook','wrong-hook-pid','wrong-hook-start','old-hook-boundary'):
            with self.subTest(change=change):
                await self.run_scenario('success',followup_change=change)

    async def test_released_worker_resumes_exact_native_history_with_fresh_hooks(self):
        await self.run_scenario('success',followup=True,resume=True)

    async def test_native_stream_negative_identity_hook_model_and_cancel_boundaries(self):
        for scenario in ['no-hook','wrong-id','display-model','CANCELED','INTERRUPTED']:
            with self.subTest(scenario=scenario):await self.run_scenario(scenario)

    async def test_missing_disabled_and_workspace_mcp_are_refused_before_fake_provider_input(self):
        for scenario in ['mcp-missing','mcp-disabled','mcp-workspace']:
            with self.subTest(scenario=scenario):await self.run_scenario(scenario)

    async def test_ancestor_hooks_and_workspace_profiles_refuse_subdirectory_launch(self):
        for scenario in ('project-hooks','project-profile','project-profile-directory'):
            with self.subTest(scenario=scenario):await self.run_scenario(scenario)

    async def run_scenario(self,scenario,followup=False,followup_change=None,before_first=False,resume=False):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'HOME':directory}):
            home=Path(directory).resolve();(home/'.git').mkdir()
            cwd=home
            if scenario.startswith('project-'):
                cwd=home/'package/src';cwd.mkdir(parents=True)
                if scenario=='project-hooks':
                    override=home/'.agents/hooks.json';content='{"nativepeers":{"enabled":false}}'
                else:
                    override=home/'.agents/agents'/('nativepeers-analysis.md' if scenario=='project-profile' else 'nativepeers-analysis/agent.md')
                    content=worker_profile('workspace')
                override.parent.mkdir(parents=True);override.write_text(content)
            j=Journal(home/'state');self.addCleanup(j.close)
            profile=home/'.gemini/config/agents/nativepeers-analysis.md';profile.parent.mkdir(parents=True);profile.write_text(worker_profile('analysis'))
            hooks=home/'.gemini/config/hooks.json';hooks.write_text(json.dumps({'nativepeers':desired(home)[hooks][2]}))
            mcp=home/'.gemini/config/mcp_config.json';facade=desired(home)[mcp][2]
            if scenario!='mcp-missing':
                mcp.write_text(json.dumps({'mcpServers':{'nativepeers':dict(facade,disabled=True) if scenario=='mcp-disabled' else facade}}))
            if scenario=='mcp-workspace':
                workspace=home/'.agents/mcp_config.json';workspace.parent.mkdir()
                workspace.write_text(json.dumps({'mcpServers':{'nativepeers':dict(facade,command='/workspace-fixture')}}))
            group,worker,first,second=[new_id() for _ in range(4)]
            binary=home/'fake_antigravity.py';shutil.copyfile(ROOT/'tests/fixtures/fake_antigravity.py',binary);binary.chmod(0o755)
            binary.write_text(binary.read_text().replace('#!/usr/bin/env python3','#!'+sys.executable,1))
            binary.with_suffix('.scenario').write_text(scenario)
            # A compiled owner keeps its executable identity while Python starts.
            # A Python shebang can exec Python.app after the worker records its PID.
            native_binary=home/'.local/bin/agy';native_binary.parent.mkdir(parents=True)
            source=home/'native.c';source.write_text('''
#include <unistd.h>
#include <sys/wait.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    pid_t child = fork();
    if (child < 0) return 2;
    if (!child) {
        char **args = calloc(argc + 2, sizeof(char *));
        args[0] = PYTHON; args[1] = SCRIPT;
        for (int i = 1; i < argc; ++i) args[i + 1] = argv[i];
        execv(PYTHON, args); _exit(127);
    }
    int status;
    if (waitpid(child, &status, 0) < 0) return 3;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
''')
            compiler=shutil.which('cc')
            if compiler is None:self.skipTest('A C compiler is required for the native owner fixture')
            subprocess.run([compiler,'-DPYTHON='+json.dumps(sys.executable),'-DSCRIPT='+json.dumps(str(binary)),
                str(source),'-o',str(native_binary)],check=True,capture_output=True,timeout=30)
            # Ordinary stream/MCP startup has a bounded completion allowance;
            # deadline behavior is exercised separately, without cold-start timing assumptions.
            spec={'provider':'antigravity','model':'gemini-fixture-high','title':'fixture','cwd':str(cwd),'permission_profile':'analysis','max_task_turns':2,'wall_time_seconds':30}
            if scenario=='display-model':spec['model_display_name']='Gemini Fixture High'
            lease={'spec':spec,'parent_key':'parent','binding':str(j.root/'bindings'/new_id()),'grants':{'group_id':group,'worker_id':worker},'expires_at':time.time()+120,'executable':str(native_binary)}
            j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RESERVED','{}'))
            j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'antigravity',None,'fixture',spec['model'],None,None,str(cwd),None,'RESERVED',1,canonical(lease).decode()))
            for task in ([first] if resume else [first,second]):
                j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'QUEUED',None,None,1,'{}'))
            atomic_json(j.root/'compat/runtime.json',{'providers':{'antigravity':fingerprint(native_binary)}})
            value=Worker(j,worker)
            try:
                if scenario.startswith(('mcp-','project-')):
                    with patch.object(value,'emit') as emit,patch('asyncio.create_subprocess_exec',side_effect=AssertionError('provider launched')):
                        with self.assertRaises(PeerError) as error:await value.prepare()
                    self.assertEqual(error.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
                    if scenario.startswith('project-'):
                        check=next(item for item in error.exception.details if item['path']==str(override))
                        self.assertNotEqual(check['state'],'PRESENT')
                    self.assertIsNone(value.provider)
                    emit.assert_not_called()
                    self.assertEqual(j.one('SELECT state FROM workers')['state'],'RESERVED')
                    self.assertEqual({row['state'] for row in j.all('SELECT state FROM tasks')},{'QUEUED'})
                    return
                await value.prepare()
                async def changed_turn(task):
                    expected_code='PERMISSION_CAPABILITY_UNKNOWN'
                    if followup_change=='binary-drift':
                        native_binary.write_bytes(native_binary.read_bytes()+b'fixture binary drift')
                        expected_code='CAPABILITY_MISMATCH'
                    elif followup_change=='hooks-disabled':hooks.write_text('{"nativepeers":{"enabled":false}}')
                    elif followup_change=='profile-modified':profile.write_text('modified fixture profile')
                    elif followup_change=='mcp-disabled':mcp.write_text(json.dumps({'mcpServers':{'nativepeers':dict(facade,disabled=True)}}))
                    else:
                        binary.with_suffix('.scenario').write_text('no-hook')
                        manifest=value.manifest
                        def mismatched_hook():
                            manifest()
                            if not value.provider_identity or followup_change=='no-hook':return
                            process=dict(value.provider_identity)
                            if followup_change=='wrong-hook-pid':process['pid']+=100000
                            if followup_change=='wrong-hook-start':process['start']+='-old'
                            payload={'native_id':value.native,'model':spec['model'],'event':'PreInvocation','process':process}
                            if followup_change=='old-hook-boundary':
                                # Repeated manifest writes still represent one old hook event.
                                j.db.execute('INSERT OR IGNORE INTO events(sequence,subject_id,kind,payload_json,at) VALUES(?,?,?,?,?)',
                                    (0,worker,'native-hook-observed',canonical(payload).decode(),time.time()))
                            else:j.event(worker,'native-hook-observed',payload)
                        value.manifest=mismatched_hook
                        await value.task(task,'changed turn')
                        return
                    with patch('asyncio.create_subprocess_exec',side_effect=AssertionError('provider launched')) as launch:
                        await value.task(task,'changed turn')
                    launch.assert_not_called()
                    reason=json.loads(j.one('SELECT terminal_reason FROM tasks WHERE task_id=?',(task,))['terminal_reason'])
                    self.assertEqual(reason['code'],expected_code)
                if before_first:
                    await changed_turn(first)
                    self.assertIsNone(value.native)
                    return
                await value.task(first,'one');native=value.native
                first_process=dict(value.provider_identity or {})
                if resume:
                    await value.close()
                    saved=json.loads(j.one('SELECT lease_json FROM workers WHERE worker_id=?',(worker,))['lease_json'])
                    saved['released_native']=True
                    j.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(saved).decode(),worker))
                    value=Worker(j,worker);await value.prepare()
                    j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(second,worker,None,None,'QUEUED',None,None,1,'{}'))
                if followup:await value.task(second,'two')
                if followup_change:await changed_turn(second)
                expected='FAILED' if scenario in ('no-hook','wrong-id') else 'CANCELED' if scenario in ('CANCELED','INTERRUPTED') else 'COMPLETED'
                self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(first,))['state'],expected,
                    str(j.one('SELECT terminal_reason FROM tasks WHERE task_id=?',(first,))))
                if followup:
                    self.assertEqual(value.native,native)
                    self.assertTrue(all(r['state']=='COMPLETED' for r in j.all('SELECT state FROM tasks')))
                    self.assertNotEqual(value.provider_identity['pid'],first_process['pid'])
                    observations=[json.loads(row['payload_json']) for row in j.all("SELECT payload_json FROM events WHERE kind='native-hook-observed' ORDER BY sequence")]
                    self.assertEqual(observations[-1]['process'],value.provider_identity)
                if followup_change:
                    self.assertEqual(value.native,native)
                    self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(second,))['state'],'FAILED')
                    self.assertTrue(value.retire_after_task)
                    self.assertEqual(j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
                    reason=json.loads(j.one('SELECT terminal_reason FROM tasks WHERE task_id=?',(second,))['terminal_reason'])
                    self.assertEqual(reason['code'],'CAPABILITY_MISMATCH' if followup_change=='binary-drift' else 'PERMISSION_CAPABILITY_UNKNOWN')
                    # A terminal task is not replayed even if its input is offered again.
                    with patch('asyncio.create_subprocess_exec',side_effect=AssertionError('provider relaunched')) as launch:
                        await value.task(second,'must not replay')
                    launch.assert_not_called()
                if scenario=='display-model':self.assertEqual(value.model_observed,'gemini-fixture-high')
                if scenario!='no-hook':self.assertIsNotNone(j.one("SELECT * FROM events WHERE kind='antigravity-tool-gate'"))
                self.assertEqual(len(j.all('SELECT * FROM peer_instances')),2 if resume else 1)
            finally:await value.close()
