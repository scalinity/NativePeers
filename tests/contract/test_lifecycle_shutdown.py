"""Offline lifecycle tests with real worker and fake-provider processes."""
import asyncio
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json,canonical,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity,matches,signal_owned
from nativepeers.registry import Registry
from nativepeers.supervisor import Group


FAKE_PROVIDER='''#!/usr/bin/env python3
import json,sys,uuid
from pathlib import Path
native=str(uuid.uuid4());turn=None
def emit(value):
 print(json.dumps(value),flush=True)
for line in sys.stdin:
 request=json.loads(line);method=request.get('method');params=request.get('params',{})
 if 'id' not in request:continue
 result={}
 if method=='initialize':result={'userAgent':'lifecycle-fixture'}
 elif method=='model/list':result={'data':[{'model':'gpt-6.1-sol','supportedReasoningEfforts':[{'reasoningEffort':'xhigh'}]}]}
 elif method in ('thread/start','thread/resume'):
  native=params.get('threadId',native);result={'thread':{'id':native},'model':params['model']}
 elif method=='mcpServerStatus/list':result={'data':[{'name':'nativepeers','tools':{'peer_send':{}}}]}
 elif method=='turn/start':
  with Path(__file__).with_suffix('.inputs').open('a') as log:log.write(params['input'][0]['text']+'\\n')
  if 'UNCONFIRMED' in params['input'][0]['text']:continue
  turn=str(uuid.uuid4());result={'turn':{'id':turn}}
 elif method=='turn/interrupt':
  emit({'id':request['id'],'result':{}})
  emit({'method':'turn/completed','params':{'threadId':native,'turn':{'id':turn,'status':'interrupted','items':[]}}})
  continue
 emit({'id':request['id'],'result':result})
'''


class LifecycleShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.j=Journal(self.root);self.addCleanup(self.j.close)
        self.env=patch.dict(os.environ,{'NPEER_HOME':self.temp.name});self.env.start();self.addCleanup(self.env.stop)
        self.binary=self.root/'fake-provider.py';self.binary.write_text(FAKE_PROVIDER);self.binary.chmod(0o700)
        atomic_json(self.root/'compat/runtime.json',{'providers':{'codex':fingerprint(self.binary)}})
        parent=Registry(self.j).register('codex',self.root,new_id(),'offline parent',identity(os.getpid()).json(),self.temp.name,{'kind':'fake'},owner_kind='test')
        self.worker,self.first,self.successor,self.id=[new_id() for _ in range(4)]
        self.policy=dict(initial_tasks=[self.first,self.successor],workers=[self.worker],parent_owner=parent['owner'],
                         parent_incarnation=parent['incarnation_id'],completion_mode='wait',survive_parent=False,
                         created_at=time.time(),wall_time_seconds=60,followup_grace_seconds=0)
        spec=dict(provider='codex',title='offline worker',model='gpt-6.1-sol',reasoning_effort='xhigh',
                  permission_profile='analysis',wall_time_seconds=60,max_task_turns=8)
        lease=dict(spec=spec,grants={'worker_id':self.worker,'group_id':self.id},binding=str(self.root/'bindings'/new_id()),
                   parent_key=parent['handle'],expires_at=time.time()+120,executable=str(self.binary))
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(self.id,parent['handle'],canonical(self.policy).decode(),'RESERVED','{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(self.worker,self.id,None,'codex',None,'offline worker','gpt-6.1-sol',None,'xhigh',self.temp.name,None,'RESERVED',1,canonical(lease).decode()))
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,1,parent['handle'],'wait','UNCLAIMED',None,None,new_id(),1))
        private_directory(self.root/'groups'/self.id)
        self.group=None

    async def start(self,*,unconfirmed=False):
        for task,text in [(self.first,'UNCONFIRMED' if unconfirmed else 'active input'),(self.successor,'must never reach the provider')]:
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,self.worker,None,None,'QUEUED',None,None,1,canonical({'text':text}).decode()))
        self.group=Group(self.j,self.id)
        await self.group.initialize()
        self.assertEqual(self.j.one('SELECT state FROM groups')['state'],'RUNNING',self.group.tasks())
        async with asyncio.timeout(5):
            while not self.binary.with_suffix('.inputs').exists():await asyncio.sleep(.01)
            if not unconfirmed:
                while not self.j.one("SELECT sequence FROM events WHERE kind='native-task-started'"):await asyncio.sleep(.01)

    async def asyncTearDown(self):
        if self.group:
            await self.group.close()
            await asyncio.gather(*(child['reader'] for child in self.group.children.values()),return_exceptions=True)

    def assert_settled(self,reason):
        successor=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.successor,))
        self.assertEqual((successor['state'],successor['terminal_reason']),('CANCELED',reason))
        self.assertIsNone(successor['native_turn_id']);self.assertIsNone(successor['result_artifact_id'])
        self.assertNotIn('must never reach the provider',self.binary.with_suffix('.inputs').read_text())
        self.assertTrue(all(t['state']!='QUEUED' for t in self.group.tasks()))

    async def test_parent_loss_settles_buffered_successor_with_cause(self):
        await self.start()
        self.group.policy['parent_owner']=dict(self.policy['parent_owner'],pid=99999999,pgid=99999999)
        await asyncio.wait_for(self.group.monitor(),5)
        await self.group.close()
        self.assert_settled('PARENT_LOST')
        self.assertEqual(self.j.one('SELECT state FROM tasks WHERE task_id=?',(self.first,))['state'],'CANCELED')

    async def test_group_deadline_settles_buffered_successor_with_cause(self):
        await self.start()
        self.group.policy.update(created_at=time.time()-2,group_wall_seconds=1)
        await asyncio.wait_for(self.group.monitor(),5)
        await self.group.close()
        self.assert_settled('GROUP_DEADLINE_EXCEEDED')

    async def test_lease_eof_preserves_unconfirmed_submission_and_settles_successor(self):
        await self.start(unconfirmed=True)
        child=self.group.children[self.worker]
        os.close(child['lease_fd']);child['lease_fd']=None
        await asyncio.wait_for(child['process'].wait(),5);await child['reader']
        self.assert_settled('SUPERVISOR_LEASE_LOST')
        first=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.first,))
        self.assertEqual(first['state'],'INTERRUPTED')
        result=json.loads((self.root/'artifacts'/first['result_artifact_id']).read_text())
        self.assertEqual(result['error'],{'code':'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','cause':'SUPERVISOR_LEASE_LOST'})
        self.assertEqual(result['native_turn_ids'],[])
        manifest=json.loads((self.root/'workers'/(self.worker+'.json')).read_text())
        self.assertFalse(matches(manifest['owner']));self.assertIsNone(manifest['native_process'])
        snapshot=self.group.tasks();await self.group.close();self.assertEqual(self.group.tasks(),snapshot)

    async def test_abrupt_worker_death_keeps_submitted_task_uncertain(self):
        # Keep the fake native provider alive after its controller closes stdin.
        self.binary.write_text(FAKE_PROVIDER+'\nimport time\nwhile True: time.sleep(1)\n')
        atomic_json(self.root/'compat/runtime.json',{'providers':{'codex':fingerprint(self.binary)}})
        await self.start(unconfirmed=True)
        child=self.group.children[self.worker]
        manifest=json.loads((self.root/'workers'/(self.worker+'.json')).read_text())
        try:
            self.assertTrue(signal_owned(child['owner'],signal.SIGKILL,group=True),child['owner'])
            await asyncio.wait_for(child['process'].wait(),5);await child['reader']
            first=self.j.one('SELECT * FROM tasks WHERE task_id=?',(self.first,))
            self.assertFalse(matches(manifest['native_process']), 'Production cleanup must stop the orphan before fixture teardown')
            self.assertEqual(self.j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
            self.assertEqual(first['state'],'INTERRUPTED')
            self.assertEqual(json.loads(first['terminal_reason'])['code'],'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS')
            self.assertEqual(self.j.one('SELECT state FROM tasks WHERE task_id=?',(self.successor,))['state'],'FAILED')
            self.assertNotIn('must never reach the provider',self.binary.with_suffix('.inputs').read_text())
        finally:
            if manifest.get('native_process'):signal_owned(manifest['native_process'],signal.SIGKILL,group=True)

    async def test_cleanup_rejects_a_mismatched_provider_manifest_without_signaling(self):
        from nativepeers.errors import PeerError
        await self.start(unconfirmed=True);path=self.root/'workers'/(self.worker+'.json')
        original=json.loads(path.read_text());atomic_json(path,dict(original,generation=99))
        try:
            with self.assertRaises(PeerError) as error:await self.group.cleanup_native(self.worker,self.group.children[self.worker])
            self.assertEqual(error.exception.code,'OWNERSHIP_UNKNOWN')
            self.assertTrue(matches(original['native_process']))
        finally:atomic_json(path,original)
