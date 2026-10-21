import asyncio
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from nativepeers.files import canonical,private_directory
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity,matches
from nativepeers.registry import Registry
from nativepeers.supervisor import Group
from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json

ROOT=Path(__file__).resolve().parents[2]


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,provider,task='nonce fixture'):
        with tempfile.TemporaryDirectory() as temporary:
            j=Journal(Path(temporary));group_id=new_id();parent_id=new_id()
            parent=Registry(j).register('codex',Path(temporary),parent_id,'fake parent',identity(os.getpid()).json(),temporary,{'kind':'fake'})
            policy=dict(original_request={},workers=[],initial_tasks=[],completion_mode='wait',followup_grace_seconds=0,wall_time_seconds=10,depth=1,allow_sibling_messages=False,survive_parent=False,created_at=time.time(),parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'])
            j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group_id,parent['handle'],'{}','RESERVED','{}'))
            j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group_id,1,parent['handle'],'wait','UNCLAIMED',None,None,new_id(),1))
            private_directory(j.root/'groups'/group_id)
            for index in range(2):
                worker_id,task_id=new_id(),new_id();policy['workers'].append(worker_id);policy['initial_tasks'].append(task_id)
                spec=dict(provider=provider,title='fake '+str(index),task=task,permission_profile='analysis',model='fake-claude' if provider=='claude' else 'gpt-6.1-sol',reasoning_effort='xhigh',cwd=temporary,wall_time_seconds=5,max_task_turns=2)
                executable=ROOT/'tests/fixtures'/('fake_'+provider+'.py');executable.chmod(0o755)
                atomic_json(j.root/'compat/runtime.json',{'providers':{provider:fingerprint(executable)}})
                lease=dict(spec=spec,grants={'worker_id':worker_id,'group_id':group_id},binding=str(j.root/'bindings'/new_id()),parent_key=parent['handle'],expires_at=time.time()+30,executable=str(executable))
                j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker_id,group_id,None,provider,new_id() if provider=='claude' else None,spec['title'],spec['model'],None,'xhigh',temporary,None,'RESERVED',1,canonical(lease).decode()))
                j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,None,None,'QUEUED',None,None,1,canonical({'text':task,'initial':True}).decode()))
            j.db.execute('UPDATE groups SET policy_json=? WHERE group_id=?',(canonical(policy).decode(),group_id))
            previous=os.environ.get('NPEER_HOME');os.environ['NPEER_HOME']=temporary
            group=Group(j,group_id)
            try:
                await group.initialize();await asyncio.wait_for(group.monitor(),12)
            finally:
                await group.close()
                if previous is None:os.environ.pop('NPEER_HOME',None)
                else:os.environ['NPEER_HOME']=previous
            tasks=group.tasks();workers=j.all('SELECT * FROM workers');events=j.all("SELECT payload_json,at FROM events WHERE kind='native-task-started'")
            self.assertEqual(len({w['native_id'] for w in workers}),2)
            self.assertTrue(all(not matches(c['owner']) for c in group.children.values()))
            if task=='DROP_TERMINAL':self.assertTrue(all(t['state']=='FAILED' for t in tasks))
            else:
                self.assertTrue(all(t['state']=='COMPLETED' for t in tasks),tasks)
                self.assertEqual(len(events),2)
                terminal=j.all("SELECT at FROM events WHERE kind='native-task-terminal'")
                self.assertLess(max(e['at'] for e in events),min(e['at'] for e in terminal))
            j.close()

    async def test_two_codex_controllers_overlap(self):await self.exercise('codex')
    async def test_two_claude_controllers_overlap_and_wait_for_exit(self):await self.exercise('claude')
    async def test_lost_terminal_event_fails_without_replay(self):await self.exercise('codex','DROP_TERMINAL')
