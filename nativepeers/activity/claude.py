"""Claude `stream-json` events to activity emissions.

Shapes come from the installed CLI's own output (tests/fixtures/activity/claude): every content block is its own
`assistant` event, tool results arrive as `user` events carrying `tool_result` blocks plus a `tool_use_result` object,
and Write/Edit carry `structuredPatch` hunks. Tool names are compared as strings because builds differ in the tools
they ship. Workers launch without `--include-partial-messages`, so text is mirrored per message.
"""
from __future__ import annotations

from datetime import datetime
import re

from .events import Emit, count_lines, diff_stats, one_line, relative

KINDS = dict(Bash='exec', BashOutput='exec', KillShell='exec', Edit='edit', Write='edit', MultiEdit='edit', NotebookEdit='edit',
             Read='read', LS='read', Glob='search', Grep='search', WebFetch='web', WebSearch='web', Task='agent', Agent='agent', TodoWrite='plan')
EDITS = ('Edit', 'Write', 'MultiEdit', 'NotebookEdit')
PLAN_STATUS = dict(pending='pending', in_progress='in_progress', completed='completed')
DENIED = ('deny', 'denied', 'reject', 'ask')
EXIT = re.compile(r'^Exit code (-?\d+)')
USAGE_FIELDS = (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'), ('cache_read_input_tokens', 'cached_input_tokens'))


def tool_kind(name):
    return 'mcp' if name.startswith('mcp__') else KINDS.get(name, 'other')


def millis(stamp):
    try:
        return datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() * 1000
    except (AttributeError, ValueError):
        return None


def result_text(content):
    if isinstance(content, str):
        return content
    return '\n'.join(b['text'] for b in content or [] if isinstance(b, dict) and isinstance(b.get('text'), str))


def hunks_text(patch):
    return '\n'.join('@@ -%s,%s +%s,%s @@\n%s' % (h['oldStart'], h['oldLines'], h['newStart'], h['newLines'], '\n'.join(h['lines'])) for h in patch)


class Claude:
    def __init__(self, cwd=None):
        self.cwd = cwd
        self.tools = {}
        self.messages = set()
        self.unmapped = set()
        self.native_status = None
        self.terminal_event = None
        self.handlers = {'system': self.system, 'assistant': self.assistant, 'user': self.user, 'result': self.result}

    def feed(self, event):
        handler = self.handlers.get(event.get('type'))
        return list(handler(event)) if handler else []

    def system(self, event):
        if event.get('subtype') == 'init' and event.get('cwd'):
            self.cwd = event['cwd']
        return ()

    def assistant(self, event):
        message = event.get('message') or {}
        mid = message.get('id')
        if mid and mid not in self.messages:
            self.messages.add(mid)
            data = {name: message['usage'][key] for key, name in USAGE_FIELDS if isinstance((message.get('usage') or {}).get(key), int)}
            thinking = ((message.get('usage') or {}).get('output_tokens_details') or {}).get('thinking_tokens')
            if isinstance(thinking, int):
                data['reasoning_tokens'] = thinking
            if any(data.values()):
                yield Emit('usage', dict(data, cumulative=False), mid, 'assistant/usage')
        for block in message.get('content') or []:
            kind = block.get('type')
            if kind == 'text':
                if block.get('text', '').strip():
                    yield Emit('message.completed', dict(text=block['text'], is_final_candidate=message.get('stop_reason') == 'end_turn'), mid, 'assistant/text')
            elif kind == 'thinking':
                if block.get('thinking', '').strip():
                    yield Emit('reasoning.delta', dict(text=block['thinking']), mid, 'assistant/thinking')
            elif kind == 'tool_use':
                yield self.tool_use(block, event.get('timestamp'))
            elif kind not in ('redacted_thinking', 'image') and kind not in self.unmapped:
                self.unmapped.add(kind)
                yield Emit('gap', dict(reason='unmapped', event='assistant/' + str(kind)), mid, 'assistant/' + str(kind))

    def title(self, name, data):
        if name.startswith('mcp__'):
            return one_line('.'.join(name.split('__', 2)[1:]))
        if name == 'Bash':
            return one_line(data.get('description') or data.get('command'))
        if name in EDITS or name in ('Read', 'LS'):
            return one_line(relative(data.get('file_path') or data.get('notebook_path') or data.get('path'), self.cwd) or name)
        if name in ('Glob', 'Grep'):
            return one_line(data.get('pattern') or name)
        if name == 'WebFetch':
            return one_line(data.get('url') or name)
        if name == 'WebSearch':
            return one_line(data.get('query') or name)
        if name in ('Task', 'Agent'):
            return one_line(data.get('description') or data.get('prompt') or name)
        return 'Update todo list' if name == 'TodoWrite' else name

    def tool_use(self, block, stamp):
        name, data = block.get('name', ''), block.get('input') if isinstance(block.get('input'), dict) else {}
        self.tools[block['id']] = dict(name=name, input=data, at=millis(stamp))
        return Emit('tool.started', dict(kind=tool_kind(name), title=self.title(name, data), input=data), block['id'], 'assistant/tool_use:' + name)

    def user(self, event):
        content = (event.get('message') or {}).get('content')
        for block in content if isinstance(content, list) else ():
            if isinstance(block, dict) and block.get('type') == 'tool_result' and block.get('tool_use_id') in self.tools:
                yield from self.tool_result(block, event)

    def tool_result(self, block, event):
        use = self.tools.pop(block['tool_use_id'])
        name, text = use['name'], result_text(block.get('content'))
        meta = next((m for m in event.get('tool_result_meta') or [] if isinstance(m, dict) and m.get('id') == block['tool_use_id']), {})
        decision = (meta.get('permission_decision') or {}).get('decision')
        outcome = event.get('tool_use_result')
        failed = bool(block.get('is_error'))
        status = ('interrupted' if isinstance(outcome, dict) and outcome.get('interrupted') else
                  'declined' if failed and decision in DENIED else 'error' if failed else 'ok')
        data = dict(status=status, output=text)
        if name == 'Bash':
            found = EXIT.match(text) if failed else None
            if found or not failed:
                data['exit_code'] = int(found.group(1)) if found else 0
        ended = millis(event.get('timestamp'))
        if use['at'] is not None and ended is not None:
            data['duration_ms'] = max(0, round(ended - use['at']))
        source = 'user/tool_result:' + name
        yield Emit('tool.completed', data, block['tool_use_id'], source)
        if status != 'ok':
            return
        if name in EDITS and isinstance(outcome, dict):
            change = self.files_changed(use, outcome, block['tool_use_id'], source)
            if change:
                yield change
        elif name == 'TodoWrite':
            todos = [t for t in use['input'].get('todos') or [] if isinstance(t, dict)]
            yield Emit('plan.updated', dict(items=[dict(text=t.get('content', ''), status=PLAN_STATUS.get(t.get('status'), 'pending')) for t in todos]),
                       block['tool_use_id'], source)

    def files_changed(self, use, outcome, item, source):
        path = outcome.get('filePath') or use['input'].get('file_path') or use['input'].get('notebook_path')
        if not isinstance(path, str):
            return None
        patch = outcome.get('structuredPatch')
        content = outcome.get('content')
        if patch:
            text = hunks_text(patch)
            stats = diff_stats(text)
        elif isinstance(content, str):
            lines = count_lines(content)
            text, stats = ('@@ -0,0 +1,%d @@\n' % lines) + '\n'.join('+' + line for line in content.splitlines()), dict(added=lines, removed=0)
        else:
            text, stats = '', dict(added=0, removed=0)
        return Emit('files.changed', dict(paths=[relative(path, self.cwd)], stats=stats, diff=dict(text=text)), item, source)

    def result(self, event):
        self.native_status = event.get('terminal_reason') or event.get('subtype')
        self.terminal_event = 'result'
        return ()
