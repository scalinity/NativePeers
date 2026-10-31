"""Activation-window contracts; scripts are queue helpers, never model fixtures."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.caller import Caller
from nativepeers.compatibility import codex_inbox_runtime, fingerprint, update_verified, verify, revoke
from nativepeers.core import Core
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, advisory_lock
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.notification_policy import apply
from nativepeers.process import identity
from nativepeers.registry import Registry


class ManagedActivationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name).resolve();self.j=Journal(self.root/'state');self.addCleanup(self.j.close)
        self.enterContext(patch('pathlib.Path.home',return_value=self.root))
        self.enterContext(patch('nativepeers.registry.matches',return_value=True))
        self.old=self.binary('old');self.new=self.binary('new')
        self.current=self.root/'.local/share/codex-mcp-manager/current';self.current.parent.mkdir(parents=True)
        self.select(self.old)
        self.prior=fingerprint(self.old)
        update_verified(self.j,{'codex':self.prior,'claude':{'sha256':'unrelated','path':'keep'}})
        self.peer=self.register(self.old)
        self.caller=Caller('peer',self.peer['handle'],self.peer['incarnation_id'])
        self.core=Core(self.j)

    def binary(self,name):
        path=self.root/name/'bin/codex';path.parent.mkdir(parents=True)
        # Record real helper invocations and respond using the requested native UUID.
        path.write_text('#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n'
                        f'with Path({str(self.root/"launches")!r}).open("a") as f:f.write({name!r}+"\\n")\n'
                        'print("Queued message 11111111-1111-1111-1111-111111111111 for thread "+sys.argv[3]+".")\n')
        path.chmod(0o755);return path

    def select(self,path):
        self.current.unlink(missing_ok=True);self.current.symlink_to(path.parent.parent)

    def register(self,binary,incarnation=None):
        owner=dict(identity(os.getpid()).json(),executable=str(binary))
        return Registry(self.j).register('codex',self.root/'.codex',getattr(self,'native',None) or self.native_id(),
                                        'recipient',owner,self.root,{'kind':'codex-native-queue'},incarnation=incarnation)

    def native_id(self):self.native=new_id();return self.native

    async def send(self,request='one',**kwargs):
        return await self.core.router.send(self.caller,self.peer['handle'],'activation control',client_request_id=request,**kwargs)

    def approve(self):
        update_verified(self.j,{'codex':fingerprint(self.new)},expected={'codex':self.prior},selected={'codex':self.current/'bin/codex'})

    async def test_selected_unqualified_window_uses_old_helper_and_preserves_exact_live_grant(self):
        self.select(self.new)
        with self.assertRaises(PeerError) as error:verify(self.j,'codex',self.current/'bin/codex',renew=False)
        self.assertNotIn('--apply',error.exception.details['next_action'])
        first=await self.send();self.assertEqual(first['state'],'ACCEPTED_QUEUED')
        self.assertEqual(json.loads(first['receipts'][0]['details_json'])['runtime'],self.prior)
        self.approve()
        self.assertEqual(codex_inbox_runtime(self.j,self.peer),self.prior)
        repeated=await self.send();self.assertEqual(repeated['attempt'],first['attempt'])
        self.assertEqual((self.root/'launches').read_text(),'old\n')
        doc=json.loads((self.j.root/'compat/runtime.json').read_text())
        self.assertEqual(doc['providers']['claude'],{'sha256':'unrelated','path':'keep'})
        # A later incarnation cannot borrow the old incarnation's retained permission.
        self.register(self.old)
        with self.assertRaises(PeerError):codex_inbox_runtime(self.j,self.peer)
        with self.assertRaises(PeerError):codex_inbox_runtime(self.j,Registry(self.j).get(self.peer['handle']))

    async def test_queue_never_reresolves_current_between_verification_and_execution(self):
        spawn=asyncio.create_subprocess_exec
        async def changed(*args,**kwargs):
            self.select(self.new)
            return await spawn(*args,**kwargs)
        with patch('nativepeers.adapters.native_delivery.asyncio.create_subprocess_exec',side_effect=changed):
            result=await self.send()
        self.assertEqual(result['state'],'ACCEPTED_QUEUED')
        self.assertEqual((self.root/'launches').read_text(),'old\n')

    async def test_definite_refusal_retries_original_identity_once_after_approval(self):
        # This live recipient itself runs the candidate, which is not approved yet.
        self.peer=self.register(self.new)
        first=await self.send()
        self.assertEqual(first['state'],'ADMISSION_DEFERRED')
        self.assertFalse((self.root/'launches').exists())
        self.assertEqual(json.loads(first['attempt']['error_json'])['details']['cause']['code'],'CAPABILITY_MISMATCH')
        self.select(self.new);self.approve()
        after=await self.send()
        self.assertEqual(after['message_id'],first['message_id'])
        self.assertEqual(after['attempt']['attempt_id'],first['attempt']['attempt_id'])
        self.assertEqual(after['state'],'ACCEPTED_QUEUED')
        await self.send();self.assertEqual((self.root/'launches').read_text(),'new\n')

    async def test_deferred_expiry_and_replacement_never_submit(self):
        self.peer=self.register(self.new)
        first=await self.send(expires_seconds=.01)
        await asyncio.sleep(.02)
        self.select(self.new);self.approve()
        expired=await self.send(expires_seconds=.01)
        self.assertEqual(expired['state'],'EXPIRED_BEFORE_SUBMISSION')
        self.assertEqual(expired['message_id'],first['message_id'])
        self.old.write_text('unapproved replacement')
        self.peer=self.register(self.old)
        deferred=await self.send('replaced')
        self.register(self.new)
        rebound=await self.send('replaced')
        self.assertEqual(deferred['state'],'ADMISSION_DEFERRED');self.assertEqual(rebound['state'],'TARGET_REBOUND')
        self.assertFalse((self.root/'launches').exists())

    async def test_quiet_retention_is_permanent_and_uncertain_is_inert(self):
        await apply(self.j,Registry(self.j),self.caller,'close')
        retained=await self.send()
        self.assertEqual(retained['state'],'RETAINED')
        await apply(self.j,Registry(self.j),self.caller,'open')
        self.select(self.new);self.approve()
        self.assertEqual((await self.send())['attempt'],retained['attempt'])
        async def uncertain(peer,message):raise PeerError('DELIVERY_UNKNOWN','fixture uncertain',may_have_received=True)
        self.core.router.adapters['codex-native-queue']=uncertain
        first=await self.send('uncertain')
        self.assertEqual(first['state'],'DELIVERY_UNKNOWN')
        self.assertEqual((await self.send('uncertain'))['attempt'],first['attempt'])
        self.assertFalse((self.root/'launches').exists())

    async def test_revoked_modified_missing_runtime_never_falls_back(self):
        self.select(self.new);self.approve()
        revoke(self.j,'codex',self.prior['sha256'])
        self.assertEqual((await self.send('revoked'))['state'],'ADMISSION_DEFERRED')
        self.old.write_text('modified')
        self.assertEqual((await self.send('modified'))['state'],'ADMISSION_DEFERRED')
        self.old.unlink()
        self.assertEqual((await self.send('missing'))['state'],'ADMISSION_DEFERRED')
        self.assertFalse((self.root/'launches').exists())

    async def test_runtime_lock_contention_revalidates_before_launch(self):
        lock=advisory_lock(self.j.root/'locks/runtime.lock');lock.__enter__()
        task=asyncio.create_task(self.send())
        try:
            await asyncio.sleep(.04);self.assertFalse(task.done())
            self.old.write_text('changed while waiting')
        finally:lock.__exit__(None,None,None)
        self.assertEqual((await task)['state'],'ADMISSION_DEFERRED')
        self.assertFalse((self.root/'launches').exists())

    def test_failed_or_interrupted_approval_preserves_whole_receipt_and_selection(self):
        self.select(self.new)
        receipt=self.j.root/'compat/runtime.json';before=receipt.read_bytes()
        for mode in ('selection','expected','interrupt'):
            with self.subTest(mode=mode):
                kwargs={'expected':{'codex':self.prior},'selected':{'codex':self.current/'bin/codex'}}
                if mode=='selection':kwargs['selected']['codex']=self.old
                if mode=='expected':kwargs['expected']['codex']={'sha256':'stale'}
                if mode=='interrupt':
                    with patch('nativepeers.compatibility.atomic_json',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
                        update_verified(self.j,{'codex':fingerprint(self.new)},**kwargs)
                else:
                    with self.assertRaises(PeerError):update_verified(self.j,{'codex':fingerprint(self.new)},**kwargs)
                self.assertEqual(receipt.read_bytes(),before)
                self.assertEqual((self.current/'bin/codex').resolve(),self.new)

    def test_identical_bytes_at_a_new_package_path_preserve_exact_recipient(self):
        self.new.write_bytes(self.old.read_bytes())
        self.select(self.new);self.approve()
        self.assertEqual(codex_inbox_runtime(self.j,self.peer),self.prior)
        # Same digest at an unrelated path does not create a recipient grant.
        other=self.binary('other');other.write_bytes(self.old.read_bytes())
        later=self.register(other)
        with self.assertRaises(PeerError):codex_inbox_runtime(self.j,later)

    async def test_runtime_lock_deadline_is_definite_and_same_id_retry_succeeds(self):
        with advisory_lock(self.j.root/'locks/runtime.lock'),patch('nativepeers.adapters.native_delivery.QUEUE_LOCK_TIMEOUT',.03):
            deferred=await self.send('lock-timeout')
        self.assertEqual(deferred['state'],'ADMISSION_DEFERRED')
        self.assertFalse(json.loads(deferred['attempt']['error_json'])['may_have_received'])
        self.assertFalse((self.root/'launches').exists())
        admitted=await self.send('lock-timeout')
        self.assertEqual(admitted['message_id'],deferred['message_id'])
        self.assertEqual(admitted['attempt']['attempt_id'],deferred['attempt']['attempt_id'])
        self.assertEqual((self.root/'launches').read_text(),'old\n')

    async def test_cancel_during_lock_wait_closes_fd_and_preserves_retry(self):
        with advisory_lock(self.j.root/'locks/runtime.lock'):
            task=asyncio.create_task(self.send('cancel-lock'))
            await asyncio.sleep(.03);task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
        row=self.j.one("SELECT message_id FROM send_requests WHERE client_request_id='cancel-lock'")
        self.assertEqual(self.core.router.outcome(row['message_id'])['state'],'ADMISSION_DEFERRED')
        admitted=await asyncio.wait_for(self.send('cancel-lock'),1)
        self.assertEqual(admitted['state'],'ACCEPTED_QUEUED')
        self.assertEqual((self.root/'launches').read_text(),'old\n')

    async def test_single_fresh_hash_and_budget_after_verification(self):
        from nativepeers.compatibility import fingerprint as real_fingerprint
        with patch('nativepeers.compatibility.fingerprint',wraps=real_fingerprint) as hashed:
            await self.send('one-hash')
        self.assertEqual(sum(c.kwargs.get('fresh',False) for c in hashed.call_args_list),1)
