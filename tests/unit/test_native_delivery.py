import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from nativepeers.adapters.native_delivery import codex_queue
from nativepeers.errors import PeerError
from nativepeers.models import new_id


class QueueHelperTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,cancel=False):
        spawn=asyncio.create_subprocess_exec;wait=asyncio.wait_for;children=[];ready=asyncio.Event()
        task=readiness=None
        unrelated=await spawn(sys.executable,'-c','import time;time.sleep(30)',stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
        async def harmless_helper(*args,**kwargs):
            child=await spawn(sys.executable,'-c',"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(30)",
                              stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            children.append(child)
            self.assertEqual(await wait(child.stdout.readline(),2),b'ready\n')
            ready.set();return child
        async def fast_wait(awaitable,timeout):return await wait(awaitable,.05 if timeout==25 else timeout)
        try:
            with tempfile.TemporaryDirectory() as directory:
                home=Path(directory)
                executable=home/'.local/share/codex-mcp-manager/current/bin/codex'
                executable.parent.mkdir(parents=True)
                executable.write_text('#!/bin/sh\nexit 1\n')
                executable.chmod(0o755)
                peer={'home':str(home/'.codex'),'peer_key':{'native_session_id':new_id()}}
                with patch('nativepeers.compatibility.codex_inbox_runtime',return_value={'path':str(executable)}),patch('nativepeers.adapters.native_delivery.Path.home',return_value=home),patch('nativepeers.adapters.native_delivery.asyncio.create_subprocess_exec',side_effect=harmless_helper),patch('nativepeers.adapters.native_delivery.asyncio.wait_for',side_effect=fast_wait),patch('nativepeers.adapters.native_delivery.QUEUE_STOP_GRACE',.05,create=True):
                    message={'from':{'kind':'peer','peer_key':'fixture'},'body':{'text':'non-model'},'message_id':new_id(),'exchange_id':new_id()}
                    task=asyncio.create_task(codex_queue(peer,message,journal=SimpleNamespace(root=home)))
                    readiness=asyncio.create_task(ready.wait())
                    done,_=await asyncio.wait({task,readiness},timeout=3,return_when=asyncio.FIRST_COMPLETED)
                    if task in done:
                        await task
                        self.fail('Queue helper completed before readiness')
                    self.assertIn(readiness,done,'Queue helper did not become ready within 3 seconds')
                    if cancel:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError):await wait(task,2)
                    else:
                        with self.assertRaises(PeerError) as error:await wait(task,2)
                        self.assertEqual(error.exception.code,'DELIVERY_UNKNOWN');self.assertTrue(error.exception.may_have_received)
                    self.assertIsNotNone(children[0].returncode)
                    self.assertIsNone(unrelated.returncode)
        finally:
            pending=[item for item in (task,readiness) if item is not None]
            for item in pending:
                if not item.done():item.cancel()
            try:
                await wait(asyncio.gather(*pending,return_exceptions=True),5)
            finally:
                try:
                    for child in children:
                        if child.returncode is None:child.kill()
                        await wait(child.communicate(),2)
                finally:
                    unrelated.terminate();await wait(unrelated.wait(),2)

    async def test_timeout_kills_and_reaps_only_the_owned_stubborn_helper(self):await self.exercise()
    async def test_caller_cancellation_does_not_orphan_the_queue_helper(self):await self.exercise(cancel=True)

    async def test_startup_failure_propagates_before_helper_readiness(self):
        with patch('nativepeers.adapters.native_delivery.launch_environment',side_effect=FileNotFoundError('fixture launch failed')):
            with self.assertRaisesRegex(FileNotFoundError,'fixture launch failed'):
                await asyncio.wait_for(self.exercise(),5)
