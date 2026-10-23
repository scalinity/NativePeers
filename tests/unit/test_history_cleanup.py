"""Exact-history cleanup with fake native metadata and real local processes."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nativepeers import history_cleanup as cleanup
from nativepeers.caller import Caller
from nativepeers.cli import parser, dispatch
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json, canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity


class HistoryCleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve(); self.home = self.root/'codex-home'; self.home.mkdir()
        self.j = Journal(self.root/'journal'); self.addCleanup(self.j.close)
        atomic_json(self.j.root/'compat/runtime.json', {'providers': {'codex': {'path': '/fake/immutable/codex'}}})
        self.database = self.home/'state_5.sqlite'
        with sqlite3.connect(self.database) as db:
            db.executescript('CREATE TABLE threads(id TEXT PRIMARY KEY,archived INT,is_pinned INT,title TEXT); CREATE TABLE thread_spawn_edges(parent_thread_id TEXT,child_thread_id TEXT);')
        process = subprocess.Popen(['/bin/sleep', '60']); self.dead = identity(process.pid).json(); process.terminate(); process.wait()
        self.calls = []; self.before_mutation = None
        test = self

        class FakeClient:
            def __init__(self, executable, *, environment):
                test.assertEqual(environment, {'CODEX_HOME': str(test.home)})
            async def start(self):
                if test.before_mutation: test.before_mutation()
            async def call(self, method, params):
                test.calls.append((method, params))
                ids = [m['native_id'] for m in cleanup.native_metadata(test.home, params['threadId'], subtree=method=='thread/archive')]
                with sqlite3.connect(test.database) as db:
                    for native in ids: db.execute('UPDATE threads SET archived=? WHERE id=?', (method=='thread/archive', native))
                return {}
            async def close(self): pass

        self.client = FakeClient

    def worker(self, *, state='RELEASED_RESUMABLE', generation=1, pinned=False, native=None):
        worker, group, task = new_id(), new_id(), new_id(); native = native or new_id()
        lease = dict(provider_home=str(self.home), executable='/fake/immutable/codex')
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, 'actual-parent', '{}', 'COMPLETED', canonical({'owner': self.dead}).decode()))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (worker, group, None, 'codex', native, 'worker fixture', 'model', 'model', 'high', str(self.root), None, state, generation, canonical(lease).decode()))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, 'COMPLETED', None, None, generation, '{}'))
        atomic_json(self.j.root/'workers'/(worker+'.json'), dict(worker_id=worker, group_id=group, native_id=native, generation=generation,
                    owner=self.dead, native_process=None, cleanup=dict(state='CLOSED', output_eof=True)))
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT OR IGNORE INTO threads VALUES(?,?,?,?)', (native, False, pinned, 'fixture'))
        return worker, native, group, task

    def plan(self, *workers): return cleanup.preview(self.j, worker_ids=list(workers))

    def apply(self, manifest, reverse=False):
        with patch.object(cleanup, 'verify'):
            return asyncio.run(cleanup.apply(self.j, manifest, reverse=reverse, client_factory=self.client))

    def test_preview_has_exact_provenance_and_never_reads_titles_or_changes_native_state(self):
        worker, native, group, _ = self.worker(); before = self.database.read_bytes(); writes = self.j.db.total_changes
        value = self.plan(worker); candidate = value['candidates'][0]
        self.assertTrue(candidate['eligible'], candidate)
        self.assertEqual(candidate['native_id'], native); self.assertEqual(candidate['provider_home'], str(self.home))
        self.assertEqual(candidate['affected'][0]['ownership'][0]['group_id'], group)
        self.assertEqual(before, self.database.read_bytes()); self.assertEqual(writes, self.j.db.total_changes)
        self.assertNotIn('fixture', json.dumps(value)); self.assertFalse(value['histories_deleted'])

    def test_archive_is_idempotent_preserves_history_and_exact_reversal(self):
        worker, native, _, _ = self.worker(); value = self.plan(worker)
        result = self.apply(value); self.assertEqual(result['counts']['archived'], 1, result)
        self.assertTrue(cleanup.native_metadata(self.home, native)[0]['archived'])
        again = self.apply(value); self.assertEqual(again['counts']['already-archived'], 1, again)
        self.assertEqual(self.calls, [('thread/archive', {'threadId': native})])
        restored = self.apply(value, reverse=True); self.assertEqual(restored['counts']['unarchived'], 1, restored)
        again = self.apply(value, reverse=True); self.assertEqual(again['counts']['already-unarchived'], 1, again)
        self.assertEqual(cleanup.resume_archive_state(self.j, self.j.one('SELECT * FROM workers WHERE worker_id=?', (worker,)))['archived'], False)
        self.assertEqual(self.calls[-1], ('thread/unarchive', {'threadId': native}))
        with sqlite3.connect(self.database) as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM threads WHERE id=?', (native,)).fetchone()[0], 1)

    def test_preview_and_apply_skip_active_queued_live_unknown_and_missing_closure(self):
        worker, native, _, task = self.worker(state='RUNNING'); self.assertFalse(self.plan(worker)['candidates'][0]['eligible'])
        self.j.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE'")
        self.j.db.execute("UPDATE tasks SET state='QUEUED'"); self.assertFalse(self.plan(worker)['candidates'][0]['eligible'])
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        value = self.plan(worker)
        path = self.j.root/'workers'/(worker+'.json'); record = json.loads(path.read_text()); record['owner'] = identity(os.getpid()).json(); atomic_json(path, record)
        self.assertFalse(self.plan(worker)['candidates'][0]['eligible'])
        self.assertEqual(self.apply(value)['counts']['skipped'], 1); self.assertEqual(self.calls, [])
        record['owner'] = self.dead; record['cleanup'] = {}; atomic_json(path, record)
        self.assertEqual(self.plan(worker)['candidates'][0]['reason_code'], 'CLEANUP_UNCERTAIN')

    def test_late_generation_or_initialization_race_skips_without_mutation(self):
        worker, _, _, _ = self.worker(); value = self.plan(worker)
        self.before_mutation = lambda: self.j.db.execute("UPDATE tasks SET state='QUEUED'")
        result = self.apply(value); self.assertEqual(result['counts']['skipped'], 1, result); self.assertEqual(self.calls, [])

    def test_related_ordinary_history_blocks_entire_archive_and_new_child_after_preview(self):
        worker, native, _, _ = self.worker(); value = self.plan(worker); child = new_id()
        with sqlite3.connect(self.database) as db:
            db.execute('INSERT INTO threads VALUES(?,?,?,?)', (child, False, False, 'ordinary NP-looking conversation'))
            db.execute('INSERT INTO thread_spawn_edges VALUES(?,?)', (native, child))
        candidate = self.plan(worker)['candidates'][0]
        self.assertFalse(candidate['eligible']); self.assertEqual(candidate['reason_code'], 'OWNERSHIP_UNKNOWN')
        self.assertEqual(self.apply(value)['counts']['skipped'], 1); self.assertEqual(self.calls, [])

    def test_owned_released_subtree_archives_together_and_unarchives_exactly(self):
        worker, native, _, _ = self.worker(); child_worker, child, _, _ = self.worker(generation=2)
        with sqlite3.connect(self.database) as db: db.execute('INSERT INTO thread_spawn_edges VALUES(?,?)', (native, child))
        value = self.plan(worker); self.assertTrue(value['candidates'][0]['eligible'])
        self.assertEqual(self.apply(value)['counts']['archived'], 1)
        self.assertTrue(cleanup.resume_archive_state(self.j, self.j.one('SELECT * FROM workers WHERE worker_id=?', (child_worker,)))['archived'])
        self.assertEqual(self.apply(value, reverse=True)['counts']['unarchived'], 2)
        self.assertEqual([p['threadId'] for m, p in self.calls if m=='thread/unarchive'], [native, child])

    def test_lost_archive_response_and_partial_native_success_preserve_reversal_without_replay(self):
        worker, native, _, _ = self.worker(); value = self.plan(worker); normal = self.client
        class LostResponse(normal):
            async def call(self, method, params):
                await super().call(method, params)
                raise ConnectionResetError('response lost after commit')
        self.client = LostResponse
        result = self.apply(value); self.assertEqual(result['counts']['archived'], 1, result)
        self.assertFalse(result['outcomes'][0]['native_response_verified'])
        self.client = normal
        self.assertEqual(self.apply(value)['counts']['already-archived'], 1)
        self.assertEqual(self.apply(value, reverse=True)['counts']['unarchived'], 1)
        self.assertEqual(len([m for m,p in self.calls if m=='thread/archive']), 1)
        child_worker, child, _, _ = self.worker()
        with sqlite3.connect(self.database) as db: db.execute('INSERT INTO thread_spawn_edges VALUES(?,?)', (native, child))
        value = self.plan(worker); test = self
        class Partial(normal):
            async def call(self, method, params):
                test.calls.append((method, params))
                with sqlite3.connect(test.database) as db: db.execute('UPDATE threads SET archived=1 WHERE id=?', (native,))
                return {}
        self.client = Partial
        result = self.apply(value); self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['outcomes'][0]['affected_native_ids'], [native])
        self.client = normal
        retry = self.apply(value); self.assertEqual(retry['counts']['failed'], 1)
        self.assertEqual(retry['outcomes'][0]['remaining_native_ids'], [child])
        result = self.apply(value, reverse=True)
        self.assertEqual(result['counts']['unarchived'], 1); self.assertEqual(result['counts']['already-unarchived'], 1)

    def test_metadata_helper_cleanup_failure_retains_completed_outcome_and_stops_admission(self):
        first, native, _, _ = self.worker(); second, other, _, _ = self.worker(); value = self.plan(first, second); normal = self.client
        class UnknownClosure(normal):
            async def close(self): raise PeerError('CLEANUP_UNCERTAIN', 'fixture held output pipe')
        self.client = UnknownClosure
        result = self.apply(value)
        self.assertEqual(result['counts']['archived'], 1); self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(result['outcomes'][0]['helper_cleanup']['state'], 'uncertain')
        self.assertTrue(cleanup.native_metadata(self.home, native)[0]['archived'])
        self.assertFalse(cleanup.native_metadata(self.home, other)[0]['archived'])

    def test_unarchive_subtree_halts_after_helper_cleanup_uncertainty(self):
        worker, native, _, _ = self.worker(); _, child, _, _ = self.worker()
        with sqlite3.connect(self.database) as db: db.execute('INSERT INTO thread_spawn_edges VALUES(?,?)', (native, child))
        value = self.plan(worker); self.apply(value); normal = self.client
        class UnknownClosure(normal):
            async def close(self): raise PeerError('CLEANUP_UNCERTAIN', 'fixture held output pipe')
        self.client = UnknownClosure
        result = self.apply(value, reverse=True)
        self.assertEqual(result['counts']['unarchived'], 1); self.assertEqual(result['counts']['skipped'], 1)
        self.assertEqual(len([m for m,p in self.calls if m=='thread/unarchive']), 1)
        self.assertTrue(cleanup.native_metadata(self.home, child)[0]['archived'])

    def test_pinned_promoted_and_normal_np_looking_sessions_are_untouched(self):
        worker, native, _, _ = self.worker(pinned=True)
        self.assertFalse(self.plan(worker)['candidates'][0]['eligible'])
        with sqlite3.connect(self.database) as db: db.execute('UPDATE threads SET is_pinned=0')
        from nativepeers.models import home_id
        self.j.db.execute('INSERT INTO peer_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('normal', new_id(), native, 'codex', home_id(self.home), self.dead['pid'], self.dead['start'], 'NP fixture', 'interactive-standalone', canonical(dict(owner=self.dead, handle='normal', home=str(self.home))).decode(), 'offline', 0))
        self.assertIn('promoted', self.plan(worker)['candidates'][0]['reason'])
        normal = new_id()
        with sqlite3.connect(self.database) as db: db.execute('INSERT INTO threads VALUES(?,?,?,?)', (normal, False, False, 'NP Pro Sol Parent'))
        selected = cleanup.preview(self.j)['candidates']; self.assertEqual(len(selected), 1)
        self.assertEqual(self.calls, [])

    def test_waiters_completion_push_and_uncertain_delivery_are_preserved(self):
        worker, _, group, task = self.worker()
        self.j.db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)', (group, 1, 'parent', 'wait', 'WAIT_LEASED', 'waiter', 0, new_id(), 1))
        self.assertFalse(self.plan(worker)['candidates'][0]['eligible'])
        self.j.db.execute("UPDATE completion_routes SET state='NOTIFICATION_UNKNOWN',lease_deadline=NULL")
        # wait_output is a retained tool claim, not an active native writer.
        self.assertTrue(self.plan(worker)['candidates'][0]['eligible'])
        self.j.db.execute("UPDATE tasks SET terminal_reason='DELIVERY_UNKNOWN_AFTER_OWNER_LOSS'")
        self.assertEqual(self.plan(worker)['candidates'][0]['reason_code'], 'DELIVERY_UNKNOWN')

    def test_provider_home_provenance_is_required_and_unknown_schema_fails_closed(self):
        worker, _, _, _ = self.worker()
        self.j.db.execute("UPDATE workers SET lease_json='{}'")
        self.assertEqual(self.plan(worker)['candidates'][0]['reason_code'], 'OWNERSHIP_UNKNOWN')
        with self.assertRaises(PeerError): cleanup.native_metadata(self.root/'missing', new_id())
        self.assertEqual(cleanup.resume_archive_state(self.j, self.j.one('SELECT * FROM workers'))['checked'], False)

    def test_explicit_completed_fixture_parent_requires_exact_harness_and_released_launch(self):
        from nativepeers.files import private_directory
        from nativepeers.models import home_id
        run = private_directory(self.root/'harness'); self.j = Journal(run/'state'); self.addCleanup(self.j.close)
        atomic_json(self.j.root/'compat/runtime.json', {'providers': {'codex': {'path': '/fake/immutable/codex'}}})
        worker, child, group, _ = self.worker(); native = new_id(); caller = 'fixture-parent-key'
        descriptor = dict(owner=self.dead, home=str(self.home))
        self.j.db.execute('INSERT INTO peer_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (caller,new_id(),native,'codex',home_id(self.home),self.dead['pid'],self.dead['start'],'NP fixture parent','interactive-standalone',canonical(descriptor).decode(),'offline',0))
        self.j.db.execute('UPDATE groups SET parent_key=?', (caller,))
        with sqlite3.connect(self.database) as db: db.execute('INSERT INTO threads VALUES(?,?,?,?)', (native, False, False, 'fixture parent'))
        source = {'nativepeers/foo.py': 'exact-fixture-source-digest'}
        record = dict(provider='codex', status='PASS', source=source, parent_native_id=native, caller=caller, groups=[group])
        atomic_json(run/'codex-case.json', record); atomic_json(run/'prepared.json', {'source': source})
        atomic_json(run/'codex-launch.json', {'owner_expected_after_exec': self.dead})
        value = cleanup.preview(self.j, worker_ids=[], automation_manifests=[run/'codex-case.json'])
        self.assertTrue(value['candidates'][0]['eligible'], value)
        self.assertEqual(self.apply(value)['counts']['archived'], 1)
        self.assertEqual(self.apply(value, reverse=True)['counts']['unarchived'], 1)
        atomic_json(run/'codex-launch.json', {'owner_expected_after_exec': identity(os.getpid()).json()})
        value = cleanup.preview(self.j, worker_ids=[], automation_manifests=[run/'codex-case.json'])
        self.assertFalse(value['candidates'][0]['eligible'])
        value = cleanup.preview(self.j, worker_ids=[], automation_manifests=[run/'arbitrary-parent.json'])
        self.assertFalse(value['candidates'][0]['eligible'])

    def test_wrong_journal_manifest_ineligible_preview_and_reversal_without_receipt_refuse(self):
        worker, _, _, _ = self.worker(); value = self.plan(worker)
        result = self.apply(value, reverse=True); self.assertEqual(result['counts']['skipped'], 1); self.assertEqual(self.calls, [])
        value['journal_root'] = str(self.root/'other')
        with self.assertRaises(PeerError): self.apply(value)
        with self.assertRaises(PeerError): self.plan(worker, worker)
        with self.assertRaises(PeerError): self.plan('NP Pro Sol Parent')

    def test_cli_plan_is_operator_maintenance_and_workers_cannot_archive(self):
        worker, _, _, _ = self.worker(); path = self.j.root/'plan.json'
        with patch('nativepeers.cli.bind_caller', side_effect=PeerError('CALLER_UNBOUND', 'fixture')):
            value = asyncio.run(dispatch(parser().parse_args(['history-cleanup', '--plan', '--worker-id', worker, '--manifest', str(path)]), self.j))
        self.assertTrue(path.exists()); self.assertTrue(value['candidates'][0]['eligible'])
        with patch('nativepeers.cli.bind_caller', return_value=Caller('peer', 'worker', grants={'worker_id': worker})):
            with self.assertRaises(PeerError): asyncio.run(dispatch(parser().parse_args(['history-cleanup', '--apply', '--manifest', str(path)]), self.j))
        with self.assertRaises(PeerError): cleanup.save_manifest(path, value)


if __name__ == '__main__': unittest.main()
