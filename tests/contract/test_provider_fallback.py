"""Provider failures, clean logical-task handoff and readable native input, offline."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock,Mock,patch

from nativepeers.antigravity_install import desired
from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.claude_stream import Evidence
from nativepeers.compatibility import fingerprint
from nativepeers.delegation import Delegation,TERMINAL
from nativepeers.errors import PeerError
from nativepeers.fallback import workspace_image,image_digest,scope_limit
from nativepeers.files import atomic_json,canonical,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.presentation import render,matches,completion
from nativepeers.process import identity,matches as process_matches
from nativepeers.provider_errors import diagnostic
from nativepeers.registry import Registry
from nativepeers.supervisor import Group
from nativepeers.worker_process import Worker
from nativepeers.worktrees import git

ROOT=Path(__file__).resolve().parents[2]
MODEL='gemini-4-argon-high'


def synthetic(code='quota',**extra):
    value=dict(type='assistant',isApiErrorMessage=True,error='rate_limit_error' if code=='quota' else 'authentication_failed',
        message={'model':'<synthetic>','usage':{'input_tokens':0,'output_tokens':0},'content':[
            {'type':'text','text':"You've hit your limit · resets 11pm (America/New_York)" if code=='quota' else 'Not logged in · Please run /login'}]})
    return dict(value,**extra)


def failure(code='quota'):
    evidence=Evidence();evidence.observe(synthetic(code))
    return evidence.failure({'type':'result','subtype':'success','is_error':True,'usage':{'input_tokens':0,'output_tokens':0}},1)


class DiagnosticsTests(unittest.TestCase):
    def test_synthetic_errors_are_not_model_output_and_retain_diagnostics(self):
        for kind,code in [('quota','QUOTA_EXHAUSTED'),('auth','AUTH_REQUIRED')]:
            error=failure(kind)
            self.assertEqual(error['code'],code);self.assertTrue(error['may_have_received'])
            self.assertTrue(error['execution']['definitive_no_execution'])
            self.assertIn('<synthetic>',json.dumps(error['native_diagnostic']))
            self.assertTrue(error['message'])
        self.assertIn('Eastern',failure()['reset_time'])

    def test_native_uuid_digits_are_not_http_authentication_status(self):
        evidence=Evidence();evidence.observe(synthetic(session_id='40100000-0401-4401-8401-401000000000'))
        self.assertEqual(evidence.failure({'subtype':'error','is_error':True},1)['code'],'QUOTA_EXHAUSTED')
        self.assertEqual(diagnostic({'status':401})['code'],'AUTH_REQUIRED')
        self.assertEqual(diagnostic({'status_code':429})['code'],'RATE_LIMITED')

    def test_partial_model_tool_or_usage_evidence_cannot_be_replayed(self):
        cases=[{'type':'assistant','message':{'model':'claude-sonnet-5-5','content':[{'type':'text','text':'partial'}]}},
               {'type':'user','message':{'content':[{'type':'tool_result','content':'changed'}]}},
               synthetic(message={'model':'<synthetic>','usage':{'input_tokens':1,'output_tokens':1},'content':[]}),
               synthetic(message={'model':'<synthetic>','usage':{'input_tokens':0,'output_tokens':0,'cache_read_input_tokens':100},'content':[]})]
        for event in cases:
            with self.subTest(event=event):
                evidence=Evidence();evidence.observe(synthetic());evidence.observe(event)
                error=evidence.failure({'subtype':'error','is_error':True},1)
                self.assertFalse(error['execution']['definitive_no_execution'])

    def test_provider_reset_epoch_is_eastern_and_never_invented(self):
        error=diagnostic({'error':'usage limit','reset_at':'2026-10-23T03:00:00Z'})
        self.assertIn('2026-10-22 11:00 PM EDT',error['reset_time'])
        self.assertNotIn('reset_time',diagnostic({'error':'usage limit'}))
        huge=diagnostic({'error':'usage limit','detail':'x'*40000})
        self.assertLess(len(canonical(huge)),18000)

    def test_terminal_output_or_native_recovery_does_not_replay_useful_work(self):
        evidence=Evidence();evidence.observe(synthetic())
        for terminal in ({'subtype':'success','is_error':False,'result':'Useful task result'},
                         {'subtype':'error','is_error':True,'result':'Partly implemented the task'}):
            error=evidence.failure(terminal,0)
            self.assertFalse(error['execution']['definitive_no_execution'])
        evidence.observe({'type':'assistant','message':{'model':'claude-sonnet-5-5','content':[{'type':'text','text':'recovered native result'}]}})
        self.assertIsNone(evidence.failure({'subtype':'success','is_error':False,'result':'recovered native result'},0))

    def test_readable_wire_keeps_correlation_and_exact_reconciliation(self):
        message={'from':{'kind':'peer','peer_key':'np1_sender'},'to':{'peer_key':'np1_recipient'},
            'body':{'text':'First line\nQuoted "text" and Unicode 中'},'message_id':new_id(),'exchange_id':new_id(),
            'expect_reply':True,'in_reply_to':new_id(),'schema_version':1}
        text=render(message)
        self.assertIn(message['body']['text'],text);self.assertIn(message['message_id'],text)
        self.assertIn(message['exchange_id'],text);self.assertIn(message['in_reply_to'],text)
        self.assertNotIn('"schema_version"',text);self.assertTrue(matches(text,message))
        self.assertFalse(matches(text.replace('First line','Different body'),message))
        self.assertFalse(matches(text,dict(message,to={'peer_key':'another recipient'})))
        notice=completion('group',1,[{'worker':{'provider':'claude','title':'review','native_session_id':'uuid'},
            'status':'failed','summary':'','error':failure(),'model_requested':'opus','result_artifact_id':'art_fixture'}])
        self.assertIn('usage limit',notice);self.assertIn('FAILED',notice);self.assertIn('art_fixture',notice)


class FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.home=self.root/'home';self.home.mkdir()
        self.source=self.root/'project';self.source.mkdir();(self.source/'main.py').write_text('VALUE=1\n')
        git(self.source,'init','-q');git(self.source,'add','main.py');git(self.source,'commit','-qm','Create fixture')
        self.enterContext(patch.dict(os.environ,{'HOME':str(self.home),'CLAUDE_CONFIG_DIR':str(self.home/'.claude')}))
        (self.home/'.claude').mkdir();(self.home/'.claude/settings.json').write_text('{"model":"claude-sonnet-5-5"}')
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.registry=Registry(self.j)
        parent=self.registry.register('codex',self.home/'.codex',new_id(),'fixture parent',identity(os.getpid()).json(),self.source,{},
            owner_kind='test',policy={'profiles':['analysis','workspace']})
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id'])
        self.delegation=Delegation(self.j,self.registry);self.group=None
        self.enterContext(patch('nativepeers.supervisor.runtime_root',return_value=private_directory(self.root/'run')))
        self.catalog=self.enterContext(patch('nativepeers.antigravity.catalog',new_callable=AsyncMock,return_value={MODEL:'Gemini 4 Argon (High)'}))
        self.launch=self.enterContext(patch.object(Delegation,'ensure_supervisor',new_callable=AsyncMock))
        self.claude=self.home/'.local/bin/claude';self.claude.parent.mkdir(parents=True);self.claude.write_text('fixture');self.claude.chmod(0o700)
        self.agy=self.home/'.local/bin/agy';self.agy.write_text('fixture');self.agy.chmod(0o700)
        self.receipts()
        for path,(kind,key,value) in desired(self.home).items():
            path.parent.mkdir(parents=True,exist_ok=True)
            if kind=='file':path.write_text(value)
            else:path.write_text(json.dumps({('mcpServers' if kind=='mcp' else key):{key:value}} if kind=='mcp' else {key:value}))

    def receipts(self):
        atomic_json(self.j.root/'compat/runtime.json',{'providers':{'claude':fingerprint(self.claude),'antigravity':fingerprint(self.agy)}})

    async def asyncTearDown(self):
        if self.group and hasattr(self.group,'server'):
            await self.group.close()
            await asyncio.gather(*(child['reader'] for child in list(self.group.children.values())),return_exceptions=True)

    def request(self,editing=False,**limits):
        return {'client_request_id':'fallback-fixture','completion_mode':'wait','workers':[{
            'provider':'claude','model':'claude-sonnet-5-5','title':'focused worker','task':'Inspect main.py and return a short result.',
            'permission_profile':'workspace' if editing else 'analysis','workspace':{'mode':'isolated-worktree' if editing else 'inherit-read-only'},
            'fallbacks':[{'provider':'antigravity','model':MODEL}]}],
            'limits':dict(wall_time_seconds=30,max_task_turns_per_worker=3,followup_grace_seconds=0,**limits)}

    async def setup_worker(self,request=None):
        # Inline controller unit cases share the test PID with their parent.
        # Real controller process tests below use separate processes.
        self.j.db.execute("UPDATE peer_instances SET status='live' WHERE peer_key=?",(self.caller.key,))
        request=request or self.request()
        result=await self.delegation.spawn(self.caller,request)
        row=self.j.one('SELECT * FROM workers WHERE group_id=? ORDER BY rowid',(result['group_id'],))
        task=self.j.one('SELECT * FROM tasks WHERE worker_id=?',(row['worker_id'],))
        worker=Worker(self.j,row['worker_id']);worker.emit=Mock();worker.bootstrap()
        return worker,task,result

    def result(self,task_id):
        task=self.j.one('SELECT * FROM tasks WHERE task_id=?',(task_id,))
        return json.loads((self.j.root/'artifacts'/task['result_artifact_id']).read_text())

    async def primary_failure(self,worker,task,*,error=None,effect=None):
        async def turn(text):
            if effect:effect()
            return {'status':'failed','text':'','error':error or failure(),'native_turn_ids':[]}
        worker.claude_turn=turn
        await worker.task(task['task_id'],json.loads(task['task_json'])['text'])
        return self.j.one('SELECT * FROM tasks WHERE task_id=?',(task['task_id'],))

    async def test_no_fallback_preserves_exact_failure_and_useful_summary(self):
        request=self.request();del request['workers'][0]['fallbacks']
        worker,task,_=await self.setup_worker(request)
        final=await self.primary_failure(worker,task)
        self.assertEqual(final['state'],'FAILED');record=self.result(task['task_id'])
        self.assertEqual(record['error']['code'],'QUOTA_EXHAUSTED');self.assertIn('usage limit',record['summary'])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM workers')['n'],1)

    async def test_plan_exposes_both_selections_without_reserving(self):
        plan=await self.delegation.spawn(self.caller,self.request(),check=True)
        self.assertTrue(plan['ready']);self.assertEqual(plan['workers'][0]['model'],'claude-sonnet-5-5')
        self.assertEqual(plan['workers'][0]['fallbacks'][0]['model'],MODEL)
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM workers')['n'],0)
        self.launch.assert_not_awaited()

    async def test_partial_edits_unknown_execution_and_timeout_never_handoff(self):
        for scenario in ('edit','directory','output','unknown','timeout'):
            with self.subTest(scenario=scenario):
                request=self.request(editing=True);request['client_request_id']=scenario
                # Each case deliberately reaches an already-admitted fake turn.
                # Prior cases in this home have recorded a real-shaped refusal.
                request['workers'][0]['quota_policy']='warn'
                worker,task,_=await self.setup_worker(request)
                error=failure()
                if scenario in ('unknown','output'):error['execution']['definitive_no_execution']=False
                if scenario=='timeout':
                    worker.claude_turn=AsyncMock(side_effect=TimeoutError('uncertain native input'))
                    await worker.task(task['task_id'],'fixture')
                else:
                    effect=(lambda:(Path(worker.row['cwd'])/'main.py').write_text('changed')) if scenario=='edit' else (lambda:(Path(worker.row['cwd'])/'empty-dir').mkdir()) if scenario=='directory' else None
                    await self.primary_failure(worker,task,error=error,effect=effect)
                final=self.j.one('SELECT state FROM tasks WHERE task_id=?',(task['task_id'],))
                self.assertIn(final['state'],TERMINAL);self.assertFalse(worker.fallback_pending)

    async def test_suppression_is_only_confirmed_quota_in_same_group_and_home(self):
        worker,task,_=await self.setup_worker();await self.primary_failure(worker,task)
        error=scope_limit(self.j,worker.row,worker.home)
        self.assertTrue(error['suppressed']);self.assertFalse(error['may_have_received'])
        self.assertIsNone(scope_limit(self.j,worker.row,self.home/'other-account-home'))
        self.assertIsNone(scope_limit(self.j,dict(worker.row,group_id=new_id()),worker.home))
        self.assertIsNone(scope_limit(self.j,dict(worker.row,provider='antigravity'),worker.home))
        newtask=new_id();self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(newtask,worker.row['worker_id'],None,None,'QUEUED',None,None,1,'{"initial":true}'))
        later=Worker(self.j,worker.row['worker_id']);later.bootstrap();later.running_task=newtask
        with patch('nativepeers.worker_process.asyncio.create_subprocess_exec',side_effect=AssertionError('exhausted provider launched')):
            result=await later.claude_turn('not submitted')
        self.assertTrue(result['error']['suppressed'])
        self.j.db.execute('UPDATE tasks SET task_json=? WHERE task_id=?',('{"initial":false}',newtask))
        self.assertIsNone(scope_limit(self.j,worker.row,worker.home,newtask))

    async def test_unavailable_candidates_and_original_turn_budget_settle(self):
        for scenario in ('missing-model','turn-budget','deadline'):
            with self.subTest(scenario=scenario):
                request=self.request();request['client_request_id']=scenario
                if scenario=='missing-model':request['workers'][0]['fallbacks'][0]['model']='absent-exact-model'
                if scenario=='turn-budget':request['limits']['max_task_turns_per_worker']=1
                worker,task,result=await self.setup_worker(request);pending=await self.primary_failure(worker,task)
                self.assertEqual(pending['state'],'FALLBACK_PENDING')
                group=Group(self.j,result['group_id'])
                if scenario=='deadline':
                    payload=json.loads(pending['task_json']);payload['deadline']=time.time()-1
                    self.j.db.execute('UPDATE tasks SET task_json=? WHERE task_id=?',(canonical(payload).decode(),task['task_id']))
                    pending=self.j.one('SELECT * FROM tasks WHERE task_id=?',(task['task_id'],))
                with self.assertRaises(PeerError) as error:await group.advance_fallback(pending)
                group.finish_fallback(pending,error.exception.json())
                record=self.result(task['task_id']);self.assertIn('Fallback not completed',record['summary'])
                self.assertEqual(record['error']['code'],'QUOTA_EXHAUSTED')

    async def test_clean_editing_handoff_uses_frozen_input_distinct_id_and_one_completion(self):
        worker,task,result=await self.setup_worker(self.request(editing=True))
        old=Path(worker.row['cwd']);pending=await self.primary_failure(worker,task)
        (self.source/'main.py').write_text('PARENT_CHANGED=1\n')
        group=Group(self.j,result['group_id'])
        started=[];frames=[]
        async def start(row):started.append(row)
        async def send(worker_id,frame):frames.append((worker_id,frame))
        with patch.object(group,'start_worker',side_effect=start),patch.object(group,'send_worker',side_effect=send):
            await group.advance_fallback(pending)
        replacement=started[0];self.assertNotEqual(replacement['worker_id'],worker.row['worker_id'])
        self.assertEqual(replacement['provider'],'antigravity');self.assertIsNone(replacement['native_id'])
        self.assertNotEqual(replacement['cwd'],str(old));self.assertEqual((Path(replacement['cwd'])/'main.py').read_text(),'VALUE=1\n')
        self.assertEqual((self.source/'main.py').read_text(),'PARENT_CHANGED=1\n')
        self.assertEqual(len(frames),1);self.assertEqual(frames[0][1]['task_id'],task['task_id'])
        later=Worker(self.j,replacement['worker_id']);later.emit=Mock();later.bootstrap()
        async def finish(text):
            later.bind(new_id(),model=MODEL);(Path(replacement['cwd'])/'main.py').write_text('VALUE=2\n')
            return {'status':'completed','text':'Changed and checked main.py.'}
        later.antigravity_turn=finish
        await later.task(task['task_id'],frames[0][1]['text'])
        final=self.delegation.result(self.caller,result['group_id'])
        self.assertEqual(len(final['results']),1);record=final['results'][0]
        self.assertEqual(record['status'],'completed');self.assertEqual(record['worker']['provider'],'antigravity')
        self.assertEqual(record['fallback']['primary_failure']['code'],'QUOTA_EXHAUSTED');self.assertEqual(len(record['attempts']),2)
        self.assertIsNotNone(record['changes']['worker_delta_artifact']);self.assertIsNotNone(record['changes']['patch_artifact'])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM completion_routes')['n'],1)
        waited=await self.delegation.wait(self.caller,result['group_id'])
        self.assertTrue(waited['ready']);self.assertEqual(self.j.one('SELECT state FROM completion_routes')['state'],'NOTIFICATION_UNKNOWN')
        same=await self.delegation.spawn(self.caller,self.request(editing=True));self.assertEqual(same['group_id'],result['group_id'])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM tasks')['n'],1)

    async def test_claude_stream_synthetic_quota_auth_and_genuine_mismatch(self):
        for scenario in ('quota','auth','mismatch','missing-terminal'):
            with self.subTest(scenario=scenario):
                request=self.request();request['client_request_id']=scenario
                worker,task,_=await self.setup_worker(request)
                event=synthetic('auth' if scenario=='auth' else 'quota')
                source='import json,sys\na=sys.argv[1:];n=a[a.index("--session-id")+1];sys.stdin.read()\n'
                source+='e='+repr(event)+';e["session_id"]=n\n'
                if scenario=='mismatch':source+='e={"type":"assistant","session_id":n,"message":{"model":"claude-opus-5-5","content":[{"type":"text","text":"unexpected model"}]}}\n'
                source+='print(json.dumps(e),flush=True)\n'
                if scenario!='missing-terminal':source+='print(json.dumps({"type":"result","subtype":"success","session_id":n,"is_error":True,"usage":{"input_tokens":0,"output_tokens":0}}),flush=True)\n'
                self.claude.write_text('#!'+sys.executable+'\n'+source);self.receipts();worker.lease['executable']=str(self.claude)
                await worker.task(task['task_id'],'small fake task')
                attempt=self.j.one('SELECT result_artifact_id FROM task_attempts WHERE task_id=?',(task['task_id'],))
                record=json.loads((self.j.root/'artifacts'/attempt['result_artifact_id']).read_text())
                expected={'quota':'QUOTA_EXHAUSTED','auth':'AUTH_REQUIRED','mismatch':'MODEL_UNAVAILABLE','missing-terminal':'QUOTA_EXHAUSTED'}[scenario]
                self.assertEqual(record['error']['code'],expected);self.assertTrue(record['error']['may_have_received'])
                self.assertEqual(worker.fallback_pending,scenario in ('quota','auth'))

    async def test_real_controllers_release_primary_then_run_native_shaped_fallback(self):
        # Fake binaries only: the real supervisor, controllers, streams, hooks,
        # lease cleanup, artifacts and notification claims all execute.
        error=synthetic()
        self.claude.write_text('#!'+sys.executable+'\n'+'''
import json,sys,time
from pathlib import Path
args=sys.argv[1:];native=args[args.index('--session-id')+1];text=sys.stdin.read()
def emit(value): print(json.dumps(dict(value,session_id=native)),flush=True)
emit({'type':'system','subtype':'init','model':args[args.index('--model')+1],'tools':['Read']})
if 'healthy sibling' in text:
 Path(SIBLING_READY).touch()
 emit({'type':'assistant','message':{'model':args[args.index('--model')+1],'content':[{'type':'text','text':'sibling output'}]}})
 emit({'type':'result','subtype':'success','is_error':False,'result':'Healthy sibling result.'})
else:
 deadline=time.monotonic()+5
 while not Path(SIBLING_READY).exists():
  if time.monotonic()>=deadline:raise RuntimeError('Sibling input barrier timed out')
  time.sleep(.01)
 emit(ERROR)
 emit({'type':'result','subtype':'error_during_execution','is_error':True,'errors':['usage limit'],'usage':{'input_tokens':0,'output_tokens':0}})
'''.replace('ERROR',repr(error)).replace('SIBLING_READY',repr(str(self.root/'sibling-admitted'))))
        fixture=self.root/'fake_antigravity.py'
        script=(ROOT/'tests/fixtures/fake_antigravity.py').read_text()
        script=script.replace("time.sleep(.15)","Path('main.py').write_text('VALUE=2\\n')\ntime.sleep(.15)")
        fixture.write_text(script)
        native=self.root/'owner.c';native.write_text('''
#include <unistd.h>
#include <sys/wait.h>
#include <stdlib.h>
int main(int argc, char **argv) {
 pid_t child=fork(); if(child<0)return 2;
 if(!child){char **args=calloc(argc+2,sizeof(char*));args[0]=PYTHON;args[1]=SCRIPT;
 for(int i=1;i<argc;++i)args[i+1]=argv[i];execv(PYTHON,args);_exit(127);}
 int status;if(waitpid(child,&status,0)<0)return 3;
 return WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
}
''')
        compiler=shutil.which('cc');self.assertIsNotNone(compiler,'Offline native-owner fixture requires a C compiler')
        subprocess.run([compiler,'-DPYTHON='+json.dumps(sys.executable),'-DSCRIPT='+json.dumps(str(fixture)),str(native),'-o',str(self.agy)],check=True,capture_output=True,timeout=30)
        self.receipts()
        request=self.request(editing=True)
        request['workers'].append({'provider':'claude','model':'claude-sonnet-5-5','title':'healthy sibling','task':'healthy sibling','permission_profile':'analysis'})
        result=await self.delegation.spawn(self.caller,request)
        self.group=Group(self.j,result['group_id']);await self.group.initialize()
        async with asyncio.timeout(20):
            while any(t['state'] not in TERMINAL for t in self.group.tasks()):await asyncio.sleep(.02)
        final=self.delegation.result(self.caller,result['group_id'])
        self.assertEqual(len(final['results']),2,final)
        fallback=next(r for r in final['results'] if r['worker']['provider']=='antigravity')
        sibling=next(r for r in final['results'] if r['worker']['provider']=='claude')
        self.assertEqual(fallback['status'],'completed',fallback)
        self.assertEqual(sibling['summary'],'Healthy sibling result.')
        self.assertEqual(len(fallback['attempts']),2)
        self.assertNotEqual(fallback['attempts'][0]['native_id'],fallback['attempts'][1]['native_id'])
        self.assertEqual(fallback['attempts'][0]['state'],'FAILED')
        self.assertEqual(fallback['attempts'][1]['model_observed'],MODEL)
        old=fallback['attempts'][0]['worker_id'];child=self.group.children[old]
        self.assertFalse(process_matches(child['owner']))
        self.assertEqual((self.source/'main.py').read_text(),'VALUE=1\n')
        self.assertEqual((Path(fallback['changes']['worktree'])/'main.py').read_text(),'VALUE=2\n')
        self.assertIsNotNone(fallback['changes']['worker_delta_artifact'])
        waited=await self.delegation.wait(self.caller,result['group_id']);self.assertTrue(waited['ready'])
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM completion_routes')['n'],1)
        with patch('nativepeers.core.Core') as core:
            await self.group.notify();core.assert_not_called()

    async def test_lost_catalog_is_an_unavailable_fallback_not_a_primary_failure(self):
        self.catalog.side_effect=PeerError('AUTH_REQUIRED','Antigravity sign-in is required')
        plan=await self.delegation.spawn(self.caller,self.request(),check=True)
        self.assertTrue(plan['ready'])
        self.assertEqual(plan['workers'][0]['fallbacks'][0]['unavailable']['code'],'AUTH_REQUIRED')
        result=await self.delegation.spawn(self.caller,self.request())
        self.assertEqual(len(result['workers']),1)

    async def test_cancel_during_handoff_admits_nothing_and_keeps_attempt(self):
        worker,task,result=await self.setup_worker();pending=await self.primary_failure(worker,task)
        group=Group(self.j,result['group_id'])
        await group.cancel({'caller_key':self.caller.key,'handle':task['task_id'],'reason':'cancel fixture'})
        with patch.object(group,'start_worker',new_callable=AsyncMock) as start:
            with self.assertRaises(PeerError):await group.advance_fallback(pending)
        start.assert_not_awaited()
        self.assertEqual(self.j.one('SELECT state FROM tasks')['state'],'CANCELED')
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM task_attempts')['n'],1)

    async def test_ordered_choices_skip_unavailable_once_and_retain_failed_final(self):
        request=self.request();request['workers'][0]['fallbacks'].insert(0,{'provider':'antigravity','model':'absent'})
        worker,task,result=await self.setup_worker(request);pending=await self.primary_failure(worker,task)
        group=Group(self.j,result['group_id']);rows=[]
        with patch.object(group,'start_worker',new_callable=AsyncMock,side_effect=lambda row:rows.append(row)),patch.object(group,'send_worker',new_callable=AsyncMock):
            await group.advance_fallback(pending)
        replacement=Worker(self.j,rows[0]['worker_id']);replacement.bootstrap();replacement.emit=Mock()
        replacement.antigravity_turn=AsyncMock(return_value={'status':'failed','text':'','error':{'code':'PROVIDER_TASK_FAILED','message':'Exact alternative refused the task.','may_have_received':True}})
        await replacement.task(task['task_id'],'fixture')
        record=self.delegation.result(self.caller,result['group_id'])['results'][0]
        self.assertEqual(record['summary'],'Exact alternative refused the task.')
        self.assertEqual([c['state'] for c in record['fallback']['choices']],['UNAVAILABLE','SELECTED'])
        self.assertEqual([a['provider'] for a in record['attempts']],['claude','antigravity'])
        self.assertFalse(replacement.fallback_pending)
        self.assertEqual(self.j.one('SELECT COUNT(*) n FROM tasks')['n'],1)

    async def test_a_mirror_lease_and_the_bridge_follow_the_task_to_its_replacement(self):
        from nativepeers import mirror
        from nativepeers.bridge_backend import NativeBackend
        worker,task,result=await self.setup_worker();session=new_id();old=worker.row['worker_id']
        with self.j.transaction() as db:mirror.grant(db,old,session,'agent1',self.caller.key,display_only=False)
        backend=NativeBackend(self.j,self.registry.get(self.caller.key),session)
        pending=await self.primary_failure(worker,task)
        self.assertEqual((await backend.activity(old,0,0,512,None))['continued_by'],dict(state='pending',from_generation=1,task_id=task['task_id']))
        group=Group(self.j,result['group_id']);rows=[]
        with patch.object(group,'start_worker',new_callable=AsyncMock,side_effect=lambda row:rows.append(row)),patch.object(group,'send_worker',new_callable=AsyncMock):
            await group.advance_fallback(pending)
        replacement=rows[0]['worker_id']
        self.assertEqual((await backend.activity(old,0,0,512,None))['continued_by'],dict(state='moved',from_generation=1,worker_id=replacement,task_id=task['task_id'],generation=1))
        self.assertEqual(mirror.live_lease(self.j,replacement)['claude_agent_id'],'agent1');self.assertIsNone(mirror.live_lease(self.j,old))
        self.assertEqual(mirror.route(self.j,replacement,task_id=task['task_id'])['generation'],1)
        rows={r['workerId']:r for r in (await backend.lineage(None,0))['workers']}
        self.assertEqual(rows[old]['continuedBy']['worker_id'],replacement);self.assertIsNone(rows[replacement]['continuedBy'])

    async def test_non_git_editing_fallback_keeps_selected_frozen_bytes(self):
        # Keep the fixture's existing identity when removing its only Git config.
        # No host configuration or product commit identity is changed.
        identities={}
        for role in ('AUTHOR','COMMITTER'):
            value=git(self.source,'var','GIT_'+role+'_IDENT').stdout.decode().rsplit(' ',2)[0]
            name,email=value.rsplit(' <',1)
            identities.update({'GIT_'+role+'_NAME':name,'GIT_'+role+'_EMAIL':email.removesuffix('>')})
        self.enterContext(patch.dict(os.environ,identities))
        shutil.rmtree(self.source/'.git')
        request=self.request(editing=True);request['workers'][0]['workspace']={'mode':'isolated-copy','file_allowlist':['main.py']}
        worker,task,result=await self.setup_worker(request);pending=await self.primary_failure(worker,task)
        group=Group(self.j,result['group_id']);rows=[]
        with patch.object(group,'start_worker',new_callable=AsyncMock,side_effect=lambda row:rows.append(row)),patch.object(group,'send_worker',new_callable=AsyncMock):
            await group.advance_fallback(pending)
        replacement=rows[0];self.assertTrue((Path(replacement['cwd'])/'.git').is_dir())
        self.assertEqual((Path(replacement['cwd'])/'main.py').read_bytes(),b'VALUE=1\n')
        self.assertEqual(json.loads(replacement['lease_json'])['baseline']['workspace_mode'],'isolated-copy')
        self.assertFalse((self.source/'.git').exists())

    async def test_cli_mcp_schema_and_result_views_share_fallback_contract(self):
        from nativepeers.mcp_server import SPAWN_SCHEMA,Facade
        from nativepeers.protocol import validate
        from nativepeers.cli import dispatch,parser
        from types import SimpleNamespace
        spec=self.request();validate(spec,SPAWN_SCHEMA)
        path=self.root/'request.json';path.write_text(json.dumps(spec))
        with patch('nativepeers.cli.bind_caller',return_value=self.caller):
            cli=await dispatch(parser().parse_args(['spawn','--spec',str(path),'--json']),self.j)
        row=self.j.one('SELECT * FROM workers WHERE group_id=?',(cli['group_id'],));worker=Worker(self.j,row['worker_id']);worker.bootstrap();worker.emit=Mock()
        task=self.j.one('SELECT * FROM tasks');await self.primary_failure(worker,task)
        facade=Facade(self.j,None)
        with patch.object(facade,'binding_state',return_value={'state':'BOUND'}),patch.object(facade,'names',return_value=['task_status','task_result']),patch('nativepeers.mcp_server.bind_caller',return_value=self.caller):
            response=await facade.call(None,SimpleNamespace(name='task_status',arguments={'handle':cli['group_id']}))
        data=json.loads(response.content[0].text)
        self.assertTrue(data['ok']);self.assertEqual(data['data']['tasks'][0]['fallback']['primary_failure']['code'],'QUOTA_EXHAUSTED')
        self.assertEqual(data['data']['tasks'][0]['state'],'FALLBACK_PENDING')
        self.assertEqual(len(data['data']['tasks'][0]['attempts']),1)
