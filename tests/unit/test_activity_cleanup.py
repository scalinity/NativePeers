import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from nativepeers.activity import Recorder, listing, store
from nativepeers.caller import Caller
from nativepeers.cleanup import plan
from nativepeers.files import canonical
from nativepeers.history_cleanup import preview
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


class ActivityCleanupTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.j = Journal(self.root / 'state')
        self.addCleanup(self.j.close)
        peer = Registry(self.j).register('codex', self.root / 'home', new_id(), 'fixture parent', identity(os.getpid()).json(), str(self.root), {}, owner_kind='test')
        self.caller = Caller('peer', peer['handle'], peer['incarnation_id'])

    def worker(self, state='RELEASED_RESUMABLE', native=True, log=True):
        group, worker = new_id(), new_id()
        lease = {'parent_key': self.caller.key, 'spec': {'cwd': str(self.root)}}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, self.caller.key, '{}', 'COMPLETED', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (worker, group, None, 'codex', new_id() if native else None, 'fixture', 'fixture', 'fixture', None, str(self.root), None, state, 1, canonical(lease).decode()))
        if log:
            asyncio.run(self.record(worker))
        return worker

    async def record(self, worker):
        recorder = Recorder(self.j, worker, 'group', 'codex')
        recorder.begin('task', 1)
        recorder.terminal('completed', 'done')
        await recorder.close()

    def test_a_cleanup_plan_lists_the_log_and_keeps_it(self):
        worker = self.worker()
        result = plan(self.j, self.caller, [worker])
        logs = result['candidates'][0]['activity_logs']
        self.assertEqual([f['path'] for f in logs['files']], [str(store.active_path(self.j.root, worker))])
        self.assertGreater(logs['bytes'], 0)
        self.assertEqual((logs['deletable'], logs['removed']), (False, False))
        self.assertTrue(store.active_path(self.j.root, worker).exists())
        self.assertFalse(result['removed'])

    def test_a_worker_without_a_log_lists_none(self):
        worker = self.worker(log=False)
        self.assertEqual(plan(self.j, self.caller, [worker])['candidates'][0]['activity_logs']['files'], [])

    def test_history_cleanup_previews_list_the_log_and_remove_nothing(self):
        worker = self.worker()
        manifest = preview(self.j, worker_ids=[worker])
        self.assertEqual(len(manifest['candidates']), 1)
        logs = manifest['candidates'][0]['activity_logs']
        self.assertEqual(len(logs['files']), 1)
        self.assertFalse(manifest['activity_logs_deleted'])
        self.assertTrue(store.active_path(self.j.root, worker).exists())

    def test_only_a_worker_that_failed_before_any_history_could_ever_lose_its_log(self):
        cases = [('RUNNING', True, False), ('IDLE_GRACE', True, False), ('RELEASING', True, False), ('RELEASED_RESUMABLE', True, False),
                 ('FAILED', True, False), ('FAILED', False, True), ('EXITED', False, True)]
        for state, native, deletable in cases:
            with self.subTest(state=state, native=native):
                worker = self.worker(state, native)
                view = listing(self.j, worker)
                self.assertEqual((view['deletable'], view['removed']), (deletable, False))
                self.assertEqual(view['reason'] is None, deletable)

    def test_an_unknown_worker_is_never_deletable(self):
        self.assertFalse(listing(self.j, new_id())['deletable'])


if __name__ == '__main__':
    unittest.main()
