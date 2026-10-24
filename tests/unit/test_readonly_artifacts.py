"""Artifact CLI reads across an enforced filesystem permission boundary."""
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from nativepeers.artifacts import Artifacts
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry


ROOT = Path(__file__).resolve().parents[2]


class ReadOnlyArtifactTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT/'evidence/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.journal = Journal(self.root/'state')
        self.addCleanup(self.journal.close)
        self.owner = identity(os.getpid()).json()
        self.worker_id, self.group_id, self.native_id = new_id(), new_id(), new_id()
        self.grants = dict(worker_id=self.worker_id, group_id=self.group_id, profiles=['analysis'], can_spawn=False)
        self.peer = Registry(self.journal).register(
            'codex', self.root/'provider', self.native_id, 'read-only artifact fixture',
            self.owner, self.root, {'kind': 'owned-worker'}, owner_kind='owned-worker-controller',
            policy=self.grants)
        self.journal.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',
                                (self.group_id, 'parent', '{}', 'RUNNING', '{}'))
        lease = dict(spec=dict(permission_profile='analysis', workspace={'mode': 'inherit-read-only'}))
        self.journal.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (self.worker_id, self.group_id, self.peer['handle'], 'codex', self.native_id, 'reader',
             None, None, None, str(self.root), None, 'ACTIVE', 1, json.dumps(lease)))
        self.binding = self.journal.root/'bindings/reader'
        self.binding_value = dict(state='BOUND', kind='peer', provider='codex', owner=self.owner,
            expires_at=time.time()+120, peer_key=self.peer['handle'],
            incarnation_id=self.peer['incarnation_id'], grants=self.grants)
        atomic_json(self.binding, self.binding_value)
        self.artifacts = Artifacts(self.journal)

    @contextmanager
    def read_only_state(self):
        # chmod is a real kernel boundary for the unprivileged CLI subprocess. It also
        # works inside native sandboxes that prohibit nesting macOS sandbox-exec.
        paths = [self.journal.root, *self.journal.root.rglob('*')]
        modes = {path: path.stat().st_mode & 0o777 for path in paths}
        try:
            for path, mode in modes.items():
                path.chmod(mode & ~0o222)
            for path in (self.journal.root/'locks/schema.lock', self.journal.root/'journal.sqlite3',
                         self.journal.root/'journal.sqlite3-wal', self.journal.root/'journal.sqlite3-shm'):
                try:
                    descriptor = os.open(path, os.O_RDWR)
                except (PermissionError,FileNotFoundError):
                    continue
                os.close(descriptor)
                self.skipTest('This UID bypasses the filesystem read-only permission boundary')
            with self.assertRaises(PermissionError):
                os.open(self.journal.root/'write-probe', os.O_CREAT | os.O_WRONLY, 0o600)
            yield
        finally:
            for path, mode in modes.items():
                path.chmod(mode)

    def cli(self, handle, *, binding_value=None, bound=True, offset=0, limit=262144):
        atomic_json(self.binding, binding_value or self.binding_value)
        environment = dict(os.environ, NPEER_HOME=str(self.journal.root), PYTHONPATH=str(ROOT),
            PYTHONDONTWRITEBYTECODE='1', NPEER_BOOTSTRAP='', CODEX_THREAD_ID='',
            NPEER_BINDING=str(self.binding) if bound else '')
        command = [sys.executable, '-B', '-m', 'nativepeers', 'artifact-read', handle,
                   '--offset', str(offset), '--limit', str(limit), '--json']
        # Keep a distinct native executable in ancestry so the actual owned-binding
        # checks run, including the current generation's process manifest.
        process = subprocess.Popen(['/bin/bash', '-c', 'read -r ready\n"$@" &\nwait $!', 'reader', *command],
            cwd=ROOT, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            native = identity(process.pid).json()
            atomic_json(self.journal.root/'workers'/(self.worker_id+'.json'),
                dict(worker_id=self.worker_id, group_id=self.group_id, generation=1,
                     native_id=self.native_id, owner=self.owner, native_process=native))
            with self.read_only_state():
                before = {path.relative_to(self.journal.root): (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in self.journal.root.rglob('*') if path.is_file()}
                # Prove this fixture rejects the original writing startup.
                with self.assertRaises(PermissionError):
                    Journal(self.journal.root)
                stdout, stderr = process.communicate('go\n', timeout=10)
                after = {path.relative_to(self.journal.root): (path.read_bytes(), path.stat().st_mtime_ns)
                         for path in self.journal.root.rglob('*') if path.is_file()}
                self.assertEqual(after, before)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
        self.assertEqual(stderr, '')
        return process.returncode, json.loads(stdout)

    def test_analysis_worker_cli_reads_granted_context_from_live_wal_without_writes(self):
        # Checkpoint before publishing the context so immutable/main-file-only reads
        # cannot see either its metadata or its grant.
        self.journal.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        raw = b'Approved context, including the second page.'
        handle = self.artifacts.put(raw, 'parent', [self.worker_id])
        self.assertGreater((self.journal.root/'journal.sqlite3-wal').stat().st_size, 0)
        code, response = self.cli(handle, offset=9, limit=7)
        self.assertEqual(code, 0, response)
        self.assertTrue(response['ok'], response)
        page = response['data']
        self.assertEqual(base64.b64decode(page['data_base64']), raw[9:16])
        self.assertEqual(page['next_offset'], 16)
        self.assertEqual(page['sha256'], self.journal.one('SELECT sha256 FROM artifacts')['sha256'])

    def test_cli_keeps_reader_grants_binding_checks_and_content_integrity(self):
        handle = self.artifacts.put(b'context', 'parent', [self.worker_id])
        denied = self.artifacts.put(b'private', 'parent', ['another-worker'])
        for selected, binding, bound, expected, exit_code in (
            (denied, None, True, 'APPROVAL_BLOCKED', 4),
            (handle, dict(self.binding_value, expires_at=0), True, 'CALLER_UNBOUND', 4),
            (handle, dict(self.binding_value, incarnation_id=new_id()), True, 'TARGET_REBOUND', 3),
            (handle, None, False, 'APPROVAL_BLOCKED', 4),
        ):
            with self.subTest(expected=expected, bound=bound):
                code, response = self.cli(selected, binding_value=binding, bound=bound)
                self.assertEqual(code, exit_code, response)
                self.assertFalse(response['ok'], response)
                self.assertEqual(response['error']['code'], expected, response)
        (self.journal.root/'artifacts'/handle).write_bytes(b'changed')
        code, response = self.cli(handle)
        self.assertEqual(code, 7, response)
        self.assertEqual(response['error']['code'], 'PROTOCOL_ERROR', response)

    def test_read_only_connection_observes_live_grants_and_revocations(self):
        handle = self.artifacts.put(b'context', 'parent')
        self.journal.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        with self.read_only_state():
            reader = Journal(self.journal.root, read_only=True)
            try:
                artifacts = Artifacts(reader)
                with self.assertRaises(PeerError):
                    artifacts.read(handle, self.worker_id)
                self.artifacts.grant(handle, 'parent', [self.worker_id])
                self.assertEqual(artifacts.read(handle, self.worker_id)['bytes_read'], 7)
                self.journal.db.execute('UPDATE artifacts SET allowed_readers_json=? WHERE artifact_id=?', ('[]', handle))
                with self.assertRaises(PeerError) as denied:
                    artifacts.read(handle, self.worker_id)
                self.assertEqual(denied.exception.code, 'APPROVAL_BLOCKED')
                with self.assertRaises(sqlite3.OperationalError):
                    reader.db.execute("UPDATE artifacts SET owner_key='forged'")
            finally:
                reader.close()

    def test_read_only_startup_does_not_install_state_or_follow_symlinked_state(self):
        missing = self.root/'missing'
        with self.assertRaises(FileNotFoundError):
            Journal(missing, read_only=True)
        self.assertFalse(missing.exists())
        empty = self.root/'empty'
        empty.mkdir(mode=0o700)
        with self.assertRaises(PermissionError):
            Journal(empty, read_only=True)

        self.assertEqual(list(empty.iterdir()), [])
        link = self.root/'state-link'
        link.symlink_to(self.journal.root, target_is_directory=True)
        with self.assertRaises(PermissionError):
            Journal(link, read_only=True)
        database = empty/'journal.sqlite3'
        database.symlink_to(self.journal.root/'journal.sqlite3')
        with self.assertRaises(PermissionError):
            Journal(empty, read_only=True)

    def test_missing_wal_sidecars_return_structured_error_inside_readonly_boundary(self):
        handle=self.artifacts.put(b'context','parent',[self.worker_id])
        self.journal.close()
        self.assertFalse((self.journal.root/'journal.sqlite3-shm').exists())
        code,response=self.cli(handle)
        self.assertEqual(code,5,response)
        self.assertEqual(response['error']['code'],'CAPABILITY_MISMATCH')
        self.assertIn('WAL/SHM',response['error']['message'])


if __name__ == '__main__':
    unittest.main()
