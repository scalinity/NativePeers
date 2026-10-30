"""Reading a worker's activity log: pages after a seq, an optional item filter, a long-poll wait, rotated segments included.

The reader never blocks the writer and never writes. A torn final line is skipped until it completes. A `gap` says the
requested range is gone (for example, retention removed old segments), and reading continues from the oldest event kept.
"""
from __future__ import annotations

import asyncio
import gzip
import os
from pathlib import Path
import time

from . import store
from .config import LONG_POLL_CAP_MS, PAGE_BYTES, PAGE_EVENTS

SEEK_THRESHOLD = 1 << 20
TAIL_BYTES = 256 * 1024


class Reader:
    def __init__(self, root, worker_id):
        self.root = Path(root)
        self.worker_id = store.check_id(worker_id)

    def files(self):
        return store.logs(self.root, self.worker_id)

    def signature(self):
        try:
            info = store.active_path(self.root, self.worker_id).stat()
            active = (info.st_ino, info.st_size)
        except OSError:
            active = None
        return active, tuple(n for n, _ in store.segments(self.root, self.worker_id))

    def last_seq(self):
        """seq of the newest complete event in the active file, or None when it is empty or not yet readable."""
        path = store.active_path(self.root, self.worker_id)
        try:
            with open(path, 'rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                stream.seek(max(0, size - TAIL_BYTES))
                lines = stream.read().split(b'\n')[:-1]
        except OSError:
            return None
        for line in reversed(lines):
            event = store.parse(line)
            if event:
                return event['seq']
        return None

    def line_start(self, stream, pos):
        if pos == 0:
            return 0
        stream.seek(pos - 1)
        if stream.read(1) == b'\n':
            return pos
        stream.readline()
        return stream.tell()

    def offset_after(self, stream, size, after):
        """Byte offset of the first complete line whose seq exceeds `after` (binary search; seq grows along the file)."""
        low, high = 0, size
        while low < high:
            mid = (low + high) // 2
            start = self.line_start(stream, mid)
            stream.seek(start)
            line = stream.readline()
            if start >= size or not line.endswith(b'\n'):
                high = mid
                continue
            event = store.parse(line)
            if event and event['seq'] > after:
                high = mid
            else:
                low = start + len(line)
        return low

    def iter_file(self, path, after):
        """(event, line length) for each complete line of one file with seq above `after`."""
        try:
            if path.suffix == '.gz':
                with gzip.open(path, 'rb') as stream:
                    for line in stream:
                        event = store.parse(line)
                        if event and event['seq'] > after:
                            yield event, len(line)
                return
            with open(path, 'rb') as stream:
                size = os.fstat(stream.fileno()).st_size
                if size > SEEK_THRESHOLD:
                    stream.seek(self.offset_after(stream, size, after))
                while True:
                    line = stream.readline()
                    if not line.endswith(b'\n'):
                        return
                    event = store.parse(line)
                    if event and event['seq'] > after:
                        yield event, len(line)
        except (OSError, EOFError):
            return

    def read(self, after=0, *, limit=PAGE_EVENTS, item=None, max_bytes=PAGE_BYTES):
        """One page of events with seq above `after`. `next_seq` is the cursor to pass next, also across filtered-out events."""
        limit = max(1, min(int(limit), PAGE_EVENTS))
        after = max(0, int(after))
        files = self.files()
        firsts = [store.first_seq(path) for path in files]
        known = [first for first in firsts if first is not None]
        gap = dict(from_seq=after + 1, to_seq=known[0] - 1, reason='range-unavailable') if known and after + 1 < known[0] else None
        events, used, last, more = [], 0, after, False
        for index, path in enumerate(files):
            following = next((first for first in firsts[index + 1:] if first is not None), None)
            if following is not None and following <= after + 1:
                continue
            for event, length in self.iter_file(path, last):
                if item is not None and event.get('item') != item:
                    last = event['seq']
                    continue
                if len(events) >= limit or events and used + length > max_bytes:
                    more = True
                    break
                events.append(event)
                used += length
                last = event['seq']
            if more:
                break
        return dict(events=events, next_seq=last, last_seq=self.last_seq(), more=more, gap=gap)

    async def wait(self, after=0, *, wait_ms=0, limit=PAGE_EVENTS, item=None, max_bytes=PAGE_BYTES):
        """`read`, then up to `wait_ms` (capped) for new events. Polls file identity and size at a backing-off interval, so an idle wait costs almost nothing."""
        deadline = time.monotonic() + max(0, min(int(wait_ms), LONG_POLL_CAP_MS)) / 1000
        delay, seen = 0.025, None
        while True:
            signature = self.signature()
            if signature != seen:
                seen = signature
                page = self.read(after, limit=limit, item=item, max_bytes=max_bytes)
                after = page['next_seq']
                if page['events'] or page['gap'] or page['more']:
                    return dict(page, timed_out=False)
                delay = 0.025
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return dict(page, timed_out=True)
            await asyncio.sleep(min(delay, remaining))
            delay = min(delay * 1.6, 0.2)


def index(journal, worker_id, limit=64):
    """Turn boundaries from the journal's activity_index: where each task generation starts and ends in the log."""
    return journal.all('SELECT generation,task_id,first_seq,last_seq,terminal_seq FROM activity_index WHERE worker_id=? ORDER BY first_seq DESC LIMIT ?',
                       (worker_id, limit))[::-1]


def listing(journal, worker_id):
    """The files that hold a worker's activity log, for cleanup planning. Nothing here removes one.

    A log is deletable only for a worker that failed before it had a native history; an active or resumable worker keeps it.
    """
    files = []
    for path in store.logs(journal.root, worker_id):
        try:
            files.append(dict(path=str(path), bytes=path.stat().st_size))
        except OSError:
            continue
    row = journal.one('SELECT state,native_id FROM workers WHERE worker_id=?', (worker_id,))
    deletable = bool(row and row['state'] in ('FAILED', 'EXITED') and not row['native_id'])
    return dict(worker_id=worker_id, files=files, bytes=sum(f['bytes'] for f in files), deletable=deletable, removed=False,
                reason=None if deletable else 'active or resumable worker: the log is kept')
