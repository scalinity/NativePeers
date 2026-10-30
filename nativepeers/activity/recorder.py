"""The activity recorder: normalized events from one worker controller, appended to its log by a dedicated writer task.

`emit` never blocks and never raises, because the controller must keep reading the provider's stdout. A bounded buffer
absorbs bursts; past its bound events are dropped and one `gap` event records how many. Critical events (the task's
start, state, terminal, and the completions that close its tools) are never dropped. The controller is the only writer.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import gzip
import json
import os
import shutil
import stat
import time

from . import store
from .bounds import bound_data, output_block, scrub, size, text_block
from .config import limits as configured_limits
from .events import ORIGINS, SCHEMA, TERMINAL_STATUSES, TEXT_KINDS
from .redact import redact

UPSERT = ('INSERT INTO activity_index(worker_id,generation,task_id,first_seq,last_seq,terminal_seq) VALUES(?,?,?,?,?,?) '
          'ON CONFLICT(worker_id,generation,task_id) DO UPDATE SET last_seq=excluded.last_seq,terminal_seq=COALESCE(excluded.terminal_seq,terminal_seq)')
PREVIEW_BYTES = 2048
ENVELOPE_BYTES = 400


def stamp():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


class Task:
    def __init__(self, task_id, generation, normalizer, turn=False):
        self.task_id, self.generation, self.normalizer, self.turn = task_id, generation, normalizer, turn
        self.tools = {}
        self.closed = False
        self.faults = set()


def shape(kind, data, limits):
    """Redact and bound `data`: tool output and diffs are head-and-tail blocks, everything else is scrubbed."""
    tool_bytes, event_bytes = limits['tool_output_bytes'], limits['event_data_bytes']
    special = {}
    if kind == 'tool.completed':
        output = data.get('output')
        special['output'] = output_block(output.get('text', '') if isinstance(output, dict) else output, tool_bytes)
    elif kind == 'files.changed' and isinstance(data.get('diff'), dict):
        block = output_block(data['diff'].get('text', ''), tool_bytes)
        block.pop('bytes')
        special['diff'] = block
    shaped = scrub({k: v for k, v in data.items() if k not in special}, event_bytes if kind in TEXT_KINDS else tool_bytes)
    shaped.update(special)
    return bound_data(shaped, event_bytes)


class Recorder:
    def __init__(self, journal, worker_id, group_id, provider, *, lease=None, cwd=None, turn=None,
                 queue_events=4096, queue_bytes=16 << 20, fsync_interval=2.0):
        self.journal, self.root = journal, journal.root
        self.worker_id, self.group_id, self.provider = store.check_id(worker_id), group_id, provider
        self.lease, self.cwd, self.turn_source = lease if lease is not None else {}, cwd, turn
        self.limits = configured_limits(self.root)
        self.max_events, self.max_bytes, self.fsync_interval = queue_events, queue_bytes, fsync_interval
        self.buffer, self.buffered, self.gap_entry = deque(), 0, None
        self.seq = 0
        self.fd = None
        self.size = 0
        self.dirty = False
        self.sync_due = 0.0
        self.recovered = False
        self.dead = False
        self.closed = False
        self.stopping = False
        self.writer = None
        self.wake = None
        self.waiters = []
        self.enqueued = self.written = 0
        self.pending = {}
        self.floor = 0
        self.task = None
        self.counters = dict(dropped=0, errors=0, unattributed=0)

    # --- producing side: synchronous, non-blocking, never raises
    def begin(self, task_id, generation, *, origin='parent-model', client_request_id=None, input_preview=''):
        try:
            self.recover()
            if self.task and not self.task.closed:
                self.terminal('interrupted', error=dict(code='SUPERSEDED', message='A newer task generation started before this one ended'))
            if generation < self.floor:
                self.task = None
                self.counters['unattributed'] += 1
                self.emit('gap', dict(reason='stale-generation', generation=generation), critical=True, forced=True)
                return
            self.floor = generation
            self.task = Task(task_id, generation, self.make_normalizer())
            self.emit('task.started', dict(origin=origin if origin in ORIGINS else 'parent-model', client_request_id=client_request_id,
                                           input_preview=text_block(input_preview, PREVIEW_BYTES)[0]), event='controller/task', critical=True)
            self.emit('task.state', dict(state='RUNNING'), event='controller/task', critical=True)
        except Exception:
            self.counters['errors'] += 1

    def make_normalizer(self):
        if self.provider == 'codex':
            from .codex import Codex
            return Codex(self.cwd)
        if self.provider == 'claude':
            from .claude import Claude
            return Claude(self.cwd)
        if self.provider == 'grok':
            from .grok import Grok
            return Grok(self.cwd)
        if self.provider == 'antigravity':
            from .antigravity import Antigravity
            return Antigravity(self.cwd)
        return None

    def feed(self, event):
        task = self.task
        if not task or task.closed or task.normalizer is None:
            return
        try:
            if self.turn_source:
                task.normalizer.turn = self.turn_source()
            for emission in task.normalizer.feed(event):
                self.emit(emission.kind, emission.data, item=emission.item, event=emission.event)
        except Exception as error:
            name = str(event.get('method') or event.get('type')) if isinstance(event, dict) else 'event'
            self.counters['errors'] += 1
            if name not in task.faults:
                task.faults.add(name)
                self.emit('gap', dict(reason='normalizer-error', event=name, error=type(error).__name__))

    def emit(self, kind, data=None, *, item=None, event=None, critical=False, forced=False):
        try:
            task = self.task
            if self.dead or self.closed or (task is None and not forced) or (task is not None and task.closed and kind != 'gap'):
                self.counters['unattributed'] += 1
                return
            data = self.shape(kind, data or {})
            if task and kind == 'tool.started' and item:
                task.tools[item] = data.get('kind')
            elif task and kind == 'tool.completed':
                task.tools.pop(item, None)
            self.enqueue(self.envelope(kind, data, item, event, task), size(data) + ENVELOPE_BYTES, critical)
        except Exception:
            self.counters['errors'] += 1

    def shape(self, kind, data):
        return shape(kind, data, self.limits)

    def envelope(self, kind, data, item, event, task):
        source = dict(provider=self.provider)
        runtime = (self.lease.get('verified_runtime') or {}).get('sha256')
        if runtime:
            source['runtime_sha256'] = runtime
        source['event'] = event
        turn = task.turn if task and task.turn is not False else self.turn_source() if self.turn_source else None
        return dict(schema=SCHEMA, seq=0, at=stamp(), worker_id=self.worker_id, group_id=self.group_id,
                    task_id=task.task_id if task else None, generation=task.generation if task else None,
                    native_turn=turn, item=item, kind=kind, data=data, source=source)

    def enqueue(self, entry, nbytes, critical):
        if not critical and (len(self.buffer) >= self.max_events or self.buffered + nbytes > self.max_bytes):
            self.counters['dropped'] += 1
            if self.gap_entry is None:
                self.gap_entry = self.envelope('gap', dict(reason='overflow', dropped=0), None, 'recorder/overflow', self.task)
                self.buffer.append((self.gap_entry, ENVELOPE_BYTES))
                self.buffered += ENVELOPE_BYTES
                self.enqueued += 1
            self.gap_entry['data']['dropped'] += 1
            self.poke()
            return
        if entry['kind'] != 'gap':
            self.gap_entry = None
        self.buffer.append((entry, nbytes))
        self.buffered += nbytes
        self.enqueued += 1
        self.poke()

    def terminal(self, status, final_text='', *, result_artifact_id=None, error=None):
        """Close the current task generation: the only completion signal. Unpaired tools are closed as interrupted first."""
        task = self.task
        try:
            if not task or task.closed:
                return False
            for item in list(task.tools):
                self.emit('tool.completed', dict(status='interrupted', output=''), item=item, event='controller/terminal', critical=True)
            data = dict(status=status if status in TERMINAL_STATUSES else 'failed', final_text=final_text or '')
            if result_artifact_id:
                data['result_artifact_id'] = result_artifact_id
            if error:
                data['error'] = dict(code=str(error.get('code') or 'UNKNOWN'), message=redact(str(error.get('message') or error.get('code') or ''))[:1000])
            native = getattr(task.normalizer, 'native_status', None)
            if native:
                data['native_status'] = str(native)
            self.emit('task.terminal', data, event=getattr(task.normalizer, 'terminal_event', None) or 'controller/terminal', critical=True)
            task.closed = True
            return True
        except Exception:
            self.counters['errors'] += 1
            return False

    # --- consuming side
    def poke(self):
        if self.dead:
            return
        try:
            if self.wake is None:
                self.wake = asyncio.Event()
            if self.writer is None or self.writer.done():
                self.writer = asyncio.get_running_loop().create_task(self.run())
        except RuntimeError:
            return
        self.wake.set()

    async def flush(self, timeout=3.0):
        """Wait until everything queued so far is written and fsynced; False when the writer is gone or the wait times out."""
        if self.dead:
            return False
        self.recover()
        if not self.enqueued or self.written >= self.enqueued and not self.dirty:
            return True
        self.poke()
        if self.writer is None:
            return False
        future = asyncio.get_running_loop().create_future()
        self.waiters.append((self.enqueued, future))
        self.wake.set()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except (asyncio.TimeoutError, TimeoutError):
            return False

    async def close(self):
        if self.closed:
            return
        await self.flush()
        self.closed = self.stopping = True
        if self.writer is not None and not self.writer.done():
            self.wake.set()
            try:
                await asyncio.wait_for(self.writer, 5)
            except (asyncio.TimeoutError, TimeoutError, Exception):
                self.writer.cancel()
        self.release()

    def release(self):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    async def run(self):
        try:
            while True:
                if not self.buffer and not self.stopping:
                    self.wake.clear()
                    try:
                        await asyncio.wait_for(self.wake.wait(), max(0.0, self.sync_due - time.monotonic()) if self.dirty else None)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                batch, total = [], 0
                while self.buffer and len(batch) < 256 and total < (1 << 20):
                    entry, nbytes = self.buffer.popleft()
                    self.buffered -= nbytes
                    batch.append(entry)
                    total += nbytes
                if batch:
                    self.append(batch)
                    if self.size >= self.limits['log_bytes']:
                        await self.rotate()
                due = self.dirty and (self.waiters or time.monotonic() >= self.sync_due or self.stopping and not self.buffer
                                      or any(e['kind'] == 'task.terminal' for e in batch))
                if due:
                    await self.sync()
                self.resolve()
                if self.stopping and not self.buffer:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            self.dead = True
            self.counters['errors'] += 1
        finally:
            self.resolve(final=True)

    def resolve(self, final=False):
        keep = []
        for target, future in self.waiters:
            if not future.done():
                if final or self.written >= target and not self.dirty:
                    future.set_result(not self.dead)
                else:
                    keep.append((target, future))
        self.waiters = keep

    def append(self, batch):
        self.open_log()
        lines = []
        for event in batch:
            self.seq += 1
            event['seq'] = self.seq
            if event is self.gap_entry:
                self.gap_entry = None
            lines.append(json.dumps(event, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8', 'replace') + b'\n')
            key = (event['generation'], event['task_id'])
            if key[1] is not None:
                row = self.pending.setdefault(key, [self.seq, self.seq, None])
                row[1] = self.seq
                if event['kind'] == 'task.terminal':
                    row[2] = self.seq
        data = b''.join(lines)
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view):]
        self.size += len(data)
        self.written += len(batch)
        if not self.dirty:
            self.sync_due = time.monotonic() + self.fsync_interval
        self.dirty = True

    async def sync(self):
        await asyncio.to_thread(os.fsync, self.fd)
        self.dirty = False
        rows, self.pending = [(self.worker_id, g, t, *row) for (g, t), row in self.pending.items()], {}
        if rows:
            await asyncio.to_thread(self.write_index, rows)

    def write_index(self, rows):
        # A short-lived connection: the controller's own connection is bound to the loop thread. The log is the record; the index is a seek aid.
        from ..journal import Journal
        try:
            db = Journal.existing(self.root, timeout=.25)
        except Exception:
            self.counters['errors'] += 1
            return
        try:
            db.db.execute('BEGIN IMMEDIATE')
            db.db.executemany(UPSERT, rows)
            db.db.execute('COMMIT')
        except Exception:
            self.counters['errors'] += 1
        finally:
            db.close()

    # --- file lifecycle
    def open_log(self):
        if self.fd is not None:
            return
        self.recover()
        path = store.active_path(self.root, self.worker_id)
        store.directory(self.root, create=True)
        created = not path.exists()
        self.fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
            self.release()
            raise PermissionError('Unsafe activity log')
        self.size = info.st_size
        if created:
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def recover(self):
        """Continue from what is on disk: the last seq, a torn tail removed, and a crashed task generation closed first."""
        if self.recovered:
            return
        self.recovered = True
        try:
            path = store.active_path(self.root, self.worker_id)
            state = None
            if path.is_file():
                state = store.scan_tail(path)
                if state['torn']:
                    os.truncate(path, state['size'] - state['torn'])
                self.seq = state['last_seq'] or 0
            if not self.seq:
                parts = store.segments(self.root, self.worker_id)
                self.seq = (store.last_seq_of_gzip(parts[-1][1]) or 0) if parts else 0
            # The index can only be ahead of the files (a removed log): a seq is never reused, because consumers resume from a persisted cursor.
            indexed = self.journal.one('SELECT MAX(last_seq) AS last FROM activity_index WHERE worker_id=?', (self.worker_id,))
            self.seq = max(self.seq, (indexed or {}).get('last') or 0)
            open_task = state['task'] if state else None
            if state and state['generation'] is not None:
                self.floor = state['generation']
            if open_task is None:
                row = self.journal.one('SELECT generation,task_id FROM activity_index WHERE worker_id=? AND terminal_seq IS NULL ORDER BY first_seq DESC LIMIT 1',
                                       (self.worker_id,))
                open_task = dict(row, group_id=self.group_id) if row and state is not None and not state['saw_task'] and not state['complete'] else None
            if state and state['torn']:
                self.emit('gap', dict(reason='torn-tail', bytes=state['torn']), event='recorder/recover', critical=True, forced=True)
            if open_task:
                closing = Task(open_task['task_id'], open_task['generation'], None, turn=None)
                for item in (state['tools'] if state else {}):
                    self.enqueue(self.envelope('tool.completed', self.shape('tool.completed', dict(status='interrupted', output='')), item,
                                               'recorder/recover', closing), ENVELOPE_BYTES, True)
                error = dict(code='DELIVERY_UNKNOWN_AFTER_OWNER_LOSS', message='The previous controller ended before recording a terminal event')
                self.enqueue(self.envelope('task.terminal', self.shape('task.terminal', dict(status='interrupted', final_text='', error=error)), None,
                                           'recorder/recover', closing), ENVELOPE_BYTES, True)
        except Exception:
            self.counters['errors'] += 1

    async def rotate(self):
        """Compress the full active file into the next gzip segment, then switch to a fresh active file. Readers dedupe by seq."""
        await self.sync()
        parts = store.segments(self.root, self.worker_id)
        number = (parts[-1][0] if parts else 0) + 1
        active = store.active_path(self.root, self.worker_id)
        target = active.with_name('%s.%d.ndjson.gz' % (self.worker_id, number))

        def work():
            temporary = target.with_name(target.name + '.tmp')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as out, gzip.GzipFile(fileobj=out, mode='wb', filename='', mtime=0) as packed, open(active, 'rb') as source:
                shutil.copyfileobj(source, packed)
                packed.flush()
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, target)
            fresh = active.with_name(active.name + '.new')
            os.close(os.open(fresh, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
            os.replace(fresh, active)
            directory = os.open(active.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

        await asyncio.to_thread(work)
        self.release()
        self.size = 0
        self.open_log()
