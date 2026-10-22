import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest

from nativepeers.antigravity import catalog


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_reaps_a_native_process_that_ignores_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pid=root/'pid';binary=root/'fake-agy'
            binary.write_text('#!'+sys.executable+'\nimport os,signal\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\nsignal.pause()\n')
            binary.chmod(0o700)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(catalog(binary,timeout=.2,terminate_grace=.05),3)
            self.assertTrue(pid.exists())
            with self.assertRaises(ProcessLookupError):os.kill(int(pid.read_text()),0)
