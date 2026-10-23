import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock

from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity, matches, signal_owned
from nativepeers.worker_process import Worker


class WorkerDescendants(unittest.IsolatedAsyncioTestCase):
    async def test_dead_leader_cleanup_tracks_escaped_child_or_retains_uncertainty(self):
        for captured, mismatch in [(True,False),(False,False),(True,True)]:
            with self.subTest(captured=captured,mismatch=mismatch),tempfile.TemporaryDirectory() as d:
                root=Path(d).resolve();j=Journal(root/'state');group,worker,native=[new_id() for _ in range(3)]
                lease={'spec':{'model':'sonnet'},'provider_home':str(root),'binding':str(root/'binding')}
                j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RUNNING','{}'))
                j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (worker,group,None,'claude',native,'fixture','sonnet',None,None,str(root),None,'RUNNING',1,canonical(lease).decode()))
                w=Worker(j,worker);w.emit=Mock()
                script='import subprocess,sys,time,pathlib; p=subprocess.Popen([sys.executable,\'-c\',"import time,pathlib;pathlib.Path(\'ready\').touch();time.sleep(30)"],start_new_session=True); pathlib.Path(\'pid\').write_text(str(p.pid));\nwhile not pathlib.Path(\'release\').exists():time.sleep(.01)'
                process=await asyncio.create_subprocess_exec(sys.executable,'-c',script,cwd=root,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
                w.provider=process;w.provider_identity=identity(process.pid).json()
                child=None
                try:
                    async with asyncio.timeout(3):
                        while not ((root/'pid').exists() and (root/'ready').exists()):await asyncio.sleep(.01)
                    child=identity(int((root/'pid').read_text())).json()
                    if captured:
                        w.track_provider()
                        async with asyncio.timeout(3):
                            while w.provider_descendants.get(child['pid'])!=child:await asyncio.sleep(.01)
                        if mismatch:
                            w.provider_monitor.cancel();await asyncio.gather(w.provider_monitor,return_exceptions=True);w.provider_monitor=None
                            w.provider_descendants[child['pid']]=dict(child,start='wrong')
                    (root/'release').touch()
                    async with asyncio.timeout(3):
                        while process.returncode is None:await asyncio.sleep(.01)
                    if captured and not mismatch:
                        await asyncio.wait_for(w.close(),4)
                        self.assertFalse(matches(child))
                        self.assertEqual(j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
                    else:
                        with self.assertRaises(PeerError) as error:await asyncio.wait_for(w.close(),4)
                        self.assertEqual(error.exception.code,'CLEANUP_UNCERTAIN');self.assertTrue(matches(child))
                        self.assertNotEqual(j.one('SELECT state FROM workers')['state'],'RELEASED_RESUMABLE')
                    manifest=json.loads((j.root/'workers'/(worker+'.json')).read_text())
                    self.assertEqual(manifest['cleanup']['state'],'CLOSED' if captured and not mismatch else 'UNCERTAIN')
                    if mismatch:self.assertEqual(manifest['native_descendants'][0]['start'],'wrong')
                finally:
                    if child and matches(child):signal_owned(child,signal.SIGKILL)
                    if process.returncode is None:process.kill()
                    await asyncio.wait_for(process.wait(),3)
                    if process._transport:process._transport.close()
                    j.close()
