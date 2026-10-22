import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import threading
import unittest
from unittest.mock import AsyncMock,patch

from nativepeers.artifacts import Artifacts
from nativepeers.core import Core
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.models import INLINE_BODY,bounded_text
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


class Reader:
    def __init__(self,disconnected=False,ready=None):self.disconnected=disconnected;self.ready=ready
    async def read(self,n):
        if self.ready is not None:self.ready.set()
        if self.disconnected:return b''
        await asyncio.Future()


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.j=Journal(Path(self.temp.name));self.addCleanup(self.j.close)
        parent=Registry(self.j).register('codex',Path(self.temp.name),new_id(),'test parent',identity(os.getpid()).json(),self.temp.name,{'kind':'fake'},owner_kind='test')
        self.parent=parent['handle'];self.id=new_id();self.worker=new_id();self.task=new_id()
        policy={'initial_tasks':[self.task],'phases':{'1':[self.task]},'parent_owner':parent['owner'],'parent_incarnation':parent['incarnation_id'],'completion_mode':'push','workers':[self.worker],'created_at':time.time(),'wall_time_seconds':60}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.id,self.parent,canonical(policy).decode(),'RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.worker,self.id,None,'codex',new_id(),'test','gpt-6.1-sol',None,'xhigh',self.temp.name,None,'RUNNING',1,'{}'))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(self.task,self.worker,None,None,'RUNNING',None,None,1,'{}'))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,1,self.parent,'push','UNCLAIMED',None,None,new_id(),1))
        self.group=Group(self.j,self.id)

    def route(self):return self.j.one('SELECT * FROM completion_routes')

    def complete(self,task=None):
        task=task or self.task;worker=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.worker,))
        record={'summary':'retained finding','task_id':task,'status':'completed',
                'worker':{'provider':worker['provider'],'native_session_id':worker['native_id'],'title':worker['role_title']}}
        artifact=Artifacts(self.j).put(canonical(record),self.worker,[self.parent],media_type='application/json')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED',result_artifact_id=? WHERE task_id=?",(artifact,task))
        return artifact

    async def test_wait_timeout_releases_claim_and_rearms_requested_push(self):
        result=await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':5,'on_timeout':'push'},Reader())
        self.assertEqual(result['notification_disposition'],'push');self.assertEqual(self.route()['state'],'UNCLAIMED')

    async def test_wait_for_one_task_does_not_consume_the_batch_notification(self):
        second=new_id();policy=dict(self.group.policy)
        policy['initial_tasks']=[self.task,second];policy['phases']={'1':[self.task,second]};self.group.policy=policy
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(second,self.worker,None,None,'RUNNING',None,None,1,'{}'))
        ready=asyncio.Event()
        waiter=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.task,'timeout_ms':1000},Reader(ready=ready)))
        await asyncio.wait_for(ready.wait(),1)
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(self.task,));self.group.changed.set()
        result=await waiter
        self.assertTrue(result['ready']);self.assertEqual(self.route()['state'],'UNCLAIMED')
        self.assertEqual(result['notification_disposition'],'batch-notification-unchanged')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(second,));calls=[]
        class FakeRouter:
            async def send(s,*args,**kwargs):
                calls.append(kwargs['client_request_id']);return {'state':'ACCEPTED_QUEUED','message_id':new_id()}
        class FakeCore:
            def __init__(s,j):s.router=FakeRouter()
        with patch('nativepeers.core.Core',FakeCore):
            await self.group.notify();await self.group.notify()
        self.assertEqual(len(calls),1);self.assertEqual(self.route()['state'],'PUSH_ACCEPTED')

    async def test_disconnect_before_output_does_not_keep_a_claim(self):
        await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':100,'on_timeout':'retain'},Reader(True))
        self.assertEqual(self.route()['state'],'UNCLAIMED');self.assertEqual(self.route()['mode'],'wait')

    async def test_single_initial_task_wait_preserves_its_separate_batch_push(self):
        ready=asyncio.Event()
        waiter=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.task,'timeout_ms':1000},Reader(ready=ready)))
        await asyncio.wait_for(ready.wait(),1)
        self.assertEqual(self.route()['state'],'UNCLAIMED')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(self.task,));self.group.changed.set()
        result=await waiter
        self.assertTrue(result['ready']);self.assertEqual(result['notification_disposition'],'batch-notification-unchanged')
        calls=[]
        class FakeRouter:
            async def send(s,*args,**kwargs):
                calls.append(kwargs['client_request_id']);return {'state':'ACCEPTED_QUEUED','message_id':new_id()}
        class FakeCore:
            def __init__(s,j):s.router=FakeRouter()
        with patch('nativepeers.core.Core',FakeCore):
            await self.group.notify();await self.group.notify()
        self.assertEqual(len(calls),1);self.assertEqual(self.route()['state'],'PUSH_ACCEPTED')

    async def test_uncertain_wait_output_suppresses_automatic_duplicate(self):
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        result=await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':10},Reader())
        self.assertTrue(result['ready']);self.assertEqual(self.route()['state'],'NOTIFICATION_UNKNOWN')
        await self.group.notify();self.assertEqual(self.route()['state'],'NOTIFICATION_UNKNOWN')

    async def test_competing_notifications_submit_only_once_with_stable_delivery_id(self):
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'");calls=[];delivery=self.route()['delivery_id']
        class FakeRouter:
            async def send(s,*args,**kwargs):
                calls.append(kwargs['client_request_id']);await asyncio.sleep(.01)
                return {'state':'ACCEPTED_QUEUED','message_id':new_id()}
        class FakeCore:
            def __init__(s,j):s.router=FakeRouter()
        with patch('nativepeers.core.Core',FakeCore):await asyncio.gather(*[self.group.notify() for _ in range(5)])
        self.assertEqual(calls,[delivery]);self.assertEqual(self.route()['delivery_id'],delivery);self.assertEqual(self.route()['state'],'PUSH_ACCEPTED')

    async def test_deferred_completion_retries_original_intent_and_budget_once(self):
        from nativepeers.files import advisory_lock,async_advisory_lock,sha256,atomic_json
        from nativepeers.budget import LiveBudget
        self.complete();calls=[];core=Core(self.j)
        async def deliver(peer,message):calls.append(message['message_id']);return {'stage':'ACCEPTED_QUEUED','evidence_kind':'fixture'}
        core.router.adapters['fake']=deliver
        ledger=self.j.root/'budget.json'
        atomic_json(ledger,dict(active_intervals=[],ceiling={'active_seconds':3600,'submitted_turns':4},reservations=[],reserved_turns=0,consumed_turns=0,outstanding_native_requests=[]))
        atomic_json(self.j.root/'test-budget.json',{'path':str(ledger)})
        lock=self.j.root/'locks'/(sha256(self.parent)+'.lock')
        with patch('nativepeers.core.Core',return_value=core):
            with advisory_lock(lock),patch('nativepeers.routing.async_advisory_lock',side_effect=lambda path:async_advisory_lock(path,.02)):
                await self.group.notify()
            self.assertEqual(self.route()['state'],'UNCLAIMED');self.assertEqual(calls,[])
            self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'ADMISSION_DEFERRED')
            await asyncio.gather(self.group.notify(),self.group.notify())
        self.assertEqual(self.route()['state'],'PUSH_ACCEPTED');self.assertEqual(len(calls),1)
        self.assertEqual(self.j.one('SELECT COUNT(*) AS n FROM messages')['n'],1)
        budget=json.loads(ledger.read_text());self.assertEqual(budget['consumed_turns'],1);self.assertEqual(len(budget['reservations']),1)
        self.assertEqual(len(self.j.all("SELECT * FROM events WHERE kind='parent-completion-submitted'")),1)
        self.assertEqual(len(self.j.all("SELECT * FROM events WHERE kind='parent-completion-deferred'")),1)

    async def test_unknown_completion_never_rearmed_and_definite_failure_retained(self):
        self.complete()
        for state,expected in [('DELIVERY_UNKNOWN','NOTIFICATION_UNKNOWN'),('REJECTED','RETAINED'),('TARGET_GONE','RETAINED')]:
            with self.subTest(state=state):
                self.j.db.execute("UPDATE completion_routes SET state='UNCLAIMED'")
                with patch('nativepeers.core.Core') as fake:
                    send=fake.return_value.router.send=AsyncMock(return_value={'state':state})
                    await self.group.notify();await self.group.notify();send.assert_awaited_once()
                self.assertEqual(self.route()['state'],expected)

    async def test_idle_grace_does_not_abandon_a_definitely_unsubmitted_completion(self):
        self.complete();self.group.policy.update(created_at=time.time(),wall_time_seconds=5,survive_parent=False,followup_grace_seconds=1)
        self.group.finished_at=time.time()-2;count=0
        async def notify(generation):
            nonlocal count
            count+=1
            if count==2:self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED'")
        with patch.object(self.group,'notify',side_effect=notify):await asyncio.wait_for(self.group.monitor(),3)
        self.assertEqual(count,2)

    async def test_multibyte_and_escaped_completion_summaries_fit_the_final_body(self):
        tasks=[self.task]+[new_id() for _ in range(3)];self.group.policy['phases']={'1':tasks}
        for task in tasks[1:]:self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,self.worker,None,None,'COMPLETED',None,None,1,'{}'))
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        summary=('中💡\n"\\\t'*400)
        results=[{'task_id':task,'worker':{'title':'worker '+str(i),'provider':'claude','native_session_id':new_id()},'status':'completed','summary':summary,'result_artifact_id':'art_'+new_id()} for i,task in enumerate(tasks)]
        bodies=[]
        class FakeRouter:
            async def send(s,caller,target,text,**kwargs):
                bounded_text(text);bodies.append(text);return {'state':'ACCEPTED_QUEUED','message_id':new_id()}
        class FakeCore:
            def __init__(s,j):s.router=FakeRouter()
        with patch('nativepeers.core.Core',FakeCore),patch('nativepeers.delegation.Delegation.result',return_value={'results':results}):await self.group.notify()
        self.assertEqual(self.route()['state'],'PUSH_ACCEPTED');self.assertLessEqual(len(bodies[0].encode()),INLINE_BODY)
        sent=json.loads(bodies[0].split('\n',1)[1]);self.assertEqual(len(sent),4)
        for expected,actual in zip(results,sent):
            self.assertEqual(actual['native_session_id'],expected['worker']['native_session_id']);self.assertEqual(actual['result_artifact_id'],expected['result_artifact_id']);self.assertTrue(actual['summary_truncated'])

    async def test_public_terminal_group_wait_claims_once_without_supervisor_or_later_push(self):
        artifact=self.complete();core=Core(self.j);tasks=self.group.tasks()
        content=(self.j.root/'artifacts'/artifact).read_bytes();delivery=self.route()['delivery_id']
        with patch('nativepeers.delegation.group_call',side_effect=AssertionError('No supervisor should be contacted')), \
             patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock()
            first=await core.tool(self.group.parent,'task_wait',{'handle':self.id})
            self.assertTrue(first['ready']);self.assertTrue(first['notification_output_uncertain'])
            self.assertEqual(first['result']['results'][0]['summary'],'retained finding')
            self.assertEqual(first['notification_disposition'],'tool-result-output-pending-observation')
            self.assertEqual(first['result']['notifications'][0]['state'],'NOTIFICATION_UNKNOWN')
            route=self.route();self.assertEqual(route['state'],'NOTIFICATION_UNKNOWN')
            again=await core.tool(self.group.parent,'task_wait',{'handle':self.id})
            self.assertTrue(again['ready']);self.assertFalse(again['notification_output_uncertain'])
            self.assertEqual(again['notification_disposition'],'retained-result-read');self.assertEqual(self.route(),route)
            await asyncio.gather(*(self.group.notify() for _ in range(3)))
            send.assert_not_awaited()
        self.assertEqual(self.route()['delivery_id'],delivery);self.assertEqual(self.group.tasks(),tasks)
        self.assertEqual((self.j.root/'artifacts'/artifact).read_bytes(),content)

    async def test_public_result_artifact_option_returns_bounded_authorized_pages(self):
        import base64
        from nativepeers.cli import parser,dispatch
        from nativepeers.mcp_server import Facade
        from types import SimpleNamespace
        artifact=self.complete()
        handles=[Artifacts(self.j).put(bytes([index])*20000,self.worker,[self.parent]) for index in range(18)]
        raw=json.loads((self.j.root/'artifacts'/artifact).read_text());raw['artifacts']=handles
        replacement=Artifacts(self.j).put(canonical(raw),self.worker,[self.parent],media_type='application/json')
        self.j.db.execute('UPDATE tasks SET result_artifact_id=? WHERE task_id=?',(replacement,self.task))
        before=self.route()
        with patch('nativepeers.cli.bind_caller',return_value=self.group.parent):
            ordinary=await dispatch(parser().parse_args(['result',self.id]),self.j)
            value=await dispatch(parser().parse_args(['result',self.id,'--include-artifacts']),self.j)
        self.assertNotIn('artifact_pages',ordinary)
        pages=value['artifact_pages'];self.assertEqual(len(pages),16);self.assertEqual(value['artifact_handles_omitted'],3)
        self.assertLessEqual(sum(len(base64.b64decode(page['data_base64'])) for page in pages),262144)
        self.assertTrue(any(page['next_offset']==16384 for page in pages))
        facade=Facade(self.j,None)
        with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.group.parent):
            response=await facade.call(None,SimpleNamespace(name='task_result',arguments={'handle':self.id,'include_artifacts':True}))
        self.assertFalse(response.is_error);self.assertEqual(json.loads(response.content[0].text)['data'],value)
        self.assertEqual(self.route(),before)
        secret=Artifacts(self.j).put(b'not granted','different-parent')
        raw['artifacts']=[secret];replacement=Artifacts(self.j).put(canonical(raw),self.worker,[self.parent],media_type='application/json')
        self.j.db.execute('UPDATE tasks SET result_artifact_id=? WHERE task_id=?',(replacement,self.task))
        with self.assertRaises(PeerError) as error:Delegation(self.j,Registry(self.j)).result(self.group.parent,self.id,include_artifacts=True)
        self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')

    async def test_public_terminal_initial_task_wait_preserves_separate_batch_push(self):
        self.complete();core=Core(self.j)
        with patch('nativepeers.delegation.group_call',side_effect=AssertionError('No supervisor should be contacted')):
            value=await core.tool(self.group.parent,'task_wait',{'handle':self.task})
        self.assertTrue(value['ready']);self.assertFalse(value['notification_output_uncertain'])
        self.assertEqual(self.route()['state'],'UNCLAIMED')
        with patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock(return_value={'state':'ACCEPTED_QUEUED','message_id':new_id()})
            await self.group.notify();await self.group.notify()
            send.assert_awaited_once()
        self.assertEqual(self.route()['state'],'PUSH_ACCEPTED')

    async def test_public_terminal_followup_wait_claims_only_its_completion_generation(self):
        self.complete();followup=new_id();self.group.policy.update(phases={'1':[self.task],'2':[followup]},controller_generation=2)
        self.group.generation=2
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(self.group.policy).decode(),))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(followup,self.worker,None,None,'QUEUED',None,None,1,'{}'))
        self.complete(followup)
        self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED'");initial=self.route()
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,2,self.parent,'push','UNCLAIMED',None,None,new_id(),2))
        core=Core(self.j)
        with patch('nativepeers.delegation.group_call',side_effect=AssertionError('No supervisor should be contacted')), \
             patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock()
            value=await core.tool(self.group.parent,'task_wait',{'handle':followup})
            self.assertTrue(value['notification_output_uncertain'])
            self.assertEqual([r['task_id'] for r in value['result']['results']],[followup])
            await self.group.notify(2);await self.group.notify(2);send.assert_not_awaited()
        self.assertEqual(self.j.one('SELECT * FROM completion_routes WHERE completion_generation=1'),initial)
        route=self.j.one('SELECT * FROM completion_routes WHERE completion_generation=2')
        self.assertEqual((route['state'],route['claimed_generation']),('NOTIFICATION_UNKNOWN',2))

    async def test_terminal_wait_reclaims_expired_wait_without_later_duplicate_push(self):
        self.complete();self.j.db.execute("UPDATE completion_routes SET state='WAIT_LEASED',waiter_id='expired',lease_deadline=?,claimed_generation=0",(time.time()-1,))
        value=await Delegation(self.j,Registry(self.j)).wait(self.group.parent,self.id)
        self.assertTrue(value['notification_output_uncertain']);self.assertEqual(self.route()['state'],'NOTIFICATION_UNKNOWN')
        self.assertNotEqual(self.route()['waiter_id'],'expired');self.assertEqual(self.route()['claimed_generation'],1)
        with patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock();await self.group.notify();send.assert_not_awaited()

    async def test_terminal_wait_claim_fences_an_expired_supervisor_waiter(self):
        ready=asyncio.Event()
        old_wait=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':1000},Reader(ready=ready)))
        await asyncio.wait_for(ready.wait(),1)
        self.j.db.execute('UPDATE completion_routes SET lease_deadline=?',(time.time()-1,))
        self.complete()
        terminal=await Core(self.j).tool(self.group.parent,'task_wait',{'handle':self.id})
        self.assertTrue(terminal['notification_output_uncertain']);route=self.route()
        self.group.changed.set();old=await old_wait
        self.assertTrue(old['ready']);self.assertFalse(old['notification_output_uncertain'])
        self.assertEqual(self.route(),route)
        with patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock();await self.group.notify();send.assert_not_awaited()

    async def test_terminal_wait_preserves_existing_claims_and_immutable_reads(self):
        self.complete();delegation=Delegation(self.j,Registry(self.j))
        before=self.route();delegation.result(self.group.parent,self.id);self.assertEqual(self.route(),before)
        for state in ['WAIT_LEASED','NOTIFICATION_UNKNOWN','PUSH_SUBMITTING','PUSH_ACCEPTED','HELD','RETAINED']:
            with self.subTest(state=state):
                self.j.db.execute('UPDATE completion_routes SET state=?,waiter_id=?,lease_deadline=?,claimed_generation=0',(state,'other-waiter',time.time()+60))
                before=self.route();value=await delegation.wait(self.group.parent,self.id)
                self.assertTrue(value['ready']);self.assertFalse(value['notification_output_uncertain'])
                self.assertEqual(self.route(),before)

    async def test_terminal_worker_result_read_cannot_consume_parent_completion(self):
        from nativepeers.caller import Caller
        self.complete();caller=Caller('peer','worker-peer',new_id(),{'worker_id':self.worker,'group_id':self.id})
        value=await Delegation(self.j,Registry(self.j)).wait(caller,self.task)
        self.assertTrue(value['ready']);self.assertFalse(value['notification_output_uncertain'])
        self.assertEqual(self.route()['state'],'UNCLAIMED')

    async def test_late_old_generation_cannot_reclaim_output(self):
        self.j.db.execute("UPDATE completion_routes SET state='NOTIFICATION_UNKNOWN',claimed_generation=0")
        await self.group.notify();self.assertEqual(self.route()['claimed_generation'],0)

    async def test_budget_exhaustion_retains_result_without_push_or_supervisor_crash(self):
        from nativepeers.files import atomic_json
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        ledger=self.j.root/'ledger.json'
        atomic_json(ledger,{'active_intervals':[],'ceiling':{'active_seconds':3600,'submitted_turns':0},'reservations':[]})
        atomic_json(self.j.root/'test-budget.json',{'path':str(ledger)})
        await self.group.notify()
        self.assertEqual(self.route()['state'],'RETAINED')

    async def test_orphan_recovery_requires_dead_exact_owned_writers_and_never_replays(self):
        from nativepeers.files import atomic_json,private_directory
        from nativepeers.recovery import reconcile_owned
        manifest=self.j.root/'workers'/(self.worker+'.json')
        row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.worker,))
        owner=identity(os.getpid()).json()
        atomic_json(manifest,{'worker_id':self.worker,'native_id':row['native_id'],'generation':1,'owner':owner,'native_process':None})
        self.assertEqual(reconcile_owned(self.j,self.group.parent,self.id),[])
        owner=dict(owner,pid=99999999,pgid=99999999)
        atomic_json(manifest,{'worker_id':self.worker,'native_id':row['native_id'],'generation':1,'owner':owner,'native_process':None})
        self.assertTrue(reconcile_owned(self.j,self.group.parent,self.id,dry_run=True))
        self.assertEqual(self.j.one('SELECT state FROM tasks')['state'],'RUNNING')
        reconcile_owned(self.j,self.group.parent,self.id)
        self.assertEqual(self.j.one('SELECT state FROM tasks')['state'],'INTERRUPTED')
        self.assertEqual(self.j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
        self.assertEqual(self.j.all('SELECT * FROM send_requests'),[])

    async def test_old_worker_failure_cannot_overwrite_new_generation(self):
        self.j.db.execute('UPDATE workers SET generation=2')
        self.group.fail_worker(self.worker,{'code':'WORKER_CRASHED'},generation=1)
        self.assertEqual(self.j.one('SELECT state FROM workers')['state'],'RUNNING')
        self.assertEqual(self.j.one('SELECT state FROM tasks')['state'],'RUNNING')

    async def test_followup_admitted_during_notification_does_not_expire_idle_grace(self):
        self.complete();self.group.policy.update(created_at=time.time(),wall_time_seconds=5,survive_parent=False,followup_grace_seconds=0)
        self.group.finished_at=time.time()-20;followup=new_id();calls=0
        async def notify(generation):
            nonlocal calls
            calls+=1
            if calls==1:
                self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(followup,self.worker,None,None,'RUNNING',None,None,1,'{}'))
                self.group.finished_at=None
            else:
                self.assertIsNone(self.group.finished_at)
                self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(followup,))
            self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED'")
        with patch.object(self.group,'notify',side_effect=notify):await asyncio.wait_for(self.group.monitor(),3)
        self.assertEqual(calls,2);self.assertIsNone(self.group.shutdown_reason)

    async def test_running_monitor_honors_owned_resume_lifetime_renewal(self):
        self.complete();self.group.policy.update(created_at=time.time()-.9,wall_time_seconds=1,survive_parent=False,followup_grace_seconds=0)
        calls=0;followup=new_id()
        async def notify(generation):
            nonlocal calls
            calls+=1
            if calls==1:
                await asyncio.sleep(.2)
                # The resume connection reloads this same persisted policy on a live supervisor.
                self.group.policy.update(created_at=time.time(),wall_time_seconds=10)
                self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(self.group.policy).decode(),))
                self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(followup,self.worker,None,None,'RUNNING',None,None,1,'{}'))
            else:self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(followup,))
            self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED'")
        with patch.object(self.group,'notify',side_effect=notify):await asyncio.wait_for(self.group.monitor(),3)
        self.assertEqual(calls,2);self.assertIsNone(self.group.shutdown_reason)
        restarted=Group(self.j,self.id)
        self.assertEqual(restarted.lifetime_deadline(),self.group.lifetime_deadline())

    async def test_expired_completion_is_not_submitted_before_lifetime_check(self):
        self.complete();self.group.policy.update(created_at=time.time()-2,wall_time_seconds=1,survive_parent=False,followup_grace_seconds=0)
        with patch('nativepeers.core.Core') as core:
            send=core.return_value.router.send=AsyncMock()
            await self.group.notify();await self.group.monitor();send.assert_not_awaited()
        self.assertEqual(self.group.shutdown_reason,'GROUP_DEADLINE_EXCEEDED')

    async def test_completion_expiry_is_rechecked_after_recipient_lock(self):
        from nativepeers.files import advisory_lock,sha256
        self.complete();core=Core(self.j);calls=[]
        async def deliver(peer,message):calls.append(message);return {'stage':'ACCEPTED_QUEUED'}
        core.router.adapters['fake']=deliver
        self.group.policy.update(created_at=time.time(),wall_time_seconds=.04)
        lock=self.j.root/'locks'/(sha256(self.parent)+'.lock')
        with patch('nativepeers.core.Core',return_value=core):
            with advisory_lock(lock):
                task=asyncio.create_task(self.group.notify());await asyncio.sleep(.08)
            await asyncio.wait_for(task,1)
        self.assertEqual(calls,[]);self.assertEqual(self.route()['state'],'RETAINED')
        self.assertEqual(self.j.one('SELECT state FROM attempts')['state'],'EXPIRED_BEFORE_SUBMISSION')

    async def test_deferred_reservation_settles_on_terminal_wait_or_shutdown_only(self):
        from nativepeers.files import advisory_lock,async_advisory_lock,sha256,atomic_json
        for disposition in ('wait','shutdown','unknown'):
            with self.subTest(disposition=disposition):
                self.j.db.execute('DELETE FROM send_requests');self.j.db.execute('DELETE FROM receipts');self.j.db.execute('DELETE FROM attempts');self.j.db.execute('DELETE FROM messages');self.j.db.execute('DELETE FROM events')
                self.j.db.execute("UPDATE completion_routes SET state='UNCLAIMED'")
                self.complete();core=Core(self.j);core.router.adapters['fake']=AsyncMock(return_value={'stage':'ACCEPTED_QUEUED'})
                ledger=self.j.root/'budget.json';atomic_json(ledger,dict(active_intervals=[],ceiling={'active_seconds':60,'submitted_turns':1},reservations=[],reserved_turns=0,consumed_turns=0,outstanding_native_requests=[]))
                atomic_json(self.j.root/'test-budget.json',{'path':str(ledger)})
                lock=self.j.root/'locks'/(sha256(self.parent)+'.lock')
                with patch('nativepeers.core.Core',return_value=core),advisory_lock(lock),patch('nativepeers.routing.async_advisory_lock',side_effect=lambda path:async_advisory_lock(path,.01)):
                    await self.group.notify()
                self.assertEqual(len(json.loads(ledger.read_text())['outstanding_native_requests']),1)
                if disposition=='unknown':
                    self.j.db.execute("UPDATE attempts SET state='DELIVERY_UNKNOWN'")
                    from nativepeers.notifications import settle_deferred
                    settle_deferred(self.j,self.id,self.route()['delivery_id'])
                elif disposition=='wait':
                    value=await Delegation(self.j,Registry(self.j)).wait(self.group.parent,self.id)
                    self.assertTrue(value['notification_output_uncertain'])
                else:
                    self.group.server=type('Server',(),{'close':lambda s:None,'wait_closed':AsyncMock()})()
                    await self.group.close()
                budget=json.loads(ledger.read_text())
                self.assertEqual(budget['consumed_turns'],1 if disposition=='unknown' else 0)
                self.assertEqual(len(budget['outstanding_native_requests']),1 if disposition=='unknown' else 0)
                if disposition!='unknown':self.assertIsNotNone(budget['active_intervals'][0]['end'])
                core.router.adapters['fake'].assert_not_awaited()

    async def test_idle_close_serializes_with_resume_reservation(self):
        from nativepeers.files import async_advisory_lock
        self.complete();self.group.policy.update(created_at=time.time(),wall_time_seconds=10,survive_parent=False,followup_grace_seconds=0)
        self.group.finished_at=time.time()-20;self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED'")
        successor=new_id()
        async with async_advisory_lock(self.j.root/'locks'/(self.id+'.resume.lock')):
            monitor=asyncio.create_task(self.group.monitor());await asyncio.sleep(.03)
            self.assertFalse(monitor.done())
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(successor,self.worker,None,None,'RUNNING',None,None,1,'{}'))
            self.group.finished_at=None
        await asyncio.sleep(.03);self.assertFalse(monitor.done());self.assertFalse(self.group.closing)
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(successor,));self.group.changed.set()
        await asyncio.wait_for(monitor,2)
        self.assertTrue(self.group.closing);self.assertEqual(self.j.one('SELECT state FROM groups')['state'],'RELEASING')

    async def test_closing_supervisor_refuses_resume_before_new_worker_launch(self):
        from types import SimpleNamespace
        self.group.closing=True;output=[]
        reader=SimpleNamespace(readline=AsyncMock(return_value=canonical({'token':self.group.token,'operation':'resume','payload':{'caller_key':self.parent}})+b'\n'))
        writer=SimpleNamespace(write=output.append,drain=AsyncMock(),close=lambda:None,wait_closed=AsyncMock())
        with patch.object(self.group,'start_worker',new_callable=AsyncMock) as start:
            await self.group.connection(reader,writer);start.assert_not_awaited()
        self.assertEqual(json.loads(output[0])['error']['code'],'SUPERVISOR_RELEASING')

    async def test_wait_uses_terminal_result_when_supervisor_exits_during_connect(self):
        async def departed(*args,**kwargs):
            self.complete();raise FileNotFoundError('owned endpoint already removed')
        with patch('nativepeers.delegation.group_call',side_effect=departed) as call:
            result=await Delegation(self.j,Registry(self.j)).wait(self.group.parent,self.id)
        self.assertTrue(result['ready']);self.assertTrue(result['notification_output_uncertain'])
        self.assertEqual(result['result']['results'][0]['summary'],'retained finding');call.assert_awaited_once()

    async def followup(self):
        worker=Registry(self.j).register('codex',self.j.root,new_id(),'followup target',identity(os.getpid()).json(),str(self.j.root),
            {'kind':'owned-worker','group_id':self.id,'worker_id':self.worker},owner_kind='test')
        self.j.db.execute('UPDATE workers SET peer_key=? WHERE worker_id=?',(worker['handle'],self.worker))
        core=Core(self.j)
        async def enqueue(peer,message):return await self.group.enqueue({'worker_id':self.worker,'message':message})
        core.router.adapters['owned-worker']=enqueue
        with patch.object(self.group,'send_worker',new_callable=AsyncMock):
            receipt=await core.router.send(self.group.parent,worker['handle'],'followup input',client_request_id=new_id())
        self.assertEqual(receipt['state'],'ACCEPTED_QUEUED')
        return self.j.one('SELECT task_id FROM messages WHERE message_id=?',(receipt['message_id'],))['task_id']

    async def assert_no_phase_push(self):
        with patch('nativepeers.core.Core') as fake:
            send=fake.return_value.router.send=AsyncMock()
            for phase in self.group.phases():await self.group.notify(int(phase))
            send.assert_not_awaited()

    async def test_terminal_cli_and_mcp_group_wait_claim_every_returned_phase(self):
        from nativepeers.cli import parser,dispatch
        from nativepeers.mcp_server import Facade
        from types import SimpleNamespace
        self.complete();followup=await self.followup();self.complete(followup)
        for interface in ('cli','mcp'):
            with self.subTest(interface=interface):
                self.j.db.execute("UPDATE completion_routes SET state='UNCLAIMED',mode='push',waiter_id=NULL")
                if interface=='cli':
                    with patch('nativepeers.cli.bind_caller',return_value=self.group.parent):
                        value=await dispatch(parser().parse_args(['wait',self.id,'--json']),self.j)
                else:
                    facade=Facade(self.j,None)
                    with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.group.parent):
                        response=await facade.call(None,SimpleNamespace(name='task_wait',arguments={'handle':self.id}))
                    self.assertFalse(response.is_error);value=json.loads(response.content[0].text)['data']
                self.assertEqual({r['task_id'] for r in value['result']['results']},{self.task,followup})
                self.assertEqual([r['state'] for r in self.j.all('SELECT state FROM completion_routes')],['NOTIFICATION_UNKNOWN']*2)
                await self.assert_no_phase_push()

    async def test_live_group_wait_claims_a_phase_added_while_waiting(self):
        ready=asyncio.Event()
        waiter=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':1000},Reader(ready=ready)))
        await asyncio.wait_for(ready.wait(),1)
        followup=await self.followup()
        self.complete();self.complete(followup);self.group.changed.set()
        value=await asyncio.wait_for(waiter,2)
        self.assertEqual(len(value['result']['results']),2)
        self.assertEqual([r['state'] for r in self.j.all('SELECT state FROM completion_routes')],['NOTIFICATION_UNKNOWN']*2)
        await self.assert_no_phase_push()

    async def test_multi_phase_wait_releases_all_claims_before_output(self):
        await self.followup()
        for disposition in ('timeout','disconnect','cancel'):
            with self.subTest(disposition=disposition):
                ready=asyncio.Event()
                class LiveReader(asyncio.StreamReader):
                    async def read(self,n):ready.set();return await super().read(n)
                reader=LiveReader()
                waiter=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':30,'on_timeout':'retain'},reader))
                await asyncio.wait_for(ready.wait(),1)
                self.assertEqual([r['state'] for r in self.j.all('SELECT state FROM completion_routes')],['WAIT_LEASED']*2)
                if disposition=='disconnect':
                    reader.feed_eof()
                if disposition=='cancel':waiter.cancel()
                await asyncio.gather(waiter,return_exceptions=True)
                routes=self.j.all('SELECT * FROM completion_routes')
                self.assertEqual([r['state'] for r in routes],['UNCLAIMED']*2)
                self.assertTrue(all(r['waiter_id'] is None and r['mode']=='wait' for r in routes))
        await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':30,'on_timeout':'push'},Reader(True))
        self.assertEqual([r['mode'] for r in self.j.all('SELECT mode FROM completion_routes')],['push']*2)

    async def test_established_wait_eof_or_abort_returns_committed_result_through_cli_and_mcp(self):
        from nativepeers.cli import parser,dispatch
        from nativepeers.files import atomic_json,private_directory
        from nativepeers.mcp_server import Facade
        from types import SimpleNamespace
        endpoint=self.group.endpoint
        for interface in ('cli','mcp'):
            for close_mode in ('eof','abort','truncated'):
                with self.subTest(interface=interface,close_mode=close_mode):
                    self.j.db.execute("UPDATE tasks SET state='RUNNING',result_artifact_id=NULL")
                    self.j.db.execute("UPDATE completion_routes SET state='UNCLAIMED',waiter_id=NULL")
                    async def connection(reader,writer):
                        await reader.readline();self.complete()
                        if close_mode=='truncated':writer.write(b'{"ok":true,"data":');await writer.drain()
                        if close_mode=='abort':writer.transport.abort()
                        else:writer.close();await writer.wait_closed()
                    server=await asyncio.start_unix_server(connection,path=str(endpoint));os.chmod(endpoint,0o600)
                    atomic_json(self.j.root/'groups'/self.id/'owner.json',{'owner':self.group.owner,'endpoint':str(endpoint),'token':'test-token'})
                    try:
                        if interface=='cli':
                            with patch('nativepeers.cli.bind_caller',return_value=self.group.parent):value=await dispatch(parser().parse_args(['wait',self.id,'--json']),self.j)
                        else:
                            facade=Facade(self.j,None)
                            with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.group.parent):
                                response=await facade.call(None,SimpleNamespace(name='task_wait',arguments={'handle':self.id}))
                            self.assertFalse(response.is_error);value=json.loads(response.content[0].text)['data']
                        self.assertTrue(value['ready']);self.assertTrue(value['notification_output_uncertain'])
                        self.assertEqual(value['result']['results'][0]['summary'],'retained finding')
                        await self.assert_no_phase_push()
                    finally:server.close();await server.wait_closed();endpoint.unlink(missing_ok=True)

    async def test_established_connection_loss_with_running_task_is_structured_and_never_retried(self):
        from nativepeers.control import group_call
        from nativepeers.errors import PeerError
        from nativepeers.files import atomic_json
        from types import SimpleNamespace
        endpoint=self.group.endpoint
        server=await asyncio.start_unix_server(lambda r,w:None,path=str(endpoint));os.chmod(endpoint,0o600)
        atomic_json(self.j.root/'groups'/self.id/'owner.json',{'owner':self.group.owner,'endpoint':str(endpoint),'token':'test-token'})
        try:
            for operation in ('wait','enqueue','resume'):
                writer=SimpleNamespace(write=lambda value:None,drain=AsyncMock(),close=lambda:None,wait_closed=AsyncMock())
                reader=SimpleNamespace(readline=AsyncMock(side_effect=ConnectionResetError('fixture reset')))
                with patch('nativepeers.control.asyncio.open_unix_connection',AsyncMock(return_value=(reader,writer))) as connect:
                    with self.assertRaises(PeerError) as failure:
                        if operation=='wait':await Delegation(self.j,Registry(self.j)).wait(self.group.parent,self.id)
                        else:await group_call(self.j,self.id,operation,{})
                    self.assertEqual(failure.exception.code,'SUPERVISOR_LOST')
                    self.assertEqual(failure.exception.may_have_received,operation!='wait');connect.assert_awaited_once()
            self.assertEqual(self.j.one('SELECT state FROM tasks')['state'],'RUNNING')
            self.assertEqual(self.route()['state'],'UNCLAIMED')
        finally:server.close();await server.wait_closed();endpoint.unlink(missing_ok=True)

    async def test_terminal_wait_freezes_membership_against_a_separate_writer(self):
        from nativepeers.cli import parser,dispatch
        from nativepeers.mcp_server import Facade
        from types import SimpleNamespace
        for interface in ('cli','mcp','supervised'):
            with self.subTest(interface=interface):
                self.j.db.execute('DELETE FROM tasks WHERE task_id!=?',(self.task,))
                self.j.db.execute('DELETE FROM completion_routes WHERE completion_generation!=1')
                self.j.db.execute("UPDATE completion_routes SET state='UNCLAIMED',mode='push',waiter_id=NULL")
                self.group.policy['phases']={'1':[self.task]}
                self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(self.group.policy).decode(),))
                self.complete();late=new_id()
                artifact=Artifacts(self.j).put(canonical({'task_id':late,'summary':'new concurrent result','status':'completed','worker':{'provider':'codex','native_session_id':new_id(),'title':'late'}}),self.worker,[self.parent],media_type='application/json')
                ready,start,attempted,finished=[threading.Event() for _ in range(4)];errors=[]
                def writer():
                    try:
                        other=Journal(self.j.root)
                        try:
                            ready.set()
                            assert start.wait(2)
                            attempted.set()
                            with other.transaction() as db:
                                policy=json.loads(other.one('SELECT policy_json FROM groups')['policy_json'])
                                policy['phases']['2']=[late]
                                db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
                                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(late,self.worker,None,None,'COMPLETED',artifact,None,1,'{}'))
                                db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,2,self.parent,'push','UNCLAIMED',None,None,new_id(),1))
                        finally:other.close()
                    except BaseException as error:errors.append(error)
                    finally:finished.set()
                thread=threading.Thread(target=writer);thread.start();self.assertTrue(ready.wait(2))
                original_group=Delegation.group;original_render=Delegation._render_result;injected=False
                def lookup(delegation,caller,handle):
                    nonlocal injected
                    value=original_group(delegation,caller,handle)
                    if not injected and delegation.journal.db.in_transaction:
                        injected=True;start.set();self.assertTrue(attempted.wait(2))
                    return value
                def render(delegation,status,policy,handle):
                    self.assertTrue(finished.wait(2))
                    return original_render(delegation,status,policy,handle)
                try:
                    with patch.object(Delegation,'group',lookup),patch.object(Delegation,'_render_result',render):
                        if interface=='cli':
                            with patch('nativepeers.cli.bind_caller',return_value=self.group.parent):value=await dispatch(parser().parse_args(['wait',self.id]),self.j)
                        elif interface=='mcp':
                            facade=Facade(self.j,None)
                            with patch.object(facade,'binding_state',return_value={'state':'BOUND','provider':'codex','grants':{}}),patch('nativepeers.mcp_server.bind_caller',return_value=self.group.parent):
                                response=await facade.call(None,SimpleNamespace(name='task_wait',arguments={'handle':self.id}))
                            self.assertFalse(response.is_error);value=json.loads(response.content[0].text)['data']
                        else:value=await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':1000},Reader())
                finally:
                    start.set();thread.join(timeout=3)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
                self.assertEqual([r['task_id'] for r in value['result']['results']],[self.task])
                routes=self.j.all('SELECT * FROM completion_routes ORDER BY completion_generation')
                self.assertEqual([r['state'] for r in routes],['NOTIFICATION_UNKNOWN','UNCLAIMED'])
                self.group.policy=json.loads(self.j.one('SELECT policy_json FROM groups')['policy_json'])
                with patch('nativepeers.core.Core') as fake:
                    send=fake.return_value.router.send=AsyncMock(return_value={'state':'ACCEPTED_QUEUED','message_id':new_id()})
                    await self.group.notify(1);send.assert_not_awaited()
                    await self.group.notify(2);send.assert_awaited_once()
