"""Separate journal writers allocate phases without relying on supervisor cache."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import subprocess
import time
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.caller import Caller
from nativepeers.control import group_call
from nativepeers.core import Core
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.recovery import resume_worker
from nativepeers.registry import Registry
from nativepeers.supervisor import Group
from nativepeers.artifacts import Artifacts
from nativepeers.recovery import assert_released
from nativepeers.errors import PeerError


class PhaseAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve();self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.other=Journal(self.j.root);self.addCleanup(self.other.close)
        registry=Registry(self.j)
        parent=registry.register('codex',self.root,new_id(),'phase parent',identity(os.getpid()).json(),str(self.root),{'kind':'test'},owner_kind='test',policy={'profiles':['analysis']})
        self.caller=Caller('peer',parent['handle'],parent['incarnation_id']);self.gid=new_id();self.released=new_id();self.live=new_id()
        first,second=new_id(),new_id()
        policy=dict(initial_tasks=[first,second],phases={'1':[first,second]},workers=[self.released,self.live],completion_mode='wait',parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'],created_at=time.time(),wall_time_seconds=60)
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.gid,self.caller.key,canonical(policy).decode(),'RUNNING','{}'))
        lease={'provider_home':str(self.root),'spec':{'model':'fixture','permission_profile':'analysis','max_task_turns':2,'wall_time_seconds':60},'grants':{'depth':1}}
        for worker,state,task in [(self.released,'RELEASED_RESUMABLE',first),(self.live,'RUNNING',second)]:
            process=subprocess.Popen(['/bin/sleep','30'])
            self.addCleanup(lambda p=process:(p.terminate(),p.wait(timeout=2)))
            peer=registry.register('codex',self.root,new_id(),worker,identity(process.pid).json(),str(self.root),{'kind':'owned-worker','group_id':self.gid,'worker_id':worker},owner_kind='test')
            self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,self.gid,peer['handle'],'codex',peer['peer_key']['native_session_id'],worker,'fixture','fixture',None,str(self.root),None,state,1,canonical(lease).decode()))
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED' if worker==self.released else 'RUNNING',None,None,1,'{}'))
            if worker==self.live:self.target=peer['handle']
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.gid,1,self.caller.key,'wait','UNCLAIMED',None,None,new_id(),1))
        self.group=Group(self.j,self.gid)
        self.server=await asyncio.start_unix_server(self.group.connection,path=str(self.group.endpoint));os.chmod(self.group.endpoint,0o600)
        atomic_json(self.j.root/'groups'/self.gid/'owner.json',{'owner':self.group.owner,'generation':1,'endpoint':str(self.group.endpoint),'token':self.group.token})
        self.sent=[]
        async def send(worker,frame):self.sent.append((worker,frame))
        self.enterContext(patch.object(self.group,'send_worker',side_effect=send))
        self.enterContext(patch.object(self.group,'start_worker',new_callable=AsyncMock))
        self.enterContext(patch('nativepeers.recovery.assert_released'))
        self.core=Core(self.other)
        self.addAsyncCleanup(self.stop)

    async def stop(self):
        self.server.close();await self.server.wait_closed();self.group.endpoint.unlink(missing_ok=True)

    async def send(self):
        return await self.core.router.send(self.caller,self.target,'followup',client_request_id='one-followup')

    async def resume(self):
        return await resume_worker(self.other,self.caller,self.released,'resume input','wait','one-resume')

    async def verify_admissions(self,resumed,sent):
        self.assertEqual(sent['state'],'ACCEPTED_QUEUED')
        retry=await self.send();self.assertEqual(retry['message_id'],sent['message_id'])
        self.assertEqual((await self.resume())['task_id'],resumed['task_id'])
        policy=json.loads(self.j.one('SELECT policy_json FROM groups')['policy_json'])
        self.assertEqual(sorted(policy['phases']),['1','2','3'])
        self.assertEqual(len(self.j.all('SELECT * FROM completion_routes')),3)
        phase_tasks=[tasks[0] for phase,tasks in policy['phases'].items() if phase!='1']
        self.assertEqual(len(set(phase_tasks)),2)
        self.assertEqual({frame['task_id'] for _,frame in self.sent},set(phase_tasks))
        self.assertEqual(len(self.sent),2)
        self.assertEqual(self.group.policy['phases'],policy['phases'])

    async def test_resume_commit_before_rpc_does_not_collide_with_cached_enqueue(self):
        ready,release=asyncio.Event(),asyncio.Event()
        async def delayed(*args,**kwargs):
            ready.set();await release.wait();return await group_call(*args,**kwargs)
        with patch('nativepeers.recovery.group_call',side_effect=delayed):
            pending=asyncio.create_task(self.resume())
            try:
                await asyncio.wait_for(ready.wait(),2)
                self.assertEqual(sorted(self.group.policy['phases']),['1'])
                sent=await self.send();release.set();resumed=await asyncio.wait_for(pending,2)
            finally:
                release.set()
                if not pending.done():pending.cancel();await asyncio.gather(pending,return_exceptions=True)
        await self.verify_admissions(resumed,sent)

    async def test_enqueue_commit_after_resume_policy_read_is_merged(self):
        original=self.other.one;injected=False
        def read(sql,params=()):
            nonlocal injected
            value=original(sql,params)
            if sql=='SELECT * FROM groups WHERE group_id=?' and not injected:
                injected=True
                # A second writer commits after resume fetched its group/policy.
                policy=json.loads(value['policy_json']);external=new_id()
                with self.j.transaction() as db:
                    policy['phases']['2']=[external]
                    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(external,self.live,None,None,'COMPLETED',None,None,1,'{}'))
                    db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
                    db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.gid,2,self.caller.key,'wait','UNCLAIMED',None,None,new_id(),1))
            return value
        with patch.object(self.other,'one',side_effect=read):resumed=await self.resume()
        self.assertEqual(resumed['completion_generation'],3)
        policy=json.loads(self.j.one('SELECT policy_json FROM groups')['policy_json'])
        self.assertEqual(sorted(policy['phases']),['1','2','3'])
        self.assertEqual((await self.resume())['task_id'],resumed['task_id'])
        self.assertEqual(len(self.sent),1)

    async def test_post_commit_pipe_loss_is_uncertain_through_real_group_socket(self):
        from unittest.mock import Mock
        from nativepeers.supervisor import Group
        captured=[]
        class Pipe:
            def write(self,wire):captured.append(json.loads(wire))
            async def drain(self):raise BrokenPipeError('after write')
        self.group.children[self.live]={'process':Mock(stdin=Pipe())}
        async def handoff(worker,frame):return await Group.send_worker(self.group,worker,frame)
        with patch.object(self.group,'send_worker',side_effect=handoff):
            sent=await self.send()
        self.assertEqual(sent['state'],'DELIVERY_UNKNOWN')
        error=json.loads(sent['attempt']['error_json'])
        self.assertTrue(error['may_have_received']);self.assertEqual(error['code'],'DELIVERY_UNKNOWN')
        task=self.j.one('SELECT * FROM tasks WHERE request_message_id=?',(sent['message_id'],))
        self.assertEqual(task['task_id'],error['details']['task_id'])
        self.assertEqual(task['task_id'],captured[0]['task_id']);self.assertEqual(task['state'],'QUEUED')
        retry=await self.send();self.assertEqual(retry['message_id'],sent['message_id']);self.assertEqual(len(captured),1)
        peer=self.core.registry.get(self.target)
        self.core.router.record(sent['message_id'],sent['attempt']['attempt_id'],{'stage':'INGESTED','evidence_kind':'late-native-turn'},peer)
        self.assertEqual((await self.send())['state'],'INGESTED');self.assertEqual(len(captured),1)

    async def test_pre_admission_validation_remains_definite(self):
        self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?",(self.live,))
        sent=await self.send();self.assertEqual(sent['state'],'TARGET_OFFLINE')
        self.assertFalse(json.loads(sent['attempt']['error_json'])['may_have_received'])
        self.assertFalse(self.sent)

    async def test_parent_followup_modes_link_status_and_push_only_their_exact_phase(self):
        # A previously accepted first phase must never be replayed by a follow-up.
        self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED' WHERE completion_generation=1")
        original=self.j.one('SELECT * FROM completion_routes WHERE completion_generation=1')
        for index,(group_mode,requested,expected) in enumerate([('push',False,'push'),('push',True,'push'),('wait',True,'push'),('wait',False,'wait')]):
            policy=json.loads(self.j.one('SELECT policy_json FROM groups')['policy_json']);policy['completion_mode']=group_mode
            self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
            sent=await self.core.router.send(self.caller,self.target,'followup '+str(index),expect_reply=requested,client_request_id='mode-'+str(index))
            linked=sent['followup'];self.assertEqual(linked['completion_mode'],expected)
            self.assertEqual(linked['group_id'],self.gid);self.assertEqual(linked['completion_phase'],index+2)
            self.assertIn(linked['task_id'],linked['wait_command']);self.assertIn('--on-timeout '+('push' if expected=='push' else 'retain'),linked['next_action'])
            task=linked['task_id']
            record=dict(task_id=task,group_id=self.gid,status='completed',summary='Exact followup '+str(index),worker=dict(title='fixture',provider='codex',native_session_id='fixture'),findings=[])
            artifact=Artifacts(self.j).put(canonical(record),self.caller.key,media_type='application/json')
            self.j.db.execute("UPDATE tasks SET state='COMPLETED',result_artifact_id=? WHERE task_id=?",(artifact,task))
            status=await self.core.status(self.caller,sent['message_id'])
            self.assertEqual(status['followup']['task_state'],'COMPLETED')
            self.assertEqual(status['followup']['next_action'],'npeer result '+task+' --json')
            # Use the real router and journal, replacing only native parent delivery.
            deliver=AsyncMock(return_value=dict(stage='ACCEPTED_QUEUED',evidence_kind='fixture-parent'))
            self.core.router.adapters['test']=deliver
            with patch('nativepeers.core.Core',return_value=self.core):
                await self.group.notify(index+2);await self.group.notify(index+2)
            if expected=='push':
                deliver.assert_awaited_once();body=deliver.call_args.args[1]['body']['text']
                self.assertIn(task,body);self.assertIn('Exact followup '+str(index),body)
            else:deliver.assert_not_awaited()
            route=self.j.one('SELECT * FROM completion_routes WHERE completion_generation=?',(index+2,))
            self.assertEqual(route['state'],'PUSH_ACCEPTED' if expected=='push' else 'UNCLAIMED')
        self.assertEqual(self.j.one('SELECT * FROM completion_routes WHERE completion_generation=1'),original)

    async def test_live_writer_refusal_names_exact_serialized_followup_route(self):
        row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.live,))
        with self.assertRaises(PeerError) as error:assert_released(self.j,row)
        self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT')
        self.assertEqual(error.exception.details['worker_id'],self.live)
        self.assertIn(self.target,error.exception.details['command'])
        self.assertIn('--request-reply',error.exception.details['command'])
        for state in ('RESERVED','RELEASING','FAILED','EXITED'):
            with self.assertRaises(PeerError) as refused:assert_released(self.j,dict(row,state=state))
            self.assertEqual(refused.exception.details['command'],'npeer status '+self.gid+' --json')
            self.assertIn('cannot accept',refused.exception.details['next_action'])

    async def test_shutdown_after_admission_before_reply_retains_uncertainty_without_replay(self):
        admitted=asyncio.Event();enqueue=self.group.enqueue
        async def pause_reply(payload):
            await enqueue(payload)
            admitted.set()
            await asyncio.Future()
        self.group.server=self.server
        with patch.object(self.group,'enqueue',side_effect=pause_reply):
            request=asyncio.create_task(self.send())
            try:
                await asyncio.wait_for(admitted.wait(),2)
                committed=self.other.one('SELECT message_id,task_id FROM messages')
                self.assertTrue(committed['task_id']);self.assertEqual(len(self.sent),1)
                await asyncio.wait_for(self.group.close(),2)
                response=await asyncio.wait_for(request,2)
            finally:
                if not request.done():request.cancel();await asyncio.gather(request,return_exceptions=True)
        self.assertEqual(response['state'],'DELIVERY_UNKNOWN')
        self.assertTrue(json.loads(response['attempt']['error_json'])['may_have_received'])
        retry=await self.send()
        self.assertEqual(retry['message_id'],committed['message_id'])
        self.assertEqual(self.other.one('SELECT task_id FROM messages')['task_id'],committed['task_id'])
        self.assertEqual(len(self.sent),1)

    async def test_live_supervisor_positive_controller_failure_releases_exact_generation(self):
        from nativepeers.errors import PeerError
        previous=subprocess.Popen(['/bin/sleep','30'])
        previous_owner=identity(previous.pid).json();previous.terminate();previous.wait(timeout=2)
        row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.released,))
        atomic_json(self.j.root/'workers'/(self.released+'.json'),{'worker_id':self.released,'native_id':row['native_id'],'generation':1,'owner':previous_owner,'native_process':None})
        async def start(row):return await Group.start_worker(self.group,row)
        with patch.object(self.group,'start_worker',side_effect=start),patch('nativepeers.supervisor.asyncio.create_subprocess_exec',side_effect=FileNotFoundError('positive precreation failure')) as launch:
            with self.assertRaises(PeerError) as error:await self.resume()
            self.assertEqual(error.exception.code,'WORKER_LAUNCH_FAILED');self.assertFalse(error.exception.may_have_received)
            task=self.j.one('SELECT * FROM tasks WHERE worker_id=? AND generation=2',(self.released,))
            retry=await self.resume();self.assertEqual((retry['task_id'],retry['state']),(task['task_id'],'FAILED'))
            launch.assert_awaited_once()
        self.assertEqual(self.j.one('SELECT state FROM workers WHERE worker_id=?',(self.released,))['state'],'RELEASED_RESUMABLE')
        self.assertEqual(self.j.one('SELECT state FROM workers WHERE worker_id=?',(self.live,))['state'],'RUNNING')
        marker=json.loads(self.j.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='controller-not-created'",(self.released,))['payload_json'])
        self.assertEqual(marker['generation'],2);self.assertIs(marker['controller_created'],False)
        fresh=await resume_worker(self.other,self.caller,self.released,'fresh input','wait','fresh-resume')
        self.assertNotEqual(fresh['task_id'],task['task_id']);self.assertEqual(len(self.sent),1)
        self.assertEqual((await self.resume())['state'],'FAILED')

    async def test_live_supervisor_unknown_startup_keeps_reservation_and_retry(self):
        from nativepeers.errors import PeerError
        with patch.object(self.group,'start_worker',side_effect=PeerError('SUPERVISOR_LOST','after creation unknown',may_have_received=True)) as launch:
            with self.assertRaises(PeerError):await self.resume()
            retry=await self.resume();self.assertEqual(retry['state'],'QUEUED');launch.assert_awaited_once()
        self.assertEqual(self.j.one('SELECT state FROM workers WHERE worker_id=?',(self.released,))['state'],'RESERVED')
        self.assertFalse(self.sent)

    async def test_resume_post_write_loss_is_uncertain_and_exact_retry_sends_no_frame(self):
        from unittest.mock import Mock
        from nativepeers.errors import PeerError
        frames=[]
        class Pipe:
            def write(self,wire):frames.append(json.loads(wire))
            async def drain(self):raise BrokenPipeError('resume frame may have arrived')
        self.group.children[self.released]={'process':Mock(stdin=Pipe())}
        async def handoff(worker,frame):return await Group.send_worker(self.group,worker,frame)
        with patch.object(self.group,'send_worker',side_effect=handoff):
            with self.assertRaises(PeerError) as error:await self.resume()
        self.assertEqual(error.exception.code,'DELIVERY_UNKNOWN');self.assertTrue(error.exception.may_have_received)
        retry=await self.resume();self.assertEqual(retry['task_id'],frames[0]['task_id'])
        self.assertEqual(retry['state'],'QUEUED');self.assertEqual(len(frames),1)
        self.assertEqual(self.j.one('SELECT state FROM workers WHERE worker_id=?',(self.released,))['state'],'RESERVED')

    async def test_push_followup_bare_and_advertised_wait_timeouts_preserve_push(self):
        class Reader:
            async def read(self,n):await asyncio.Future()
        sent=await self.core.router.send(self.caller,self.target,'push requested in wait group',expect_reply=True,client_request_id='push-wait')
        linked=sent['followup'];phase=linked['completion_phase'];task=linked['task_id']
        for args in ({},{'on_timeout':'push'}):
            result=await self.group.wait(dict(caller_key=self.caller.key,handle=task,timeout_ms=1,**args),Reader())
            self.assertEqual(result['notification_disposition'],'push')
            route=self.j.one('SELECT state,mode FROM completion_routes WHERE completion_generation=?',(phase,))
            self.assertEqual(route,dict(state='UNCLAIMED',mode='push'))
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(task,))
        deliver=AsyncMock(return_value=dict(state='ACCEPTED_QUEUED',message_id=new_id()))
        with patch('nativepeers.core.Core') as core:
            core.return_value.router.send=deliver
            await self.group.notify(phase);await self.group.notify(phase)
        deliver.assert_awaited_once()

    async def test_sibling_request_does_not_create_a_parent_push(self):
        row=self.j.one('SELECT * FROM workers WHERE worker_id=?',(self.released,));peer=self.core.registry.get(row['peer_key'])
        sibling=Caller('peer',peer['handle'],peer['incarnation_id'],grants=dict(worker_id=self.released,group_id=self.gid,allow_sibling_messages=True))
        policy=json.loads(self.j.one('SELECT policy_json FROM groups')['policy_json']);policy['completion_mode']='push'
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(policy).decode(),))
        sent=await self.core.router.send(sibling,self.target,'sibling followup',expect_reply=True,client_request_id='sibling')
        self.assertEqual(sent['followup']['completion_mode'],'wait')

    async def test_mirror_owns_push_until_lease_expires_then_exact_phase_rearms(self):
        from nativepeers import mirror
        meta=dict(claude_session='fixture-session',delivery_owner='mirror',origin='fixture',client_request_id='mirror-followup')
        self.j.db.execute('INSERT INTO bridge_requests VALUES(?,?,?,?,?,?,?)',(self.caller.key,'input','mirror-followup','fixture',canonical(meta).decode(),None,time.time()))
        with self.j.transaction() as db:mirror.grant(db,self.live,'fixture-session','agent',self.caller.key,display_only=False)
        sent=await self.core.router.send(self.caller,self.target,'mirror followup',expect_reply=True,client_request_id='mirror-followup')
        phase=sent['followup']['completion_phase'];task=sent['followup']['task_id']
        self.assertEqual(sent['followup']['notification_state'],'MIRROR_PENDING')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(task,))
        send=AsyncMock(return_value=dict(state='ACCEPTED_QUEUED',message_id=new_id()))
        with patch('nativepeers.core.Core') as core:
            core.return_value.router.send=send;await self.group.notify(phase);send.assert_not_awaited()
            self.j.db.execute('UPDATE mirror_leases SET expires_at=? WHERE worker_id=?',(time.time()-1,self.live))
            mirror.expire(self.j,group_id=self.gid,supervised=True)
            self.assertEqual(self.j.one('SELECT state,mode FROM completion_routes WHERE completion_generation=?',(phase,)),dict(state='UNCLAIMED',mode='push'))
            await self.group.notify(phase);await self.group.notify(phase);send.assert_awaited_once()

    async def test_new_quiet_phase_retains_completion_without_changing_input_receipts(self):
        from nativepeers.notification_policy import apply
        await apply(self.j,self.core.registry,self.caller,'close')
        sent=await self.core.router.send(self.caller,self.target,'quiet followup',expect_reply=True,client_request_id='quiet-followup')
        receipts=sent['receipts'];phase=sent['followup']['completion_phase'];task=sent['followup']['task_id']
        self.j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(task,))
        deliver=AsyncMock(return_value=dict(stage='ACCEPTED_QUEUED',evidence_kind='fixture-parent'))
        self.core.router.adapters['test']=deliver
        with patch('nativepeers.core.Core',return_value=self.core):await self.group.notify(phase);await self.group.notify(phase)
        deliver.assert_not_awaited()
        self.assertEqual(self.j.one('SELECT state FROM completion_routes WHERE completion_generation=?',(phase,))['state'],'RETAINED')
        self.assertEqual(self.core.router.outcome(sent['message_id'])['receipts'],receipts)

    async def test_exact_retry_preserves_explicit_wait_mode_and_task_identity(self):
        class Reader:
            async def read(self,n):await asyncio.Future()
        sent=await self.core.router.send(self.caller,self.target,'retry',expect_reply=True,client_request_id='retry-route')
        task=sent['followup']['task_id'];phase=sent['followup']['completion_phase']
        await self.group.wait(dict(caller_key=self.caller.key,handle=task,timeout_ms=1,on_timeout='retain'),Reader())
        before=self.j.one('SELECT * FROM completion_routes WHERE completion_generation=?',(phase,));count=len(self.sent)
        retry=await self.core.router.send(self.caller,self.target,'retry',expect_reply=True,client_request_id='retry-route')
        self.assertEqual(retry['followup']['task_id'],task);self.assertEqual(retry['followup']['completion_mode'],'wait')
        self.assertEqual(self.j.one('SELECT * FROM completion_routes WHERE completion_generation=?',(phase,)),before)
        self.assertEqual(len(self.sent),count)

    async def test_monitor_visits_each_committed_phase_without_replaying_accepted_initial(self):
        for n in range(2):await self.core.router.send(self.caller,self.target,'monitor '+str(n),expect_reply=True,client_request_id='monitor-'+str(n))
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        self.j.db.execute("UPDATE completion_routes SET state='PUSH_ACCEPTED' WHERE completion_generation=1")
        initial=self.j.one('SELECT * FROM completion_routes WHERE completion_generation=1')
        self.group.policy.update(survive_parent=False,followup_grace_seconds=0)
        self.j.db.execute('UPDATE groups SET policy_json=?',(canonical(self.group.policy).decode(),))
        send=AsyncMock(return_value=dict(state='ACCEPTED_QUEUED',message_id=new_id()))
        with patch('nativepeers.core.Core') as core:
            core.return_value.router.send=send;await asyncio.wait_for(self.group.monitor(),2)
        self.assertEqual(send.await_count,2)
        self.assertEqual(self.j.one('SELECT * FROM completion_routes WHERE completion_generation=1'),initial)
        self.assertEqual([r['state'] for r in self.j.all('SELECT state FROM completion_routes ORDER BY completion_generation')],['PUSH_ACCEPTED']*3)
