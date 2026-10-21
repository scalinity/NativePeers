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

    async def test_same_request_concurrency(self):
        rows=await asyncio.gather(*[self.router.send(self.ca,'b','same',client_request_id='concurrent') for _ in range(8)])
        self.assertEqual(len({r['message_id'] for r in rows}),1);self.assertEqual(len(self.admissions),1)

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

    async def test_frame_limits_and_ack(self):
        with self.assertRaises(PeerError):await self.router.send(self.ca,'b','x'*12289)
        result=await self.router.send(self.ca,'b','ok')
        with self.assertRaises(PeerError):self.router.ack(self.ca,result['message_id'])
        self.router.ack(self.cb,result['message_id']);self.router.ack(self.cb,result['message_id'])
        self.assertEqual(len([r for r in self.router.outcome(result['message_id'])['receipts'] if r['stage']=='MODEL_REPORTED_PROCESSED']),1)


if __name__=='__main__':unittest.main()
