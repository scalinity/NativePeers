import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from tests.live.pro_review import artifact_nonce_matches, import_context
from tests.live.pro_review_collect import artifact_read_evidence


class ProReviewArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name).resolve();self.home=self.root/'home';self.home.mkdir()
        environment=patch.dict(os.environ,{'CODEX_HOME':str(self.home/'.codex'),'CLAUDE_CONFIG_DIR':str(self.home/'.claude')})
        environment.start();self.addCleanup(environment.stop)
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        peer=Registry(self.journal).register('codex',self.home,new_id(),'fixture',identity(os.getpid()).json(),self.root,{'kind':'fake'})
        caller=Caller('peer',peer['handle'],peer['incarnation_id'])
        self.fixture=self.root/'fixture';self.fixture.mkdir();(self.fixture/'brief.txt').write_text('VALUE=7\n')
        with patch('tests.live.pro_review.RUN',self.root):self.context=import_context(self.journal,caller,'codex')
        self.page=Artifacts(self.journal).read(self.context['artifact_id'],caller.key)
        self.nonce=base64.b64decode(self.page['data_base64']).decode().split('NONCE=')[1].strip()
        self.response={'content':[{'type':'text','text':json.dumps({'schema_version':1,'ok':True,'data':self.page,'error':None})}],'isError':False}
        self.at=datetime.fromtimestamp(10,timezone.utc).isoformat()
        self.interval={'started_at':9,'completed_at':11}

    def write_history(self,provider,events,full=False):
        native=new_id();directory=self.home/native;directory.mkdir()
        history=directory/'transcript.jsonl';history.write_text('\n'.join(json.dumps(event) for event in events)+'\n')
        if full:
            history.with_name('transcript_full.jsonl').write_bytes(history.read_bytes())
            history.write_text(json.dumps({'type':'PLANNER_RESPONSE','step_index':0,'created_at':self.at,'content':'truncated','truncated_fields':['content']})+'\n')
        return {'peer_key':{'provider':provider,'native_session_id':native},'home':str(self.home),'native_transcript':str(history)}

    def codex_events(self,response=None,call_id='read-1',name='mcp__nativepeers__artifact_read',handle=None):
        return [
            {'type':'response_item','timestamp':self.at,'payload':{'type':'function_call','name':name,'call_id':call_id,
                'arguments':json.dumps({'handle':handle or self.context['artifact_id'],'limit':4096})}},
            {'type':'response_item','timestamp':self.at,'payload':{'type':'function_call_output','call_id':'read-1',
                'output':json.dumps(self.response if response is None else response)}}]

    def antigravity_events(self,response=None,name='mcp_nativepeers_artifact_read',status='DONE',handle=None):
        return [
            {'type':'PLANNER_RESPONSE','step_index':3,'created_at':self.at,'tool_calls':[
                {'name':name,'arguments':{'handle':handle or self.context['artifact_id'],'limit':4096}}]},
            {'type':'MCP_TOOL','step_index':4,'status':status,'created_at':self.at,
                'content':'Tool response:\n'+json.dumps(self.response if response is None else response)}]

    def test_nonce_exists_only_in_the_imported_context_before_worker_execution(self):
        self.assertFalse((self.root/'artifact-input-codex.txt').exists())
        self.assertEqual((self.fixture/'brief.txt').read_text(),'VALUE=7\n')
        self.assertNotIn(self.nonce,json.dumps(self.context))
        self.assertTrue(artifact_nonce_matches(self.nonce,self.context))
        self.assertFalse(artifact_nonce_matches('NP-ARTIFACT-'+'0'*32,self.context))
        self.assertEqual(self.context['nonce_sha256'],hashlib.sha256(self.nonce.encode()).hexdigest())
        peer=self.journal.one('SELECT peer_key,incarnation_id FROM peer_instances')
        caller=Caller('peer',peer['peer_key'],peer['incarnation_id'])
        with patch('tests.live.pro_review.RUN',self.root):other=import_context(self.journal,caller,'antigravity')
        self.assertNotEqual(other['nonce_sha256'],self.context['nonce_sha256'])
        self.assertFalse((self.root/'artifact-input-antigravity.txt').exists())

    def test_both_native_formats_require_an_artifact_call_and_successful_result(self):
        for provider,builder in [('codex',self.codex_events),('antigravity',self.antigravity_events)]:
            with self.subTest(provider=provider):
                peer=self.write_history(provider,builder(),full=provider=='antigravity')
                evidence=artifact_read_evidence(peer,self.context,self.interval)
                self.assertEqual(evidence['artifact_id'],self.context['artifact_id'])
                self.assertEqual(evidence['native_session_id'],peer['peer_key']['native_session_id'])
                self.assertIsNotNone(evidence['call_event_id']);self.assertIsNotNone(evidence['result_event_id'])
            invalid_page=dict(self.page,data_base64=base64.b64encode(b'VALUE=7\nNONCE=wrong\n').decode())
            invalid_response={'content':[{'type':'text','text':json.dumps({'ok':True,'data':invalid_page})}]}
            for label,events in [('no-call',builder()[1:]),('no-result',builder()[:1]),
                                 ('other-tool',builder(name='read_file')),('wrong-handle',builder(handle='art_wrong')),
                                 ('wrong-content',builder(response=invalid_response)),
                                 ('error',builder(response=dict(self.response,isError=True)))]:
                with self.subTest(provider=provider,case=label):
                    peer=self.write_history(provider,events)
                    self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))
            peer=self.write_history(provider,builder())
            self.assertIsNone(artifact_read_evidence(peer,self.context,{'started_at':20,'completed_at':21}))

    def test_native_correlation_and_tool_step_types_cannot_be_replaced_by_answer_text(self):
        peer=self.write_history('codex',self.codex_events(call_id='different-call'))
        self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))
        events=self.antigravity_events();events[1]['type']='PLANNER_RESPONSE'
        peer=self.write_history('antigravity',events)
        self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))
        peer=self.write_history('antigravity',self.antigravity_events(status='ERROR'))
        self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))

    def test_codex_mcp_events_correlate_the_same_successful_native_result(self):
        invocation={'server':'nativepeers','tool':'artifact_read','arguments':{'handle':self.context['artifact_id']}}
        events=[{'type':'event_msg','timestamp':self.at,'payload':{'type':kind,'call_id':'read-1',
                'invocation':invocation,'result':{'Ok':self.response}}} for kind in ('mcp_tool_call_begin','mcp_tool_call_end')]
        peer=self.write_history('codex',events)
        self.assertIsNotNone(artifact_read_evidence(peer,self.context,self.interval))

    def test_installed_gemini_wrapper_and_exact_owned_history_path(self):
        events=self.antigravity_events()
        events[0]['tool_calls']=[{'name':'call_mcp_tool','args':{'ServerName':'nativepeers','ToolName':'artifact_read','Arguments':{'handle':self.context['artifact_id'],'limit':4096}}}]
        events[1]['type']='GENERIC'
        peer=self.write_history('antigravity',events)
        old=Path(peer.pop('native_transcript'));target=self.home/'brain'/peer['peer_key']['native_session_id']/'.system_generated/logs'
        target.mkdir(parents=True);(target/'transcript.jsonl').write_bytes(old.read_bytes())
        self.assertIsNotNone(artifact_read_evidence(peer,self.context,self.interval))
        events[0]['tool_calls'][0]['args']['ServerName']='unrelated'
        (target/'transcript.jsonl').write_text('\n'.join(json.dumps(x) for x in events)+'\n')
        self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))

    def test_installed_codex_completed_mcp_item_carries_native_call_and_result(self):
        item={'type':'McpToolCall','id':'exec-native-event','server':'nativepeers','tool':'artifact_read','arguments':{'handle':self.context['artifact_id']},'status':'completed','result':self.response}
        events=[{'type':'event_msg','timestamp':self.at,'payload':{'type':'item_completed','thread_id':'placeholder','item':item}}]
        peer=self.write_history('codex',events);path=Path(peer['native_transcript'])
        events[0]['payload']['thread_id']=peer['peer_key']['native_session_id'];path.write_text(json.dumps(events[0])+'\n')
        self.assertIsNotNone(artifact_read_evidence(peer,self.context,self.interval))
        events[0]['payload']['thread_id']=new_id();path.write_text(json.dumps(events[0])+'\n')
        self.assertIsNone(artifact_read_evidence(peer,self.context,self.interval))
