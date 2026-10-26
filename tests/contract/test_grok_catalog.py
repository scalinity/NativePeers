import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from nativepeers.errors import PeerError
from nativepeers.grok_worker import catalog,parse_catalog


CATALOG='You are logged in with grok.com.\n\nDefault model: grok-4.7\n\nAvailable models:\n  * grok-4.7 (default)\n  - grok-4.7-build-fast\n  - grok-4.6\n  - grok-4.5\n'


class GrokCatalogTests(unittest.IsolatedAsyncioTestCase):
    def test_native_catalog_preserves_exact_names_and_default(self):
        value=parse_catalog(CATALOG)
        self.assertEqual(value['default'],'grok-4.7')
        self.assertEqual(list(value['models']),['grok-4.7','grok-4.7-build-fast','grok-4.6','grok-4.5'])
        for content in ('',CATALOG.replace('Default model: grok-4.7','Default model: missing'),'grok-4.7'):
            with self.assertRaises(PeerError):parse_catalog(content)

    async def test_catalog_launch_pins_executable_home_and_update_disable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();binary=root/'native';link=root/'grok';record=root/'argv.json'
            binary.write_text('#!'+sys.executable+'\nimport os,sys,json,time\nfrom pathlib import Path\n'+
                'Path('+repr(str(record))+').write_text(json.dumps([sys.argv,os.environ.get("GROK_HOME"),os.environ.get("GROK_DISABLE_AUTOUPDATER")]))\n'+
                'print('+repr(CATALOG)+',flush=True)\ntime.sleep(.1)\n');binary.chmod(0o700);link.symlink_to(binary)
            self.assertEqual((await catalog(link,home=root))['default'],'grok-4.7')
            argv,home,updates=json.loads(record.read_text())
            self.assertEqual(argv,[str(binary),'--no-auto-update','models'])
            self.assertEqual(home,str(root));self.assertEqual(updates,'1')

    async def test_native_catalog_failures_remain_distinct(self):
        for text,exit_code,expected in [('Please sign in',1,'AUTH_REQUIRED'),('HTTP 429',1,'SOURCE_RATE_LIMITED'),
                                        ('network unavailable',1,'SOURCE_UNAVAILABLE'),('invalid catalog',0,'CAPABILITY_MISMATCH')]:
            with self.subTest(expected=expected),tempfile.TemporaryDirectory() as directory:
                binary=Path(directory)/'native';binary.write_text('#!'+sys.executable+'\nimport sys,time\nprint('+repr(text)+',flush=True)\ntime.sleep(.1)\nsys.exit('+str(exit_code)+')\n');binary.chmod(0o700)
                with self.assertRaises(PeerError) as caught:await catalog(binary)
                self.assertEqual(caught.exception.code,expected)

    async def test_timeout_reaps_captured_descendant_after_leader_exit(self):
        from nativepeers.process import owned_status
        for pipe in ('stdout','stderr'):
            with self.subTest(pipe=pipe),tempfile.TemporaryDirectory() as directory:
                root=Path(directory).resolve();binary=root/'native';birth=root/'birth.json'
                source='''import json,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,ROOT)
from nativepeers.process import identity
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],stdout=None if PIPE=='stdout' else subprocess.DEVNULL,stderr=None if PIPE=='stderr' else subprocess.DEVNULL,start_new_session=True)
Path(BIRTH).write_text(json.dumps(identity(child.pid).json()))
print(CATALOG,flush=True)
time.sleep(.2)
'''
                binary.write_text('#!'+sys.executable+'\n'+source.replace('ROOT',repr(str(Path(__file__).resolve().parents[2]))).replace('PIPE',repr(pipe)).replace('BIRTH',repr(str(birth))).replace('CATALOG',repr(CATALOG)));binary.chmod(0o700)
                with self.assertRaises(TimeoutError):await asyncio.wait_for(catalog(binary,timeout=.6,terminate_grace=.1),3)
                self.assertEqual(owned_status(json.loads(birth.read_text())),'DEAD')

    async def test_cancellation_still_closes_owned_process(self):
        from nativepeers.process import identity,owned_status
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();binary=root/'native';pid=root/'pid'
            binary.write_text('#!'+sys.executable+'\nimport os,time\nfrom pathlib import Path\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\ntime.sleep(30)\n');binary.chmod(0o700)
            task=asyncio.create_task(catalog(binary,terminate_grace=.1))
            async with asyncio.timeout(2):
                while not pid.exists():await asyncio.sleep(.01)
            owner=identity(int(pid.read_text())).json();task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(owned_status(owner),'DEAD')
