"""Issue 53: immutable asking-task identity selects inbox OR a new phase, never both."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nativepeers.artifacts import Artifacts
from nativepeers.bridge_backend import NativeBackend
from nativepeers.caller import Caller
from nativepeers.core import Core
from nativepeers.delegation import Delegation
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.receipts import delivery_error
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


class InTaskReplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        registry=Registry(self.j);owner=identity(os.getpid()).json()
        parent=registry.register('codex',self.root,new_id(),'parent',owner,str(self.root),{'kind':'fake'},owner_kind='test')
        self.parent=Caller('peer',parent['handle'],parent['incarnation_id'])
        self.gid,self.wid,self.tid=new_id(),new_id(),new_id()
        process=subprocess.Popen(['/bin/sleep','120'])
        self.addCleanup(lambda:(process.terminate(),process.wait(timeout=2)))
        peer=registry.register('claude',self.root,new_id(),'worker',identity(process.pid).json(),str(self.root),{'kind':'owned-worker','group_id':self.gid,'worker_id':self.wid},owner_kind='test')
        self.worker=Caller('peer',peer['handle'],peer['incarnation_id'],dict(worker_id=self.wid,message_targets=[self.parent.key]))
        policy=dict(initial_tasks=[self.tid],phases={'1':[self.tid]},workers=[self.wid],completion_mode='wait',parent_owner=owner,parent_incarnation=self.parent.incarnation,created_at=time.time(),wall_time_seconds=60)
        lease=dict(spec=dict(max_task_turns=2),logical_tasks_used=1,logical_tasks_generation=1)
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.gid,self.parent.key,canonical(policy).decode(),'RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.wid,self.gid,peer['handle'],'claude',peer['peer_key']['native_session_id'],'worker','sonnet','claude-sonnet-5-5',None,str(self.root),None,'RUNNING',1,canonical(lease).decode()))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(self.tid,self.wid,None,None,'RUNNING',None,None,1,'{}'))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.gid,1,self.parent.key,'wait','UNCLAIMED',None,None,new_id(),1))
        self.group=Group(self.j,self.gid);self.group.send_worker=AsyncMock()
        self.group.server=Mock(wait_closed=AsyncMock())
        self.core=Core(self.j)
        async def enqueue(peer,message):return await self.group.enqueue(dict(worker_id=self.wid,message=message))
        self.core.router.adapters['owned-worker']=enqueue
        self.core.router.adapters['fake']=AsyncMock(return_value=dict(stage='INGESTED',evidence_kind='test-parent'))
        self.art=Artifacts(self.j).put(b'answer: 53',self.parent.key,[],media_type='text/plain')

    async def ask(self):
        receipt=await self.core.router.send(self.worker,self.parent.key,'Please supply the answer artifact',expect_reply=True,client_request_id=new_id())
        return receipt['message_id']

    async def reply(self,question,request=None,attach=True):
        return await self.core.router.send(self.parent,'reply-author','Use the attached answer',in_reply_to=question,artifact_refs=[self.art] if attach else [],client_request_id=request or new_id())

    def complete(self,task=None):
        task=task or self.tid
        record=dict(schema_version=1,group_id=self.gid,task_id=task,worker=dict(provider='claude',native_session_id=self.worker.key,title='worker'),status='completed',summary='answer: 53',model_requested='sonnet',model_observed='claude-sonnet-5-5',native_turn_ids=[],completed_at=time.time())
        art=Artifacts(self.j).put(canonical(record),self.wid,[self.parent.key],media_type='application/json')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED',result_artifact_id=? WHERE task_id=?",(art,task))

    async def test_five_reported_group_shapes_keep_both_replies_inside_initial_task(self):
        # Each of the five reported groups had two parent replies and a two-input limit.
        for shape in range(5):
            for _ in range(2):
                question=await self.ask();reply=await self.reply(question)
                self.assertEqual(reply['state'],'ACCEPTED_QUEUED')
                self.assertNotIn('followup',reply)
                self.assertEqual(reply['reply_delivery']['completion_phase'],1)
                self.assertFalse(reply['reply_delivery']['starts_phase'])
                self.assertEqual(reply['reply_delivery']['task_turns_spent'],0)
                inbox=self.core.router.inbox(self.worker,100)
                received=next(m for m in inbox if m['message_id']==reply['message_id'])
                self.assertEqual(received['artifact_refs'],[self.art])
                self.assertEqual(Artifacts(self.j).read(self.art,self.worker.key,0,100)['byte_size'],10)
                self.assertEqual(self.core.router.outcome(reply['message_id'])['state'],'INGESTED')
            self.assertEqual(len(self.group.tasks()),1)
            self.assertEqual(len(self.group.phases()),1)
        self.group.send_worker.assert_not_awaited()
        self.complete();await self.group.close()
        for value in [self.core.delegation.status(self.parent,self.gid),self.core.delegation.result(self.parent,self.gid),self.core.delegation.result(self.parent,self.gid,compact=True),self.core.delegation.groups(self.parent)['groups'][0]]:
            self.assertEqual(value['state'],'COMPLETED')
        self.assertEqual(self.core.delegation.status(self.parent,self.gid)['workers'][0]['logical_task_slots']['used'],1)

    async def test_retry_after_task_completion_keeps_original_inbox_route(self):
        question=await self.ask();request=new_id();original=await self.reply(question,request)
        self.complete();retry=await self.reply(question,request)
        self.assertEqual(original['message_id'],retry['message_id'])
        self.assertEqual(retry['reply_delivery']['state'],'UNREAD_AT_TASK_END')
        self.assertEqual(len(self.group.tasks()),1);self.group.send_worker.assert_not_awaited()

    async def test_unread_reply_remains_attributed_and_later_task_cannot_ingest_it(self):
        question=await self.ask();reply=await self.reply(question)
        self.complete()
        outcome=await self.core.status(self.parent,reply['message_id'])
        self.assertEqual(outcome['state'],'ACCEPTED_QUEUED')
        self.assertEqual(outcome['reply_delivery']['state'],'UNREAD_AT_TASK_END')
        result=self.core.delegation.result(self.parent,self.tid)['results'][0]
        self.assertEqual(result['unread_in_task_replies'],[reply['message_id']])
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(new_id(),self.wid,None,None,'RUNNING',None,None,1,'{}'))
        received=next(m for m in self.core.router.inbox(self.worker) if m['message_id']==reply['message_id'])
        self.assertFalse(received['body_available'])
        self.assertEqual(self.core.router.outcome(reply['message_id'])['state'],'ACCEPTED_QUEUED')
        self.assertEqual(len(self.group.phases()),1)

    async def test_finished_question_gets_one_explicit_phase_with_remaining_turns(self):
        question=await self.ask();self.complete()
        reply=await self.reply(question)
        followup=reply['followup'];self.assertTrue(followup['starts_phase'])
        self.assertEqual((followup['completion_phase'],followup['task_turns_remaining_at_admission'],followup['task_turns_remaining_after_admission']),(2,1,0))
        self.assertNotIn('input_submitted',followup)
        self.assertEqual(followup['completion_mode'],'wait');self.group.send_worker.assert_awaited_once()
        self.assertFalse(next(m for m in self.core.router.inbox(self.worker) if m['message_id']==reply['message_id'])['body_available'])
        status=await self.core.status(self.parent,reply['message_id'])
        self.assertEqual(status['followup'],followup)
        self.complete(followup['task_id']);await self.group.close()
        self.assertEqual(self.core.delegation.result(self.parent,self.gid)['state'],'COMPLETED')

    async def test_queued_reply_reserves_last_slot_and_next_reply_is_refused(self):
        first,second=await self.ask(),await self.ask();self.complete()
        await self.reply(first);request=new_id();refused=await self.reply(second,request)
        self.assertEqual(refused['state'],'NOT_DELIVERED_BUDGET')
        self.assertEqual(delivery_error(refused).code,'NOT_DELIVERED_BUDGET')
        self.assertFalse(delivery_error(refused).may_have_received)
        self.assertFalse(delivery_error(refused).details['input_submitted'])
        self.assertEqual(delivery_error(refused).details['task_turns_remaining'],0)
        self.assertEqual(len(self.group.tasks()),2)
        self.assertEqual(await self.reply(second,request),refused)
        self.group.send_worker.assert_awaited_once()

    async def test_reply_at_zero_budget_creates_no_failed_task(self):
        question=await self.ask();self.complete()
        lease=json.loads(self.j.one('SELECT lease_json FROM workers')['lease_json']);lease['logical_tasks_used']=2
        self.j.db.execute('UPDATE workers SET lease_json=?',(canonical(lease).decode(),))
        reply=await self.reply(question)
        self.assertEqual(reply['state'],'NOT_DELIVERED_BUDGET')
        self.assertNotIn('followup',reply);self.assertEqual(len(self.group.tasks()),1)
        self.group.send_worker.assert_not_awaited();await self.group.close()
        self.assertEqual(self.core.delegation.groups(self.parent)['groups'][0]['state'],'COMPLETED')

    async def test_committed_inbox_read_before_adapter_return_is_not_downgraded(self):
        question=await self.ask();enqueue=self.core.router.adapters['owned-worker']
        async def read_before_return(peer,message):
            receipt=await enqueue(peer,message)
            received=next(m for m in self.core.router.inbox(self.worker) if m['message_id']==message['message_id'])
            self.assertEqual(received['artifact_refs'],[self.art])
            return receipt
        self.core.router.adapters['owned-worker']=read_before_return
        reply=await self.reply(question)
        self.assertEqual(reply['state'],'INGESTED')
        self.assertEqual(len(self.group.tasks()),1)

    async def test_task_slot_claim_after_supervisor_row_read_is_seen_at_admission(self):
        question=await self.ask();self.complete()
        other=Journal(self.j.root);self.addCleanup(other.close)
        original=self.j.one;claimed=False
        def racing_read(sql,params=()):
            nonlocal claimed
            value=original(sql,params)
            if sql=='SELECT * FROM workers WHERE worker_id=? AND group_id=?' and not claimed:
                claimed=True
                lease=json.loads(value['lease_json']);lease['logical_tasks_used']=2
                with other.transaction() as db:db.execute('UPDATE workers SET lease_json=?',(canonical(lease).decode(),))
            return value
        with patch.object(self.j,'one',side_effect=racing_read):reply=await self.reply(question)
        self.assertTrue(claimed);self.assertEqual(reply['state'],'NOT_DELIVERED_BUDGET')
        self.assertEqual(json.loads(self.j.one('SELECT lease_json FROM workers')['lease_json'])['logical_tasks_used'],2)
        self.assertEqual(len(self.group.tasks()),1)

    async def test_exhausted_reply_is_typed_through_real_socket_and_mcp(self):
        from types import SimpleNamespace
        from nativepeers.files import atomic_json
        from nativepeers.mcp_server import Facade
        question=await self.ask();self.complete()
        lease=json.loads(self.j.one('SELECT lease_json FROM workers')['lease_json']);lease['logical_tasks_used']=2
        self.j.db.execute('UPDATE workers SET lease_json=?',(canonical(lease).decode(),))
        server=await asyncio.start_unix_server(self.group.connection,path=str(self.group.endpoint))
        os.chmod(self.group.endpoint,0o600)
        atomic_json(self.j.root/'groups'/self.gid/'owner.json',dict(owner=self.group.owner,generation=1,endpoint=str(self.group.endpoint),token=self.group.token))
        try:
            facade=Facade(self.j,None)
            args=dict(message_id=question,text='Deliberate exhausted phase request',client_request_id=new_id(),artifact_refs=[self.art])
            with patch.object(facade,'binding_state',return_value=dict(state='BOUND',provider='codex',grants={})),patch('nativepeers.mcp_server.bind_caller',return_value=self.parent):
                response=await facade.call(None,SimpleNamespace(name='peer_reply',arguments=args))
            value=json.loads(response.content[0].text)
            self.assertTrue(response.is_error);self.assertFalse(value['ok'])
            self.assertEqual(value['error']['code'],'NOT_DELIVERED_BUDGET')
            self.assertFalse(value['error']['may_have_received'])
            self.assertFalse(value['error']['details']['input_submitted'])
            self.assertEqual(value['data']['state'],'NOT_DELIVERED_BUDGET')
            self.assertEqual(len(self.group.tasks()),1);self.assertEqual(len(self.group.phases()),1)
        finally:
            server.close();await server.wait_closed();self.group.endpoint.unlink(missing_ok=True)

    async def test_inbox_cannot_read_reply_before_route_or_steal_finished_task_phase(self):
        question=await self.ask()
        enqueue=self.core.router.adapters['owned-worker']
        async def delayed(peer,message):
            hidden=next(m for m in self.core.router.inbox(self.worker) if m['message_id']==message['message_id'])
            self.assertFalse(hidden['body_available'])
            self.complete()  # Terminal commit wins before the enqueue transaction.
            return await enqueue(peer,message)
        self.core.router.adapters['owned-worker']=delayed
        reply=await self.reply(question)
        self.assertTrue(reply['followup']['starts_phase']);self.assertNotIn('reply_delivery',reply)

    async def test_old_task_generation_cannot_be_routed_to_new_running_task(self):
        question=await self.ask()
        self.j.db.execute('UPDATE workers SET generation=2')
        reply=await self.reply(question)
        self.assertNotIn('reply_delivery',reply);self.assertTrue(reply['followup']['starts_phase'])

    async def test_undelivered_initial_batch_is_not_success_or_failed_work(self):
        self.j.db.execute("UPDATE tasks SET state='NOT_DELIVERED_BUDGET',terminal_reason='BUDGET_EXHAUSTED'")
        await self.group.close()
        for value in [self.core.delegation.result(self.parent,self.gid),self.core.delegation.groups(self.parent)['groups'][0]]:
            self.assertEqual(value['state'],'NOT_DELIVERED_BUDGET')
        from nativepeers.supervisor import completed_state
        self.assertEqual(completed_state([dict(state='COMPLETED'),dict(state='FAILED')]),'PARTIAL')

    async def test_unsubmitted_phase_is_terminal_and_summary_preserves_completed_work(self):
        self.complete();blocked=new_id()
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(blocked,self.wid,None,None,'NOT_DELIVERED_BUDGET',None,'NOT_DELIVERED_BUDGET',1,'{}'))
        policy=self.group.policy;policy['phases']['2']=[blocked]
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
        await self.group.close()
        compact=self.core.delegation.result(self.parent,self.gid,compact=True)
        self.assertEqual(compact['state'],'COMPLETED');self.assertEqual(compact['lifecycle']['active'],0)
        self.assertEqual(compact['lifecycle']['latest'],{'NOT_DELIVERED_BUDGET':1})
        backend=NativeBackend(self.j,Registry(self.j).get(self.parent.key),new_id())
        w=self.j.one('SELECT * FROM workers');g=self.j.one('SELECT * FROM groups')
        self.assertEqual(backend.worker_row(w,g,None)['status'],'completed')
        self.assertEqual(backend.worker_row(w,g,None)['taskId'],self.tid)
        self.assertEqual(backend.worker_row(w,g,None)['undeliveredPhases'],1)
        self.assertEqual(self.core.delegation.result(self.parent,self.gid)['results'][-1]['status'],'not_delivered_budget')
