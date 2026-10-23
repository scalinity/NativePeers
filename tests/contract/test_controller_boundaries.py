"""Real control pipes and creation boundaries, without native providers."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.files import atomic_json,canonical,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.supervisor import Group


class ControllerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.gid,self.wid,self.native=[new_id() for _ in range(3)]
        policy=dict(parent_incarnation=new_id(),initial_tasks=[],workers=[self.wid],created_at=time.time(),wall_time_seconds=60)
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.gid,'parent',canonical(policy).decode(),'RUNNING','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.wid,self.gid,None,'claude',self.native,'fixture','sonnet',None,None,str(self.root),None,'RUNNING',1,'{}'))
        private_directory(self.j.root/'groups'/self.gid)
        self.group=Group(self.j,self.gid)

    async def fixture(self,fd):
        # stdin is never read; losing the independent lease terminates the process.
        return await asyncio.create_subprocess_exec(sys.executable,'-c','import os,sys; os.read(int(sys.argv[1]),1)',str(fd),
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,pass_fds=(fd,),start_new_session=True)

    async def test_saturated_control_pipe_cannot_hold_release_or_cancellation(self):
        for action in ('release','cancel','group-close'):
            with self.subTest(action=action):
                read,write=os.pipe();process=await self.fixture(read);os.close(read)
                child=dict(process=process,owner=identity(process.pid).json(),lease_fd=write,row=self.j.one('SELECT * FROM workers'))
                self.group.children={self.wid:child}
                atomic_json(self.j.root/'workers'/(self.wid+'.json'),dict(worker_id=self.wid,generation=1,native_id=self.native,
                    native_process=None,native_descendants=[],cleanup={'state':'CLOSED','output_eof':True}))
                sends=[asyncio.create_task(self.group.send_worker(self.wid,dict(operation='task',task_id=new_id(),text='x'*12000))) for _ in range(16)]
                await asyncio.sleep(.05)
                self.assertTrue(any(not task.done() for task in sends))
                try:
                    with patch('nativepeers.supervisor.CONTROL_WRITE_SECONDS',.1):
                        if action=='cancel':await asyncio.wait_for(self.group.cancel_worker(self.wid),1)
                        if action=='group-close':
                            self.group.server=await asyncio.start_unix_server(lambda r,w:w.close(),path=str(self.group.endpoint))
                            await asyncio.wait_for(self.group.close(),4)
                        else:await asyncio.wait_for(self.group.release_worker(self.wid),4)
                    self.assertIsNone(child['lease_fd']);self.assertEqual(process.returncode,0)
                    outcomes=await asyncio.gather(*sends,return_exceptions=True)
                    self.assertTrue(any(isinstance(e,PeerError) and e.may_have_received for e in outcomes))
                    self.assertEqual(self.j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
                finally:
                    if child.get('lease_fd') is not None:os.close(child['lease_fd']);child['lease_fd']=None
                    await asyncio.wait_for(process.wait(),2)
                    await asyncio.gather(*sends,return_exceptions=True)
