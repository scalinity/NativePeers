import asyncio
import json
from pathlib import Path
import os
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nativepeers.artifacts import Artifacts
from nativepeers.caller import Caller
from nativepeers.cli import parser
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.mcp_server import Facade
from nativepeers.models import new_id
from nativepeers.notification_policy import apply,retention
from nativepeers.process import identity
from nativepeers.receipts import delivery_error
from nativepeers.registry import Registry
from nativepeers.routing import Router
from nativepeers.supervisor import Group


class NotificationPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.journal=Journal(Path(self.temporary.name));self.addCleanup(self.journal.close)
        self.registry=Registry(self.journal);self.admissions=[]
        async def deliver(peer,message):
            self.admissions.append((peer['handle'],message));await asyncio.sleep(.001)
            return dict(stage='ACCEPTED_QUEUED',evidence_kind='fake-provider')
        self.router=Router(self.journal,self.registry,{'fake':deliver})
        self.a=self.peer('a','00000000-0000-4000-8000-000000000001')
        self.b=self.peer('b','00000000-0000-4000-8000-000000000002')
        self.journal.db.execute("UPDATE peer_instances SET status='live'")
        self.ca=Caller('peer',self.a['handle'],self.a['incarnation_id'])
        self.cb=Caller('peer',self.b['handle'],self.b['incarnation_id'])

    def peer(self,name,native):
        return self.registry.register('codex',Path(self.temporary.name)/'home',native,name,identity(os.getpid()).json(),self.temporary.name,{'kind':'fake'},owner_kind='test')

    async def policy(self,caller,action):return await apply(self.journal,self.registry,caller,action)

    def delivered(self):return [message['body']['text'] for _,message in self.admissions]

    def retained(self,outcome,reason):
        self.assertEqual(outcome['state'],'RETAINED');self.assertIsNone(delivery_error(outcome))
        self.assertEqual([(r['stage'],r['evidence_kind'],json.loads(r['details_json'])) for r in outcome['receipts']],
                         [('RETAINED','notification-policy',{'reason':reason,'submitted':False})])

    async def test_default_open_leaves_active_collaboration_unchanged(self):
        self.assertEqual(await self.policy(self.cb,'status'),dict(state='OPEN',configured=False,message_cutoff=0,route_cutoff=0,closed_at=None,opened_at=None,written_by_incarnation=None))
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        answer=await self.router.send(self.ca,'reply-author','answer',in_reply_to=ask['message_id'],expect_reply=True)
        note=await self.router.send(self.ca,'b','note')
        self.assertEqual([answer['state'],note['state']],['ACCEPTED_QUEUED','ACCEPTED_QUEUED'])
        self.assertEqual(self.delivered(),['question','answer','note'])

    async def test_close_is_idempotent_and_scoped_to_verified_caller(self):
        await self.router.send(self.ca,'b','before')
        first=await self.policy(self.cb,'close');second=await self.policy(self.cb,'close')
        self.assertEqual(first,second);self.assertEqual(first['state'],'QUIET')
        self.assertEqual(first['message_cutoff'],self.journal.one('SELECT MAX(rowid) n FROM messages')['n'])
        self.assertEqual((await self.policy(self.ca,'status'))['state'],'OPEN')
        for caller,action in ((Caller('peer',self.b['handle'],'forged'),'close'),(Caller('external-local','external-local'),'close'),(self.cb,'mute')):
            with self.assertRaises(PeerError):await self.policy(caller,action)

    async def test_quiet_admits_only_unexpired_new_root_requests(self):
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        await self.policy(self.cb,'close')
        self.retained(await self.router.send(self.ca,'reply-author','late ack',in_reply_to=ask['message_id'],expect_reply=True),'exchange-closed')
        self.retained(await self.router.send(self.ca,'b','fyi'),'quiet-informational')
        fresh=await self.router.send(self.ca,'b','new task',expect_reply=True)
        self.assertEqual(fresh['state'],'ACCEPTED_QUEUED')
        working=await self.router.send(self.cb,'reply-author','working',in_reply_to=fresh['message_id'])
        self.retained(await self.router.send(self.ca,'reply-author','thanks',in_reply_to=working['message_id'],expect_reply=True),'quiet-informational')
        self.assertEqual(self.delivered(),['question','new task','working'])
        envelope=json.loads(self.journal.one('SELECT payload_json FROM messages WHERE message_id=?',(fresh['message_id'],))['payload_json'])
        for recipient in (self.b['handle'],'unconfigured-peer'):
            self.assertEqual(retention(self.journal.db,fresh['message_id'],dict(envelope,to={'peer_key':recipient}),envelope['expires_at']),'expired')
        self.assertIsNone(retention(self.journal.db,fresh['message_id'],envelope,time.time()))

    async def test_closed_exchanges_stay_quiet_after_reopen(self):
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        closed=await self.policy(self.cb,'close');opened=await self.policy(self.cb,'open')
        self.assertEqual((opened['state'],opened['message_cutoff'],opened['route_cutoff']),('OPEN',closed['message_cutoff'],closed['route_cutoff']))
        self.retained(await self.router.send(self.ca,'reply-author','late',in_reply_to=ask['message_id'],expect_reply=True),'exchange-closed')
        self.assertEqual((await self.router.send(self.ca,'b','new note'))['state'],'ACCEPTED_QUEUED')
        self.assertEqual(self.delivered(),['question','new note'])

    async def test_close_waits_for_inflight_admission_and_never_rewrites_it(self):
        started=asyncio.Event();release=asyncio.Event()
        async def pending(peer,message):
            started.set();await release.wait();return dict(stage='ACCEPTED_QUEUED',evidence_kind='fake-provider')
        self.router.adapters['fake']=pending
        sending=asyncio.create_task(self.router.send(self.ca,'b','in flight'))
        await started.wait()
        closing=asyncio.create_task(self.policy(self.cb,'close'))
        await asyncio.sleep(.2);self.assertFalse(closing.done())
        release.set();sent=await sending;closed=await closing
        self.assertEqual(self.router.outcome(sent['message_id'])['state'],'ACCEPTED_QUEUED')
        self.assertEqual(closed['message_cutoff'],self.journal.one('SELECT rowid n FROM messages WHERE message_id=?',(sent['message_id'],))['n'])

    async def test_completion_phase_open_at_close_stays_quiet(self):
        controller=Caller('controller','controller:g:1',grants={'parent_key':self.b['handle'],'group_id':'g','generation':1})
        def route(generation):
            self.journal.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',('g',generation,self.b['handle'],'push','PUSH_SUBMITTING',None,None,'delivery-'+str(generation),1))
        route(1);await self.policy(self.cb,'close');route(2)
        self.retained(await self.router.send(controller,'b','phase 2 done',kind='completion',client_request_id='delivery-2'),'quiet-informational')
        await self.policy(self.cb,'open');route(3)
        self.retained(await self.router.send(controller,'b','phase 1 done',kind='completion',client_request_id='delivery-1'),'completion-phase-closed')
        self.assertEqual((await self.router.send(controller,'b','phase 3 done',kind='completion',client_request_id='delivery-3'))['state'],'ACCEPTED_QUEUED')
        self.retained(await self.router.send(controller,'b','phase 2 done',kind='completion',client_request_id='delivery-2'),'quiet-informational')
        self.assertEqual(self.delivered(),['phase 3 done'])

    async def test_policy_retained_receipt_matches_the_receipt_schema_contract(self):
        from nativepeers.protocol import valid
        from nativepeers.receipts import RANK
        schema=json.loads((Path(__file__).resolve().parents[2]/'schemas/receipt.v1.json').read_text())
        await self.policy(self.cb,'close')
        receipt=(await self.router.send(self.ca,'b','late'))['receipts'][0]
        self.assertEqual((receipt['stage'],receipt['evidence_kind']),('RETAINED','notification-policy'))
        self.assertTrue(valid(receipt,schema));self.assertFalse(valid(dict(receipt,stage='NOT_A_STAGE'),schema))
        # Every reducible stage, including RETAINED, is a published receipt stage.
        self.assertEqual(set(schema['properties']['stage']['enum']),set(RANK))

    async def test_owned_workers_cannot_close_and_parent_followups_ignore_stale_policy(self):
        worker=self.registry.register('codex',Path(self.temporary.name)/'worker-home','00000000-0000-4000-8000-0000000000c1','worker',identity(os.getpid()).json(),
                                      self.temporary.name,{'kind':'owned-worker','group_id':'g','worker_id':'w'},owner_kind='owned-worker-controller')
        self.journal.db.execute("UPDATE peer_instances SET status='live'")
        core=Core(self.journal);enqueued=[]
        async def enqueue(journal,group_id,operation,payload):
            enqueued.append((group_id,operation,payload['worker_id']));return {'stage':'ACCEPTED_QUEUED','evidence_kind':'owned-worker-controller'}
        with patch('nativepeers.core.group_call',enqueue):
            self.assertEqual((await core.router.send(self.ca,worker['handle'],'first followup'))['state'],'ACCEPTED_QUEUED')
            for grants in ({'worker_id':'w'},{}):
                caller=Caller('peer',worker['handle'],worker['incarnation_id'],grants=grants)
                for action in ('close','open'):
                    with self.assertRaises(PeerError) as error:await core.tool(caller,'peer_notifications',{'action':action})
                    self.assertEqual(error.exception.code,'APPROVAL_BLOCKED')
                self.assertEqual((await core.tool(caller,'peer_notifications',{'action':'status'}))['state'],'OPEN')
            # A legacy quiet row written before this restriction cannot block controller-routed follow-ups.
            self.journal.db.execute("INSERT INTO notification_policies VALUES(?,'QUIET',?,?,?,NULL,?)",(worker['handle'],10**9,10**9,time.time(),worker['incarnation_id']))
            second=await core.router.send(self.ca,worker['handle'],'second followup')
        self.assertEqual((second['state'],enqueued),('ACCEPTED_QUEUED',[('g','enqueue','w')]*2))
        self.assertNotIn('RETAINED',[r['stage'] for r in second['receipts']])

    async def test_completion_status_and_result_expose_consumption_suppression(self):
        from nativepeers.delegation import Delegation
        from nativepeers.notification_policy import guard
        from nativepeers.presentation import render
        core=Core(self.journal);core.router.adapters['fake']=self.router.adapters['fake'];group=self.group('suppressed')
        with patch('nativepeers.core.Core',return_value=core):await group.notify(1)
        await self.policy(self.cb,'close')
        payload=json.loads(self.journal.one("SELECT payload_json FROM messages WHERE to_key=? AND json_extract(payload_json,'$.kind')='completion'",(self.b['handle'],))['payload_json'])
        peer=self.journal.one('SELECT * FROM peer_instances WHERE incarnation_id=?',(self.b['incarnation_id'],))
        self.assertEqual(guard(self.journal,peer,render(payload))['decision'],'block')
        delegation=Delegation(self.journal,Registry(self.journal))
        for value in (delegation.result(self.cb,group.id),delegation.status(self.cb,group.id)):
            route=value['notifications'][0]
            self.assertEqual((route['state'],route['delivery']['state'],route['delivery']['suppression']['reason']),('PUSH_ACCEPTED','ACCEPTED_QUEUED','completion-phase-closed'))
        self.assertEqual(len(self.admissions),1)

    async def test_retained_retries_reopen_and_reads_never_replay(self):
        await self.policy(self.cb,'close')
        first=await self.router.send(self.ca,'b','late',client_request_id='late')
        await self.policy(self.cb,'open')
        again=await self.router.send(self.ca,'b','late',client_request_id='late')
        self.assertEqual(again['attempt'],first['attempt']);self.retained(again,'quiet-informational')
        self.router.inbox(self.cb);self.router.inbox(self.cb,retained=True)
        await Core(self.journal).status(self.cb,first['message_id'])
        self.assertEqual(self.admissions,[]);self.retained(self.router.outcome(first['message_id']),'quiet-informational')

    async def test_retained_reads_expose_only_policy_retention_and_keep_page_scope(self):
        states={}
        async def lost(peer,message):raise ConnectionError('lost')
        for stage in ('HELD','REJECTED','SUBMITTED_UNCONFIRMED',None):
            async def staged(peer,message,stage=stage):return dict(stage=stage,evidence_kind='fake-provider')
            self.router.adapters['fake']=staged if stage else lost
            states[(await self.router.send(self.ca,'b',str(stage)+' body'))['message_id']]=stage or 'DELIVERY_UNKNOWN'
        await self.policy(self.cb,'close')
        quiet=[(await self.router.send(self.ca,'b','quiet '+str(i)))['message_id'] for i in range(3)]
        states.update(dict.fromkeys(quiet,'RETAINED'))
        # Ordinary reads keep native visibility rules and never relabel uncertainty as retention.
        with patch.object(self.registry,'get',return_value=dict(self.registry.get(self.b['handle']),owner_kind='interactive-standalone')):
            ordinary=self.router.inbox(self.cb)
        self.assertEqual({m['message_id']:(m['delivery_state'],m['body_available']) for m in ordinary},{m:(state,False) for m,state in states.items()})
        page=self.router.inbox(self.cb,limit=2,retained=True,paged=True)
        rest=self.router.inbox(self.cb,limit=2,retained=True,cursor=page['next_cursor'])
        self.assertIsNone(rest['next_cursor'])
        self.assertEqual([m['message_id'] for m in page['messages']+rest['messages']],quiet[::-1])
        self.assertEqual([m['body']['text'] for m in page['messages']+rest['messages']],['quiet 2','quiet 1','quiet 0'])
        with self.assertRaises(PeerError):self.router.inbox(self.cb,limit=2,cursor=page['next_cursor'])
        with self.assertRaises(PeerError):self.router.inbox(self.ca,limit=2,retained=True,cursor=page['next_cursor'])
        self.assertEqual(self.router.inbox(self.ca,retained=True),[])
        with self.assertRaises(PeerError):self.router.inbox(Caller('peer',self.b['handle'],'forged'),retained=True)
        self.assertEqual({r['stage'] for m in quiet for r in self.router.outcome(m)['receipts']},{'RETAINED'})

    async def test_uncertain_and_canceled_unchanged_but_deferred_intent_retained(self):
        async def lost(peer,message):raise ConnectionError('lost after admission')
        async def canceled(peer,message):raise asyncio.CancelledError()
        def busy(path):raise TimeoutError('lock busy')
        self.router.adapters['fake']=lost;await self.router.send(self.ca,'b','uncertain',client_request_id='unknown')
        self.router.adapters['fake']=canceled
        with self.assertRaises(asyncio.CancelledError):await self.router.send(self.ca,'b','canceled',client_request_id='canceled')
        with patch('nativepeers.routing.async_advisory_lock',busy):
            self.assertEqual((await self.router.send(self.ca,'b','deferred',client_request_id='deferred'))['state'],'ADMISSION_DEFERRED')
        await self.policy(self.cb,'close')
        for request_id,text in (('unknown','uncertain'),('canceled','canceled')):
            outcome=await self.router.send(self.ca,'b',text,client_request_id=request_id)
            self.assertEqual(outcome['state'],'DELIVERY_UNKNOWN');self.assertNotIn('RETAINED',[r['stage'] for r in outcome['receipts']])
        deferred=await self.router.send(self.ca,'b','deferred',client_request_id='deferred')
        # Intent committed before close belongs to a closed exchange even though it was never submitted.
        self.retained(deferred,'exchange-closed')
        self.assertEqual([m['message_id'] for m in self.router.inbox(self.cb,retained=True)],[deferred['message_id']])

    async def test_mcp_and_cli_expose_scoped_actions(self):
        facade=Facade(self.journal,None);state={'state':'BOUND','provider':'codex','grants':{}}
        async def call(name,args):
            with patch.object(facade,'binding_state',return_value=state),patch('nativepeers.mcp_server.bind_caller',return_value=self.cb):
                response=await facade.call(None,SimpleNamespace(name=name,arguments=args))
            return json.loads(response.content[0].text)
        with patch.object(facade,'binding_state',return_value=state):self.assertIn('peer_notifications',facade.names())
        with patch.object(facade,'binding_state',return_value=dict(state,grants={'worker_id':'w'})):self.assertNotIn('peer_notifications',facade.names())
        self.assertEqual((await call('peer_notifications',{'action':'close'}))['data']['state'],'QUIET')
        for args in ({},{'action':'mute'},{'action':'close','target':'a'}):
            self.assertFalse((await call('peer_notifications',args))['ok'])
        self.assertEqual((await call('peer_inbox',{'retained':True}))['data'],[])
        self.assertEqual((await self.policy(self.ca,'status'))['state'],'OPEN')
        self.assertEqual(parser().parse_args(['notifications','close','--json']).action,'close')
        self.assertTrue(parser().parse_args(['inbox','--retained']).retained)


    async def test_resumed_incarnation_keeps_policy_by_stable_peer_key(self):
        controller=Caller('controller','controller:g:1',grants={'parent_key':self.b['handle'],'group_id':'g','generation':1})
        ask=await self.router.send(self.cb,'a','question',expect_reply=True)
        self.journal.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',('g',1,self.b['handle'],'push','PUSH_SUBMITTING',None,None,'delivery-1',1))
        closed=await self.policy(self.cb,'close')
        resumed=self.peer('b','00000000-0000-4000-8000-000000000002')
        self.journal.db.execute("UPDATE peer_instances SET status='live' WHERE incarnation_id IN (?,?)",(self.a['incarnation_id'],resumed['incarnation_id']))
        self.assertEqual(resumed['handle'],self.b['handle']);self.assertNotEqual(resumed['incarnation_id'],self.b['incarnation_id'])
        current=Caller('peer',resumed['handle'],resumed['incarnation_id'])
        self.assertEqual(await self.policy(current,'status'),closed)
        for action in ('open','close','status'):
            with self.assertRaises(PeerError):await self.policy(self.cb,action)
        # A fresh human action in the resumed session reopens without forgetting what was closed.
        opened=await self.policy(current,'open')
        self.assertEqual((opened['state'],opened['message_cutoff'],opened['route_cutoff'],opened['written_by_incarnation']),
                         ('OPEN',closed['message_cutoff'],closed['route_cutoff'],resumed['incarnation_id']))
        self.retained(await self.router.send(self.ca,'reply-author','late',in_reply_to=ask['message_id'],expect_reply=True),'exchange-closed')
        self.retained(await self.router.send(controller,'b','phase 1 done',kind='completion',client_request_id='delivery-1'),'completion-phase-closed')
        self.assertEqual((await self.router.send(self.ca,'b','new note'))['state'],'ACCEPTED_QUEUED')
        self.assertEqual(self.delivered(),['question','new note'])

    async def test_repeated_close_never_absorbs_fresh_requests(self):
        def busy(path):raise TimeoutError('lock busy')
        await self.router.send(self.ca,'b','before')
        first=await self.policy(self.cb,'close')
        with patch('nativepeers.routing.async_advisory_lock',busy):
            self.assertEqual((await self.router.send(self.ca,'b','fresh task',expect_reply=True,client_request_id='fresh'))['state'],'ADMISSION_DEFERRED')
        await self.router.send(self.cb,'a','unrelated traffic')
        self.assertEqual(await self.policy(self.cb,'close'),first)
        fresh=await self.router.send(self.ca,'b','fresh task',expect_reply=True,client_request_id='fresh')
        self.assertEqual(fresh['state'],'ACCEPTED_QUEUED')
        working=await self.router.send(self.cb,'reply-author','working',in_reply_to=fresh['message_id'])
        await self.policy(self.cb,'open')
        self.assertEqual((await self.router.send(self.ca,'reply-author','follow-up',in_reply_to=working['message_id']))['state'],'ACCEPTED_QUEUED')
        self.assertEqual(self.delivered(),['before','unrelated traffic','fresh task','working','follow-up'])

    def group(self,name):
        group_id,worker,task,native=name+'-group',name+'-worker',name+'-task',new_id()
        policy={'initial_tasks':[task],'phases':{'1':[task]},'parent_owner':self.b['owner'],'parent_incarnation':self.b['incarnation_id'],
                'completion_mode':'push','workers':[worker],'created_at':time.time(),'wall_time_seconds':60}
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group_id,self.b['handle'],canonical(policy).decode(),'RUNNING','{}'))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group_id,None,'codex',native,'test','gpt-6.1-sol',None,'xhigh',self.temporary.name,None,'RUNNING',1,'{}'))
        record={'summary':'finding','task_id':task,'status':'completed','worker':{'provider':'codex','native_session_id':native,'title':'test'}}
        artifact=Artifacts(self.journal).put(canonical(record),worker,[self.b['handle']],media_type='application/json')
        self.journal.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'COMPLETED',artifact,None,1,'{}'))
        self.journal.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group_id,1,self.b['handle'],'push','UNCLAIMED',None,None,new_id(),1))
        return Group(self.journal,group_id)

    async def test_completion_retention_releases_only_definitely_unsubmitted_reservations(self):
        calls=[]
        class Budget:
            def __init__(budget,path):pass
            def reserve(budget,label,tranche=None):calls.append('reserve');return 'reservation'
            def admit(budget,reservation):calls.append('admit')
            def settle(budget,reservation,observed_turns=None,**_):calls.append(('settle',observed_turns))
            def release_unsubmitted(budget,reservation):calls.append('release');return True
        async def rejected(peer,message):return dict(stage='REJECTED',evidence_kind='fake-provider')
        async def lost(peer,message):raise ConnectionError('lost after admission')
        (self.journal.root/'test-budget.json').write_text(json.dumps({'path':str(self.journal.root/'budget.json')}))
        core=Core(self.journal);outcomes=[]
        for name,adapter in (('accepted',self.router.adapters['fake']),('uncertain',lost),('rejected',rejected),('quiet',self.router.adapters['fake'])):
            if name=='quiet':await self.policy(self.cb,'close')
            core.router.adapters['fake']=adapter;group=self.group(name);del calls[:]
            with patch('nativepeers.core.Core',return_value=core),patch('nativepeers.budget.LiveBudget',Budget):await group.notify(1)
            outcomes.append((self.journal.one('SELECT state FROM completion_routes WHERE group_id=?',(group.id,))['state'],list(calls)))
        # Accepted and uncertain work stays reserved, a genuine refusal keeps its zero-turn settlement,
        # and only definite quiet retention returns the reservation.
        self.assertEqual(outcomes,[('PUSH_ACCEPTED',['reserve','admit']),('NOTIFICATION_UNKNOWN',['reserve','admit']),
                                   ('RETAINED',['reserve','admit',('settle',0)]),('RETAINED',['reserve','admit','release'])])
        self.assertEqual(len(self.admissions),1)

    async def test_quiet_retained_completion_returns_real_budget_reservation(self):
        from nativepeers.budget import LiveBudget
        path=self.journal.root/'budget.json'
        path.write_text(json.dumps({'ceiling':{'active_seconds':None,'submitted_turns':1},'reservations':[],'active_intervals':[],
                                    'outstanding_native_requests':[],'consumed_turns':0,'reserved_turns':0}))
        (self.journal.root/'test-budget.json').write_text(json.dumps({'path':str(path)}))
        core=Core(self.journal);core.router.adapters['fake']=self.router.adapters['fake']
        def route(group):return self.journal.one('SELECT state FROM completion_routes WHERE group_id=?',(group.id,))['state']
        await self.policy(self.cb,'close');quiet=self.group('quiet')
        with patch('nativepeers.core.Core',return_value=core):await quiet.notify(1)
        ledger=json.loads(path.read_text())
        self.assertEqual((route(quiet),self.admissions,ledger['consumed_turns'],ledger['outstanding_native_requests'],[r['state'] for r in ledger['reservations']]),
                         ('RETAINED',[],0,[],['not_admitted']))
        # The cap-1 ledger accepts the next real reservation; that accepted completion stays consumed.
        await self.policy(self.cb,'open');accepted=self.group('accepted')
        with patch('nativepeers.core.Core',return_value=core):await accepted.notify(1)
        ledger=json.loads(path.read_text())
        self.assertEqual((route(accepted),len(self.admissions),ledger['consumed_turns'],[r['state'] for r in ledger['reservations']]),
                         ('PUSH_ACCEPTED',1,1,['not_admitted','admitted']))
        with self.assertRaises(RuntimeError):LiveBudget(path).reserve('over cap',tranche='first_cross_provider_delegations')


if __name__=='__main__':unittest.main()
