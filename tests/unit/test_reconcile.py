import asyncio,contextlib,io,json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from nativepeers.adapters.codex_app_server import ProviderError
from nativepeers.caller import Caller
from nativepeers.cli import main
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.mcp_server import Facade
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.routing import Router
from nativepeers.reconcile import observe

class ReconcileTests(unittest.IsolatedAsyncioTestCase):
 async def test_antigravity_reads_only_exact_persisted_native_input(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d)/'state');self.addCleanup(j.close);r=Registry(j);home=Path(d)/'native';home.mkdir();native=new_id()
   sender=r.register('claude',home,new_id(),'a',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test')
   peer=r.register('antigravity',home,native,'b',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test');j.db.execute("UPDATE peer_instances SET status='live'")
   path=home/'brain'/native/'transcript.jsonl';path.parent.mkdir(parents=True);peer['native_transcript']=str(path)
   j.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',(canonical(peer).decode(),peer['handle']))
   async def delivery(*args):return {'stage':'SUBMITTED_UNCONFIRMED','evidence_kind':'local-journal-pending-antigravity-hook'}
   router=Router(j,r,{'fake':delivery});outcome=await router.send(Caller('peer',sender['handle'],sender['incarnation_id']),'b','fixture')
   message=json.loads(j.one('SELECT payload_json FROM messages')['payload_json'])
   for event in [{'type':'PLANNER_RESPONSE','step_index':1,'content':canonical(message).decode()},
                 {'type':'USER_INPUT','conversation_id':new_id(),'step_index':2,'content':canonical(message).decode()},
                 {'type':'USER_INPUT','step_index':3,'content':canonical(dict(message,body={'text':'changed'})).decode()}]:
    path.write_text(json.dumps(event)+'\n')
    result=await observe(j,r,router,outcome['message_id'])
    self.assertEqual(result['state'],'SUBMITTED_UNCONFIRMED')
    self.assertIn('persisted',result['reconciliation']['reason'])
   path.write_text(json.dumps({'type':'USER_INPUT','step_index':4,'content':'NativePeers messages: '+canonical([message]).decode()})+'\n')
   result=await observe(j,r,router,outcome['message_id']);self.assertEqual(result['state'],'INGESTED')

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

 async def test_public_refresh_is_scoped_observation_without_submission(self):
  from unittest.mock import patch
  from nativepeers.core import Core
  from nativepeers.errors import PeerError
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d)/'state');self.addCleanup(j.close);core=Core(j);r=core.registry;home=Path(d)/'native';home.mkdir();native=new_id()
   a=r.register('claude',home,new_id(),'a',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test')
   b=r.register('claude',home,native,'b',identity(os.getpid()).json(),d,{'kind':'fake'},owner_kind='test');j.db.execute("UPDATE peer_instances SET status='live'")
   path=home/'transcript.jsonl';b['native_transcript']=str(path);j.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',(canonical(b).decode(),b['handle']))
   calls=[]
   async def delivery(*args):calls.append(1);return {'stage':'DELIVERY_UNKNOWN','evidence_kind':'fake'}
   core.router.adapters['fake']=delivery;caller=Caller('peer',a['handle'],a['incarnation_id']);recipient=Caller('peer',b['handle'],b['incarnation_id'])
   sent=await core.router.send(caller,'b','observe');handle=sent['message_id'];message=json.loads(j.one('SELECT payload_json FROM messages')['payload_json'])
   for event in (None,{'type':'user','sessionId':new_id(),'message':{'content':canonical(message).decode()}}):
    if event:path.write_text(json.dumps(event)+'\n')
    result=await core.tool(caller,'task_status',{'handle':handle,'refresh':True});self.assertEqual(result['state'],'DELIVERY_UNKNOWN')
   with patch('pathlib.Path.open',side_effect=PermissionError('fixture')):
    result=await core.status(caller,handle,refresh=True)
   self.assertEqual(result['state'],'DELIVERY_UNKNOWN');self.assertEqual(result['reconciliation']['error'],'PermissionError')
   with self.assertRaises(PeerError):await core.status(Caller('peer','other','x'),handle,refresh=True)
   path.write_text(json.dumps({'type':'user','sessionId':native,'uuid':new_id(),'message':{'content':canonical(message).decode()}})+'\n')
   result=await core.status(recipient,handle,refresh=True);self.assertEqual(result['state'],'INGESTED')
   self.assertEqual(len(calls),1)
   for table in ('messages','send_requests','attempts'):self.assertEqual(j.one('SELECT COUNT(*) AS n FROM '+table)['n'],1)


class ReconcileAvailabilityTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.root=Path(self.temp.name);self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
  self.core=Core(self.journal);registry=self.core.registry
  sender=registry.register('claude',self.root/'sender',new_id(),'sender',identity(os.getpid()).json(),self.root,{'kind':'fake'},owner_kind='test')
  self.peer=registry.register('codex',self.root/'recipient',new_id(),'recipient',identity(os.getpid()).json(),self.root,{'kind':'fake'},owner_kind='test')
  self.journal.db.execute("UPDATE peer_instances SET status='live'")
  self.caller=Caller('peer',sender['handle'],sender['incarnation_id'])
  self.delivery=AsyncMock(return_value={'stage':'SUBMITTED_UNCONFIRMED','evidence_kind':'fake-submission'})
  self.core.router.adapters['fake']=self.delivery
  self.sent=asyncio.run(self.core.router.send(self.caller,self.peer['handle'],'retain on lookup failure',client_request_id='lookup'))

 def test_lookup_failures_retain_outcome_through_cli_and_mcp(self):
  cases=(('constructor',FileNotFoundError('fixture executable')),
         ('start',TimeoutError('fixture initialization')),
         ('start',ProviderError('initialize',{'message':'fixture rejection'})),
         ('call',ProviderError('thread/read',{'message':'fixture history unavailable'})),
         ('call',TimeoutError('fixture read')),
         ('call',ConnectionError('fixture disconnect')))
  for phase,failure in cases:
   for surface in ('cli','mcp'):
    with self.subTest(phase=phase,error=type(failure).__name__,surface=surface):
     client=AsyncMock()
     if phase!='constructor':getattr(client,phase).side_effect=failure
     with patch('nativepeers.reconcile.AppServer',side_effect=failure if phase=='constructor' else None,return_value=client):
      if surface=='cli':
       output=io.StringIO()
       with patch('sys.argv',['npeer','status',self.sent['message_id'],'--refresh','--json']),\
            patch('nativepeers.cli.Journal',return_value=self.journal),patch.object(self.journal,'close'),\
            patch('nativepeers.cli.bind_caller',return_value=self.caller),patch('nativepeers.cli.Core',return_value=self.core),\
            contextlib.redirect_stdout(output),self.assertRaises(SystemExit) as raised:
        main()
       self.assertEqual(raised.exception.code,6);response=json.loads(output.getvalue())
      else:
       facade=Facade(self.journal,None)
       with patch.object(facade,'binding_state',return_value={'state':'BOUND','grants':{}}),\
            patch('nativepeers.mcp_server.bind_caller',return_value=self.caller),patch('nativepeers.core.Core',return_value=self.core):
        result=asyncio.run(facade.call(None,SimpleNamespace(name='task_status',arguments={'handle':self.sent['message_id'],'refresh':True})))
       self.assertFalse(result.is_error);response=json.loads(result.content[0].text)
     self.assertTrue(response['ok']);self.assertIsNone(response['error'])
     outcome=response['data'];self.assertEqual(outcome['reconciliation']['state'],'UNCONFIRMED')
     self.assertEqual(outcome['reconciliation']['error'],type(failure).__name__)
     for key in ('message_id','exchange_id','target','state','attempt','receipts'):self.assertEqual(outcome[key],self.sent[key])
     if phase!='constructor':client.close.assert_awaited_once()
  self.delivery.assert_awaited_once()
  for table in ('messages','send_requests','attempts'):self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM '+table)['n'],1)

 def test_lookup_identity_and_path_mismatches_stay_errors_after_cleanup(self):
  outside=self.root/'outside.jsonl';outside.write_text('')
  for thread in ({'id':new_id()},{'id':self.peer['peer_key']['native_session_id'],'path':str(outside)}):
   with self.subTest(thread=thread):
    client=AsyncMock();client.call.return_value={'thread':thread}
    client.close.side_effect=ConnectionError('fixture cleanup disconnect')
    with patch('nativepeers.reconcile.AppServer',return_value=client),self.assertRaises(PeerError) as raised:
     asyncio.run(self.core.status(self.caller,self.sent['message_id'],refresh=True))
    self.assertEqual(raised.exception.code,'OWNERSHIP_UNKNOWN');client.close.assert_awaited_once()

 def test_lookup_cancellation_propagates_after_cleanup(self):
  for phase in ('start','call'):
   with self.subTest(phase=phase):
    client=AsyncMock();getattr(client,phase).side_effect=asyncio.CancelledError()
    client.close.side_effect=ConnectionError('fixture cleanup disconnect')
    with patch('nativepeers.reconcile.AppServer',return_value=client),self.assertRaises(asyncio.CancelledError):
     asyncio.run(self.core.status(self.caller,self.sent['message_id'],refresh=True))
    client.close.assert_awaited_once()
  self.assertEqual(self.core.router.outcome(self.sent['message_id'])['state'],'SUBMITTED_UNCONFIRMED')
