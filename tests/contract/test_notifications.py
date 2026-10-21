import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.models import INLINE_BODY,bounded_text
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


class Reader:
    def __init__(self,disconnected=False):self.disconnected=disconnected
    async def read(self,n):
        if self.disconnected:return b''
        await asyncio.Future()


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.j=Journal(Path(self.temp.name));self.addCleanup(self.j.close)
        parent=Registry(self.j).register('codex',Path(self.temp.name),new_id(),'test parent',identity(os.getpid()).json(),self.temp.name,{'kind':'fake'},owner_kind='test')
        self.parent=parent['handle'];self.id=new_id();self.worker=new_id();self.task=new_id()
        policy={'initial_tasks':[self.task],'phases':{'1':[self.task]},'parent_owner':parent['owner'],'parent_incarnation':parent['incarnation_id'],'completion_mode':'push','workers':[self.worker]}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.id,self.parent,canonical(policy).decode(),'RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.worker,self.id,None,'codex',new_id(),'test','gpt-6.1-sol',None,'xhigh',self.temp.name,None,'RUNNING',1,'{}'))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(self.task,self.worker,None,None,'RUNNING',None,None,1,'{}'))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,1,self.parent,'push','UNCLAIMED',None,None,new_id(),1))
        self.group=Group(self.j,self.id)

    def route(self):return self.j.one('SELECT * FROM completion_routes')

    async def test_wait_timeout_releases_claim_and_rearms_requested_push(self):
        result=await self.group.wait({'caller_key':self.parent,'handle':self.id,'timeout_ms':5,'on_timeout':'push'},Reader())
        self.assertEqual(result['notification_disposition'],'push');self.assertEqual(self.route()['state'],'UNCLAIMED')

    async def test_wait_for_one_task_does_not_consume_the_batch_notification(self):
        second=new_id();policy=dict(self.group.policy)
        policy['initial_tasks']=[self.task,second];policy['phases']={'1':[self.task,second]};self.group.policy=policy
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(second,self.worker,None,None,'RUNNING',None,None,1,'{}'))
        waiter=asyncio.create_task(self.group.wait({'caller_key':self.parent,'handle':self.task,'timeout_ms':1000},Reader()))
        await asyncio.sleep(.01)
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
