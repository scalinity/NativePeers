"""Bounded views with explicit omissions and immutable inspection routes."""
from collections import Counter
import json

from .errors import PeerError


def clip(value, limit=400):
    raw = str(value).encode('utf-8')
    return raw[:limit].decode('utf-8', errors='ignore') + ('…' if len(raw) > limit else '')


def phase(policy, task_id):
    return next((int(n) for n, ids in policy.get('phases', {}).items() if task_id in ids), None)


def lifecycle(status, policy):
    tasks = status['tasks']
    initial = set(policy.get('initial_tasks', []))
    latest = max((phase(policy, t['task_id']) or 0 for t in tasks), default=0)
    return dict(initial=dict(Counter(t['state'] for t in tasks if t['task_id'] in initial)),
                latest_phase=latest, latest=dict(Counter(t['state'] for t in tasks if phase(policy, t['task_id']) == latest)),
                active=sum(t['state'] not in {'COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED','QUEUED'} for t in tasks),
                queued=sum(t['state']=='QUEUED' for t in tasks))


def result_view(service, status, policy, handle, *, results=True):
    selected = [t for t in status['tasks'] if t['task_id'] == handle] if handle != status['group_id'] else status['tasks'][-2:]
    rows = []
    for task in selected:
        worker = next((w for w in status['workers'] if w['worker_id'] == task['worker_id']),None)
        if worker is None:
            rows.append(dict(task_id=task['task_id'],phase=phase(policy,task['task_id']),state=task['state'],
                             result_artifact_id=task['result_artifact_id'],
                             unavailable='Worker membership changed during this read; inspect this exact task again. No input was replayed.'))
            continue
        row = dict(task_id=task['task_id'], phase=phase(policy, task['task_id']), state=task['state'],
                   worker_id=worker['worker_id'], provider=worker['provider'], native_id=worker['native_id'],
                   model_requested=clip(worker['model_requested'], 128), model_observed=None,
                   result_artifact_id=task['result_artifact_id'])
        if task['result_artifact_id'] or task['state'] in {'COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED'}:
            # A live worker can already belong to a later resume or fallback.
            # Historical identity is unknown until its own artifact is read.
            row.update(provider=None, native_id=None, model_requested=None, model_observed=None)
        if results:
            try:
                rendered = service._render_result(dict(status, tasks=[task]), policy, task['task_id'])['results']
                record = rendered[0] if rendered else {}
                if record and task['result_artifact_id']:
                    # Completed attribution belongs to this immutable task, not a
                    # later resume/fallback observation on its mutable worker row.
                    identity=record.get('worker') or {}
                    row.update(provider=identity.get('provider'),native_id=identity.get('native_session_id'),
                               model_requested=clip(record['model_requested'],128) if record.get('model_requested') else None,
                               model_observed=clip(record['model_observed'],128) if record.get('model_observed') else None)
                error = record.get('error') or {}
                verification = record.get('verification') or {}
                row.update(summary=clip(record.get('summary', ''), 420),
                           failure=dict(code=error.get('code'), message=clip(error.get('message', ''), 240),
                                        may_have_received=error.get('may_have_received'), limit=error.get('limit'), execution=error.get('execution')) if error else None,
                           findings=dict(count=len(record.get('findings', [])), preview=clip(json.dumps(record.get('findings', [])[:1], ensure_ascii=False), 250)),
                           checks=dict(count=len(verification.get('tests_run', [])), preview=clip(json.dumps(verification.get('tests_run', [])[:1], ensure_ascii=False), 250)),
                           limitations=clip(json.dumps(verification.get('limitations', []), ensure_ascii=False), 200),
                           changes={k:v for k,v in (record.get('changes') or {}).items() if k in ('worktree', 'patch_artifact', 'worker_delta_artifact')})
            except (OSError, ValueError, TypeError, KeyError, PeerError) as error:
                row['unavailable'] = dict(reason=type(error).__name__, next_action='Inspect the retained result artifact; no model input was replayed')
        rows.append(row)
    value = dict(compact=True, handle=handle, group_id=status['group_id'], state=status['state'],
                 lifecycle=lifecycle(status, policy), tasks=rows,
                 omissions=dict(earlier_tasks=max(0, len(status['tasks'])-len(rows)) if handle == status['group_id'] else 0,
                                detail='Bounded previews; full findings, checks, failures, attempts and history remain in result/artifact reads. Delivery is not inferred.'),
                 next_action='npeer result '+handle+' --json')
    # Leave room for either CLI or MCP JSON framing, including ensure_ascii escaping.
    if len(json.dumps(value).encode()) > 3400:
        for row in rows:
            for key in ('findings', 'checks', 'limitations', 'changes'):
                if key in row:row[key] = dict(omitted=True, count=row[key].get('count') if isinstance(row[key],dict) else None, full_result=row['result_artifact_id'])
            if 'summary' in row:row['summary'] = clip(row['summary'], 160)
            if row.get('failure'):
                row['failure'] = {k:v for k,v in row['failure'].items() if k in ('code', 'may_have_received')}
                row['failure']['details_omitted'] = True
    if len(json.dumps(value).encode()) > 3400:
        value['tasks'] = [dict(task_id=row['task_id'], phase=row['phase'], state=row['state'],
                              result_artifact_id=row['result_artifact_id'], details_omitted=True,
                              findings_count=(row.get('findings') or {}).get('count'),
                              checks_count=(row.get('checks') or {}).get('count'),
                              unavailable=bool(row.get('unavailable')),
                              failure=dict(code=clip(row['failure']['code'], 96) if row['failure'].get('code') else None,
                                           may_have_received=row['failure'].get('may_have_received'),
                                           details_omitted=True) if row.get('failure') else None)
                          for row in rows]
    return value


def preflight(caller, parent, plan):
    workers = []
    for worker in plan['workers']:
        workers.append({k:worker.get(k) for k in ('provider', 'title', 'model', 'permission_profile', 'provider_home', 'executable', 'tools', 'review_context')})
    return dict(compact=True, state=plan['state'], ready=plan['ready'], ready_scope=plan['ready_scope'],
                caller=dict(key=caller.key, native_id=parent['peer_key']['native_session_id'], owner=parent['owner']),
                workers=workers, source={k:v for k,v in plan['baseline'].items() if k in ('root','head','branch','git','dirty_input_files','dirty_input_bytes')}, limits=plan['limits'], failures=plan['failures'],
                quota=[dict(provider=q['provider'], state=q['state']) for q in plan['usage']['workers']],
                quota_blocked=plan['quota_blocked'], pool_concurrency_blocked=plan['pool_concurrency_blocked'],
                capacity=plan['capacity'], completion_delivery=plan.get('completion_delivery'), push_ready=plan.get('push_ready'),
                model_input_submitted=False, next_action='npeer spawn --spec SAME_SPEC --json' if plan['ready'] else 'Resolve the named blockers; rerun preflight only after inputs change',
                limitations=plan['limitations'])
