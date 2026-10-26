import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import AsyncMock,patch

from nativepeers.caller import Caller
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.grok_worker import arguments,allowed_tools,tool_ids,HookEvidence
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment,provider_home,home_environment,apply_home
from nativepeers.models import new_id,PeerKey,home_id
from nativepeers.process import identity
from nativepeers.recovery import resume_command
from nativepeers.registry import Registry


class GrokWorkerTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_arguments_and_exact_uuid_resume(self):
        for profile in ('analysis','workspace'):
            with self.subTest(profile=profile):
                spec=dict(model='grok-4.7',permission_profile=profile,native_session_id=new_id())
                args=arguments(spec)
                self.assertEqual(args[args.index('--session-id')+1],spec['native_session_id'])
                self.assertEqual(args[args.index('--sandbox')+1],'nativepeers-'+profile)
                self.assertEqual(args[args.index('--tools')+1],','.join(tool_ids(profile)))
                self.assertEqual(args[args.index('--permission-mode')+1],'dontAsk')
                self.assertEqual(args[args.index('--max-turns')+1],'8')
                self.assertEqual(args[args.index('--deny')+1],'MCPTool(*)')
                self.assertIn('--no-auto-update',args);self.assertIn('--no-subagents',args);self.assertIn('--disable-web-search',args)
                if profile=='workspace':
                    self.assertIn('run_terminal_command',allowed_tools(profile))
                    self.assertIn('run_terminal_cmd',tool_ids(profile))
                native=new_id();resume=arguments(spec,native)
                self.assertNotIn('--session-id',resume);self.assertEqual(resume[resume.index('--resume')+1],native)
        with self.assertRaises(PeerError) as caught:arguments(dict(model='grok-4.7',permission_profile='analysis',reasoning_effort='high'))
        self.assertEqual(caught.exception.code,'EFFORT_UNSUPPORTED')

    def test_launch_home_and_no_credentials_or_ambient_overlays_are_imported(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'GROK_HOME':directory,'XAI_API_KEY':'not-imported',
             'GROK_CONFIG':'not-imported','GROK_CLAUDE_HOOKS_ENABLED':'true','GROK_DISABLE_AUTOUPDATER':'0'}):
            env=launch_environment('grok')
            self.assertEqual(provider_home('grok'),Path(directory).resolve())
            self.assertNotIn('XAI_API_KEY',env);self.assertNotIn('GROK_CONFIG',env)
            self.assertEqual(env['GROK_DISABLE_AUTOUPDATER'],'1')
            self.assertEqual(env['GROK_CLAUDE_HOOKS_ENABLED'],'false')
            home=Path(directory)/'pinned'
            self.assertEqual(apply_home('grok',home,env)['GROK_HOME'],str(home))
            self.assertEqual(home_environment('grok',home),{'GROK_HOME':str(home)})
            key=PeerKey(new_id(),'grok',home_id(home),new_id())
            self.assertTrue(key.handle.startswith('np1_'))

    async def test_spawn_check_resolves_native_default_once_and_preserves_exact_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();native_home=root/'.grok';native_home.mkdir();binary=root/'grok';binary.write_text('fixture')
            journal=Journal(root/'state');self.addCleanup(journal.close);registry=Registry(journal)
            parent=registry.register('codex',root,new_id(),'parent',identity(os.getpid()).json(),root,{'kind':'fake'})
            caller=Caller('peer',parent['handle'],parent['incarnation_id']);delegation=Delegation(journal,registry)
            catalog={'models':{'grok-4.7':'grok-4.7','grok-4.5':'grok-4.5'},'default':'grok-4.7'}
            with patch('nativepeers.grok.executable',return_value=binary),patch.dict(os.environ,{'GROK_HOME':str(native_home)}), \
                 patch('nativepeers.grok_worker.catalog',new_callable=AsyncMock,return_value=catalog), \
                 patch('nativepeers.compatibility.verify'),patch('nativepeers.grok_install.verify_worker_ready'):
                result=await delegation.spawn(caller,{'workers':[{'provider':'grok','title':'default','task':'fixture'},
                    {'provider':'grok','title':'explicit','task':'fixture','model':'grok-4.5'}]},check=True)
                self.assertEqual(result['state'],'CHECKED')
                self.assertEqual([item['model'] for item in result['workers']],['grok-4.7','grok-4.5'])
                self.assertEqual([item['provider_home'] for item in result['workers']],[str(native_home)]*2)
                self.assertEqual([item['executable'] for item in result['workers']],[str(binary)]*2)
                self.assertEqual(journal.one('SELECT COUNT(*) AS n FROM workers')['n'],0)
                with self.assertRaises(PeerError) as caught:
                    await delegation.spawn(caller,{'workers':[{'provider':'grok','title':'bad','task':'fixture','model':'missing'}]},check=True)
                self.assertEqual(caught.exception.code,'MODEL_UNAVAILABLE')
                with self.assertRaises(PeerError):
                    await delegation.spawn(caller,{'workers':[{'provider':'grok','title':'bad','task':'fixture','fallbacks':[{'provider':'antigravity','model':'gemini-fixture'}]}]},check=True)

    async def test_unapproved_runtime_cannot_launch_the_catalog_even_for_spawn_check(self):
        from nativepeers.compatibility import fingerprint
        from nativepeers.files import atomic_json
        for check in (False,True):
            for approval in ('missing','changed'):
                with self.subTest(check=check,approval=approval),tempfile.TemporaryDirectory() as directory:
                    root=Path(directory).resolve();home=root/'.grok';home.mkdir();binary=root/'grok';binary.write_text('approved fixture')
                    journal=Journal(root/'state');self.addCleanup(journal.close);registry=Registry(journal)
                    parent=registry.register('codex',root,new_id(),'parent',identity(os.getpid()).json(),root,{'kind':'fake'})
                    caller=Caller('peer',parent['handle'],parent['incarnation_id']);delegation=Delegation(journal,registry)
                    if approval=='changed':
                        atomic_json(journal.root/'compat/runtime.json',{'providers':{'grok':fingerprint(binary)}})
                        binary.write_text('changed unapproved fixture runtime')
                    with patch('nativepeers.grok.executable',return_value=binary),patch.dict(os.environ,{'GROK_HOME':str(home)}), \
                         patch('nativepeers.grok_worker.catalog',new_callable=AsyncMock,side_effect=AssertionError('Unapproved catalog was called')) as catalog, \
                         patch('nativepeers.grok_worker.subprocess.Popen') as process:
                        with self.assertRaises(PeerError) as caught:
                            await delegation.spawn(caller,{'workers':[{'provider':'grok','title':'fixture','task':'fixture'}]},check=check)
                        self.assertEqual(caught.exception.code,'CAPABILITY_MISMATCH')
                        catalog.assert_not_called();process.assert_not_called()
                    self.assertEqual(journal.one('SELECT COUNT(*) AS n FROM workers')['n'],0)

    def test_manual_resume_uses_leased_binary_uuid_model_home_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();binary=root/'grok-1.0.70';binary.touch();native=new_id()
            spec=dict(model='grok-4.7',provider='grok',permission_profile='analysis',cwd=str(root))
            row=dict(worker_id='worker',provider='grok',native_id=native,model_requested='grok-4.7',model_observed='grok-4.7',effort_requested=None,
                     cwd=str(root),lease_json=json.dumps(dict(executable=str(binary),provider_home=str(root),spec=spec)))
            ambient=root/'ambient home'
            with patch.dict(os.environ,{'GROK_HOME':str(ambient)}), \
                 patch('nativepeers.recovery.owned_worker',return_value=(row,{})),patch('nativepeers.recovery.assert_released'):
                result=resume_command(None,None,'worker')
            argv=result['native_argv']
            self.assertEqual(argv[0],str(binary));self.assertEqual(argv[argv.index('--resume')+1],native)
            self.assertEqual(argv[argv.index('--model')+1],'grok-4.7');self.assertIn('--no-auto-update',argv)
            self.assertNotIn('--prompt-file',argv);self.assertNotIn('--output-format',argv)
            self.assertEqual(result['native_env']['GROK_HOME'],str(root));self.assertEqual(result['native_env']['GROK_DISABLE_AUTOUPDATER'],'1')
            self.assertIn('GROK_HOME='+str(root),shlex.split(result['command']))
            self.assertNotIn('GROK_HOME='+str(ambient),shlex.split(result['command']))

    def hook_event(self,native):
        return {'method':'_x.ai/session/update','params':{'sessionId':native,'update':{'sessionUpdate':'hook_execution',
            'event_name':'session_start','runs':[{'name':'project/nativepeers:session_start[0].hooks[0]','status':{'status':'success'}}]},
            '_meta':{'eventId':'native-hook-fixture'}}}

    def test_native_hook_reader_ignores_old_records_and_waits_for_complete_new_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();native=new_id();initial=HookEvidence(root,root,native)
            path=initial.path;path.parent.mkdir(parents=True)
            old=(json.dumps(self.hook_event(native))+'\n').encode();path.write_bytes(old)
            cursor=HookEvidence(root,root,native);self.assertIsNone(cursor.read())
            fresh=(json.dumps(self.hook_event(native))+'\n').encode()
            with path.open('ab') as stream:stream.write(fresh[:19])
            self.assertIsNone(cursor.read())
            with path.open('ab') as stream:stream.write(fresh[19:])
            proof=cursor.read()
            self.assertEqual(proof['native_id'],native);self.assertEqual(proof['from_offset'],len(old))
            self.assertEqual(proof['to_offset'],len(old)+len(fresh));self.assertEqual(cursor.read(),proof)

    def test_native_hook_reader_refuses_replacement_truncation_and_oversized_appends(self):
        for change,code in [('replace','OWNERSHIP_CONFLICT'),('truncate','OWNERSHIP_CONFLICT'),('oversize','SOURCE_OUTPUT_LIMIT')]:
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();initial=HookEvidence(root,root,new_id());path=initial.path;path.parent.mkdir(parents=True)
                path.write_text('{}\n');cursor=HookEvidence(root,root,initial.native_id)
                if change=='replace':
                    replacement=path.with_suffix('.new');replacement.write_text('{}\n');replacement.replace(path)
                elif change=='truncate':path.write_text('')
                else:
                    with path.open('ab') as stream:stream.write(b'x'*(HookEvidence.LIMIT+1))
                with self.assertRaises(PeerError) as caught:cursor.read()
                self.assertEqual(caught.exception.code,code)

    def test_native_hook_reader_requires_exact_uuid_and_successful_owned_handler(self):
        for invalid,code in [('uuid','OWNERSHIP_CONFLICT'),('status','PERMISSION_CAPABILITY_UNKNOWN')]:
            with self.subTest(invalid=invalid),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();native=new_id();cursor=HookEvidence(root,root,native);cursor.path.parent.mkdir(parents=True)
                event=self.hook_event(new_id() if invalid=='uuid' else native)
                if invalid=='status':event['params']['update']['runs'][0]['status']['status']='error'
                cursor.path.write_text(json.dumps(event)+'\n')
                with self.assertRaises(PeerError) as caught:cursor.read()
                self.assertEqual(caught.exception.code,code)
