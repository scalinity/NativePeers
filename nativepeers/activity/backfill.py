"""Activity rebuilt from native histories, read-only, for workers recorded before the activity log existed.

Claude transcripts (`<CLAUDE_CONFIG_DIR>/projects/*/<native id>.jsonl`) feed the Claude normalizer, adapting the
transcript's `toolUseResult` to stream-json's `tool_use_result`. Codex rollouts (`<CODEX_HOME>/sessions/**/
*<thread id>.jsonl`) carry their own completed-item shapes, mapped here. Other providers yield one gap. Every event
is shaped and redacted like a recorded one and carries `source.event: 'backfill'`; native histories are never written.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .claude import Claude
from .config import limits
from .events import SCHEMA, Emit, diff_stats, one_line, relative
from .recorder import shape

MAX_LINES = 200000


def _lines(path):
    with Path(path).open(encoding='utf-8', errors='replace') as stream:
        for n, line in enumerate(stream):
            if n >= MAX_LINES:
                return
            try:
                yield json.loads(line)
            except ValueError:
                continue


def claude_emissions(path, cwd=None):
    normalizer = Claude(cwd)
    for row in _lines(path):
        if row.get('type') not in ('assistant', 'user') or row.get('isSidechain') and row.get('agentId'):
            continue
        if normalizer.cwd is None and isinstance(row.get('cwd'), str):
            normalizer.cwd = row['cwd']
        event = dict(row)
        if 'toolUseResult' in event and 'tool_use_result' not in event:
            event['tool_use_result'] = event['toolUseResult']
        yield from normalizer.feed(event)


def codex_emissions(path, cwd=None):
    total = None
    for row in _lines(path):
        payload = row.get('payload') or {}
        if row.get('type') == 'event_msg' and payload.get('type') == 'token_count':
            usage = ((payload.get('info') or {}).get('total_token_usage')) or {}
            total = {name: usage[key] for key, name in (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
                     ('cached_input_tokens', 'cached_input_tokens'), ('reasoning_output_tokens', 'reasoning_tokens')) if isinstance(usage.get(key), int)} or total
            continue
        if row.get('type') != 'event_msg' or payload.get('type') != 'item_completed':
            continue
        item = payload.get('item') or {}
        kind, item_id = item.get('type'), item.get('id')
        if kind == 'AgentMessage':
            text = '\n'.join(c.get('text', '') for c in item.get('content') or [] if isinstance(c, dict))
            if text.strip():
                yield Emit('message.completed', dict(text=text, is_final_candidate=item.get('phase') in (None, 'final_answer')), item_id, 'item_completed:AgentMessage')
        elif kind == 'CommandExecution':
            command = item.get('command')
            command = ' '.join(command) if isinstance(command, list) else str(command or '')
            yield Emit('tool.started', dict(kind='exec', title=one_line(command), input=dict(command=command, cwd=item.get('cwd'))), item_id, 'item_completed:CommandExecution')
            duration = item.get('duration') if isinstance(item.get('duration'), dict) else {}
            data = dict(status='ok' if item.get('exit_code') == 0 else 'error', output=str(item.get('aggregated_output') or ''))
            if isinstance(item.get('exit_code'), int):
                data['exit_code'] = item['exit_code']
            if duration:
                data['duration_ms'] = int(duration.get('secs', 0)) * 1000 + int(duration.get('nanos', 0)) // 1000000
            yield Emit('tool.completed', data, item_id, 'item_completed:CommandExecution')
        elif kind == 'FileChange':
            changes = item.get('changes') if isinstance(item.get('changes'), dict) else {}
            paths = [relative(p, cwd) for p in changes]
            yield Emit('tool.started', dict(kind='edit', title=one_line(', '.join(paths[:3])), input=dict(paths=paths)), item_id, 'item_completed:FileChange')
            ok = item.get('status') in (None, 'completed', 'Completed')
            yield Emit('tool.completed', dict(status='ok' if ok else 'error', output=str(item.get('stdout') or item.get('stderr') or '')), item_id, 'item_completed:FileChange')
            text = '\n'.join(str(c.get('unified_diff') or c.get('content') or '') for c in changes.values() if isinstance(c, dict))
            if ok:
                yield Emit('files.changed', dict(paths=paths, stats=diff_stats(text), diff=dict(text=text)), item_id, 'item_completed:FileChange')
    if total:
        yield Emit('usage', dict(total, cumulative=True), None, 'token_count')


def history(provider, home, native_id):
    """The native history file of one owned worker, or None."""
    if not native_id or '/' in native_id:
        return None
    root = Path(home)
    pattern = ('projects/*/%s.jsonl' if provider == 'claude' else 'sessions/**/*%s*.jsonl' if provider == 'codex' else None)
    if not pattern:
        return None
    return next(iter(sorted(root.glob(pattern % native_id))), None)


def backfill(journal, row, home):
    """Events for one worker from its native history, seq from 1, ending in the task's recorded outcome."""
    provider, worker_id = row['provider'], row['worker_id']
    found = history(provider, home, row.get('native_id'))
    stamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    bounds = limits(journal.root)
    task = journal.one('SELECT task_id,generation,state FROM tasks WHERE worker_id=? ORDER BY rowid DESC LIMIT 1', (worker_id,))
    out = []

    def add(kind, data, item=None):
        out.append(dict(schema=SCHEMA, seq=len(out)+1, at=stamp, worker_id=worker_id, group_id=row['group_id'], task_id=task['task_id'] if task else None,
                        generation=task['generation'] if task else row['generation'], native_turn=None, item=item, kind=kind,
                        data=shape(kind, data, bounds), source=dict(provider=provider, event='backfill')))
    add('task.started', dict(origin='controller', client_request_id=None, input_preview=''))
    if found is None:
        add('gap', dict(reason='no-history-reader' if provider not in ('claude', 'codex') else 'history-not-found'))
    else:
        source = claude_emissions(found, row.get('cwd')) if provider == 'claude' else codex_emissions(found, row.get('cwd'))
        for emission in source:
            add(emission.kind, emission.data, emission.item)
    state = (task['state'] if task else 'COMPLETED').lower()
    status = state if state in ('completed', 'interrupted', 'failed', 'canceled', 'timed_out') else 'interrupted'
    add('task.terminal', dict(status=status, final_text=next((e['data'].get('text', '') for e in reversed(out) if e['kind'] == 'message.completed'), '')))
    return out
