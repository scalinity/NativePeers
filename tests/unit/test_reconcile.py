import json,os,tempfile,unittest
from pathlib import Path
from nativepeers.caller import Caller
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.routing import Router
from nativepeers.reconcile import observe

class ReconcileTests(unittest.IsolatedAsyncioTestCase):
 async def test_busy_native_attachment_is_ingestion_but_queue_record_is_not(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d)/'state');r=Registry(j);home=Path(d)/'native';home.mkdir();native=new_id()
   a=r.register('claude',home,new_id(),'a',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test')
   b=r.register('claude',home,native,'b',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test');j.db.execute("UPDATE peer_instances SET status='live'")
   path=home/'test.jsonl';b['native_transcript']=str(path);j.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',(canonical(b).decode(),b['handle']))
   async def delivery(*a):return {'stage':'SUBMITTED_UNCONFIRMED','evidence_kind':'socket-write'}
   router=Router(j,r,{'fake':delivery});outcome=await router.send(Caller('peer',a['handle'],a['incarnation_id']),'b','fixture');message=json.loads(j.one('SELECT payload_json FROM messages')['payload_json'])
   path.write_text(json.dumps({'type':'queue-operation','operation':'enqueue','sessionId':native,'content':canonical(message).decode()})+'\n')
   self.assertEqual((await observe(j,r,router,outcome['message_id']))['state'],'SUBMITTED_UNCONFIRMED')
   with path.open('a') as f:f.write(json.dumps({'type':'attachment','sessionId':native,'uuid':new_id(),'attachment':{'type':'queued_command','prompt':canonical(message).decode()}})+'\n')
   self.assertEqual((await observe(j,r,router,outcome['message_id']))['state'],'INGESTED')
   j.close()
