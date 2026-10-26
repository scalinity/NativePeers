"""Offline Grok-native identity, receipt and owned installation boundaries."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nativepeers.caller import bind_caller
from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json,canonical
from nativepeers.grok import summaries,resolve_name,queue_native_hook
from nativepeers.grok_hooks import observe,kernel_sandbox
from nativepeers.grok_install import install,desired,desired_profiles,verify_worker_ready
from nativepeers.install import compare_write
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class GrokTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'grok';self.home.mkdir()
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.native=new_id();self.owner=identity(os.getpid());self.binary=Path(self.owner.executable)
        self.summary=self.home/'sessions/encoded'/self.native/'summary.json';self.summary.parent.mkdir(parents=True,mode=0o700)
        self.metadata=dict(info=dict(id=self.native,cwd=str(self.root)),session_summary='Grok Review',current_model_id='grok-4.7',sandbox_profile='read-only')
        atomic_json(self.summary,self.metadata)
        atomic_json(self.j.root/'compat/runtime.json',{'providers':{'grok':fingerprint(self.binary)}})
        self.event=dict(sessionId=self.native,session_id=self.native,hookEventName='session_start',hook_event_name='SessionStart',cwd=str(self.root),workspaceRoot=str(self.root),permissionMode='plan')
        self.project=self.root/'project';self.project.mkdir();entry=self.project/'bin/npeer-grok-hook';entry.parent.mkdir();entry.write_text('#!/bin/sh\n');entry.chmod(0o755)
        for name,value in [('nativepeers.grok_hooks.executable',self.binary),('nativepeers.grok.executable',self.binary),('nativepeers.grok_hooks.native_home',self.home),('nativepeers.grok.native_home',self.home)]:
            p=patch(name,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch('nativepeers.grok_hooks.ancestry',return_value=[self.owner,self.owner]);p.start();self.addCleanup(p.stop)
        p=patch.dict(os.environ,{'NPEER_BOOTSTRAP':'','NPEER_BINDING':'','CODEX_THREAD_ID':''});p.start();self.addCleanup(p.stop)
        p=patch('nativepeers.grok_install.ROOT',self.project);p.start();self.addCleanup(p.stop)
        p=patch('nativepeers.grok_install.executable',return_value=self.binary);p.start();self.addCleanup(p.stop)
        self.evidence=self.root/'capabilities.json';atomic_json(self.evidence,{'providers':{'grok':dict(executable_sha256=fingerprint(self.binary)['sha256'],version='fixture')}})

    def hook(self,kind,**kwargs):
        events={'SessionStart':'session_start','SessionEnd':'session_end','UserPromptSubmit':'user_prompt_submit','PreToolUse':'pre_tool_use'}
        return observe(self.j,dict(self.event,hookEventName=events[kind],hook_event_name=kind,**kwargs),kind)

    def peer(self):return Registry(self.j).list('grok')[0]

    def message(self,peer,text='peer question'):
        message=new_id();payload=dict(schema_version=1,message_id=message,exchange_id=new_id(),kind='notify',to={'peer_key':peer['handle']},body={'text':text},**{'from':{'kind':'peer','peer_key':'sender'}})
        self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,payload['exchange_id'],'sender',peer['handle'],None,None,canonical(payload).decode(),'hash',time.time()+60,time.time()))
        self.j.db.execute('INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',(new_id(),message,peer['incarnation_id'],'grok-native-hook','SUBMITTED_UNCONFIRMED',time.time()))
        return payload

    def test_native_summaries_require_exact_own_uuid_and_title(self):
        self.assertEqual(resolve_name('Grok Review'),self.native)
        imported=self.home/'imported-claude'/self.native/'summary.json';imported.parent.mkdir(parents=True,mode=0o700);atomic_json(imported,self.metadata)
        self.assertEqual(len(summaries(title='Grok Review')),1)
        other=self.home/'sessions/encoded'/new_id()/'summary.json';atomic_json(other,self.metadata)
        self.assertEqual(len(summaries(title='Grok Review')),1)
        atomic_json(other,dict(self.metadata,info=dict(id=other.parent.name,cwd=str(self.root))))
        with self.assertRaises(PeerError) as error:resolve_name('Grok Review')
        self.assertEqual(error.exception.code,'AMBIGUOUS_TARGET')
        with self.assertRaises(PeerError):summaries(native_id='../outside')

    def test_registration_binds_exact_native_owner_and_updates_mode_without_rebinding(self):
        self.hook('SessionStart');peer=self.peer()
        with patch('nativepeers.caller.ancestry',return_value=[self.owner]):
            caller=bind_caller(self.j,Registry(self.j),allow_external=False)
        self.assertEqual(caller.key,peer['handle']);self.assertEqual(caller.grants['profiles'],['analysis'])
        self.hook('PreToolUse',permissionMode='dontAsk',toolName='read_file')
        self.assertEqual(self.peer()['incarnation_id'],peer['incarnation_id'])
        self.assertEqual(self.peer()['policy']['profiles'],['analysis','workspace'])
        self.assertEqual(Registry(self.j).resolve('grok:Grok Review')['handle'],peer['handle'])
        self.hook('SessionEnd');self.assertEqual(Registry(self.j).list('grok'),[])

    def test_rejected_native_payloads_do_not_replace_peer(self):
        self.hook('SessionStart');before=self.j.all('SELECT * FROM peer_instances')
        for updates in [dict(sessionId=new_id()),dict(session_id=new_id()),dict(cwd='relative',workspaceRoot='relative'),dict(hookEventName='SessionStart')]:
            with self.subTest(updates=updates),self.assertRaises(PeerError):observe(self.j,dict(self.event,**updates),'SessionStart')
            self.assertEqual(self.j.all('SELECT * FROM peer_instances'),before)
        child=new_id();atomic_json(self.summary.parent.parent/child/'summary.json',dict(self.metadata,info=dict(id=child,cwd=str(self.root))))
        with self.assertRaises(PeerError):self.hook('PreToolUse',sessionId=child,session_id=child,toolName='read_file')
        self.assertEqual(self.j.all('SELECT * FROM peer_instances'),before)

    def test_grok_compatibility_hook_refuses_outer_claude_misattribution(self):
        from nativepeers.hooks import observe as compatible
        outer=SimpleNamespace(json=lambda:dict(self.owner.json(),executable=str((Path.home()/'.local/bin/claude').resolve())))
        with patch('nativepeers.hooks.ancestry',return_value=[self.owner,self.owner,outer]):
            with self.assertRaises(PeerError) as error:compatible('claude',self.event,self.j)
        self.assertEqual(error.exception.code,'CALLER_UNBOUND');self.assertEqual(self.j.all('SELECT * FROM peer_instances'),[])

    def test_outer_binding_is_not_borrowed_by_unregistered_grok(self):
        outer=identity(os.getppid()).json();registry=Registry(self.j)
        peer=registry.register('claude',self.root,new_id(),'outer',outer,self.root,{'kind':'fake'})
        binding=self.j.root/'bindings/outer';atomic_json(binding,dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],owner=outer,expires_at=time.time()+60))
        with patch('nativepeers.caller.ancestry',return_value=[self.owner,SimpleNamespace(json=lambda:outer)]),patch.dict(os.environ,{'NPEER_BINDING':str(binding)}):
            with self.assertRaises(PeerError) as error:bind_caller(self.j,registry,allow_external=False)
        self.assertEqual(error.exception.code,'CALLER_UNBOUND')

    def test_inbox_is_emitted_once_only_on_documented_tool_context_route(self):
        self.hook('SessionStart');peer=self.peer();payload=self.message(peer)
        self.assertEqual(self.hook('UserPromptSubmit'),{})
        context=self.hook('PreToolUse',toolName='read_file')['hookSpecificOutput']['additionalContext']
        self.assertIn(payload['message_id'],context);self.assertIn('peer question',context)
        self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{})
        self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'SUBMITTED_UNCONFIRMED')
        self.assertEqual(asyncio.run(queue_native_hook(peer,payload))['stage'],'SUBMITTED_UNCONFIRMED')

    def test_changed_runtime_cannot_register_or_emit(self):
        receipt=self.j.root/'compat/runtime.json';atomic_json(receipt,{'providers':{'grok':dict(fingerprint(self.binary),sha256='changed')}})
        with self.assertRaises(PeerError) as error:self.hook('SessionStart')
        self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH');self.assertEqual(self.j.all('SELECT * FROM peer_instances'),[])

    def test_receipt_reads_only_exact_native_user_record(self):
        from nativepeers.reconcile import observe as reconcile
        from nativepeers.routing import Router
        self.hook('SessionStart');peer=self.peer();payload=self.message(peer)
        history=self.summary.parent/'chat_history.jsonl'
        history.write_text(json.dumps(dict(type='assistant',content=[dict(type='text',text=canonical(payload).decode())]))+'\n')
        router=Router(self.j,Registry(self.j))
        result=asyncio.run(reconcile(self.j,Registry(self.j),router,payload['message_id']))
        self.assertEqual(result['state'],'SUBMITTED_UNCONFIRMED')
        history.write_text(json.dumps(dict(type='user',content=[dict(type='text',text=canonical(payload).decode())],prompt_index=3))+'\n')
        result=asyncio.run(reconcile(self.j,Registry(self.j),router,payload['message_id']))
        self.assertEqual(result['state'],'INGESTED');self.assertEqual(result['reconciliation']['source_path'],str(history.resolve()))

    def test_installer_idempotence_preserves_other_files_and_existing_approvals(self):
        sibling=self.home/'hooks/unrelated.json';atomic_json(sibling,{'hooks':{}});before=sibling.read_bytes()
        receipt=self.j.root/'compat/runtime.json';approval=receipt.read_bytes()
        self.assertTrue(install(self.j,home=self.home)['changed'])
        self.assertEqual(install(self.j,home=self.home)['operations'],[])
        self.assertEqual(install(self.j,home=self.home,remove=True,dry_run=True)['operations'],[{'path':str(path),'operation':'remove-owned'} for path in (self.home/'hooks/nativepeers.json',self.home/'sandbox.toml')])
        self.assertTrue(install(self.j,home=self.home,remove=True)['changed'])
        self.assertEqual(install(self.j,home=self.home,remove=True)['operations'],[])
        self.assertEqual(sibling.read_bytes(),before);self.assertEqual(receipt.read_bytes(),approval)

    def test_installer_refuses_owned_local_edits_and_preserves_concurrent_write(self):
        install(self.j,home=self.home);path=self.home/'hooks/nativepeers.json'
        current=json.loads(path.read_text());current['later']='keep';path.write_text(json.dumps(current));before=path.read_bytes()
        with self.assertRaises(PeerError):install(self.j,home=self.home,remove=True)
        self.assertEqual(path.read_bytes(),before)
        path.unlink();(self.j.root/'grok-install.json').unlink()
        def concurrent(path,raw,expected):
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('{"later":"keep"}');compare_write(path,raw,expected)
        with patch('nativepeers.grok_install.compare_write',side_effect=concurrent),self.assertRaises(PeerError) as error:install(self.j,home=self.home)
        self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT');self.assertEqual(json.loads(path.read_text()),{'later':'keep'})

    def test_interrupted_installer_recovers_before_and_after_write(self):
        for after_write in (False,True):
            with self.subTest(after_write=after_write):
                def interrupted(*args):
                    if after_write:compare_write(*args)
                    raise OSError('fixture interruption')
                with patch('nativepeers.grok_install.compare_write',side_effect=interrupted),self.assertRaises(OSError):install(self.j,home=self.home)
                self.assertTrue(install(self.j,home=self.home)['changed'])
                install(self.j,home=self.home,remove=True)

    def test_initial_approval_requires_exact_nonquota_runtime_evidence(self):
        receipt=self.j.root/'compat/runtime.json';receipt.unlink()
        for updates in [dict(executable_sha256='changed',version='fixture'),dict(executable_sha256=fingerprint(self.binary)['sha256'],version='fixture',scope='usage-only')]:
            atomic_json(self.evidence,{'providers':{'grok':updates}})
            with self.assertRaises(PeerError) as error:install(self.j,home=self.home,verified_source=self.evidence)
            self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH');self.assertFalse((self.home/'hooks/nativepeers.json').exists())
        atomic_json(receipt,{'providers':{'claude':{'sha256':'preserve'}}})
        atomic_json(self.evidence,{'providers':{'grok':dict(executable_sha256=fingerprint(self.binary)['sha256'],version='fixture')}})
        self.assertTrue(install(self.j,home=self.home,verified_source=self.evidence)['compatibility_updated'])
        self.assertEqual(json.loads(receipt.read_text())['providers']['claude'],{'sha256':'preserve'})

    def test_readiness_requires_runtime_hooks_and_native_sandbox_tools(self):
        spec=dict(cwd=str(self.root),provider_home=str(self.home),permission_profile='analysis',model='grok-4.7')
        with self.assertRaises(PeerError):verify_worker_ready(self.j,spec,self.binary)
        install(self.j,home=self.home);verify_worker_ready(self.j,spec,self.binary)
        with patch('nativepeers.grok_worker.arguments',return_value=['--tools','read_file']),self.assertRaises(PeerError):verify_worker_ready(self.j,spec,self.binary)

    def test_kernel_check_refuses_unknown_and_requires_expected_permissions(self):
        spec=dict(cwd=str(self.root),permission_profile='analysis',state_root=str(self.j.root))
        with patch('nativepeers.grok_hooks.sys.platform','other'):self.assertFalse(kernel_sandbox(spec)['verified'])
        class Check:
            restype=None
            def __call__(self,*args):return 1
        with patch('ctypes.CDLL',return_value=SimpleNamespace(sandbox_check=Check())),patch('nativepeers.grok_hooks.sys.platform','darwin'):
            self.assertTrue(kernel_sandbox(spec)['verified'])
            self.assertFalse(kernel_sandbox(dict(spec,permission_profile='workspace'))['verified'])

    @unittest.skipUnless(sys.platform=='darwin','Requires macOS Seatbelt')
    def test_kernel_check_uses_real_variadic_path_arguments_under_seatbelt(self):
        source=Path(__file__).resolve().parents[2]
        cwd=self.root.resolve()/'allowed';cwd.mkdir()
        state=self.j.root.resolve();denied=state/'grok-kernel-denied'
        parent_probe=cwd.parent/'.npeer-kernel-probe'
        denied.mkdir()
        for probe in (cwd/'.npeer-kernel-probe',denied/'.npeer-kernel-probe',parent_probe):probe.write_text('fixture\n')
        profile='(version 1)\n(allow default)\n(deny file-write* (literal '+json.dumps(str(parent_probe))+') (subpath '+json.dumps(str(denied))+'))\n'
        spec=dict(cwd=str(cwd),state_root=str(state),permission_profile='workspace')
        program='import json,sys; from nativepeers.grok_hooks import kernel_sandbox; print(json.dumps(kernel_sandbox(json.loads(sys.argv[1]))))'
        result=subprocess.run(['/usr/bin/sandbox-exec','-p',profile,sys.executable,'-c',program,json.dumps(spec)],
            cwd=cwd,env=dict(os.environ,PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        observation=json.loads(result.stdout)
        self.assertTrue(observation['verified'],observation)
        self.assertEqual([(check['path'],check['result']) for check in observation['checks']],
            [(str(cwd/'.npeer-kernel-probe'),0),(str(denied/'.npeer-kernel-probe'),1),(str(parent_probe),1)])

    def test_actual_entrypoint_denies_unverified_owned_tool_call_with_exit_two(self):
        source=Path(__file__).resolve().parents[2]
        env=dict(os.environ,NPEER_HOME=str(self.j.root),NPEER_BOOTSTRAP=str(self.j.root/'bindings/missing'),PYTHONPATH=str(source))
        event=dict(self.event,hookEventName='pre_tool_use',hook_event_name='PreToolUse',toolName='read_file')
        result=subprocess.run([sys.executable,'-m','nativepeers.grok_hooks','PreToolUse'],input=json.dumps(event),capture_output=True,text=True,env=env,cwd=source,timeout=5)
        self.assertEqual(result.returncode,2);self.assertEqual(json.loads(result.stdout)['decision'],'deny')

    def owned_fixture(self):
        worker=new_id();group=new_id();binding=self.j.root/'bindings'/new_id()
        spec=dict(cwd=str(self.root),provider_home=str(self.home),permission_profile='analysis',model='grok-4.7')
        lease=dict(spec=spec,executable=str(self.binary),verified_runtime=fingerprint(self.binary))
        atomic_json(binding,dict(state='BOUND',owner=self.owner.json(),expires_at=time.time()+60,grants={'worker_id':worker}))
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'grok',self.native,'worker','grok-4.7',None,None,str(self.root),None,'RUNNING',1,canonical(lease).decode()))
        atomic_json(self.j.root/'workers'/(worker+'.json'),dict(worker_id=worker,group_id=group,generation=1,owner=self.owner.json(),native_process=self.owner.json(),native_id=self.native))
        return worker,binding

    def test_owned_hook_requires_kernel_and_fixed_tool_proofs(self):
        worker,binding=self.owned_fixture()
        with patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding)}),patch('nativepeers.grok_hooks.kernel_sandbox',return_value={'verified':True,'profile':'analysis'}):
            self.hook('SessionStart')
            self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{'decision':'allow'})
            self.assertEqual(self.hook('PreToolUse',toolName='write')['decision'],'deny')
        event=json.loads(self.j.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='grok-kernel-check'",(worker,))['payload_json'])
        self.assertEqual(event['native_id'],self.native);self.assertEqual(event['process'],self.owner.json());self.assertTrue(event['verified'])
        with patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding)}),patch('nativepeers.grok_hooks.kernel_sandbox',return_value={'verified':False,'profile':'analysis'}):
            self.assertEqual(self.hook('PreToolUse',toolName='read_file')['decision'],'deny')
        self.assertEqual(self.j.all('SELECT * FROM peer_instances'),[])

    def test_owned_hook_uses_exact_pinned_runtime_after_current_symlink_moves(self):
        worker,binding=self.owned_fixture();new=self.root/'updated-runtime';new.write_text('updated')
        with patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding)}),patch('nativepeers.grok_hooks.executable',return_value=new),patch('nativepeers.grok_hooks.kernel_sandbox',return_value={'verified':True,'profile':'analysis'}):
            self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{'decision':'allow'})
        self.assertIsNotNone(self.j.one("SELECT * FROM events WHERE subject_id=? AND kind='native-hook-observed'",(worker,)))

    def test_fixture_project_hooks_can_qualify_without_ignoring_global_conflict(self):
        spec=dict(cwd=str(self.root),provider_home=str(self.home),permission_profile='analysis',model='grok-4.7')
        project=self.root/'.grok/hooks/nativepeers.json';atomic_json(project,next(iter(desired(self.home).values())))
        import tomlkit
        (self.root/'.grok/sandbox.toml').write_text(tomlkit.dumps({'profiles':desired_profiles(self.j.root)}))
        verify_worker_ready(self.j,spec,self.binary)
        global_path=self.home/'hooks/nativepeers.json';atomic_json(global_path,{'hooks':{}})
        with self.assertRaises(PeerError) as error:verify_worker_ready(self.j,spec,self.binary)
        self.assertEqual(error.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')

    def test_installer_preserves_unrelated_sandbox_bytes_and_uninstalls_only_named_profiles(self):
        path=self.home/'sandbox.toml'
        before='# Existing sandbox comment\n[profiles.personal]\nextends = "strict" # keep this\nread_only = ["/a path"]\n'
        path.write_text(before)
        install(self.j,home=self.home)
        self.assertTrue(path.read_text().startswith(before))
        self.assertEqual(install(self.j,home=self.home)['operations'],[])
        with path.open('a') as stream:stream.write('\n# Later addition\n[profiles.later]\nextends = "workspace"\n')
        install(self.j,home=self.home,remove=True)
        self.assertEqual(path.read_text(),before+'\n# Later addition\n[profiles.later]\nextends = "workspace"\n')

    def test_installer_preserves_concurrent_sandbox_edits(self):
        path=self.home/'sandbox.toml';calls=0
        def concurrent(target,raw,expected):
            nonlocal calls
            calls+=1
            if target==path:path.write_text('# concurrent content\n')
            compare_write(target,raw,expected)
        with patch('nativepeers.grok_install.compare_write',side_effect=concurrent),self.assertRaises(PeerError) as error:install(self.j,home=self.home)
        self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT');self.assertEqual(path.read_text(),'# concurrent content\n')
        install(self.j,home=self.home)
        self.assertTrue(path.read_text().startswith('# concurrent content\n'))

    def test_grok_owner_takes_precedence_over_inherited_codex_thread(self):
        self.hook('SessionStart');peer=self.peer()
        with patch('nativepeers.caller.ancestry',return_value=[self.owner]),patch.dict(os.environ,{'CODEX_THREAD_ID':new_id()}):
            caller=bind_caller(self.j,Registry(self.j),allow_external=False)
        self.assertEqual(caller.key,peer['handle']);self.assertEqual(caller.incarnation,peer['incarnation_id'])

    def test_individually_oversized_frame_is_refused_before_hook_queue_acceptance(self):
        from nativepeers.caller import Caller
        from nativepeers.routing import Router
        self.hook('SessionStart');peer=self.peer()
        result=asyncio.run(Router(self.j,Registry(self.j)).send(Caller('peer','sender'),peer['handle'],'x'*12000))
        self.assertEqual(result['state'],'PAYLOAD_TOO_LARGE')
        self.assertFalse(json.loads(result['attempt']['error_json'])['may_have_received'])
        self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{})
        self.assertIsNone(self.j.one("SELECT * FROM events WHERE kind='grok-hook-output-unconfirmed'"))

    def test_hook_aggregate_limit_keeps_complete_frames_for_later_invocations(self):
        from nativepeers.presentation import render
        self.hook('SessionStart');peer=self.peer()
        first=self.message(peer,'a'*6000);second=self.message(peer,'b'*6000)
        first_context=self.hook('PreToolUse',toolName='read_file')['hookSpecificOutput']['additionalContext']
        self.assertEqual(first_context,render(first));self.assertLessEqual(len(first_context),10000)
        self.assertIsNone(self.j.one("SELECT * FROM events WHERE subject_id=? AND kind='grok-hook-output-unconfirmed'",(second['message_id'],)))
        second_context=self.hook('PreToolUse',toolName='read_file')['hookSpecificOutput']['additionalContext']
        self.assertEqual(second_context,render(second));self.assertLessEqual(len(second_context),10000)
        self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{})
        self.assertEqual({row['state'] for row in self.j.all('SELECT state FROM attempts')},{'SUBMITTED_UNCONFIRMED'})

    def test_oversized_unemitted_legacy_frame_cannot_block_later_or_replay_uncertain_output(self):
        self.hook('SessionStart');peer=self.peer()
        uncertain=self.message(peer,'u'*12000)
        self.j.event(uncertain['message_id'],'grok-hook-output-unconfirmed',{'native_id':self.native})
        legacy=self.message(peer,'x'*12000);later=self.message(peer,'deliver this complete frame')
        context=self.hook('PreToolUse',toolName='read_file')['hookSpecificOutput']['additionalContext']
        self.assertIn(later['message_id'],context)
        for payload in (uncertain,legacy):self.assertNotIn(payload['message_id'],context)
        refused=self.j.one('SELECT state,error_json FROM attempts WHERE message_id=?',(legacy['message_id'],))
        self.assertEqual(refused['state'],'REJECTED');self.assertFalse(json.loads(refused['error_json'])['may_have_received'])
        self.assertIsNotNone(self.j.one("SELECT * FROM events WHERE subject_id=? AND kind='grok-hook-output-refused'",(legacy['message_id'],)))
        self.assertEqual(self.j.one('SELECT state FROM attempts WHERE message_id=?',(uncertain['message_id'],))['state'],'SUBMITTED_UNCONFIRMED')
        self.assertEqual(self.hook('PreToolUse',toolName='read_file'),{})

    def test_hook_context_character_boundary_counts_unicode_and_preserves_byte_bound(self):
        from nativepeers.presentation import render
        self.hook('SessionStart');peer=self.peer();payload=self.message(peer,'')
        prefix=len(render(payload));payload['body']['text']='🙂'*(10000-prefix)
        self.assertEqual(len(render(payload)),10000)
        self.assertGreater(len(render(payload).encode()),10000)
        self.assertEqual(asyncio.run(queue_native_hook(peer,payload))['stage'],'SUBMITTED_UNCONFIRMED')
        self.j.db.execute('UPDATE messages SET payload_json=? WHERE message_id=?',(canonical(payload).decode(),payload['message_id']))
        output=self.hook('PreToolUse',toolName='read_file')
        self.assertEqual(output['hookSpecificOutput']['additionalContext'],render(payload))
        self.assertLessEqual(len(canonical(output)),65536)
        payload['body']['text']+='🙂'
        with self.assertRaises(PeerError) as error:asyncio.run(queue_native_hook(peer,payload))
        self.assertEqual(error.exception.code,'PAYLOAD_TOO_LARGE')
        self.assertEqual(error.exception.details['rendered_characters'],10001)
