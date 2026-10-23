import asyncio
import json
from pathlib import Path
import os
import tempfile
import unittest

from nativepeers.caller import Caller,bind_caller
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import PeerKey,home_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.routing import Router


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.journal=Journal(Path(self.temporary.name));self.addCleanup(self.journal.close)
        self.registry=Registry(self.journal);self.admissions=[]
        async def deliver(peer,message):
            self.admissions.append((peer['handle'],message));await asyncio.sleep(.001)
            return dict(stage='ACCEPTED_QUEUED',evidence_kind='fake-provider')
        self.router=Router(self.journal,self.registry,{'fake':deliver})
        self.a=self.peer('a','00000000-0000-4000-8000-000000000001',os.getpid())
        self.b=self.peer('b','00000000-0000-4000-8000-000000000002',os.getpid())
        # Different native conversations can share the test's fake owner, but use separate homes.
        self.journal.db.execute("UPDATE peer_instances SET status='live'")
        self.ca=Caller('peer',self.a['handle'],self.a['incarnation_id'])
        self.cb=Caller('peer',self.b['handle'],self.b['incarnation_id'])

    def peer(self,name,native,pid,provider='codex',home='home'):
        return self.registry.register(provider,Path(self.temporary.name)/home,native,name,identity(pid).json(),self.temporary.name,{'kind':'fake'},owner_kind='test')

    async def test_reply_to_reply_and_exchange(self):
        first=await self.router.send(self.ca,'b','first',expect_reply=True)
        second=await self.router.send(self.cb,'ignored-body-route','second',in_reply_to=first['message_id'],expect_reply=True)
        third=await self.router.send(self.ca,'ignored','third',in_reply_to=second['message_id'])
        self.assertEqual(second['target'],self.ca.key);self.assertEqual(third['target'],self.cb.key)
        self.assertEqual(first['exchange_id'],third['exchange_id'])
        with self.assertRaises(PeerError):await self.router.send(self.ca,'b','forged',in_reply_to=first['message_id'])

    async def test_retry_before_alias_resolution(self):
        first=await self.router.send(self.ca,'b','pinned',client_request_id='retry')
        self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(self.b['handle'],))
        again=await self.router.send(self.ca,'b','pinned',client_request_id='retry')
        self.assertEqual(first['message_id'],again['message_id']);self.assertEqual(len(self.admissions),1)
        with self.assertRaises(PeerError) as error:await self.router.send(self.ca,'b','changed',client_request_id='retry')
        self.assertEqual(error.exception.code,'IDEMPOTENCY_CONFLICT')

    async def test_unknown_never_replayed_and_late_evidence(self):
        async def unknown(peer,message):raise ConnectionError('lost after admission')
        self.router.adapters['fake']=unknown
        one=await self.router.send(self.ca,'b','uncertain',client_request_id='unknown')
        self.assertEqual(one['state'],'DELIVERY_UNKNOWN')
        two=await self.router.send(self.ca,'b','uncertain',client_request_id='unknown')
        self.assertEqual(one['attempt'],two['attempt'])
        self.router.record(one['message_id'],one['attempt']['attempt_id'],dict(stage='INGESTED',evidence_kind='matching-native-event'),self.b)
        self.assertEqual(self.router.outcome(one['message_id'])['state'],'INGESTED')

    async def test_late_adapter_errors_preserve_independent_native_receipts(self):
        observer_journal=Journal(self.journal.root);self.addCleanup(observer_journal.close)
        observer=Router(observer_journal,Registry(observer_journal))
        for stage in ('SUBMITTED_UNCONFIRMED','HELD','REJECTED','ACCEPTED_QUEUED','INGESTED',
                      'MODEL_REPORTED_PROCESSED','REPLY_RECORDED','REPLY_SUBMITTED'):
            for failure in (TimeoutError('lost adapter response'),PeerError('TARGET_GONE','late disconnect')):
                with self.subTest(stage=stage,error=type(failure).__name__):
                    started=asyncio.Event();release=asyncio.Event();messages=[]
                    async def pending(peer,message):
                        messages.append(message);started.set();await release.wait();raise failure
                    self.router.adapters['fake']=pending
                    request_id=stage+type(failure).__name__
                    sending=asyncio.create_task(self.router.send(self.ca,'b','confirmed first',client_request_id=request_id))
                    await started.wait()
                    message_id=messages[0]['message_id'];attempt_id=observer.outcome(message_id)['attempt']['attempt_id']
                    observer.record(message_id,attempt_id,dict(stage=stage,evidence_kind='independent-native-event',
                                    external_submission_id='native-submission'),self.b)
                    release.set();result=await sending
                    self.assertEqual(result['state'],stage)
                    self.assertEqual(result['attempt']['external_submission_id'],'native-submission')
                    error=json.loads(result['attempt']['error_json'])
                    self.assertEqual(error['code'],'TARGET_GONE' if isinstance(failure,PeerError) else 'DELIVERY_UNKNOWN')
                    self.assertEqual([row['stage'] for row in result['receipts']],[stage])
                    again=await self.router.send(self.ca,'b','confirmed first',client_request_id=request_id)
                    self.assertEqual(again,result);self.assertEqual(len(messages),1)

    async def test_canceled_adapter_preserves_confirmed_ingestion(self):
        started=asyncio.Event();messages=[]
        async def pending(peer,message):
            messages.append(message);started.set();await asyncio.Event().wait()
        self.router.adapters['fake']=pending
        sending=asyncio.create_task(self.router.send(self.ca,'b','ingested before cancellation',client_request_id='cancel-ingested'))
        await started.wait()
        message_id=messages[0]['message_id'];attempt_id=self.router.outcome(message_id)['attempt']['attempt_id']
        self.router.record(message_id,attempt_id,dict(stage='INGESTED',evidence_kind='owned-native-turn-start'),self.b)
        sending.cancel()
        with self.assertRaises(asyncio.CancelledError):await sending
        result=await self.router.send(self.ca,'b','ingested before cancellation',client_request_id='cancel-ingested')
        self.assertEqual(result['state'],'INGESTED');self.assertEqual(len(messages),1)
        self.assertEqual(json.loads(result['attempt']['error_json'])['message'],'CancelledError')

    async def test_correlated_reply_reduces_only_its_original_even_if_return_delivery_fails(self):
        async def unknown(peer,message):raise ConnectionError('lost response')
        self.router.adapters['fake']=unknown
        first=await self.router.send(self.ca,'b','original',client_request_id='reply-original')
        other=await self.router.send(self.ca,'b','separate',client_request_id='reply-other')
        reply=await self.router.send(self.cb,'ignored','answer',in_reply_to=first['message_id'],client_request_id='correlated')
        self.assertEqual(reply['state'],'DELIVERY_UNKNOWN')
        self.assertEqual(self.router.outcome(first['message_id'])['state'],'REPLY_RECORDED')
        self.assertEqual(self.router.outcome(other['message_id'])['state'],'DELIVERY_UNKNOWN')
        again=await self.router.send(self.cb,'ignored','answer',in_reply_to=first['message_id'],client_request_id='correlated')
        self.assertEqual(reply,again)
        self.router.record(first['message_id'],first['attempt']['attempt_id'],
            dict(stage='REPLY_SUBMITTED',evidence_kind='submitted-reply'),self.b)
        await self.router.send(self.cb,'ignored','another answer',in_reply_to=first['message_id'],client_request_id='another-correlated')
        self.assertEqual(self.router.outcome(first['message_id'])['state'],'REPLY_SUBMITTED')

    async def test_reply_during_pending_admission_survives_late_transport_failure(self):
        started=asyncio.Event();release=asyncio.Event();messages=[]
        async def pending(peer,message):
            messages.append(message)
            if message['in_reply_to']:
                return dict(stage='ACCEPTED_QUEUED',evidence_kind='fake-reply-queue')
            started.set();await release.wait();raise TimeoutError('late response loss')
        self.router.adapters['fake']=pending
        sending=asyncio.create_task(self.router.send(self.ca,'b','pending',client_request_id='pending-reply'))
        await started.wait();message_id=messages[0]['message_id']
        self.assertEqual(self.router.outcome(message_id)['state'],'SUBMITTING')
        try:
            await self.router.send(self.cb,'ignored','answer',in_reply_to=message_id,client_request_id='reply-before-response')
        finally:release.set()
        result=await sending
        self.assertEqual(result['state'],'REPLY_RECORDED')
        self.assertEqual((await self.router.send(self.ca,'b','pending',client_request_id='pending-reply'))['state'],'REPLY_RECORDED')
        self.assertEqual(len(messages),2)

    async def test_same_request_concurrency(self):
        rows=await asyncio.gather(*[self.router.send(self.ca,'b','same',client_request_id='concurrent') for _ in range(8)])
        self.assertEqual(len({r['message_id'] for r in rows}),1);self.assertEqual(len(self.admissions),1)

    async def test_lock_timeout_retries_exact_intent_once(self):
        from unittest.mock import patch
        from nativepeers.files import advisory_lock, async_advisory_lock, sha256
        lock = self.journal.root/'locks'/(sha256(self.b['handle'])+'.lock')
        with advisory_lock(lock), patch('nativepeers.routing.async_advisory_lock', side_effect=lambda path: async_advisory_lock(path, .03)):
            deferred = await self.router.send(self.ca, 'b', 'contended', client_request_id='lock-retry')
        self.assertEqual(deferred['state'], 'ADMISSION_DEFERRED')
        self.assertFalse(json.loads(deferred['attempt']['error_json'])['may_have_received'])
        self.assertEqual(self.admissions, [])
        # Mutable aliases are not consulted when retrying the committed intent.
        with patch.object(self.registry, 'resolve', side_effect=AssertionError('alias re-resolved')):
            outcomes = await asyncio.gather(*[self.router.send(self.ca, 'b', 'contended', client_request_id='lock-retry') for _ in range(8)])
        self.assertEqual({row['message_id'] for row in outcomes}, {deferred['message_id']})
        self.assertEqual(len(self.admissions), 1)
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM messages')['n'], 1)
        self.assertEqual(self.journal.one('SELECT COUNT(*) AS n FROM attempts')['n'], 1)

    async def test_unsubmitted_retry_revalidates_original_incarnation_and_expiry(self):
        from unittest.mock import patch
        from nativepeers.files import advisory_lock, async_advisory_lock, sha256
        for stale in ('incarnation', 'expiry'):
            with self.subTest(stale=stale):
                lock = self.journal.root/'locks'/(sha256(self.b['handle'])+'.lock')
                with advisory_lock(lock), patch('nativepeers.routing.async_advisory_lock', side_effect=lambda path: async_advisory_lock(path, .03)):
                    first = await self.router.send(self.ca, 'b', stale, client_request_id=stale)
                if stale == 'incarnation':
                    self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?", (self.b['handle'],))
                    self.peer('b', self.b['peer_key']['native_session_id'], os.getpid())
                else:
                    row=self.journal.one('SELECT payload_json FROM messages WHERE message_id=?',(first['message_id'],))
                    payload=json.loads(row['payload_json']);payload['expires_at']=0
                    self.journal.db.execute('UPDATE messages SET payload_json=? WHERE message_id=?',(json.dumps(payload),first['message_id']))
                result=await self.router.send(self.ca,'b',stale,client_request_id=stale)
                self.assertNotIn(result['state'], ('ACCEPTED_QUEUED','SUBMITTING','DELIVERY_UNKNOWN'))
                self.assertEqual(len(self.admissions),0)

    async def test_parallel_recipient_admission(self):
        await asyncio.gather(*[self.router.send(self.ca,'b',str(i)) for i in range(3)])
        self.assertEqual(len(self.admissions),3)

    async def test_unbound_and_controller_limits(self):
        with self.assertRaises(PeerError):await self.router.send(Caller('external-local','external'),'b','no authority')
        control=Caller('controller','controller:g:1',grants={'group_id':'g','generation':1,'parent_key':self.a['handle']})
        result=await self.router.send(control,self.a['handle'],'complete',kind='completion')
        envelope=self.admissions[-1][1]
        self.assertEqual(envelope['from']['kind'],'controller');self.assertIsNone(envelope['reply_to'])
        with self.assertRaises(PeerError):await self.router.send(control,'b','wrong parent',kind='completion')

    def test_duplicate_unicode_and_home(self):
        c=self.peer('b','00000000-0000-4000-8000-000000000003',os.getpid(),provider='claude',home='other')
        self.journal.db.execute("UPDATE peer_instances SET status='live'")
        with self.assertRaises(PeerError) as error:self.registry.resolve('b')
        self.assertEqual(error.exception.code,'AMBIGUOUS_TARGET')
        self.assertEqual(self.registry.resolve('claude:b')['handle'],c['handle'])
        self.assertNotEqual(PeerKey(self.journal.host_id,'codex',home_id(Path('/a')),self.a['peer_key']['native_session_id']).handle,
                            PeerKey(self.journal.host_id,'codex',home_id(Path('/b')),self.a['peer_key']['native_session_id']).handle)

    async def test_artifact_grants_are_checked_before_send(self):
        from nativepeers.artifacts import Artifacts
        artifacts=Artifacts(self.journal)
        handle=artifacts.put(b'approved',self.ca.key)
        await self.router.send(self.ca,'b','context',artifact_refs=[handle])
        self.assertEqual(artifacts.read(handle,self.cb.key)['byte_size'],8)
        outsider=artifacts.put(b'private','other')
        with self.assertRaises(PeerError):await self.router.send(self.ca,'b','no grant',artifact_refs=[outsider])
        self.assertEqual(len(self.admissions),1)

    async def test_reversed_crossed_replies_and_stale_incarnation(self):
        first=await self.router.send(self.ca,'b','one',expect_reply=True)
        second=await self.router.send(self.ca,'b','two',expect_reply=True)
        r2=await self.router.send(self.cb,'unused','two reply',in_reply_to=second['message_id'])
        r1=await self.router.send(self.cb,'unused','one reply',in_reply_to=first['message_id'])
        self.assertEqual(r2['exchange_id'],second['exchange_id']);self.assertEqual(r1['exchange_id'],first['exchange_id'])
        self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(self.b['handle'],))
        rebound=self.peer('b',self.b['peer_key']['native_session_id'],os.getpid())
        self.assertNotEqual(rebound['incarnation_id'],self.b['incarnation_id'])
        with self.assertRaises(PeerError):self.registry.verify(self.b['handle'],self.b['incarnation_id'])

    def test_native_owner_binding_survives_parent_reparenting(self):
        from dataclasses import replace
        from unittest.mock import patch
        ancestor=replace(identity(os.getpid()),ppid=99999999)
        with patch.dict(os.environ,{'CODEX_THREAD_ID':self.a['peer_key']['native_session_id'],'NPEER_BINDING':''}),patch('nativepeers.caller.ancestry',return_value=[ancestor]):
            self.assertEqual(bind_caller(self.journal,self.registry).key,self.a['handle'])

    async def test_native_titles_with_colons_and_configured_expiry(self):
        from nativepeers.files import atomic_json
        self.journal.db.execute("UPDATE peer_instances SET title=?,descriptor_json=json_set(descriptor_json,'$.title',?) WHERE peer_key=?",('Review: Fixture','Review: Fixture',self.b['handle']))
        self.assertEqual(self.registry.resolve('Review: Fixture')['handle'],self.b['handle'])
        self.assertEqual(self.registry.resolve('codex:Review: Fixture')['handle'],self.b['handle'])
        atomic_json(self.journal.root/'policy.json',{'request_expiry_seconds':2})
        outcome=await self.router.send(self.ca,'Review: Fixture','expires')
        message=self.journal.one('SELECT * FROM messages WHERE message_id=?',(outcome['message_id'],))
        self.assertEqual(message['expires_at']-message['created_at'],2)

    async def test_old_unread_survives_acknowledged_rows_before_limit(self):
        old=await self.router.send(self.ca,'b','old unread')
        for count in range(110):
            row=await self.router.send(self.ca,'b',str(count))
            self.router.ack(self.cb,row['message_id'])
            if count in (20,109):
                inbox=self.router.inbox(self.cb,limit=20,unread=True)
                self.assertEqual([m['message_id'] for m in inbox],[old['message_id']])
        self.assertEqual(len(self.router.inbox(self.cb,limit=20)),20)

    async def test_frame_limits_and_ack(self):
        with self.assertRaises(PeerError):await self.router.send(self.ca,'b','x'*12289)
        result=await self.router.send(self.ca,'b','ok')
        with self.assertRaises(PeerError):self.router.ack(self.ca,result['message_id'])
        self.router.ack(self.cb,result['message_id']);self.router.ack(self.cb,result['message_id'])
        self.assertEqual(len([r for r in self.router.outcome(result['message_id'])['receipts'] if r['stage']=='MODEL_REPORTED_PROCESSED']),1)


if __name__=='__main__':unittest.main()
