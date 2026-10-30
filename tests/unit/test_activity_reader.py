import asyncio
import gzip
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.activity import Reader, Recorder, listing, store
from nativepeers.activity import reader as reader_module
from nativepeers.journal import Journal
from nativepeers.models import new_id


def event(seq, kind='message.delta', item=None, text='x'):
    return dict(schema='nativepeers.activity.v1', seq=seq, at='2026-10-30T17:12:01.120Z', worker_id='w', group_id='g', task_id='t', generation=1,
                native_turn=None, item=item, kind=kind, data=dict(text=text), source=dict(provider='codex', event=None))


def raw(*events):
    return b''.join(json.dumps(e, separators=(',', ':')).encode() + b'\n' for e in events)


class ReaderCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Journal(Path(self.directory.name))
        self.addCleanup(self.journal.close)
        self.worker = new_id()
        store.directory(self.journal.root, create=True)
        self.active = store.active_path(self.journal.root, self.worker)
        self.reader = Reader(self.journal.root, self.worker)

    def write(self, *events, path=None):
        with open(path or self.active, 'ab') as stream:
            stream.write(raw(*events))

    def segment(self, number, *events):
        path = self.active.with_name('%s.%d.ndjson.gz' % (self.worker, number))
        path.write_bytes(gzip.compress(raw(*events)))
        return path


class ReaderTests(ReaderCase):
    async def test_pages_follow_the_cursor(self):
        self.write(*[event(n) for n in range(1, 11)])
        page = self.reader.read(0, limit=4)
        self.assertEqual(([e['seq'] for e in page['events']], page['next_seq'], page['more'], page['last_seq']), ([1, 2, 3, 4], 4, True, 10))
        page = self.reader.read(page['next_seq'], limit=100)
        self.assertEqual(([e['seq'] for e in page['events']], page['next_seq'], page['more']), ([5, 6, 7, 8, 9, 10], 10, False))
        done = self.reader.read(10)
        self.assertEqual((done['events'], done['next_seq'], done['more'], done['gap']), ([], 10, False, None))

    async def test_a_page_never_exceeds_512_events_or_the_byte_budget(self):
        self.write(*[event(n) for n in range(1, 600)])
        self.assertEqual(len(self.reader.read(0, limit=10000)['events']), 512)
        self.write(*[event(n, text='z' * 400) for n in range(600, 640)])
        page = self.reader.read(599, max_bytes=2000)
        self.assertTrue(0 < len(page['events']) < 40 and page['more'])
        self.assertEqual(page['next_seq'], page['events'][-1]['seq'])

    async def test_the_item_filter_advances_the_cursor_over_other_events(self):
        self.write(event(1, item='a'), event(2, item='b'), event(3, item='a'), event(4, item='c'))
        page = self.reader.read(0, item='a', limit=1)
        self.assertEqual(([e['seq'] for e in page['events']], page['next_seq'], page['more']), ([1], 2, True))
        page = self.reader.read(page['next_seq'], item='a')
        self.assertEqual(([e['seq'] for e in page['events']], page['next_seq']), ([3], 4))
        self.assertEqual(self.reader.read(0, item='missing')['events'], [])
        self.assertEqual(self.reader.read(0, item='missing')['next_seq'], 4)

    async def test_a_torn_final_line_waits_until_it_completes(self):
        self.write(event(1))
        with open(self.active, 'ab') as stream:
            stream.write(raw(event(2))[:-20])
        self.assertEqual([e['seq'] for e in self.reader.read(0)['events']], [1])
        with open(self.active, 'ab') as stream:
            stream.write(raw(event(2))[-20:])
        self.assertEqual([e['seq'] for e in self.reader.read(0)['events']], [1, 2])

    async def test_corrupt_lines_are_skipped(self):
        with open(self.active, 'ab') as stream:
            stream.write(raw(event(1)) + b'not json\n' + b'{"seq":"x"}\n' + raw(event(2)))
        self.assertEqual([e['seq'] for e in self.reader.read(0)['events']], [1, 2])

    async def test_rotated_segments_are_read_transparently_and_deduplicated(self):
        self.segment(1, *[event(n) for n in range(1, 6)])
        self.segment(2, *[event(n) for n in range(6, 11)])
        self.write(*[event(n) for n in range(10, 15)])  # a crash between compressing and switching leaves seq 10 in both
        page = self.reader.read(0, limit=512)
        self.assertEqual([e['seq'] for e in page['events']], list(range(1, 15)))
        self.assertEqual([e['seq'] for e in self.reader.read(7)['events']], list(range(8, 15)))
        self.assertEqual([e['seq'] for e in self.reader.read(12)['events']], [13, 14])
        self.assertIsNone(page['gap'])

    async def test_a_gap_says_the_requested_range_is_gone_and_reading_continues(self):
        self.segment(2, *[event(n) for n in range(6, 11)])
        self.write(*[event(n) for n in range(11, 13)])
        page = self.reader.read(2)
        self.assertEqual(page['gap'], dict(from_seq=3, to_seq=5, reason='range-unavailable'))
        self.assertEqual([e['seq'] for e in page['events']], list(range(6, 13)))
        self.assertIsNone(self.reader.read(5)['gap'])

    async def test_a_missing_log_is_empty_not_an_error(self):
        page = self.reader.read(0)
        self.assertEqual((page['events'], page['next_seq'], page['last_seq'], page['gap']), ([], 0, None, None))

    async def test_a_large_file_is_searched_not_scanned(self):
        padding = 'p' * 900
        self.write(*[event(n, text=padding) for n in range(1, 3001)])
        self.assertGreater(self.active.stat().st_size, 2 * reader_module.SEEK_THRESHOLD)
        with patch.object(reader_module.store, 'parse', wraps=store.parse) as parsed:
            page = self.reader.read(2990)
        self.assertEqual([e['seq'] for e in page['events']], list(range(2991, 3001)))
        self.assertLess(parsed.call_count, 200)
        self.assertEqual([e['seq'] for e in self.reader.read(0, limit=3)['events']], [1, 2, 3])
        self.assertEqual(self.reader.read(3000)['events'], [])
        for after in (1, 777, 1500, 2999):
            self.assertEqual(self.reader.read(after, limit=1)['events'][0]['seq'], after + 1)

    async def test_list_files_are_reported_for_cleanup_and_never_removed(self):
        self.segment(1, event(1))
        self.write(event(2))
        view = listing(self.journal, self.worker)
        self.assertEqual(len(view['files']), 2)
        self.assertFalse(view['deletable'])
        self.assertFalse(view['removed'])
        self.assertTrue(all(Path(f['path']).exists() for f in view['files']))


class WaitTests(ReaderCase):
    async def test_a_waiting_reader_wakes_when_an_event_arrives(self):
        self.write(event(1))

        async def append():
            await asyncio.sleep(0.15)
            self.write(event(2))

        task = asyncio.create_task(append())
        start = time.monotonic()
        page = await self.reader.wait(1, wait_ms=5000)
        elapsed = time.monotonic() - start
        await task
        self.assertEqual(([e['seq'] for e in page['events']], page['timed_out']), ([2], False))
        self.assertTrue(0.1 < elapsed < 1.0, elapsed)

    async def test_an_idle_wait_times_out_without_busy_spinning(self):
        self.write(event(1))
        with patch.object(Reader, 'signature', autospec=True, side_effect=Reader.signature) as polled:
            start = time.monotonic()
            page = await self.reader.wait(1, wait_ms=600)
            elapsed = time.monotonic() - start
        self.assertTrue(page['timed_out'] and page['events'] == [])
        self.assertTrue(0.55 < elapsed < 1.5, elapsed)
        self.assertLess(polled.call_count, 20)

    async def test_wait_is_capped_at_25_seconds(self):
        self.assertEqual(reader_module.LONG_POLL_CAP_MS, 25000)
        with patch.object(reader_module, 'LONG_POLL_CAP_MS', 150):
            start = time.monotonic()
            page = await self.reader.wait(0, wait_ms=10 ** 9)
        self.assertTrue(page['timed_out'])
        self.assertLess(time.monotonic() - start, 1.0)

    async def test_a_filtered_wait_returns_only_its_item_and_keeps_the_cursor_moving(self):
        self.write(event(1, item='other'))

        async def append():
            await asyncio.sleep(0.1)
            self.write(event(2, item='other'), event(3, item='mine', kind='tool.completed'))

        task = asyncio.create_task(append())
        page = await self.reader.wait(0, wait_ms=3000, item='mine')
        await task
        self.assertEqual(([e['seq'] for e in page['events']], page['next_seq']), ([3], 3))

    async def test_a_wait_with_events_available_returns_at_once(self):
        self.write(event(1), event(2))
        start = time.monotonic()
        page = await self.reader.wait(0, wait_ms=5000)
        self.assertLess(time.monotonic() - start, 0.2)
        self.assertEqual(len(page['events']), 2)

    async def test_a_waiting_reader_follows_a_live_recorder_across_rotation(self):
        recorder = Recorder(self.journal, self.worker, 'g', 'codex')
        recorder.limits['log_bytes'] = 3000
        recorder.begin('t', 1)
        seen = []

        async def produce():
            for number in range(40):
                recorder.emit('message.completed', dict(text='n%d ' % number + 'x' * 100, is_final_candidate=False))
                await asyncio.sleep(0.005)
            recorder.terminal('completed', 'ok')
            await recorder.flush()

        producer = asyncio.create_task(produce())
        cursor = 0
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            page = await self.reader.wait(cursor, wait_ms=500, limit=7)
            seen.extend(e['seq'] for e in page['events'])
            cursor = page['next_seq']
            if page['events'] and page['events'][-1]['kind'] == 'task.terminal':
                break
        await producer
        self.assertEqual(seen, list(range(1, len(seen) + 1)))
        self.assertEqual(len(seen), 43)
        self.assertTrue(store.segments(self.journal.root, self.worker))


if __name__ == '__main__':
    unittest.main()
