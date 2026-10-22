"""Offline backward receipt scans with bounded regular-file and FIFO fixtures."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.pagination import decode, encode
from nativepeers.process import identity
from nativepeers.reconcile import MAX_READ_BYTES, observe
from nativepeers.registry import Registry
from nativepeers.routing import Router


ROOT = Path(__file__).resolve().parents[2]


class ReceiptWindowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        scratch = ROOT / 'evidence/scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.journal = Journal(self.root / 'state')
        self.addCleanup(self.journal.close)
        self.registry = Registry(self.journal)
        self.delivery = AsyncMock(side_effect=AssertionError('native input must not be submitted'))
        self.router = Router(self.journal, self.registry, {'fake': self.delivery})
        self.app_server = patch('nativepeers.reconcile.AppServer', side_effect=AssertionError('native lookup must not be called'))
        self.app_server.start()
        self.addCleanup(self.app_server.stop)
        self.configure('claude')

    def configure(self, provider):
        self.provider = provider
        self.native = new_id()
        self.home = self.root / provider
        self.home.mkdir(exist_ok=True)
        self.peer = self.registry.register(provider, self.home, self.native, 'offline recipient',
            identity(os.getpid()).json(), str(self.root), {'kind': 'fake'}, owner_kind='test')
        self.path = self.home / self.native / 'history.jsonl' if provider == 'antigravity' else self.home / 'not-a-uuid.jsonl'
        self.path.parent.mkdir(exist_ok=True)
        self.peer['native_transcript'] = str(self.path)
        self.journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',
                                (canonical(self.peer).decode(), self.peer['handle']))
        self.message_id, self.attempt_id = new_id(), new_id()
        self.message = dict(schema_version=1, message_id=self.message_id, exchange_id=new_id(),
            kind='notify', body={'text': 'exact offline envelope'},
            to={'peer_key': self.peer['handle']}, **{'from': {'kind': 'peer', 'peer_key': 'sender'}})
        self.journal.db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)',
            (self.message_id, self.message['exchange_id'], 'sender', self.peer['handle'], None, None,
             canonical(self.message).decode(), 'fixture', time.time() + 60, time.time()))
        self.journal.db.execute(
            'INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)',
            (self.attempt_id, self.message_id, self.peer['incarnation_id'], 'fake', 'DELIVERY_UNKNOWN', time.time()))

    def event(self, message=None, *, native=None):
        text = canonical(self.message if message is None else message).decode()
        native = native or self.native
        if self.provider == 'claude':
            return canonical({'type': 'user', 'sessionId': native, 'uuid': new_id(), 'message': {'content': text}}) + b'\n'
        if self.provider == 'codex':
            return canonical({'type': 'response_item', 'payload': {'role': 'user', 'id': new_id(),
                              'content': [{'type': 'input_text', 'text': text}]}}) + b'\n'
        return canonical({'type': 'USER_INPUT', 'conversation_id': native, 'step_index': 7, 'content': text}) + b'\n'

    def header(self, native=None):
        return canonical({'type': 'session_meta', 'payload': {'id': native or self.native}}) + b'\n'

    def noise(self, size):
        line = canonical({'type': 'progress', 'padding': 'x' * 160}) + b'\n'
        return line * (size // len(line) + 1)

    async def scan(self, **kwargs):
        return await observe(self.journal, self.registry, self.router, self.message_id, **kwargs)

    def counts(self):
        return {table: self.journal.all('SELECT * FROM ' + table)
                for table in ('messages', 'send_requests', 'completion_routes', 'events')}, self.journal.all('SELECT attempt_id FROM attempts')

    async def test_default_tail_does_not_find_old_exact_event_until_explicit_older_window(self):
        for provider in ('claude', 'codex', 'antigravity'):
            with self.subTest(provider=provider):
                self.configure(provider)
                prefix = self.header() if provider == 'codex' else b''
                self.path.write_bytes(prefix + self.event() + self.noise(1048576 + 2000))
                before = self.counts()
                result = await self.scan()
                self.assertEqual(result['state'], 'DELIVERY_UNKNOWN')
                scan = result['reconciliation']
                self.assertEqual(scan['byte_range'], [self.path.stat().st_size - 1048576, self.path.stat().st_size])
                self.assertTrue(scan['next_cursor'])
                self.assertEqual(result['receipts'], [])
                older = await self.scan(cursor=scan['next_cursor'])
                self.assertEqual(older['state'], 'INGESTED')
                self.assertEqual(older['reconciliation']['byte_range'][0], 0)
                self.assertEqual(older['reconciliation']['next_cursor'], None)
                self.assertEqual(older['receipts'][0]['evidence_kind'], 'matching-native-user-event')
                self.assertEqual(before, self.counts())
        self.delivery.assert_not_awaited()

    async def test_same_uuid_wrong_content_and_other_native_history_are_not_evidence(self):
        self.path.write_bytes(self.event(dict(self.message, body={'text': 'changed'})) + self.event(native=new_id()))
        result = await self.scan()
        self.assertEqual(result['state'], 'DELIVERY_UNKNOWN')
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])
        self.configure('codex')
        self.path.write_bytes(self.header(new_id()) + self.event())
        with self.assertRaises(PeerError) as raised:
            await self.scan()
        self.assertEqual(raised.exception.code, 'OWNERSHIP_UNKNOWN')
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])
        self.path.write_bytes(self.header() + self.event(dict(self.message, body={'text': 'changed'})))
        self.assertEqual((await self.scan())['state'], 'DELIVERY_UNKNOWN')
        self.path.write_bytes(self.header() + self.event())
        self.assertEqual((await self.scan())['state'], 'INGESTED')

    async def test_append_keeps_original_snapshot_and_cursor_scope(self):
        self.path.write_bytes(self.event() + self.noise(4000))
        before = self.counts()
        first = await self.scan(max_bytes=2048)
        cursor = first['reconciliation']['next_cursor']
        self.assertTrue(cursor)
        ceiling = self.path.stat().st_size
        with self.path.open('ab') as stream:
            stream.write(self.event())
        older = await self.scan(cursor=cursor, max_bytes=2048)
        self.assertEqual(older['reconciliation']['snapshot_size'], ceiling)
        self.assertLessEqual(older['reconciliation']['byte_range'][1], ceiling)
        while older['reconciliation']['next_cursor'] and older['state'] != 'INGESTED':
            older = await self.scan(cursor=older['reconciliation']['next_cursor'], max_bytes=2048)
        self.assertEqual(older['state'], 'INGESTED')
        self.assertEqual(before, self.counts())
        original_message = self.message_id
        self.configure('claude')
        self.path.write_bytes(self.noise(5000))
        with self.assertRaises(PeerError) as raised:
            await self.scan(cursor=cursor, max_bytes=2048)
        self.assertEqual(raised.exception.code, 'PROTOCOL_ERROR')
        self.assertNotEqual(original_message, self.message_id)

    async def test_replacement_truncation_and_same_size_edit_invalidate_cursor(self):
        for mutation in ('replace', 'truncate', 'edit', 'append-edited-prefix', 'append-edited-tail'):
            with self.subTest(mutation=mutation):
                self.path.write_bytes(self.noise(20000))
                first = await self.scan(max_bytes=1024)
                cursor = first['reconciliation']['next_cursor']
                before = self.counts()
                data = self.path.read_bytes()
                if mutation == 'replace':
                    other = self.path.with_suffix('.replacement')
                    other.write_bytes(data)
                    other.replace(self.path)
                elif mutation == 'truncate':
                    self.path.write_bytes(data[:100])
                elif mutation == 'edit':
                    with self.path.open('r+b') as stream:
                        stream.seek(7000)
                        stream.write(b'!')
                elif mutation == 'append-edited-prefix':
                    self.path.write_bytes(b'!' + data[1:] + b'new append\n')
                else:
                    self.path.write_bytes(data[:-2] + b'!\n' + b'new append\n')
                with self.assertRaises(PeerError) as raised:
                    await self.scan(cursor=cursor, max_bytes=1024)
                self.assertEqual(raised.exception.code, 'PROTOCOL_ERROR')
                self.assertEqual(before, self.counts())
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])

    async def test_window_boundary_event_is_recovered_and_inspected_ranges_are_exact(self):
        event = self.event()
        prefix = self.noise(500)
        suffix = self.noise(500)
        self.path.write_bytes(prefix + event + suffix)
        size = len(prefix + event + suffix)
        window = len(suffix) + len(event) // 2
        first = await self.scan(max_bytes=window)
        self.assertEqual(first['state'], 'DELIVERY_UNKNOWN')
        self.assertEqual(first['reconciliation']['byte_range'], [size - window, size])
        self.assertEqual(first['reconciliation']['inspected_ranges'], [[len(prefix + event), size]])
        older = await self.scan(cursor=first['reconciliation']['next_cursor'], max_bytes=len(prefix + event))
        self.assertEqual(older['state'], 'INGESTED')
        self.assertEqual(older['reconciliation']['inspected_ranges'], [[0, len(prefix + event)]])
        # Starting exactly after a newline must not discard the first full event.
        self.path.write_bytes(prefix + event)
        self.assertEqual((await self.scan(max_bytes=len(event)))['state'], 'INGESTED')

    async def test_huge_and_partial_lines_make_bounded_progress_without_false_frames(self):
        event = self.event()
        # An exact-looking event suffix inside a larger physical line is not evidence.
        self.path.write_bytes(event + b'x' * 10000 + event)
        cursor = None
        ends = []
        for _ in range(30):
            result = await self.scan(cursor=cursor, max_bytes=1024)
            scan = result['reconciliation']
            ends.append(scan['byte_range'][1])
            if result['state'] == 'INGESTED':
                break
            self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])
            cursor = scan['next_cursor']
            self.assertIsNotNone(cursor)
        self.assertEqual(result['state'], 'INGESTED')
        self.assertEqual(ends, sorted(set(ends), reverse=True))
        self.path.write_bytes(b'x' * 5000)
        result = await self.scan(max_bytes=1024)
        self.assertEqual(result['reconciliation']['inspected_ranges'], [])
        # A valid JSON object at EOF without its terminating newline is not yet persisted as a full JSONL record.
        self.path.write_bytes(self.event().rstrip(b'\n'))
        count = len(self.journal.all('SELECT * FROM receipts'))
        await self.scan()
        self.assertEqual(len(self.journal.all('SELECT * FROM receipts')), count)

    async def test_total_reads_are_capped_and_never_use_unbounded_readline(self):
        self.configure('codex')
        self.path.write_bytes(self.header() + self.noise(5 * 1024 * 1024))
        original_open = Path.open
        reads = []

        class BoundedReader:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): return self.stream.__exit__(*args)
            def fileno(self): return self.stream.fileno()
            def seek(self, *args): return self.stream.seek(*args)
            def readline(self, *args): raise AssertionError('unbounded line traversal')
            def read(self, count=-1):
                if count < 0: raise AssertionError('unbounded read')
                data = self.stream.read(count)
                reads.append(len(data))
                return data

        def open_history(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            return BoundedReader(stream) if path == self.path else stream

        with patch.object(Path, 'open', open_history):
            result = await self.scan(max_bytes=MAX_READ_BYTES)
        self.assertLessEqual(sum(reads), MAX_READ_BYTES)
        self.assertEqual(sum(reads), result['reconciliation']['bytes_read'])
        self.assertTrue(result['reconciliation']['next_cursor'])
        self.assertEqual(result['state'], 'DELIVERY_UNKNOWN')
        self.delivery.assert_not_awaited()

    async def test_missing_empty_malformed_and_fifo_history_remain_inconclusive(self):
        before = self.counts()
        result = await self.scan()
        self.assertEqual(result['state'], 'DELIVERY_UNKNOWN')
        self.assertEqual(result['reconciliation']['inspected_ranges'], [])
        for data in (b'', b'[]\nnull\n42\n{"type":"user","sessionId":"bad","message":null}\n', b'[' * 2000 + b'\n'):
            with self.subTest(data=data[:20]):
                self.path.write_bytes(data)
                self.assertEqual((await self.scan())['state'], 'DELIVERY_UNKNOWN')
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(PeerError) as raised:
            await asyncio.wait_for(self.scan(), .5)
        self.assertEqual(raised.exception.code, 'OWNERSHIP_UNKNOWN')
        self.assertEqual(before, self.counts())
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])

    async def test_cursor_path_native_message_and_numeric_bounds_are_validated(self):
        self.path.write_bytes(self.noise(8000))
        first = await self.scan(max_bytes=1024)
        cursor = first['reconciliation']['next_cursor']
        before = self.counts()
        original = self.peer['native_transcript']
        moved = self.path.with_name('other-history.jsonl')
        moved.write_bytes(self.path.read_bytes())
        self.peer['native_transcript'] = str(moved)
        self.journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',
                                (canonical(self.peer).decode(), self.peer['handle']))
        with self.assertRaises(PeerError):
            await self.scan(cursor=cursor)
        self.peer['native_transcript'] = original
        self.journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',
                                (canonical(self.peer).decode(), self.peer['handle']))
        scope = ['receipt-scan', self.message_id, hashlib.sha256(canonical([
            canonical(self.message).decode(), self.peer['handle'], self.provider, self.native, str(self.path)])).hexdigest()]
        position, ceiling = decode(cursor, scope)
        for invalid in ('not-json', 'x' * 4097, base64.urlsafe_b64encode(b'[' * 1100 + b']' * 1100).decode()):
            with self.subTest(cursor=invalid[:20]), self.assertRaises(PeerError) as raised:
                await self.scan(cursor=invalid)
            self.assertEqual(raised.exception.code, 'PROTOCOL_ERROR')
        for invalid in (None, dict(position, end=True), dict(position, end=10**100), dict(position, end=0)):
            with self.subTest(position=invalid), self.assertRaises(PeerError) as raised:
                await self.scan(cursor=encode(scope, invalid, ceiling))
            self.assertEqual(raised.exception.code, 'PROTOCOL_ERROR')
        for invalid in (0, True, MAX_READ_BYTES + 1, '1024'):
            with self.subTest(max_bytes=invalid), self.assertRaises(PeerError):
                await self.scan(max_bytes=invalid)
        self.assertEqual(before, self.counts())
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])

    async def test_metadata_lookup_and_cleanup_have_finite_timeouts(self):
        self.configure('codex')
        self.path.write_bytes(self.header())
        self.peer.pop('native_transcript')
        self.journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE peer_key=?',
                                (canonical(self.peer).decode(), self.peer['handle']))
        before = self.counts()
        native_timeout = asyncio.timeout

        async def stalled():
            await asyncio.Future()

        for phase in ('start', 'close'):
            with self.subTest(phase=phase):
                client = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), call=AsyncMock())
                client.call.return_value = {'thread': {'id': self.native, 'path': str(self.path)}}
                getattr(client, phase).side_effect = stalled
                with patch('nativepeers.reconcile.AppServer', return_value=client), \
                     patch('nativepeers.reconcile.asyncio.timeout', side_effect=lambda _: native_timeout(.01)):
                    result = await asyncio.wait_for(self.scan(), .5)
                self.assertEqual(result['state'], 'DELIVERY_UNKNOWN')
                self.assertEqual(result['reconciliation']['state'], 'UNCONFIRMED')
                client.close.assert_awaited_once()
        self.assertEqual(before, self.counts())
        self.assertEqual(self.journal.all('SELECT * FROM receipts'), [])
