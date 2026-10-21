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
