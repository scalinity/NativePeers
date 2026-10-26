import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock,Mock,patch

from nativepeers.antigravity import catalog
from nativepeers.launch import launch_environment


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_parsing_authentication_and_provider_failure(self):
        for output,code,expected in [('model-a\tModel A\nmodel-b\tModel B\n',0,{'model-a':'Model A','model-b':'Model B'}),
                                     ('Please SIGN IN',1,'AUTH_REQUIRED'),('native failure',2,'SOURCE_UNAVAILABLE'),('HTTP 429',1,'SOURCE_RATE_LIMITED'),('network unavailable',1,'SOURCE_UNAVAILABLE'),('invalid catalog',0,'CAPABILITY_MISMATCH')]:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                binary=Path(directory)/'fake-agy';binary.write_text('#!'+sys.executable+'\nimport sys\nprint('+repr(output)+')\nsys.exit('+str(code)+')\n');binary.chmod(0o700)
                if isinstance(expected,str):
                    with self.assertRaises(Exception) as error:await catalog(binary)
                    self.assertEqual(error.exception.code,expected)
                    if expected!='AUTH_REQUIRED':self.assertNotIn('sign in',str(error.exception).lower())
                else:self.assertEqual(await catalog(binary),expected)

    async def test_cancellation_is_preserved_after_catalog_cleanup(self):
        from nativepeers.process import identity,owned_status
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pid=root/'pid';binary=root/'fake-agy'
            binary.write_text('#!'+sys.executable+'\nimport os,time\nfrom pathlib import Path\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\ntime.sleep(30)\n');binary.chmod(0o700)
            task=asyncio.create_task(catalog(binary,terminate_grace=.1))
            async with asyncio.timeout(2):
                while not pid.exists():await asyncio.sleep(.01)
            record=identity(int(pid.read_text())).json();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(owned_status(record),'DEAD')

    async def test_exited_leader_cannot_abandon_captured_pipe_descendant(self):
        from nativepeers.process import owned_status
        for pipe in ('stdout','stderr'):
            with self.subTest(pipe=pipe),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);birth=root/'birth.json';binary=root/'fake-agy'
                binary.write_text('#!'+sys.executable+'\n'+"""
import os,time,json,subprocess,sys
from pathlib import Path
from nativepeers.process import identity
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],
    stdout=None if PIPE=='stdout' else subprocess.DEVNULL,stderr=None if PIPE=='stderr' else subprocess.DEVNULL,start_new_session=True)
Path(BIRTH).write_text(json.dumps(identity(child.pid).json()))
print('model-a\tModel A',flush=True)
time.sleep(.2)
""".replace('PIPE',repr(pipe)).replace('BIRTH',repr(str(birth))))
                binary.chmod(0o700)
                # The fixture imports this checkout without widening the native environment.
                binary.write_text(binary.read_text().replace('from nativepeers.process import identity','sys.path.insert(0,'+repr(str(Path(__file__).resolve().parents[2]))+'); from nativepeers.process import identity'))
                with self.assertRaises(TimeoutError):await asyncio.wait_for(catalog(binary,timeout=.5,terminate_grace=.1),3)
                import json
                self.assertEqual(owned_status(json.loads(birth.read_text())),'DEAD')

    async def test_timeout_reaps_a_native_process_that_ignores_termination(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);pid=root/'pid';binary=root/'fake-agy'
            binary.write_text('#!'+sys.executable+'\nimport os,signal\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\nsignal.pause()\n')
            binary.chmod(0o700)
            create=subprocess.Popen
            process=create([str(binary),'models'],env=launch_environment('antigravity'),stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
            def launch(argv,*args,**kwargs):
                if argv==[str(binary),'models']:return process
                return create(argv,*args,**kwargs)
            try:
                # Establish SIGTERM ignore before the catalog's response deadline.
                async with asyncio.timeout(3):
                    while not pid.exists() or pid.read_text()!=str(process.pid):
                        self.assertIsNone(process.poll(),'Fixture exited before readiness')
                        await asyncio.sleep(.01)
                self.assertEqual(int(pid.read_text()),process.pid)
                with patch('subprocess.Popen',side_effect=launch),self.assertRaises(TimeoutError):
                    await asyncio.wait_for(catalog(binary,timeout=.2,terminate_grace=.05),3)
                self.assertEqual(process.returncode,-signal.SIGKILL)
                with self.assertRaises(ProcessLookupError):os.kill(process.pid,0)
            finally:
                if process.poll() is None:process.kill()
                process.wait(timeout=3)
                process.stdout.close();process.stderr.close()
