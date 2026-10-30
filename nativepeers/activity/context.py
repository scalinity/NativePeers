"""What the controller knows about a task and when the recorder is told. No failure here may reach the task.

Outcome mapping. `task.terminal.status` is the controller's own status, unchanged: completed, failed, canceled, timed_out or
interrupted. `canceled` is what the controller records for a cancel request, and also for a Codex turn the provider itself ends
as `interrupted` (`Worker.codex_turn` maps that native status to canceled). `interrupted` is what it records when the controller
or its supervisor is lost mid-task (DELIVERY_UNKNOWN_AFTER_OWNER_LOSS), and what the recorder writes for a task it finds
unfinished after a crash. The provider's own word is kept in `task.terminal.data.native_status` (Codex `turn.status`, Claude
`result.terminal_reason` or `subtype`). A `canceled` terminal with `native_status: interrupted` and no `error` is a provider-side
interrupt nobody requested; a cancel request carries `error.code` USER_CANCELED. The explicit interrupt path (M2) will record
`interrupted` itself.
"""
from __future__ import annotations

import json

from .events import ORIGINS

OUTCOMES = ('completed', 'interrupted', 'canceled', 'timed_out')
USAGE_KEYS = (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'), ('cache_read_input_tokens', 'cached_input_tokens'),
              ('cached_input_tokens', 'cached_input_tokens'), ('reasoning_tokens', 'reasoning_tokens'))


def fallback_context(journal, row, task_id, attempt):
    """From and to of a provider fallback, and why the earlier attempt ended: its recorded result, never a guess."""
    prior = journal.one('SELECT a.result_artifact_id,w.provider,COALESCE(w.model_observed,w.model_requested) AS model FROM task_attempts a '
                        'JOIN workers w USING(worker_id) WHERE a.task_id=? AND a.attempt_index<? ORDER BY a.attempt_index DESC LIMIT 1', (task_id, attempt))
    if not prior:
        return None
    reason = 'unknown'
    try:
        path = journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?', (prior['result_artifact_id'],))['relative_path']
        reason = ((json.loads((journal.root / 'artifacts' / path).read_text()).get('error') or {}).get('code')) or reason
    except Exception:
        pass
    return dict(**{'from': dict(provider=prior['provider'], model=prior['model'])}, to=dict(provider=row['provider'], model=row['model_requested']), reason=reason)


def begin_task(recorder, journal, row, task_id, task_spec, text):
    """Start the task's generation. Journal lookups only enrich it: when one fails the task still starts, with defaults."""
    attempt = task_spec.get('attempt_index', 0) if isinstance(task_spec, dict) else 0
    origin = task_spec.get('origin') if task_spec.get('origin') in ORIGINS else 'fallback' if attempt else 'parent-model'
    request = fallback = None
    try:
        request = journal.one('SELECT client_request_id FROM resume_requests WHERE task_id=?', (task_id,))
        if not request and task_spec.get('initial'):
            request = journal.one('SELECT client_request_id FROM spawn_requests WHERE group_id=? LIMIT 1', (row['group_id'],))
        if attempt:
            fallback = fallback_context(journal, row, task_id, attempt)
    except Exception:
        pass
    recorder.begin(task_id, row['generation'], origin=origin, client_request_id=request['client_request_id'] if request else None, input_preview=text)
    if fallback:
        recorder.emit('fallback.started', fallback, event='controller/fallback', critical=True)


def generic_usage(usage):
    if not isinstance(usage, dict):
        return None
    data = {name: usage[key] for key, name in USAGE_KEYS if type(usage.get(key)) is int and usage[key] >= 0}
    return dict(data, cumulative=False) if data else None


def terminal_error(error):
    if not isinstance(error, dict):
        return None
    native = error.get('native_error')
    return dict(code=error.get('code'), message=error.get('message') or (native.get('message') if isinstance(native, dict) else None) or error.get('code'))


def finish_task(recorder, provider, result, artifact):
    """Close the task generation once the controller has decided and recorded its outcome. Synchronous: the caller must not gain an await point."""
    try:
        if provider in ('antigravity', 'grok'):
            if result.get('text') and result.get('status') == 'completed':
                recorder.emit('message.completed', dict(text=result['text'], is_final_candidate=True), event='controller/result')
            usage = generic_usage(result.get('usage'))
            if usage:
                recorder.emit('usage', usage, event='controller/result')
    except Exception:
        pass
    try:
        recorder.terminal(result['status'] if result.get('status') in OUTCOMES else 'failed', result.get('text') or '',
                          result_artifact_id=artifact, error=terminal_error(result.get('error')))
    except Exception:
        pass
