"""Offline shapes from Grok 1.0.70's headless guide and native fixture probe."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,patch

from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.grok_worker import Stream
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker


FAKE='''import json,os,sys,time
from pathlib import Path
from urllib.parse import quote
sys.path.insert(0,ROOT)
args=sys.argv[1:]
profile=args[args.index('--sandbox')+1].removeprefix('nativepeers-')
native=args[args.index('--resume')+1] if '--resume' in args else args[args.index('--session-id')+1]
scenario=Path(__file__).with_suffix('.scenario').read_text()
text=sys.stdin.read()
if scenario=='auth':
    print(json.dumps({'type':'error','message':'Please sign in'}),flush=True);sys.exit(1)
home=Path(os.environ['GROK_HOME']);cwd=Path.cwd()
folder=home/'sessions'/quote(str(cwd),safe='')/native;folder.mkdir(parents=True,exist_ok=True)
summary=folder/'summary.json';old=json.loads(summary.read_text()) if summary.exists() else {}
summary.write_text(json.dumps({'info':{'id':native,'cwd':str(cwd)},'current_model_id':'wrong-model' if scenario=='model' else 'grok-4.7','sandbox_profile':'nativepeers-analysis' if scenario=='wrong-profile' else 'nativepeers-'+profile,'inputs':old.get('inputs',[])+[text],'resume':'--resume' in args}))
def hook_record():
    value={'method':'_x.ai/session/update','params':{'sessionId':'00000000-0000-0000-0000-000000000000' if scenario=='wrong-hook-id' else native,
        'update':{'sessionUpdate':'hook_execution','event_name':'session_start','runs':[{'name':'project/nativepeers:session_start[0].hooks[0]',
        'status':{'status':'error' if scenario=='failed-hook' else 'success'}}]},'_meta':{'eventId':'offline-native-hook'}}}
    with (folder/'updates.jsonl').open('ab') as stream:stream.write((json.dumps(value)+'\\n').encode())
if scenario not in ('no-hook','late-hook'):hook_record()
tools=['read_file','grep','list_dir']+(['run_terminal_command','write','search_replace'] if profile=='workspace' else [])
events=[{'type':'available_commands','tools':tools,'commands':[]}]
if scenario=='workspace':
    events.extend([
        {'type':'tool_call','toolName':'write','toolCallId':'write_1','kind':'edit','status':'in_progress','rawInput':{'path':'owned-result.txt','content':'before'}},
        {'type':'tool_call_update','toolCallId':'write_1','status':'completed','rawOutput':{'written':True}},
        {'type':'tool_call','toolName':'search_replace','toolCallId':'edit_1','kind':'edit','status':'in_progress','rawInput':{'path':'owned-result.txt','old_string':'before','new_string':'after'}},
        {'type':'tool_call_update','toolCallId':'edit_1','status':'completed','rawOutput':{'replaced':True}}])
events.extend([{'type':'text','data':'GROK_'},{'type':'text','data':'OK'}])
if scenario=='unsafe':events.append({'type':'available_commands','tools':['read_file','web_search']})
if scenario in ('mcp-catalog','mcp-call'):
    events.append({'type':'available_commands','tools':['read_file','grep','list_dir','codegraph__codegraph_explore','mcp__example__read']})
if scenario=='mcp-call':events.append({'type':'tool_call','toolName':'codegraph__codegraph_explore','toolCallId':'call_1','kind':'other','status':'in_progress','rawInput':{}})
if scenario not in ('no-end','hang'):events.append({'type':'end','stopReason':'cancelled' if scenario=='cancel' else 'end_turn','sessionId':('00000000-0000-0000-0000-000000000000' if scenario=='identity' else native),'requestId':'native-request','num_turns':1,'modelUsage':{'grok-4.7-build':{'modelCalls':1}}})
for event in events:
    if scenario=='workspace' and event.get('type')=='tool_call':
        target=cwd/'owned-result.txt'
        if event['toolName']=='write':target.write_text('before')
        elif event['toolName']=='search_replace':target.write_text(target.read_text().replace('before','after'))
    line=(json.dumps(event)+'\\n').encode()
    for start in range(0,len(line),13):os.write(1,line[start:start+13])
    if scenario=='late-hook' and event.get('type')=='available_commands':
        time.sleep(.1);hook_record()
if scenario=='hang':
    import signal,subprocess
    signal.signal(signal.SIGINT,signal.SIG_IGN)
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    time.sleep(30)
time.sleep(.1)
'''


class GrokStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_native_round_exhaustion_names_its_independent_cap(self):
        for reason in ('max_turn_requests','max_turns'):
            with self.subTest(reason=reason):
                stream=Stream(new_id(),'analysis',native_max_turns=200)
                stream.observe({'type':'end','stopReason':reason,'num_turns':200})
                error=stream.result(0)['error']
                self.assertEqual(error['code'],'BUDGET_EXHAUSTED')
                self.assertIn('max_native_turns_per_task',error['message'])
                self.assertIn('200',error['message'])
                self.assertEqual(error['limit'],{'field':'limits.max_native_turns_per_task','value':200,'native_flag':'--max-turns','observed':200})
                self.assertEqual(error['native_diagnostic']['stopReason'],reason)

    def test_terminal_reason_and_engine_attribution(self):
        native=new_id();stream=Stream(native,'analysis')
        for event in [{'type':'available_commands','tools':['read_file','grep','list_dir']},
                      {'type':'text','data':'one '},{'type':'text','data':'two'},
                      {'type':'end','sessionId':native,'stopReason':'end_turn','num_turns':7,'modelUsage':{'grok-4.7-build':{'modelCalls':7}}}]:stream.observe(event)
        result=stream.result(0)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['text'],'one two')
        self.assertIn('grok-4.7-build',result['model_usage']);self.assertEqual(result['native_internal_rounds'],7)
        with self.assertRaises(PeerError):stream.observe({'type':'text','data':'late'})
        for reason,status,code in [('max_tokens','failed','BUDGET_EXHAUSTED'),('max_turn_requests','failed','BUDGET_EXHAUSTED'),
                                   ('refusal','failed','PROVIDER_TASK_FAILED'),('cancelled','canceled','USER_CANCELED')]:
            with self.subTest(reason=reason):
                stream=Stream(native,'analysis');stream.observe({'type':'end','sessionId':native,'stopReason':reason})
                result=stream.result(0);self.assertEqual(result['status'],status);self.assertEqual(result['error']['code'],code)

    def test_advertised_mcp_catalog_never_grants_a_tool_call(self):
        from nativepeers.grok_worker import allowed_tools
        for profile in ('analysis','workspace'):
            with self.subTest(profile=profile):
                stream=Stream(new_id(),profile);builtins=list(allowed_tools(profile))
                mcp=['codegraph__codegraph_explore','mcp__example__read']
                stream.observe({'type':'available_commands','tools':builtins+mcp})
                self.assertEqual(stream.tools,builtins);self.assertEqual(stream.advertised_tools,builtins+mcp)
                self.assertEqual(stream.denied_advertised_tools,mcp)
                for name in mcp:
                    with self.assertRaises(PeerError) as caught:stream.observe({'type':'tool_call','toolName':name})
                    self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')
        for name in ('web_search','run_terminal_cmd','search_tool','use_tool'):
            with self.subTest(unexpected_builtin=name),self.assertRaises(PeerError):
                Stream(new_id(),'analysis').observe({'type':'available_commands','tools':['read_file',name]})

    async def fixture(self,scenario,profile='analysis'):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);root=Path(temporary.name).resolve()
        binary=root/'grok';binary.write_text('#!'+sys.executable+'\n'+FAKE.replace('ROOT',repr(str(Path(__file__).resolve().parents[2]))));binary.chmod(0o700)
        binary.with_suffix('.scenario').write_text(scenario)
        journal=Journal(root/'state');self.addCleanup(journal.close)
        group,worker,task,native=new_id(),new_id(),new_id(),new_id()
        spec=dict(provider='grok',model='grok-4.7',permission_profile=profile,cwd=str(root),provider_home=str(root/'native'))
        lease=dict(spec=spec,grants={},provider_home=spec['provider_home'],binding=str(journal.root/'bindings'/new_id()),
                   executable=str(binary),expires_at=time.time()+30,parent_key='parent')
        journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RESERVED','{}'))
        journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (worker,group,None,'grok',native,'test worker','grok-4.7',None,None,str(root),None,'READY',1,canonical(lease).decode()))
        journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'RUNNING',None,None,1,'{}'))
        value=Worker(journal,worker);value.bootstrap();value.running_task=task
        # The script has a Python executable and no Seatbelt profile. Mock only
        # these native surfaces; retain real process births and native-file reads.
        from dataclasses import replace
        from nativepeers.process import identity as real_identity
        calls={}
        def native_identity(pid):
            current=real_identity(pid)
            if pid==os.getpid():return current
            calls[pid]=calls.get(pid,0)+1
            if binary.with_suffix('.scenario').read_text()=='wrong-process' and calls[pid]>1:
                current=replace(current,start=current.start+'-wrong')
            return replace(current,executable=str(binary))
        def kernel(spec,*,pid):
            mode=binary.with_suffix('.scenario').read_text()
            return dict(profile='analysis' if mode=='wrong-kernel-profile' else spec['permission_profile'],
                checked_pid=pid,verified=mode!='no-kernel',checks=[])
        for mocked in (patch('nativepeers.worker_process.identity',side_effect=native_identity),
                       patch('nativepeers.grok_hooks.kernel_sandbox',side_effect=kernel)):
            mocked.start();self.addCleanup(mocked.stop)
        return value,binary

    async def test_each_turn_resumes_only_its_exact_owned_native_history(self):
        worker,binary=await self.fixture('success')
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,
                side_effect=[{'total_tokens':19,'native_turn_number':1},{'total_tokens':19,'native_turn_number':1},{'total_tokens':19,'native_turn_number':2}]):
            try:
                first=await worker.grok_turn('first');native=worker.native
                from nativepeers.grok import summaries
                initial=json.loads(Path(summaries(native_id=native,home=worker.home)[0]['path']).read_text())
                self.assertFalse(initial['resume'])
                second=await worker.grok_turn('second')
                self.assertEqual(first['status'],'completed');self.assertEqual(second['status'],'completed')
                self.assertEqual(worker.native,native);self.assertEqual(second['usage']['total_tokens'],19)
                summary=json.loads(Path(summaries(native_id=native,home=worker.home)[0]['path']).read_text())
                self.assertEqual(summary['inputs'],['first','second']);self.assertTrue(summary['resume'])
                self.assertEqual(worker.model_observed,'grok-4.7');self.assertIn('grok-4.7-build',second['model_usage'])
            finally:await worker.close_provider()
        self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])

    async def test_delayed_native_hook_flush_is_awaited_and_parent_proofs_are_recorded(self):
        worker,binary=await self.fixture('late-hook')
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,return_value={'native_turn_number':1}):
            try:
                result=await worker.grok_turn('fixture');self.assertEqual(result['status'],'completed')
                hook=json.loads(worker.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-hook-observed' ORDER BY sequence DESC",(worker.row['worker_id'],))['payload_json'])
                kernel=json.loads(worker.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='grok-kernel-check' ORDER BY sequence DESC",(worker.row['worker_id'],))['payload_json'])
                self.assertEqual(hook['evidence_kind'],'parent-native-hook-execution-read')
                self.assertEqual(hook['hook_execution']['event_id'],'offline-native-hook')
                self.assertEqual(kernel['evidence_kind'],'parent-native-kernel-check')
                self.assertTrue(kernel['reused_after_native_exit'])
            finally:await worker.close_provider()

    async def test_dead_process_cannot_borrow_another_birth_or_missing_kernel_proof(self):
        from types import SimpleNamespace
        worker,binary=await self.fixture('success');process=dict(worker.owner,pid=12345,start='offline-fixture-birth')
        worker.provider=SimpleNamespace(returncode=0);worker.provider_identity=process
        for cached in (False,True):
            launch={'process':process,'hook':None}
            if cached:launch.update(kernel={'verified':True},checked_process=dict(process,start='another-birth'))
            with self.subTest(cached=cached),patch('nativepeers.worker_process.owned_status',return_value='DEAD'):
                with self.assertRaises(PeerError) as caught:worker.grok_observation(launch)
                self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')

    async def test_cached_native_proof_waits_for_reap_after_liveness_lookup_races(self):
        from types import SimpleNamespace
        worker,binary=await self.fixture('success')
        with patch.object(worker,'verify_grok'),patch.object(worker,'grok_observation',wraps=worker.grok_observation) as observe, \
             patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,return_value={'native_turn_number':1}):
            try:
                await worker.grok_turn('fixture');launch=observe.call_args.args[0];process=worker.provider
                for race in ('dead-before-reap','gone-before-check','gone-after-check'):
                    with self.subTest(race=race):
                        worker.provider=SimpleNamespace(returncode=None)
                        lookups=[SimpleNamespace(json=lambda:dict(launch['checked_process'])),ProcessLookupError()] if race=='gone-after-check' else [ProcessLookupError()]
                        with patch.object(worker,'refresh_provider_identity'), \
                             patch('nativepeers.worker_process.owned_status',return_value='DEAD' if race=='dead-before-reap' else 'LIVE'), \
                             patch('nativepeers.worker_process.identity',side_effect=lookups):
                            with self.assertRaises(PeerError) as caught:worker.grok_observation(launch)
                            self.assertEqual(caught.exception.code,'CALLER_NOT_READY')
                        worker.provider.returncode=0
                        with patch.object(worker,'refresh_provider_identity'),patch('nativepeers.worker_process.owned_status',return_value='DEAD'):
                            self.assertEqual(worker.grok_observation(launch)['native_id'],worker.native)
                worker.provider=process
            finally:
                if not hasattr(worker.provider,'stdin'):worker.provider=process
                await worker.close_provider()

    async def test_native_exit_race_never_accepts_an_unproved_first_kernel_check(self):
        from types import SimpleNamespace
        worker,binary=await self.fixture('success');process=dict(worker.owner,pid=12345,start='offline-fixture-birth')
        worker.provider=SimpleNamespace(returncode=None);worker.provider_identity=process
        for state in ('DEAD','LIVE'):
            with self.subTest(state=state),patch.object(worker,'refresh_provider_identity'), \
                 patch('nativepeers.worker_process.owned_status',return_value=state), \
                 patch('nativepeers.worker_process.identity',side_effect=ProcessLookupError()):
                with self.assertRaises(PeerError) as caught:worker.grok_observation({'process':process,'hook':None})
                self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN')

    async def test_offline_workspace_stream_accepts_edit_tools_and_matching_observations(self):
        """Exercise production observation with synthetic proof, not native enforcement."""
        worker,binary=await self.fixture('workspace',profile='workspace')
        with patch.object(worker,'verify_grok'),patch.object(worker,'grok_observation',wraps=worker.grok_observation) as observe, \
             patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,return_value={'native_turn_number':1,'total_tokens':19}):
            try:
                result=await worker.grok_turn('offline workspace fixture')
                self.assertEqual(result['status'],'completed');self.assertEqual(result['text'],'GROK_OK')
                self.assertEqual((Path(worker.row['cwd'])/'owned-result.txt').read_text(),'after')
                self.assertGreaterEqual(observe.call_count,2)
                event=worker.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='effective-tool-profile' ORDER BY sequence DESC",(worker.row['worker_id'],))
                value=json.loads(event['payload_json'])
                self.assertEqual(value['profile'],'workspace');self.assertEqual(value['sandbox'],'nativepeers-workspace')
                self.assertEqual(value['tools'],['read_file','grep','list_dir','run_terminal_command','write','search_replace'])
                self.assertEqual(value['denied_advertised_tools'],[])
                self.assertEqual(worker.model_observed,'grok-4.7');self.assertTrue(worker.lease['grok_native_created'])
            finally:await worker.close_provider()
        self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])

    async def test_offline_workspace_stream_rejects_mismatched_observation_proofs(self):
        for scenario in ('wrong-profile','no-hook','wrong-process','no-kernel','wrong-kernel-profile'):
            with self.subTest(scenario=scenario):
                worker,binary=await self.fixture(scenario,profile='workspace')
                with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock) as usage:
                    try:
                        with self.assertRaises(PeerError) as caught:await worker.grok_turn('offline workspace fixture')
                        self.assertEqual(caught.exception.code,'OWNERSHIP_CONFLICT' if scenario=='wrong-process' else 'PERMISSION_CAPABILITY_UNKNOWN')
                        self.assertIsNone(worker.model_observed);usage.assert_not_called()
                    finally:await worker.close_provider()
                self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])

    async def test_wrong_uuid_model_hook_birth_kernel_and_missing_terminal_fail(self):
        for scenario,code in [('identity','OWNERSHIP_CONFLICT'),('model','MODEL_UNAVAILABLE'),
                              ('no-hook','PERMISSION_CAPABILITY_UNKNOWN'),('wrong-process','OWNERSHIP_CONFLICT'),
                              ('no-kernel','PERMISSION_CAPABILITY_UNKNOWN'),('unsafe','PERMISSION_CAPABILITY_UNKNOWN'),
                              ('mcp-call','PERMISSION_CAPABILITY_UNKNOWN'),('wrong-hook-id','OWNERSHIP_CONFLICT'),
                              ('failed-hook','PERMISSION_CAPABILITY_UNKNOWN'),('no-end','WORKER_CRASHED')]:
            with self.subTest(scenario=scenario):
                worker,binary=await self.fixture(scenario)
                with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock):
                    try:
                        with self.assertRaises(PeerError) as caught:await worker.grok_turn('fixture')
                        self.assertEqual(caught.exception.code,code)
                    finally:await worker.close_provider()

    async def test_worker_records_denied_mcp_catalog_separately_from_fixed_tools(self):
        worker,binary=await self.fixture('mcp-catalog')
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,return_value={'native_turn_number':1,'total_tokens':19}):
            try:
                result=await worker.grok_turn('fixture');self.assertEqual(result['status'],'completed')
                event=worker.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='effective-tool-profile' ORDER BY sequence DESC",(worker.row['worker_id'],))
                value=json.loads(event['payload_json'])
                self.assertEqual(value['tools'],['read_file','grep','list_dir'])
                self.assertEqual(value['denied_advertised_tools'],['codegraph__codegraph_explore','mcp__example__read'])
                self.assertIn('codegraph__codegraph_explore',value['advertised_catalog'])
            finally:await worker.close_provider()

    async def test_old_launch_hook_cannot_certify_followup_and_checks_repeat(self):
        worker,binary=await self.fixture('success')
        with patch.object(worker,'verify_grok') as ready,patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,return_value={'native_turn_number':1,'total_tokens':19}):
            try:
                await worker.grok_turn('first');worker.turn=None
                binary.with_suffix('.scenario').write_text('no-hook')
                with self.assertRaises(PeerError) as caught:await worker.grok_turn('second')
                self.assertEqual(caught.exception.code,'PERMISSION_CAPABILITY_UNKNOWN');self.assertEqual(ready.call_count,2)
            finally:await worker.close_provider()

    async def test_auth_before_native_start_and_cancel_remain_distinct(self):
        for scenario,status,code in [('auth','failed','AUTH_REQUIRED'),('cancel','canceled','USER_CANCELED')]:
            with self.subTest(scenario=scenario):
                worker,binary=await self.fixture(scenario)
                with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,side_effect=PeerError('SOURCE_UNAVAILABLE','fixture')):
                    try:
                        result=await worker.grok_turn('fixture')
                        self.assertEqual(result['status'],status);self.assertEqual(result['error']['code'],code)
                        self.assertIn('SOURCE_UNAVAILABLE',result['usage_error'])
                        self.assertEqual(result['usage']['status'],'UNKNOWN');self.assertEqual(result['usage']['counter_scope'],'unknown')
                    finally:await worker.close_provider()

    async def test_owned_cancel_closes_leader_and_captured_escaped_pipe_child(self):
        from nativepeers.process import owned_status
        worker,binary=await self.fixture('hang')
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock):
            task=worker.turn_task=asyncio.create_task(worker.grok_turn('fixture'))
            try:
                async with asyncio.timeout(3):
                    while not worker.turn or not worker.provider_descendants:await asyncio.sleep(.02)
                owned=[worker.provider_identity,*worker.provider_descendants.values()]
                await worker.cancel_task(worker.running_task)
                with self.assertRaises((PeerError,asyncio.CancelledError)):await task
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
                await worker.close_provider()
        self.assertTrue(all(owned_status(record)=='DEAD' for record in owned))
        self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])

    async def test_resumed_error_cannot_attribute_the_previous_turn_usage(self):
        worker,binary=await self.fixture('success')
        usage=[{'total_tokens':19,'native_turn_number':1},{'total_tokens':19,'native_turn_number':1},
               PeerError('SOURCE_SCHEMA_MISMATCH','Expected owned turn is absent')]
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock,side_effect=usage) as read:
            try:
                first=await worker.grok_turn('first');self.assertEqual(first['usage']['total_tokens'],19)
                binary.with_suffix('.scenario').write_text('auth')
                second=await worker.grok_turn('second')
                self.assertEqual(second['error']['code'],'AUTH_REQUIRED')
                self.assertEqual(second['usage']['status'],'UNKNOWN');self.assertNotIn('total_tokens',second['usage'])
                self.assertEqual(read.await_args_list[-1].kwargs,{'turn':2})
            finally:await worker.close_provider()

    async def test_optional_usage_deadline_preserves_the_validated_terminal_response(self):
        worker,binary=await self.fixture('success')
        async def stalled_usage(*args,**kwargs):
            # Reschedule only after Grok has exited and its terminal response,
            # hook identity, model and tool inventory have all been accepted.
            self.assertEqual(worker.provider.returncode,0)
            deadline.reschedule(asyncio.get_running_loop().time()+.02)
            await asyncio.Event().wait()
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',side_effect=stalled_usage):
            try:
                async with asyncio.timeout(None) as deadline:
                    worker.turn_task=asyncio.create_task(worker.grok_turn('fixture'))
                    result=await worker.turn_task
                self.assertTrue(deadline.expired())
                self.assertEqual(result['status'],'completed');self.assertEqual(result['text'],'GROK_OK')
                self.assertIsNone(result['error']);self.assertEqual(result['usage']['status'],'UNKNOWN')
                self.assertEqual(result['usage']['counter_scope'],'unknown')
                self.assertEqual(result['usage']['error'],'CancelledError')
            finally:await worker.close_provider()
        self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])

    async def test_deadline_before_terminal_response_still_cancels_the_native_turn(self):
        worker,binary=await self.fixture('hang')
        with patch.object(worker,'verify_grok'),patch('nativepeers.usage.grok_native.session_usage',new_callable=AsyncMock) as usage:
            task=worker.turn_task=asyncio.create_task(worker.grok_turn('fixture'))
            try:
                with self.assertRaises(TimeoutError):
                    async with asyncio.timeout(None) as deadline:
                        async with asyncio.timeout(3):
                            while not worker.turn or not worker.provider_descendants:await asyncio.sleep(.02)
                        deadline.reschedule(asyncio.get_running_loop().time()+.02)
                        await task
                self.assertTrue(task.cancelled());usage.assert_not_called()
            finally:
                if not task.done():task.cancel()
                await asyncio.gather(task,return_exceptions=True)
                await worker.close_provider()
        self.assertEqual(worker.provider_cleanup['state'],'CLOSED');self.assertTrue(worker.provider_cleanup['output_eof'])
