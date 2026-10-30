"""Grok Build stream events to activity emissions.

Shapes come from the controller's existing parser (nativepeers/grok_worker.py) and the recorded 1.0.70 stream
(tests/fixtures/grok): `text` chunks, `tool_call` and `tool_call_update` keyed by `toolCallId`, `usage`, and one
terminal `end` or `error`. Unknown event types become one `gap` each, never silence.
"""
from __future__ import annotations

from .events import Emit, diff_stats, one_line, relative

KINDS = dict(read_file='read', list_dir='read', view='read', grep='search', glob='search', find='search', search='search',
             write='edit', search_replace='edit', edit='edit', run_terminal_cmd='exec', bash='exec', shell='exec', web_fetch='web', web_search='web')
NATIVE_KINDS = dict(read='read', edit='edit', search='search', execute='exec', fetch='web', think='other', other='other')
EDITS = ('write', 'search_replace', 'edit')
QUIET = ('available_commands', 'session_info', 'plan')


def tool_kind(name, kind):
    if '__' in name:
        return 'mcp'
    return KINDS.get(name) or NATIVE_KINDS.get(kind or '', 'other')


def output_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('output', 'stdout', 'content', 'text', 'result'):
            if isinstance(value.get(key), str):
                return value[key]
    return '' if value is None else str(value)


class Grok:
    def __init__(self, cwd=None):
        self.cwd = cwd
        self.tools = {}
        self.unmapped = set()
        self.native_status = None
        self.terminal_event = None
        self.texts = 0

    def feed(self, event):
        kind = event.get('type')
        if kind == 'text' and isinstance(event.get('data'), str) and event['data']:
            return [Emit('message.delta', dict(text=event['data']), 'text', 'text')]
        if kind == 'tool_call':
            return list(self.tool_call(event))
        if kind == 'tool_call_update':
            return list(self.tool_update(event))
        if kind == 'usage':
            # The recorded 1.0.70 stream nests the turn's totals under `usage`.
            usage = event.get('usage') if isinstance(event.get('usage'), dict) else {}
            data = {name: usage[key] for key, name in (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
                    ('cache_read_input_tokens', 'cached_input_tokens'), ('reasoning_tokens', 'reasoning_tokens')) if isinstance(usage.get(key), int)}
            return [Emit('usage', dict(data, cumulative=True), None, 'usage')] if data else []
        if kind in ('end', 'error'):
            self.native_status = event.get('stopReason') or kind
            self.terminal_event = kind
            return []
        if kind in QUIET or kind in self.unmapped:
            return []
        self.unmapped.add(kind)
        return [Emit('gap', dict(reason='unmapped', event=str(kind)), None, str(kind))]

    def title(self, name, data):
        for key in ('command', 'path', 'file_path', 'pattern', 'query', 'url'):
            if isinstance(data.get(key), str):
                return one_line(relative(data[key], self.cwd) if key in ('path', 'file_path') else data[key])
        return name

    def tool_call(self, event):
        item, name = event.get('toolCallId'), str(event.get('toolName') or 'tool')
        data = event.get('rawInput') if isinstance(event.get('rawInput'), dict) else {}
        if not item or item in self.tools:
            return
        self.tools[item] = dict(name=name, input=data)
        yield Emit('tool.started', dict(kind=tool_kind(name, event.get('kind')), title=self.title(name, data), input=data), item, 'tool_call:' + name)
        if event.get('status') in ('completed', 'failed'):
            yield from self.tool_update(event)

    def tool_update(self, event):
        item, status = event.get('toolCallId'), event.get('status')
        use = self.tools.get(item)
        if not use or status not in ('completed', 'failed', 'canceled', 'cancelled'):
            return
        del self.tools[item]
        state = 'ok' if status == 'completed' else 'interrupted' if status in ('canceled', 'cancelled') else 'error'
        yield Emit('tool.completed', dict(status=state, output=output_text(event.get('rawOutput'))), item, 'tool_call_update')
        if state == 'ok' and use['name'] in EDITS:
            data, path = use['input'], use['input'].get('path') or use['input'].get('file_path')
            if isinstance(path, str):
                if isinstance(data.get('old_string'), str) and isinstance(data.get('new_string'), str):
                    text = '@@\n' + '\n'.join(['-' + line for line in data['old_string'].splitlines()] + ['+' + line for line in data['new_string'].splitlines()])
                else:
                    text = '@@\n' + '\n'.join('+' + line for line in str(data.get('content', '')).splitlines())
                yield Emit('files.changed', dict(paths=[relative(path, self.cwd)], stats=diff_stats(text), diff=dict(text=text)), item, 'tool_call_update')
