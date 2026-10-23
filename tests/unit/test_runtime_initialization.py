"""Integration initialization must preserve approval acquired before its lock."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from nativepeers.antigravity_install import install
from nativepeers.compatibility import fingerprint,record_verified,update_verified
from nativepeers.files import atomic_json,advisory_lock
from nativepeers.journal import Journal


class RuntimeInitializationTests(unittest.TestCase):
    def test_initial_install_cannot_replace_concurrent_approval(self):
        for integration in ('antigravity','main'):
            with self.subTest(integration=integration),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);home=root/'home';binary=home/'.local/bin/agy';binary.parent.mkdir(parents=True);binary.write_text('old inert runtime')
                newer=root/'new-runtime';newer.write_text('new inert runtime');approved=fingerprint(newer)
                providers={p:dict(executable=str(binary),executable_sha256=fingerprint(binary)['sha256'],version='fixture') for p in ('claude','codex','antigravity')}
                evidence=root/'evidence.json';atomic_json(evidence,{'providers':providers})
                waiting,continue_install=threading.Event(),threading.Event();paused=False
                @contextmanager
                def gated(path,*args,**kwargs):
                    nonlocal paused
                    if path.name=='runtime.lock' and threading.current_thread().name.startswith('installer') and not paused:
                        paused=True;waiting.set()
                        if not continue_install.wait(5):raise TimeoutError('Fixture approval barrier')
                    with advisory_lock(path,*args,**kwargs):yield
                def run():
                    journal=Journal(root/'state')
                    try:
                        if integration=='antigravity':return install(journal,home=home,verified_source=evidence)
                        record_verified(journal,evidence,preserve_existing=True)
                    finally:journal.close()
                with patch('nativepeers.antigravity_install.desired',return_value={}),patch('nativepeers.compatibility.advisory_lock',side_effect=gated),ThreadPoolExecutor(max_workers=1,thread_name_prefix='installer') as executor:
                    pending=executor.submit(run)
                    try:
                        self.assertTrue(waiting.wait(5))
                        journal=Journal(root/'state')
                        try:
                            provider='antigravity' if integration=='antigravity' else 'claude'
                            update_verified(journal,{provider:approved})
                            path=journal.root/'compat/runtime.json';before=json.loads(path.read_text())
                            continue_install.set();result=pending.result(5);after=json.loads(path.read_text())
                            self.assertEqual(after['providers'][provider],approved)
                            self.assertEqual(after.get('retained'),before.get('retained'))
                            if integration=='antigravity':self.assertFalse(result['compatibility_updated']);self.assertEqual(after,before)
                        finally:journal.close()
                    finally:continue_install.set()
