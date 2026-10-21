import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from nativepeers.adapters.native_delivery import codex_queue
from nativepeers.errors import PeerError
from nativepeers.models import new_id


class QueueHelperTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,cancel=False):
        spawn=asyncio.create_subprocess_exec;wait=asyncio.wait_for;children=[];ready=asyncio.Event()
        unrelated=await spawn(sys.executable,'-c','import time;time.sleep(30)',stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,start_new_session=True)
        async def harmless_helper(*args,**kwargs):
            child=await spawn(sys.executable,'-c',"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);print('ready',flush=True);time.sleep(30)",
                              stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            children.append(child);await wait(child.stdout.readline(),2);ready.set();return child
        async def fast_wait(awaitable,timeout):return await wait(awaitable,.05 if timeout==25 else timeout)
        peer={'home':str(Path.home()/'.codex'),'peer_key':{'native_session_id':new_id()}}
        try:
            with patch('nativepeers.adapters.native_delivery.asyncio.create_subprocess_exec',side_effect=harmless_helper),patch('nativepeers.adapters.native_delivery.asyncio.wait_for',side_effect=fast_wait),patch('nativepeers.adapters.native_delivery.QUEUE_STOP_GRACE',.05,create=True):
                task=asyncio.create_task(codex_queue(peer,{'fixture':'non-model'}));await ready.wait()
                if cancel:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await wait(task,2)
                else:
                    with self.assertRaises(PeerError) as error:await wait(task,2)
                    self.assertEqual(error.exception.code,'DELIVERY_UNKNOWN');self.assertTrue(error.exception.may_have_received)
                self.assertIsNotNone(children[0].returncode)
                self.assertIsNone(unrelated.returncode)
        finally:
            for child in children:
                if child.returncode is None:child.kill()
                await child.communicate()
            unrelated.terminate();await unrelated.wait()

    async def test_timeout_kills_and_reaps_only_the_owned_stubborn_helper(self):await self.exercise()
    async def test_caller_cancellation_does_not_orphan_the_queue_helper(self):await self.exercise(cancel=True)
