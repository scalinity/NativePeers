import asyncio
import io
import os
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from nativepeers.worker_process import run


class WorkerLeaseCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_loss_during_startup_with_full_queue_has_one_uncancelled_cleanup(self):
        counts={'close_started':0,'close_finished':0}
        class FakeWorker:
            def __init__(self,*args):
                self.queue=asyncio.Queue(maxsize=1);self.queue.put_nowait('buffered task')
                self.stopping=False;self.shutdown_reason=None
            async def prepare(self):await asyncio.sleep(10)
            async def interrupt(self):pass
            async def close(self):
                counts['close_started']+=1
                await asyncio.sleep(.01)
                counts['close_finished']+=1
            def emit(self,*args,**kwargs):pass
        read_fd,write_fd=os.pipe();os.close(write_fd)
        journal=Mock()
        try:
            with patch('nativepeers.worker_process.Worker',FakeWorker), \
                 patch('nativepeers.worker_process.Journal',return_value=journal), \
                 patch('nativepeers.worker_process.sys.stdin',SimpleNamespace(buffer=io.BytesIO())), \
                 patch('nativepeers.worker_process.os._exit'):
                with self.assertRaises(asyncio.CancelledError):await run('owned-fixture',read_fd)
            self.assertEqual(counts,{'close_started':1,'close_finished':1})
            journal.close.assert_called_once()
        finally:
            try:os.close(read_fd)
            except OSError:pass
