"""On-disk layout of one worker's activity log: an append-only active file plus rotated gzip segments.

    $NPEER_HOME/activity/<worker_id>.ndjson          active, mode 0600, written only by the worker's controller
    $NPEER_HOME/activity/<worker_id>.<n>.ndjson.gz   older segments, n counting up from 1

The directory is mode 0700. `seq` is strictly increasing across the segments and the active file.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
import re

from ..files import private_directory

SEGMENT = re.compile(r'^(?P<id>.+)\.(?P<n>\d+)\.ndjson\.gz$')
TAIL_WINDOW = 1 << 20


def check_id(worker_id):
    if not isinstance(worker_id, str) or not worker_id or Path(worker_id).name != worker_id or worker_id.startswith('.'):
        raise ValueError('Invalid worker ID')
    return worker_id


def directory(root, *, create=False):
    path = Path(root) / 'activity'
    return private_directory(path) if create else path


def active_path(root, worker_id):
    return directory(root) / (check_id(worker_id) + '.ndjson')


def segments(root, worker_id):
    """Rotated gzip segments, oldest first, as [(n, path)]."""
    found = []
    base = directory(root)
    if base.is_dir():
        for path in base.iterdir():
            match = SEGMENT.match(path.name)
            if match and match['id'] == check_id(worker_id):
                found.append((int(match['n']), path))
    return sorted(found)


def logs(root, worker_id):
    """Every file of this worker's log, oldest first."""
    files = [path for _, path in segments(root, worker_id)]
    active = active_path(root, worker_id)
    if active.is_file():
        files.append(active)
    return files


def parse(line):
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return event if isinstance(event, dict) and type(event.get('seq')) is int else None


def first_seq(path):
    """seq of the first parsable event in a file, or None when it holds none."""
    opener = gzip.open if path.suffix == '.gz' else open
    try:
        with opener(path, 'rb') as stream:
            for _ in range(32):
                line = stream.readline()
                if not line:
                    return None
                event = parse(line)
                if event:
                    return event['seq']
    except (OSError, EOFError):
        return None
    return None


def last_seq_of_gzip(path):
    last = None
    try:
        with gzip.open(path, 'rb') as stream:
            for line in stream:
                event = parse(line)
                if event:
                    last = event['seq']
    except (OSError, EOFError):
        pass
    return last


def scan_tail(path):
    """State at the end of the active file: last seq, torn trailing bytes, the open task and its unpaired tools."""
    size = path.stat().st_size
    with open(path, 'rb') as stream:
        start = max(0, size - TAIL_WINDOW)
        stream.seek(start)
        data = stream.read()
    if start:
        data = data[data.find(b'\n') + 1:] if b'\n' in data else b''
    lines = data.split(b'\n')
    torn = len(lines.pop())
    state = dict(size=size, torn=torn, last_seq=None, task=None, tools={}, generation=None, complete=start == 0, saw_task=False)
    for line in lines:
        event = parse(line)
        if not event:
            continue
        state['last_seq'] = event['seq']
        kind = event.get('kind')
        if kind in ('task.started', 'task.terminal'):
            state['saw_task'] = True
        if kind == 'task.started':
            state.update(task=dict(task_id=event.get('task_id'), generation=event.get('generation'), group_id=event.get('group_id')), tools={})
        elif kind == 'task.terminal':
            state.update(task=None, tools={})
        elif kind == 'tool.started' and event.get('item'):
            state['tools'][event['item']] = event.get('native_turn')
        elif kind == 'tool.completed':
            state['tools'].pop(event.get('item'), None)
        if type(event.get('generation')) is int:
            state['generation'] = event['generation']
    return state
