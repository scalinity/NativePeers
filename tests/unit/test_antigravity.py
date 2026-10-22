import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.antigravity import observe, resolve_name
from nativepeers.antigravity_install import install, allowed_tools, desired
from nativepeers.files import canonical,atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.compatibility import fingerprint


class AntigravityTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.home=Path(self.temporary.name);self.j=Journal(self.home/'state');self.addCleanup(self.j.close)
        self.native=new_id();self.provider_home=self.home/'.gemini/antigravity-cli';self.provider_home.mkdir(parents=True)
        with sqlite3.connect(self.provider_home/'conversation_summaries.db') as db:
            db.execute('CREATE TABLE conversation_summaries(conversation_id TEXT,title TEXT)')
            db.execute('INSERT INTO conversation_summaries VALUES(?,?)',(self.native,'Argon Review'))
        self.owner=identity(os.getpid())
        binary=self.home/'.local/bin/agy';binary.parent.mkdir(parents=True);binary.symlink_to(self.owner.executable)
        self.evidence=self.home/'capabilities.json';self.evidence.write_text(json.dumps({'providers':{'antigravity':{'executable_sha256':fingerprint(binary)['sha256'],'version':'fixture'}}}))
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

    def test_install_is_idempotent_and_uninstall_preserves_later_unrelated_edits(self):
        path=self.home/'.gemini/config/hooks.json';path.parent.mkdir(parents=True);path.write_text('{"existing":{"enabled":false}}')
        install(self.j,home=self.home,verified_source=self.evidence)
        self.assertFalse(install(self.j,home=self.home,verified_source=self.evidence)['changed'])
        data=json.loads(path.read_text());data['later']={'enabled':True};path.write_text(json.dumps(data))
        install(self.j,home=self.home,remove=True)
        self.assertEqual(json.loads(path.read_text()),{'existing':{'enabled':False},'later':{'enabled':True}})
        self.assertTrue((self.home/'.local/bin/agy').exists())

    def test_changed_runtime_is_refused_before_any_configuration_write(self):
        self.evidence.write_text(json.dumps({'providers':{'antigravity':{'executable_sha256':'not-the-installed-binary','version':'fixture'}}}))
        with self.assertRaises(Exception) as error:install(self.j,home=self.home,verified_source=self.evidence)
        self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
        self.assertFalse((self.home/'.gemini/config/hooks.json').exists())

    def test_analysis_tool_gate_excludes_mutation_plugins_and_recursive_agents(self):
        self.assertTrue(allowed_tools('analysis','view_file'))
        self.assertTrue(allowed_tools('analysis','mcp__nativepeers__peer_reply'))
        for tool in ['write_to_file','run_command','invoke_subagent','mcp__unrelated__write','mcp__nativepeers__unknown_write','mcp_nativepeers_evil_write','schedule']:
            self.assertFalse(allowed_tools('analysis',tool))
        self.assertTrue(allowed_tools('workspace','run_command'))

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
        event=dict(self.event,toolCall={'name':'write_to_file','args':{'TargetFile':str(self.home/'canary')}})
        with patch('nativepeers.antigravity.Path.home',return_value=self.home), \
             patch('nativepeers.antigravity.ancestry',return_value=[self.owner,self.owner]), \
             patch.dict(os.environ,{'NPEER_BOOTSTRAP':str(binding)}):
            self.assertEqual(observe(self.j,event,'PreToolUse')['decision'],'deny')
            self.assertEqual(observe(self.j,dict(self.event,toolCall={'name':'view_file','args':{}}),'PreToolUse'),{'decision':'allow'})
        self.assertFalse((self.home/'canary').exists())
        self.assertIsNotNone(self.j.one("SELECT * FROM events WHERE subject_id=? AND kind='antigravity-tool-gate'",(worker,)))
