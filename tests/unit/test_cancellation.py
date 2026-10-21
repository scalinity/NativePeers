import json,os,tempfile,time,unittest
from pathlib import Path
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker

class CancellationTests(unittest.IsolatedAsyncioTestCase):
 async def test_known_interrupt_retains_partial_result_as_canceled(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d));group,worker,task,native=[new_id() for _ in range(4)]
   spec={'provider':'claude','model':'sonnet','max_task_turns':8,'wall_time_seconds':5,'permission_profile':'analysis'}
   lease={'spec':spec,'binding':str(j.root/'bindings/test'),'parent_key':'parent'}
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
    lease={'spec':{'provider':'codex','model':'gpt-6.1-sol','wall_time_seconds':1},'binding':str(j.root/'bindings/test'),'parent_key':'parent'}
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
