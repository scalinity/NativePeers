"""Vocabulary of nativepeers.activity.v1 and the shape normalizers hand to the recorder."""
from __future__ import annotations

import os
import re
from typing import NamedTuple

SCHEMA = 'nativepeers.activity.v1'
KINDS = ('task.started', 'message.delta', 'message.completed', 'reasoning.delta', 'tool.started', 'tool.progress', 'tool.completed',
         'files.changed', 'plan.updated', 'usage', 'input.queued', 'input.accepted', 'task.state', 'fallback.started', 'task.terminal', 'gap')
TOOL_KINDS = ('exec', 'edit', 'read', 'search', 'web', 'mcp', 'plan', 'agent', 'other')
TOOL_STATUSES = ('ok', 'error', 'declined', 'interrupted')
TERMINAL_STATUSES = ('completed', 'interrupted', 'failed', 'canceled', 'timed_out')
ORIGINS = ('parent-model', 'human', 'controller', 'fallback')
TEXT_KINDS = ('message.delta', 'message.completed', 'reasoning.delta', 'task.terminal')


class Emit(NamedTuple):
    """One normalized observation: `event` is the provider event name recorded in `source.event`."""
    kind: str
    data: dict
    item: str | None = None
    event: str | None = None


def one_line(text, limit=200):
    text = re.sub(r'\s+', ' ', text if isinstance(text, str) else '' if text is None else str(text)).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def relative(path, cwd):
    """Path relative to the worker's cwd when it lies beneath it (macOS spells the same directory with and without /private)."""
    if not isinstance(path, str) or not cwd:
        return path
    for base in {cwd.rstrip('/'), cwd.rstrip('/').removeprefix('/private')}:
        if base and path.startswith(base + '/'):
            return os.path.relpath(path, base)
    return path


def diff_stats(text):
    """Added and removed line counts of unified-diff text; file headers are not counted."""
    added = removed = 0
    for line in text.splitlines():
        if line.startswith('+') and not line.startswith('+++'):
            added += 1
        elif line.startswith('-') and not line.startswith('---'):
            removed += 1
    return dict(added=added, removed=removed)


def count_lines(text):
    return len(text.splitlines())
