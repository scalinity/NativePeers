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
