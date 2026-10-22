import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nativepeers.antigravity import observe, resolve_name, queue_native_hook
from nativepeers.antigravity_install import install, allowed_tools, desired, verify_tool_gate
from nativepeers.install import compare_write
from nativepeers.files import canonical,atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.compatibility import fingerprint


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.home=Path(self.temporary.name);self.j=Journal(self.home/'state');self.addCleanup(self.j.close)
        self.project=self.home/'project'
        skill=self.project/'integrations/claude/skills/nativepeers/SKILL.md'
        skill.parent.mkdir(parents=True);skill.write_text('NativePeers fixture skill\n')
        root_patch=patch('nativepeers.antigravity_install.ROOT',self.project)
        root_patch.start();self.addCleanup(root_patch.stop)
        self.native=new_id();self.provider_home=self.home/'.gemini/antigravity-cli';self.provider_home.mkdir(parents=True)
        with sqlite3.connect(self.provider_home/'conversation_summaries.db') as db:
            db.execute('CREATE TABLE conversation_summaries(conversation_id TEXT,title TEXT)')
            db.execute('INSERT INTO conversation_summaries VALUES(?,?)',(self.native,'Argon Review'))
        self.owner=identity(os.getpid())
        binary=self.home/'.local/bin/agy';binary.parent.mkdir(parents=True);binary.symlink_to(self.owner.executable)
        self.evidence=self.home/'capabilities.json';self.evidence.write_text(json.dumps({'providers':{'antigravity':{'executable_sha256':fingerprint(binary)['sha256'],'version':'fixture'}}}))
        atomic_json(self.j.root/'compat/runtime.json',{'providers':{'antigravity':fingerprint(binary)}})
        self.event={'conversationId':self.native,'workspacePaths':[str(self.home)],'modelName':'gemini-4-argon-high',
                    'transcriptPath':str(self.provider_home/'brain'/self.native/'.system_generated/logs/transcript.jsonl')}

    def test_exact_native_names_reject_duplicates_and_leave_native_database_unchanged(self):
        with patch('nativepeers.antigravity.native_home',return_value=self.provider_home):
            self.assertEqual(resolve_name('Argon Review'),self.native)
            with sqlite3.connect(self.provider_home/'conversation_summaries.db') as db:
                db.execute('INSERT INTO conversation_summaries VALUES(?,?)',(new_id(),'Argon Review'))
            with self.assertRaises(Exception) as error:resolve_name('Argon Review')
            self.assertEqual(error.exception.code,'AMBIGUOUS_TARGET')

    def test_hook_keeps_incarnation_and_emits_pending_peer_text_only_once(self):
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            observe(self.j,self.event,'SessionStart')
            descriptor=json.loads(self.j.one('SELECT descriptor_json FROM peer_instances')['descriptor_json'])
            self.assertTrue(descriptor['policy']['can_spawn'])
            peer=self.j.one('SELECT peer_key,incarnation_id FROM peer_instances')
            message,attempt=new_id(),new_id();payload={'message_id':message,'from':{'kind':'peer'},'body':{'text':'fixture question'}}
            self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,new_id(),'sender',peer['peer_key'],None,None,canonical(payload).decode(),'hash',time.time()+60,time.time()))
            self.j.db.execute('INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
                              (attempt,message,peer['incarnation_id'],'antigravity-native-hook','SUBMITTED_UNCONFIRMED',time.time()))
            output=observe(self.j,self.event,'PreInvocation')
            self.assertIn('fixture question',output['injectSteps'][0]['ephemeralMessage'])
            self.assertEqual(observe(self.j,self.event,'PreInvocation'),{})
            self.assertEqual(self.j.one('SELECT incarnation_id FROM peer_instances')['incarnation_id'],peer['incarnation_id'])
            self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'SUBMITTED_UNCONFIRMED')

    def test_hook_advances_past_ten_previously_emitted_messages(self):
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            observe(self.j,self.event,'SessionStart')
            peer=self.j.one('SELECT peer_key,incarnation_id FROM peer_instances')
            messages=[]
            for index in range(11):
                message=new_id();messages.append(message)
                payload={'message_id':message,'body':{'text':'message '+str(index)}}
                self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,new_id(),'sender',peer['peer_key'],None,None,canonical(payload).decode(),'hash',time.time()+60,index))
                self.j.db.execute('INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
                    (new_id(),message,peer['incarnation_id'],'antigravity-native-hook','SUBMITTED_UNCONFIRMED',time.time()))
            first=observe(self.j,self.event,'PreInvocation')['injectSteps'][0]['ephemeralMessage']
            self.assertTrue(all(message in first for message in messages[:10]))
            self.assertNotIn(messages[10],first)
            second=observe(self.j,self.event,'PreInvocation')['injectSteps'][0]['ephemeralMessage']
            self.assertIn(messages[10],second)
            self.assertFalse(any(message in second for message in messages[:10]))
            self.assertEqual(observe(self.j,self.event,'PreInvocation'),{})
            self.assertEqual({r['state'] for r in self.j.all('SELECT state FROM attempts')},{'SUBMITTED_UNCONFIRMED'})

    def test_rejected_hook_payload_cannot_replace_a_registered_peer(self):
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            observe(self.j,self.event,'SessionStart')
            before=self.j.all('SELECT * FROM peer_instances')
            bad_events=[dict(self.event,conversationId=new_id()),
                        dict(self.event,transcriptPath=str(self.home/'outside.jsonl')),
                        dict(self.event,workspacePaths=[])]
            for event in bad_events:
                with self.subTest(event=event):
                    with self.assertRaises(Exception):observe(self.j,event,'SessionStart')
                    self.assertEqual(self.j.all('SELECT * FROM peer_instances'),before)

    def test_updated_binary_cannot_register_or_inject_retained_messages(self):
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            observe(self.j,self.event,'SessionStart')
            before=self.j.all('SELECT * FROM peer_instances');peer=before[0];message=new_id()
            self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,new_id(),'sender',peer['peer_key'],None,None,canonical({'message_id':message}).decode(),'hash',time.time()+60,time.time()))
            self.j.db.execute('INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
                (new_id(),message,peer['incarnation_id'],'antigravity-native-hook','SUBMITTED_UNCONFIRMED',time.time()))
            binary=self.home/'.local/bin/agy';binary.unlink();binary.write_text('changed executable')
            changed=SimpleNamespace(json=lambda:dict(self.owner.json(),executable=str(binary.resolve())))
            with patch('nativepeers.antigravity.ancestry',return_value=[self.owner,changed]):
                for kind in ['SessionStart','PreInvocation']:
                    with self.subTest(kind=kind):
                        with self.assertRaises(Exception) as error:observe(self.j,self.event,kind)
                        self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
            self.assertEqual(self.j.all('SELECT * FROM peer_instances'),before)
            self.assertIsNone(self.j.one("SELECT * FROM events WHERE kind='antigravity-hook-output-unconfirmed'"))
            self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'SUBMITTED_UNCONFIRMED')

    def test_install_is_idempotent_and_uninstall_preserves_later_unrelated_edits(self):
        path=self.home/'.gemini/config/hooks.json';path.parent.mkdir(parents=True);path.write_text('{"existing":{"enabled":false}}')
        install(self.j,home=self.home,verified_source=self.evidence)
        self.assertFalse(install(self.j,home=self.home,verified_source=self.evidence)['changed'])
        data=json.loads(path.read_text());data['later']={'enabled':True};path.write_text(json.dumps(data))
        install(self.j,home=self.home,remove=True)
        self.assertEqual(json.loads(path.read_text()),{'existing':{'enabled':False},'later':{'enabled':True}})
        self.assertTrue((self.home/'.local/bin/agy').exists())

    def test_bound_facade_checks_identity_without_title_discovery(self):
        from nativepeers.mcp_server import Facade
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':'','NPEER_BINDING':''}):
            observe(self.j,self.event,'SessionStart')
            facade=Facade(self.j,None)
            plan=self.j.all("EXPLAIN QUERY PLAN SELECT descriptor_json FROM peer_instances WHERE provider=? AND pid=? AND process_start=? AND status='live'",('antigravity',self.owner.pid,self.owner.start))
            self.assertTrue(any('peers_owner' in row['detail'] for row in plan))
            with patch('nativepeers.registry.Registry.list',side_effect=AssertionError('discovery called')), \
                 patch('nativepeers.antigravity.summaries',side_effect=AssertionError('native titles read')):
                for _ in range(10):
                    self.assertEqual(facade.binding_state()['state'],'BOUND')
                    self.assertIn('worker_spawn',facade.names())

    def test_changed_runtime_is_refused_before_any_configuration_write(self):
        self.evidence.write_text(json.dumps({'providers':{'antigravity':{'executable_sha256':'not-the-installed-binary','version':'fixture'}}}))
        with self.assertRaises(Exception) as error:install(self.j,home=self.home,verified_source=self.evidence)
        self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
        self.assertFalse((self.home/'.gemini/config/hooks.json').exists())

    def test_final_manifest_failure_recovers_install_and_uninstall_state(self):
        manifest=self.j.root/'antigravity-install.json'
        for remove,terminal in [(False,'INSTALLED'),(True,'UNINSTALLED')]:
            with self.subTest(remove=remove):
                def interrupt_final(path,value):
                    if path==manifest and value.get('state')==terminal:raise OSError('fixture final manifest interruption')
                    atomic_json(path,value)
                with patch('nativepeers.antigravity_install.atomic_json',side_effect=interrupt_final):
                    with self.assertRaises(OSError):install(self.j,home=self.home,verified_source=self.evidence,remove=remove)
                self.assertEqual(json.loads(manifest.read_text())['pending'],[])
                self.assertTrue(install(self.j,home=self.home,verified_source=self.evidence,remove=remove)['changed'])
                self.assertEqual(json.loads(manifest.read_text())['state'],terminal)

    def test_interrupted_install_recovers_every_owned_target(self):
        for fail_at,after_write in [(2,False),(3,False),(2,True),(3,True)]:
            with self.subTest(fail_at=fail_at,after_write=after_write):
                hooks=self.home/'.gemini/config/hooks.json';hooks.parent.mkdir(parents=True,exist_ok=True)
                hooks.write_text('{"unrelated":{"enabled":false}}')
                calls=0
                def interrupted(*args):
                    nonlocal calls
                    calls+=1
                    if calls==fail_at and not after_write:raise OSError('fixture interruption')
                    compare_write(*args)
                    if calls==fail_at and after_write:raise OSError('fixture interruption after write')
                with patch('nativepeers.antigravity_install.compare_write',side_effect=interrupted):
                    with self.assertRaises(OSError):install(self.j,home=self.home,verified_source=self.evidence)
                value=json.loads(hooks.read_text());value['later']={'enabled':True};hooks.write_text(json.dumps(value))
                install(self.j,home=self.home,verified_source=self.evidence)
                manifest=json.loads((self.j.root/'antigravity-install.json').read_text())
                self.assertEqual(set(manifest['owned']),{str(p) for p in desired(self.home)})
                install(self.j,home=self.home,remove=True)
                self.assertEqual(json.loads(hooks.read_text()),{'unrelated':{'enabled':False},'later':{'enabled':True}})
                self.assertFalse((self.home/'.gemini/config/agents/nativepeers-analysis.md').exists())
                self.assertNotIn('nativepeers',json.loads((self.home/'.gemini/config/mcp_config.json').read_text())['mcpServers'])

    def test_analysis_tool_gate_excludes_mutation_plugins_and_recursive_agents(self):
        self.assertTrue(allowed_tools('analysis','view_file'))
        self.assertTrue(allowed_tools('analysis','mcp__nativepeers__peer_reply'))
        for tool in ['write_to_file','run_command','invoke_subagent','mcp__unrelated__write','mcp__nativepeers__unknown_write','mcp_nativepeers_evil_write','schedule']:
            self.assertFalse(allowed_tools('analysis',tool))
        self.assertTrue(allowed_tools('workspace','run_command'))

    def test_tool_gate_refuses_missing_modified_and_overridden_hooks(self):
        hooks=self.home/'.gemini/config/hooks.json';hooks.parent.mkdir(parents=True)
        project=self.home/'.agents/hooks.json';settings=self.home/'.gemini/antigravity-cli/settings.json'
        with patch('nativepeers.antigravity_install.Path.home',return_value=self.home):
            for case in ['missing','modified','project','settings']:
                with self.subTest(case=case):
                    hooks.write_text(json.dumps({'nativepeers':desired(self.home)[hooks][2]}))
                    project.unlink(missing_ok=True);settings.unlink(missing_ok=True)
                    if case=='missing':hooks.unlink()
                    if case=='modified':hooks.write_text('{"nativepeers":{}}')
                    if case=='project':project.parent.mkdir(exist_ok=True);project.write_text('{"nativepeers":{}}')
                    if case=='settings':settings.write_text('{"hooks":{"nativepeers":{}}}')
                    with self.assertRaises(Exception) as error:verify_tool_gate({'cwd':str(self.home)})
                    self.assertEqual(error.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')

    def test_actual_hook_entry_point_denies_unverified_owned_tool_calls(self):
        source=Path(__file__).resolve().parents[2]
        env=dict(os.environ,HOME=str(self.home),PYTHONPATH=str(source),NPEER_HOME=str(self.j.root),NPEER_BINDING='',NPEER_BOOTSTRAP=str(self.j.root/'bindings/missing'))
        result=subprocess.run([sys.executable,'-m','nativepeers.antigravity','hook','PreToolUse'],input=json.dumps(dict(self.event,toolCall={'name':'view_file','args':{}})),
            capture_output=True,text=True,env=env,cwd=source,timeout=5,check=True)
        self.assertEqual(json.loads(result.stdout)['decision'],'deny')
        self.assertIsNotNone(self.j.one("SELECT * FROM events WHERE kind='hook-binding-error'"))

    def test_queue_adapter_route_retains_unknown_native_delivery(self):
        from nativepeers.caller import Caller
        from nativepeers.registry import Registry
        from nativepeers.routing import Router
        registry=Registry(self.j)
        sender=registry.register('claude',self.home,new_id(),'sender',self.owner.json(),str(self.home),{'kind':'fake'},owner_kind='test')
        target=registry.register('antigravity',self.provider_home,self.native,'target',self.owner.json(),str(self.home),{'kind':'antigravity-native-hook'},owner_kind='test')
        self.j.db.execute("UPDATE peer_instances SET status='live'")
        router=Router(self.j,registry,{'antigravity-native-hook':queue_native_hook})
        result=asyncio.run(router.send(Caller('peer',sender['handle'],sender['incarnation_id']),target['handle'],'fixture question'))
        self.assertEqual(result['state'],'SUBMITTED_UNCONFIRMED')
        self.assertEqual(self.j.one('SELECT target_incarnation FROM attempts')['target_incarnation'],target['incarnation_id'])

    def test_inbox_excludes_expired_messages_and_bounds_each_batch(self):
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            observe(self.j,self.event,'SessionStart');peer=self.j.one('SELECT peer_key,incarnation_id FROM peer_instances')
            messages=[]
            for index in range(3):
                message=new_id();messages.append(message);payload={'message_id':message,'body':{'text':'x'*33000}}
                self.j.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',(message,new_id(),'sender',peer['peer_key'],None,None,canonical(payload).decode(),'hash',time.time()+(-1 if index==0 else 60),index))
                self.j.db.execute('INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
                    (new_id(),message,peer['incarnation_id'],'antigravity-native-hook','SUBMITTED_UNCONFIRMED',time.time()))
            first=observe(self.j,self.event,'PreInvocation')['injectSteps'][0]['ephemeralMessage']
            self.assertIn(messages[1],first);self.assertNotIn(messages[0],first);self.assertNotIn(messages[2],first)
            second=observe(self.j,self.event,'PreInvocation')['injectSteps'][0]['ephemeralMessage']
            self.assertIn(messages[2],second);self.assertNotIn(messages[1],second)
            self.assertEqual(observe(self.j,self.event,'PreInvocation'),{})

    def test_native_tool_handlers_use_matchers_and_explicit_allow(self):
        config=desired(self.home)[self.home/'.gemini/config/hooks.json'][2]
        handler=config['PreToolUse'][0]
        self.assertEqual(handler['matcher'],'.*');self.assertEqual(handler['hooks'][0]['type'],'command')
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':''}):
            self.assertEqual(observe(self.j,dict(self.event,toolCall={'name':'view_file','args':{}}),'PreToolUse'),{'decision':'allow'})

    def test_bound_worker_hook_uses_its_granted_id_and_denies_writes(self):
        worker=new_id();binding=self.j.root/'bindings'/new_id()
        lease={'state':'BOUND','owner':self.owner.json(),'expires_at':time.time()+60,'grants':{'worker_id':worker}}
        atomic_json(binding,lease)
        group=new_id();self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'antigravity',self.native,'worker','gemini-4-argon-high',None,None,str(self.home),None,'RUNNING',1,canonical({'spec':{'permission_profile':'analysis'}}).decode()))
        atomic_json(self.j.root/'workers'/(worker+'.json'),dict(worker_id=worker,group_id=group,generation=1,
            owner=self.owner.json(),native_process=self.owner.json(),native_id=self.native))
        event=dict(self.event,toolCall={'name':'write_to_file','args':{'TargetFile':str(self.home/'canary')}})
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding)}):
            self.assertEqual(observe(self.j,event,'PreToolUse')['decision'],'deny')
            self.assertEqual(observe(self.j,dict(self.event,toolCall={'name':'view_file','args':{}}),'PreToolUse'),{'decision':'allow'})
            for args,decision in [({'ServerName':'nativepeers','ToolName':'artifact_read','Arguments':{}},'allow'),
                                  ({'ServerName':'nativepeers','ToolName':'task_status','Arguments':{}},'allow'),
                                  ({'ServerName':'other','ToolName':'artifact_read','Arguments':{}},'deny'),
                                  ({'ServerName':'nativepeers','ToolName':'arbitrary','Arguments':{}},'deny'),
                                  ({},'deny'),('malformed','deny')]:
                with self.subTest(args=args):
                    self.assertEqual(observe(self.j,dict(self.event,toolCall={'name':'call_mcp_tool','args':args}),'PreToolUse')['decision'],decision)
        self.assertFalse((self.home/'canary').exists())
        self.assertIsNotNone(self.j.one("SELECT * FROM events WHERE subject_id=? AND kind='antigravity-tool-gate'",(worker,)))
import asyncio
