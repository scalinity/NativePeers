import asyncio,json,os,tempfile,time,unittest
from unittest.mock import AsyncMock,Mock,patch
from pathlib import Path
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker
from nativepeers.supervisor import Group


def fixture(j):
 group,worker,a,b,native=[new_id() for _ in range(5)]
 policy={'initial_tasks':[a],'parent_incarnation':new_id(),'phases':{'1':[a],'2':[b]}}
 lease={'provider_home':str(j.root.resolve()),'spec':{'provider':'claude','model':'sonnet','max_task_turns':8,'wall_time_seconds':5},'binding':str(j.root/'bindings/test'),'parent_key':'parent'}
 j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent',canonical(policy).decode(),'RUNNING','{}'))
 j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,'peer','claude',native,'worker','sonnet',None,None,str(j.root),None,'RUNNING',1,canonical(lease).decode()))
 for task,state in [(a,'RUNNING'),(b,'QUEUED')]:j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,state,None,None,1,'{}'))
 supervisor=Group(j,group);supervisor.children[worker]={}
 return supervisor,Worker(j,worker),a,b


async def cancel(group,handle):
 reader=asyncio.StreamReader();reader.feed_data(canonical({'operation':'cancel','token':group.token,'payload':{'caller_key':'parent','handle':handle}})+b'\n');reader.feed_eof()
 writer=Mock();writer.drain=AsyncMock();writer.wait_closed=AsyncMock()
 await group.connection(reader,writer)
 response=json.loads(writer.write.call_args.args[0]);assert response['ok'],response
 return response['data']

class CancellationTests(unittest.IsolatedAsyncioTestCase):
 async def test_cancel_queued_task_preserves_running_task_and_buffered_input_stays_canceled(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));self.addCleanup(j.close);group,w,a,b=fixture(j)
   with patch.object(group,'send_worker',new_callable=AsyncMock) as send,patch.object(group,'cancel_and_release',new_callable=AsyncMock):
    result=await cancel(group,b);await asyncio.sleep(0)
    send.assert_not_awaited()
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(a,))['state'],'RUNNING')
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'CANCELED')
   w.claude_turn=AsyncMock(return_value={'status':'completed','text':'should not execute'});w.emit=Mock()
   await w.task(b,'buffered control frame')
   w.claude_turn.assert_not_awaited();self.assertEqual(w.tasks_started,0)

 async def test_cancel_terminal_task_does_not_touch_a_running_followup(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));self.addCleanup(j.close);group,w,a,b=fixture(j)
   j.db.execute("UPDATE tasks SET state='COMPLETED' WHERE task_id=?",(a,));j.db.execute("UPDATE tasks SET state='RUNNING' WHERE task_id=?",(b,))
   with patch.object(group,'send_worker',new_callable=AsyncMock) as send,patch.object(group,'cancel_and_release',new_callable=AsyncMock):
    result=await cancel(group,a);await asyncio.sleep(0);send.assert_not_awaited()
   self.assertEqual(result['state'],'COMPLETED');self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'RUNNING')

 async def test_running_task_cancel_is_exact_and_preserves_the_followup(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));self.addCleanup(j.close);group,w,a,b=fixture(j)
   with patch.object(group,'send_worker',new_callable=AsyncMock) as send,patch.object(group,'cancel_and_release',new_callable=AsyncMock):
    await cancel(group,a);await asyncio.sleep(0)
    send.assert_awaited_once_with(w.row['worker_id'],{'operation':'cancel-task','task_id':a})
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'QUEUED')
   j.db.execute("UPDATE tasks SET state='QUEUED' WHERE task_id=?",(a,))
   started=asyncio.Event();interrupted=asyncio.Event();calls=[]
   async def turn(text):
    calls.append(text)
    if len(calls)==1:
     started.set();await interrupted.wait();return {'status':'canceled','text':'partial'}
    return {'status':'completed','text':'followup'}
   async def interrupt():interrupted.set()
   w.claude_turn=turn;w.interrupt=interrupt;w.emit=Mock()
   task=asyncio.create_task(w.task(a,'first'));await started.wait();await w.cancel_task(a);await task
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(a,))['state'],'CANCELED')
   self.assertFalse(w.stopping);self.assertFalse(w.retire_after_task)
   await w.cancel_task(a)  # A late cancel cannot interrupt the next task.
   await w.task(b,'second');self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'COMPLETED')

 async def test_stalled_codex_cancel_closes_writer_before_resuming_pending_work(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));self.addCleanup(j.close);group,w,a,b=fixture(j)
   w.row['provider']='codex';j.db.execute("UPDATE tasks SET state='QUEUED' WHERE task_id=?",(a,))
   self.assertNotIn('history_visibility',w.lease)
   started=asyncio.Event();closed=asyncio.Event();native=w.native
   async def stalled(text):started.set();await asyncio.Future()
   async def close():closed.set()
   async def prepare():
    self.assertTrue(closed.is_set());self.assertTrue(w.lease['released_native']);self.assertEqual(w.native,native)
   w.codex_turn=stalled;w.interrupt=AsyncMock();w.client=Mock();w.client.close=AsyncMock(side_effect=close)
   w.prepare=AsyncMock(side_effect=prepare);w.emit=Mock()
   task=asyncio.create_task(w.task(a,'first'));await started.wait();await w.cancel_task(a);await task
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(a,))['state'],'CANCELED')
   terminal=j.one('SELECT result_artifact_id FROM tasks WHERE task_id=?',(a,))
   result=json.loads((j.root/'artifacts'/terminal['result_artifact_id']).read_text())
   self.assertEqual(result['history_visibility']['state'],'LEGACY_UNVERIFIED')
   self.assertEqual(result['history_visibility']['capability'],'UNKNOWN')
   self.assertFalse(result['history_visibility']['runtime_acknowledged'])
   self.assertIsNone(result['history_visibility']['persisted_source'])
   self.assertEqual(result['history_visibility']['desktop_verification'],'NOT_RUN')
   self.assertFalse(w.retire_after_task);w.prepare.assert_awaited_once()
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'QUEUED')
   w.codex_turn=AsyncMock(return_value={'status':'completed','text':'pending finding'});await w.task(b,'second')
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'COMPLETED')

 async def test_group_cancel_still_cancels_the_whole_owned_queue(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));self.addCleanup(j.close);group,w,a,b=fixture(j)
   with patch.object(group,'send_worker',new_callable=AsyncMock) as send,patch.object(group,'cancel_and_release',new_callable=AsyncMock):
    await cancel(group,group.id);await asyncio.sleep(0)
    send.assert_awaited_once_with(w.row['worker_id'],{'operation':'cancel'})
   self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'CANCELED')

 async def test_known_interrupt_retains_partial_result_as_canceled(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));group,worker,task,native=[new_id() for _ in range(4)]
   spec={'provider':'claude','model':'sonnet','max_task_turns':8,'wall_time_seconds':5,'permission_profile':'analysis'}
   lease={'provider_home':str(j.root.resolve()),'spec':spec,'binding':str(j.root/'bindings/test'),'parent_key':'parent'}
   j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
   j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,'peer','claude',native,'owned worker','sonnet',None,None,d,None,'RUNNING',1,canonical(lease).decode()))
   j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'QUEUED',None,None,1,'{}'))
   w=Worker(j,worker)
   async def interrupted(text):
    w.stopping=True
    return {'status':'failed','text':'partial owned finding','error':{'code':'WORKER_CRASHED'}}
   w.claude_turn=interrupted;w.emit=lambda *a,**k:None
   await w.task(task,'fixture')
   t=j.one('SELECT * FROM tasks WHERE task_id=?',(task,));r=json.loads((j.root/'artifacts'/t['result_artifact_id']).read_text())
   self.assertEqual(t['state'],'CANCELED');self.assertEqual(r['summary'],'partial owned finding');self.assertEqual(r['error']['code'],'USER_CANCELED');self.assertEqual(r['model_requested'],'sonnet')
   j.close()

 async def test_protocol_timeout_and_auth_error_release_writer_before_followup(self):
  from nativepeers.adapters.codex_app_server import ProviderError
  for error,expected in [(TimeoutError('wall time'),'TIMED_OUT'),(ProviderError('turn/start',{'message':'401 unauthorized'}),'FAILED')]:
   with tempfile.TemporaryDirectory() as d:
    j=Journal(Path(d));group,worker,task,native=[new_id() for _ in range(4)]
    lease={'provider_home':str(j.root.resolve()),'spec':{'provider':'codex','model':'gpt-6.1-sol','wall_time_seconds':1},'binding':str(j.root/'bindings/test'),'parent_key':'parent'}
    j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
    j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,'peer','codex',native,'owned worker','gpt-6.1-sol',None,'xhigh',d,None,'RUNNING',1,canonical(lease).decode()))
    j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'QUEUED',None,None,1,'{}'))
    w=Worker(j,worker);w.emit=lambda *a,**k:None
    async def fail(text):raise error
    w.codex_turn=fail
    await w.task(task,'fixture')
    self.assertTrue(w.retire_after_task);self.assertEqual(j.one('SELECT state FROM tasks')['state'],expected);self.assertEqual(j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
    result=json.loads((j.root/'artifacts'/j.one('SELECT result_artifact_id FROM tasks')['result_artifact_id']).read_text())
    self.assertEqual(result['error']['code'],'BUDGET_EXHAUSTED' if expected=='TIMED_OUT' else 'AUTH_REQUIRED')
    j.close()

class CreationBoundaryTests(unittest.IsolatedAsyncioTestCase):
 async def test_control_cancel_during_real_creation_submits_zero_bytes_and_successor_runs(self):
  import sys
  from nativepeers.files import atomic_json
  from nativepeers.process import identity,matches
  create=asyncio.create_subprocess_exec
  for provider in ('claude','antigravity'):
   with self.subTest(provider=provider),tempfile.TemporaryDirectory() as d:
    j=Journal(Path(d));group,w,a,b=fixture(j)
    try:
     j.db.execute("UPDATE tasks SET state='QUEUED' WHERE task_id=?",(a,))
     j.db.execute('UPDATE workers SET provider=? WHERE worker_id=?',(provider,w.row['worker_id']))
     w.row['provider']=provider;w.spec.update(permission_profile='analysis',provider=provider)
     script=Path(d)/'provider.py';capture=Path(d)/'input';ready=Path(d)/'ready'
     script.write_text('import sys,json,signal,pathlib\n'
       'signal.signal(signal.SIGINT,signal.SIG_IGN)\n'
       f'pathlib.Path({str(ready)!r}).touch()\nraw=sys.stdin.buffer.read()\npathlib.Path({str(capture)!r}).write_bytes(raw)\n'
       'if not raw:sys.exit(0)\n'
       +('print(json.dumps({"type":"system","subtype":"init","session_id":'+repr(w.native)+',"model":"sonnet","tools":["Read"]}))\nprint(json.dumps({"type":"result","session_id":'+repr(w.native)+',"subtype":"success","is_error":False,"result":"success"}))\n' if provider=='claude' else
         'print(json.dumps({"event":"init","conversation_id":'+repr(w.native)+',"init":{"tools":[]}}))\nprint(json.dumps({"event":"result","result":{"status":"SUCCESS","response":"success"}}))\n'))
     w.lease['executable']=sys.executable;w.verify_antigravity=Mock();w.bind=Mock();w.ingested=Mock();w.emit=Mock()
     created=asyncio.Event();release=asyncio.Event();owners=[];calls=0
     async def delayed(*args,**kw):
      nonlocal calls
      calls+=1;process=await create(sys.executable,str(script),**kw)
      owners.append(identity(process.pid).json())
      if calls==1:
       async with asyncio.timeout(3):
        while not ready.exists():await asyncio.sleep(.005)
       created.set();await release.wait()
      j.event(w.row['worker_id'],'native-hook-observed',{'native_id':w.native,'model':'sonnet','process':identity(process.pid).json()})
      return process
     with patch('nativepeers.worker_process.asyncio.create_subprocess_exec',side_effect=delayed),patch('nativepeers.worker_process.claude_arguments',return_value=[]),patch('nativepeers.antigravity.arguments',return_value=[]):
      running=asyncio.create_task(w.task(a,'must not submit'))
      await asyncio.wait_for(created.wait(),3)
      async def command(worker,value):await w.cancel_task(value['task_id'])
      with patch.object(group,'send_worker',side_effect=command):
       cancellation=asyncio.create_task(cancel(group,a))
       async with asyncio.timeout(1):
        while w.canceling_task!=a:await asyncio.sleep(0)
       release.set();await cancellation;await asyncio.wait_for(running,5)
      self.assertEqual(capture.read_bytes(),b'');self.assertFalse(w.input_submitted)
      self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(a,))['state'],'CANCELED')
      self.assertFalse(matches(owners[0]));self.assertFalse(w.retire_after_task)
      await asyncio.wait_for(w.task(b,'successor'),5)
      self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(b,))['state'],'COMPLETED',j.one('SELECT terminal_reason FROM tasks WHERE task_id=?',(b,)))
      await w.close_provider();self.assertTrue(all(not matches(owner) for owner in owners))
    finally:j.close()

 async def test_codex_canceled_at_entry_never_calls_turn_start(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d))
   try:
    _,w,a,_=fixture(j);w.running_task=a;w.canceling_task=a;w.client=Mock();w.client.call=AsyncMock()
    from nativepeers.errors import PeerError
    with self.assertRaises(PeerError):await w.codex_turn('canceled')
    w.client.call.assert_not_awaited();self.assertFalse(w.input_submitted)
   finally:j.close()
