"""Antigravity `--output-format stream-json` events to activity emissions.

Shapes follow the headless CLI's documented stream (one `init`, `step_update` events, one `result`): `agent_response`
steps carry `text_delta`, `tool` steps go ACTIVE then DONE with `tool_info` {name, parameters, output}, steps may carry
per-step `usage`. No recorded worker stream exists yet, so tests use documented shapes; unknown step types become one
`gap` each.
"""
from __future__ import annotations

from .events import Emit, one_line, relative

KINDS = dict(run_command='exec', send_command_input='exec', write_to_file='edit', replace_file_content='edit', multi_replace_file_content='edit',
             view_file='read', view_file_outline='read', view_code_item='read', list_dir='read', grep_search='search', find_by_name='search',
             codebase_search='search', read_url_content='web', search_web='web', browser='web')
EDITS = ('write_to_file', 'replace_file_content', 'multi_replace_file_content')
QUIET = ('user_input', 'checkpoint', 'system')


def tool_kind(name):
    return 'mcp' if '__' in name or name.startswith('mcp_') else KINDS.get(name, 'other')


class Antigravity:
    def __init__(self, cwd=None):
        self.cwd = cwd
        self.started = set()
        self.unmapped = set()
        self.native_status = None
        self.terminal_event = None

    def feed(self, event):
        kind = event.get('event')
        if kind == 'init':
            self.cwd = (event.get('init') or {}).get('cwd') or self.cwd
            return []
        if kind == 'result':
            self.native_status = (event.get('result') or {}).get('status')
            self.terminal_event = 'result'
            return []
        if kind != 'step_update':
            return [] if kind in ('user',) else self.unknown('event/' + str(kind))
        return list(self.step(event.get('step_update') or {}))

    def unknown(self, name):
        if name in self.unmapped:
            return []
        self.unmapped.add(name)
        return [Emit('gap', dict(reason='unmapped', event=name), None, name)]

    def step(self, step):
        item, kind, state = 'step%s' % step.get('step_index'), step.get('step_type'), step.get('state')
        usage = step.get('usage')
        if kind == 'agent_response':
            if isinstance(step.get('text_delta'), str) and step['text_delta']:
                yield Emit('message.delta', dict(text=step['text_delta']), item, 'step_update/agent_response')
        elif kind == 'tool':
            yield from self.tool(step, item, state)
        elif kind not in QUIET:
            yield from self.unknown('step_update/' + str(kind))
        if isinstance(usage, dict) and state == 'DONE':
            data = {name: usage[key] for key, name in (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
                    ('thinking_tokens', 'reasoning_tokens'), ('cache_read_tokens', 'cached_input_tokens')) if isinstance(usage.get(key), int)}
            if data:
                yield Emit('usage', dict(data, cumulative=False), item, 'step_update/usage')

    def tool(self, step, item, state):
        info = step.get('tool_info') if isinstance(step.get('tool_info'), dict) else {}
        name = str(step.get('tool_name') or info.get('name') or 'tool')
        params = info.get('parameters') if isinstance(info.get('parameters'), dict) else {}
        if item not in self.started:
            self.started.add(item)
            target = next((params[k] for k in ('CommandLine', 'TargetFile', 'AbsolutePath', 'SearchPath', 'Query', 'Url', 'DirectoryPath') if isinstance(params.get(k), str)), name)
            title = one_line(relative(target, self.cwd))
            yield Emit('tool.started', dict(kind=tool_kind(name), title=title, input=params), item, 'step_update/tool:' + name)
        if state != 'DONE':
            return
        failed = bool(info.get('error')) or str(info.get('status', '')).lower() in ('error', 'failed')
        data = dict(status='error' if failed else 'ok', output=str(info.get('output') or info.get('error') or ''))
        if isinstance(step.get('duration_seconds'), (int, float)):
            data['duration_ms'] = round(step['duration_seconds'] * 1000)
        yield Emit('tool.completed', data, item, 'step_update/tool:' + name)
        path = params.get('TargetFile') or params.get('AbsolutePath')
        if not failed and name in EDITS and isinstance(path, str):
            yield Emit('files.changed', dict(paths=[relative(path, self.cwd)], stats=dict(added=0, removed=0), diff=dict(text='')), item, 'step_update/tool:' + name)
