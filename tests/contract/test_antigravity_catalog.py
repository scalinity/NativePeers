import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,Mock,patch

from nativepeers.antigravity import catalog


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_parsing_authentication_and_provider_failure(self):
        for output,code,expected in [('model-a\tModel A\nmodel-b\tModel B\n',0,{'model-a':'Model A','model-b':'Model B'}),
                                     ('Please SIGN IN',1,'AUTH_REQUIRED'),('native failure',2,'CAPABILITY_MISMATCH')]:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                binary=Path(directory)/'fake-agy';binary.write_text('#!'+sys.executable+'\nimport sys\nprint('+repr(output)+')\nsys.exit('+str(code)+')\n');binary.chmod(0o700)
                if code:
                    with self.assertRaises(Exception) as error:await catalog(binary)
                    self.assertEqual(error.exception.code,expected)
                else:self.assertEqual(await catalog(binary),expected)

    async def test_cancellation_is_preserved_after_catalog_cleanup(self):
        process=Mock(returncode=None);process.communicate=AsyncMock(side_effect=[asyncio.CancelledError(),(b'',b'')])
        with patch('nativepeers.antigravity.asyncio.create_subprocess_exec',new=AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.CancelledError):await catalog(Path('fixture'))
        process.terminate.assert_called_once();self.assertEqual(process.communicate.await_count,2)

    async def test_timeout_reaps_a_native_process_that_ignores_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pid=root/'pid';binary=root/'fake-agy'
            binary.write_text('#!'+sys.executable+'\nimport os,signal\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\nsignal.pause()\n')
            binary.chmod(0o700)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(catalog(binary,timeout=.2,terminate_grace=.05),3)
            self.assertTrue(pid.exists())
            with self.assertRaises(ProcessLookupError):os.kill(int(pid.read_text()),0)
