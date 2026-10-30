"""Codex app-server notifications to activity emissions.

Method and item names are those of the leased runtime's generated schema (ServerNotification.json, ThreadItem);
tests/fixtures/activity/codex/README.md records the executable and the bundle digests they came from.
Reasoning is exposed through summaries only: raw reasoning text and `reasoning/textDelta` are never recorded.
"""
from __future__ import annotations

import json

from .events import Emit, count_lines, diff_stats, one_line, relative

PROGRESS_BYTES = 16384
IGNORED_ITEMS = {'userMessage', 'hookPrompt', 'functionCallOutput', 'contextCompaction', 'enteredReviewMode', 'exitedReviewMode',
                 'subAgentActivity', 'sleep', 'imageGeneration'}
STATUS = {'completed': 'ok', 'failed': 'error', 'declined': 'declined', 'inProgress': 'interrupted'}
PLAN_STATUS = {'pending': 'pending', 'inProgress': 'in_progress', 'completed': 'completed'}
USAGE_FIELDS = (('inputTokens', 'input_tokens'), ('outputTokens', 'output_tokens'),
                ('cachedInputTokens', 'cached_input_tokens'), ('reasoningOutputTokens', 'reasoning_tokens'))


def mcp_text(item):
    if isinstance(item.get('error'), dict):
        return str(item['error'].get('message', ''))
    result = item.get('result') or {}
    texts = [block['text'] for block in result.get('content') or [] if isinstance(block, dict) and isinstance(block.get('text'), str)]
    if texts:
        return '\n'.join(texts)
    return json.dumps(result['structuredContent'], ensure_ascii=False) if result.get('structuredContent') is not None else ''


class Codex:
    def __init__(self, cwd=None):
        self.cwd = cwd
        self.turn = None
        self.open = {}
        self.streamed = set()
        self.said = set()
        self.summarised = {}
        self.progress = {}
        self.changes = {}
        self.state = 'RUNNING'
        self.baseline = None
        self.native_status = None
        self.terminal_event = None
        self.unmapped = set()
        self.handlers = {'item/started': self.item_started, 'item/completed': self.item_completed,
                         'item/agentMessage/delta': self.message_delta, 'item/reasoning/summaryTextDelta': self.reasoning_delta,
                         'item/commandExecution/outputDelta': self.output_delta, 'item/mcpToolCall/progress': self.mcp_progress,
                         'item/fileChange/patchUpdated': self.patch_updated, 'turn/plan/updated': self.plan_updated,
                         'thread/tokenUsage/updated': self.token_usage, 'thread/status/changed': self.status_changed,
                         'turn/completed': self.turn_completed}

    def feed(self, event):
        params = event.get('params') or {}
        scoped = params.get('turnId') or (params.get('turn') or {}).get('id')
        if self.turn and scoped and scoped != self.turn:
            return []
        handler = self.handlers.get(event.get('method'))
        return list(handler(params, event['method'])) if handler else []

    # --- items
    def item_started(self, params, method):
        item = params['item']
        if item['type'] in IGNORED_ITEMS or item['type'] in ('agentMessage', 'reasoning', 'plan'):
            return
        yield from self.start(item, method, params.get('startedAtMs'))

    def start(self, item, method, at=None):
        kind, title, data = self.describe(item)
        if kind is None:
            if item['type'] not in self.unmapped:
                self.unmapped.add(item['type'])
                yield Emit('gap', dict(reason='unmapped', event='item:' + item['type']), item['id'], method)
            return
        self.open[item['id']] = at
        if item['type'] == 'fileChange':
            self.changes[item['id']] = item.get('changes') or []
        yield Emit('tool.started', dict(kind=kind, title=title, input=data), item['id'], method)

    def describe(self, item):
        t = item['type']
        if t == 'commandExecution':
            return 'exec', one_line(item['command']), dict(command=item['command'], cwd=item.get('cwd'))
        if t == 'fileChange':
            paths = [relative(c['path'], self.cwd) for c in item.get('changes') or []]
            return 'edit', one_line('Edit ' + ', '.join(paths)) if paths else 'Edit files', dict(paths=paths)
        if t == 'mcpToolCall':
            return 'mcp', item['server'] + '.' + item['tool'], dict(server=item['server'], tool=item['tool'], arguments=item.get('arguments'))
        if t == 'webSearch':
            action = item.get('action') or {}
            return 'web', one_line(item['query'] or action.get('url') or 'web search'), dict(query=item['query'], action=action or None)
        if t == 'dynamicToolCall':
            name = (item.get('namespace') + '.' if item.get('namespace') else '') + item['tool']
            return 'other', name, dict(tool=item['tool'], namespace=item.get('namespace'), arguments=item.get('arguments'))
        if t == 'collabAgentToolCall':
            return 'agent', one_line(item['tool']), dict(tool=item['tool'], prompt=item.get('prompt'), model=item.get('model'),
                                                       receivers=item.get('receiverThreadIds'))
        if t == 'imageView':
            return 'read', relative(item['path'], self.cwd), dict(path=item['path'])
        return None, None, None

    def item_completed(self, params, method):
        item = params['item']
        t = item['type']
        if t == 'agentMessage':
            yield from self.said_message(item, method)
        elif t == 'reasoning':
            if item['id'] not in self.summarised and item.get('summary'):
                yield Emit('reasoning.delta', dict(text='\n\n'.join(item['summary'])), item['id'], method)
        elif t == 'plan':
            yield Emit('plan.updated', dict(items=[dict(text=item['text'], status='pending')]), item['id'], method)
        elif t not in IGNORED_ITEMS:
            if item['id'] not in self.open:
                yield from self.start(item, method)
            if item['id'] in self.open:
                yield from self.complete(item, method, params.get('completedAtMs'))

    def said_message(self, item, method):
        if item['id'] not in self.said:
            self.said.add(item['id'])
            yield Emit('message.completed', dict(text=item['text'], is_final_candidate=item.get('phase') in (None, 'final_answer')), item['id'], method)

    def complete(self, item, method, at):
        started = self.open.pop(item['id'], None)
        t = item['type']
        duration = item.get('durationMs')
        if duration is None and started is not None and at is not None:
            duration = max(0, at - started)
        data = dict(status=STATUS.get(item.get('status'), 'ok'))
        if t == 'commandExecution':
            code = item.get('exitCode')
            if data['status'] == 'ok' and code not in (None, 0):
                data['status'] = 'error'
            if code is not None:
                data['exit_code'] = code
            data['output'] = item.get('aggregatedOutput') or ''
        elif t == 'mcpToolCall':
            data['status'] = 'error' if item.get('status') == 'failed' or item.get('error') else data['status']
            data['output'] = mcp_text(item)
        elif t == 'dynamicToolCall':
            data['status'] = 'error' if item.get('status') == 'failed' or item.get('success') is False else data['status']
            data['output'] = '\n'.join(c['text'] for c in item.get('contentItems') or [] if isinstance(c, dict) and isinstance(c.get('text'), str))
        elif t == 'fileChange':
            changes = item.get('changes') or self.changes.get(item['id'], [])
            data['output'] = '\n'.join(c['kind']['type'] + ' ' + relative(c['path'], self.cwd) for c in changes)
        elif t == 'webSearch':
            data.update(status='ok', output='\n'.join(one_line(r) for r in (item.get('results') or [])[:20]))
        else:
            data['output'] = ''
        if duration is not None:
            data['duration_ms'] = duration
        yield Emit('tool.completed', data, item['id'], method)
        if t == 'fileChange' and data['status'] == 'ok' and (item.get('changes') or self.changes.get(item['id'])):
            yield self.files_changed(item.get('changes') or self.changes[item['id']], item['id'], method)

    def files_changed(self, changes, item, method):
        added = removed = 0
        parts = []
        for change in changes:
            kind, diff = change['kind']['type'], change.get('diff') or ''
            unified = '@@' in diff
            counts = diff_stats(diff) if unified or kind == 'update' else dict(added=count_lines(diff) if kind == 'add' else 0,
                                                                                removed=count_lines(diff) if kind == 'delete' else 0)
            added, removed = added + counts['added'], removed + counts['removed']
            parts.append('--- ' + relative(change['path'], self.cwd) + '\n' + diff)
        return Emit('files.changed', dict(paths=[relative(c['path'], self.cwd) for c in changes], stats=dict(added=added, removed=removed),
                                          diff=dict(text='\n'.join(parts))), item, method)

    # --- streams
    def message_delta(self, params, method):
        self.streamed.add(params['itemId'])
        yield Emit('message.delta', dict(text=params['delta']), params['itemId'], method)

    def reasoning_delta(self, params, method):
        previous = self.summarised.get(params['itemId'])
        self.summarised[params['itemId']] = params['summaryIndex']
        separator = '\n\n' if previous is not None and previous != params['summaryIndex'] else ''
        yield Emit('reasoning.delta', dict(text=separator + params['delta']), params['itemId'], method)

    def output_delta(self, params, method):
        used = self.progress.get(params['itemId'], 0)
        if used < PROGRESS_BYTES and params['itemId'] in self.open:
            self.progress[params['itemId']] = used + len(params['delta'].encode('utf-8', 'replace'))
            yield Emit('tool.progress', dict(text=params['delta']), params['itemId'], method)

    def mcp_progress(self, params, method):
        if params['itemId'] in self.open:
            yield Emit('tool.progress', dict(text=params['message']), params['itemId'], method)

    def patch_updated(self, params, method):
        if params['itemId'] in self.open:
            self.changes[params['itemId']] = params['changes']
        return ()

    # --- turn level
    def plan_updated(self, params, method):
        yield Emit('plan.updated', dict(items=[dict(text=s['step'], status=PLAN_STATUS.get(s['status'], 'pending')) for s in params['plan']]),
                   None, method)

    def token_usage(self, params, method):
        usage = params.get('tokenUsage') or {}
        total, last = usage.get('total') or {}, usage.get('last') or {}
        if self.baseline is None:
            self.baseline = {key: total.get(key, 0) - last.get(key, 0) for key, _ in USAGE_FIELDS}
        data = {name: max(0, total[key] - self.baseline[key]) for key, name in USAGE_FIELDS if key in total}
        if data:
            yield Emit('usage', dict(data, cumulative=True), None, method)

    def status_changed(self, params, method):
        status = params.get('status') or {}
        flags = status.get('activeFlags') or []
        if status.get('type') != 'active':
            return
        state = 'APPROVAL_BLOCKED' if 'waitingOnApproval' in flags else 'WAITING' if flags else 'RUNNING'
        if state != self.state:
            self.state = state
            yield Emit('task.state', dict(state=state, **(dict(reason=','.join(flags)) if flags else {})), None, method)

    def turn_completed(self, params, method):
        turn = params['turn']
        self.native_status = turn.get('status')
        self.terminal_event = method
        for item in turn.get('items') or []:
            if item['type'] == 'agentMessage':
                yield from self.said_message(item, method)
