"""Passive parent-route checks and exact, retained completion delivery evidence."""
import json
from pathlib import Path

from .errors import PeerError, require
from .registry import Registry


def readiness(journal, parent_key):
    value = dict(ready=False, state='UNKNOWN', parent_key=parent_key,
                 result_collection='retained results remain available',
                 scope='Passive route check; native policy and later ingestion are not guaranteed')
    try:
        peer = Registry(journal).get(parent_key)
        kind = peer['transport'].get('kind')
        value.update(provider=peer['peer_key']['provider'], transport=kind)
        if kind in ('claude-inbox', 'codex-native-queue'):
            from .compatibility import verify
            provider = peer['peer_key']['provider']
            executable = Path.home()/('.local/bin/claude' if provider == 'claude'
                                      else '.local/share/codex-mcp-manager/current/bin/codex')
            value['runtime'] = verify(journal, provider, executable)
            if kind == 'claude-inbox':
                from .adapters.native_delivery import socket_endpoint
                socket_endpoint(peer)
        elif kind == 'owned-worker':
            worker = journal.one('SELECT state,generation FROM workers WHERE worker_id=?',
                                 (peer['transport']['worker_id'],))
            require(worker and worker['state'] in ('READY', 'RUNNING', 'IDLE_GRACE'),
                    'TARGET_OFFLINE', 'Parent worker controller has released ownership')
            value['generation'] = worker['generation']
        elif kind == 'none' and peer.get('capabilities', {}).get('mirror'):
            # A desktop-hosted parent has no inbox: the np mod mirrors its workers and NativePeers retains completions.
            value.update(state='MIRROR_ONLY', reason='Desktop-hosted session without an inbox: completions are retained, and the np mod mirrors its workers',
                         next_action='Collect retained results with npeer result, or watch np:* workers in the session')
            return value
        elif kind in ('antigravity-native-hook','grok-native-hook'):
            from .compatibility import verify
            from .grok import executable as grok_executable
            provider=peer['peer_key']['provider']
            value['runtime'] = verify(journal,provider,grok_executable() if provider=='grok' else Path.home()/'.local/bin/agy')
            value.update(state='NEXT_INVOCATION', reason='The hook cannot wake an idle parent',
                         next_action='Collect retained results or use the next normal native invocation')
            return value
        else:
            raise PeerError('UNSUPPORTED_TRANSPORT', 'Parent has no verified completion push route')
        value.update(ready=True, state='READY', reason='No passive completion-route blocker detected')
    except Exception as error:
        value.update(state=error.code if isinstance(error, PeerError) else 'UNKNOWN',
                     reason=str(error)[:1000],
                     next_action='Run npeer doctor --json; use bounded wait/status and retained result collection')
        if isinstance(error, PeerError):value['error'] = error.json()
    return value


def describe(journal, route):
    try:return _describe(journal, route)
    except Exception as error:
        return dict(route,delivery=dict(state='UNKNOWN',reason='Completion diagnostic unavailable: '+type(error).__name__))


def _describe(journal, route):
    """Read an existing attempt, never retry a completion or claim its result."""
    value = dict(route)
    value['push_configured']=route['mode']=='push'
    value['collection_command']='npeer result '+route['group_id']+' --json'
    message = journal.one('SELECT m.message_id FROM send_requests s JOIN messages m USING(message_id) '
                          "WHERE s.client_request_id=? AND m.to_key=? AND json_extract(m.payload_json,'$.from.kind')='controller' "
                          "AND json_extract(m.payload_json,'$.from.group_id')=? ORDER BY m.created_at DESC LIMIT 1",
                          (route['delivery_id'], route['parent_key'],route['group_id']))
    if message:
        from .routing import Router
        from .receipts import delivery_error
        outcome = Router(journal, Registry(journal)).outcome(message['message_id'])
        error = delivery_error(outcome)
        # Consumption-time suppression is additive disposition evidence; route and transport states stay unchanged.
        value['delivery'] = dict(message_id=message['message_id'], state=outcome['state'],
                                 error=error.json() if error else None,
                                 **({'suppression': outcome['suppression']} if outcome.get('suppression') else {}))
    else:
        event = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='completion-delivery' ORDER BY sequence DESC LIMIT 1",
                            (route['delivery_id'],))
        if event:value['delivery'] = json.loads(event['payload_json'])
    return value


def followup(journal,message_id):
    """Exact committed message/task/phase linkage; no receipt or delivery mutation."""
    row=journal.one('SELECT t.task_id,t.state,t.result_artifact_id,w.group_id,g.policy_json FROM tasks t '
                    'JOIN workers w USING(worker_id) JOIN groups g USING(group_id) WHERE t.request_message_id=?',(message_id,))
    if not row:return None
    policy=json.loads(row['policy_json'])
    phase=next((int(n) for n,ids in policy.get('phases',{}).items() if row['task_id'] in ids),None)
    route=journal.one('SELECT mode,state FROM completion_routes WHERE group_id=? AND completion_generation=?',(row['group_id'],phase))
    fallback='push' if route and route['mode']=='push' else 'retain'
    wait='npeer wait '+row['task_id']+' --timeout-ms 30000 --on-timeout '+fallback+' --json'
    result='npeer result '+row['task_id']+' --json'
    return dict(group_id=row['group_id'],task_id=row['task_id'],completion_phase=phase,
                completion_mode=route['mode'] if route else None,notification_state=route['state'] if route else 'MISSING',
                task_state=row['state'],result_artifact_id=row['result_artifact_id'],
                wait_command=wait,result_command=result,next_action=result if row['state'] in ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED') else wait)


def annotate(value, route, *, push=False):
    value['completion_delivery'] = route
    value['push_ready'] = route['ready']
    if push and not route['ready']:
        value['warnings'] = [*value.get('warnings', []),
                             'Completion push unavailable: '+route['state']+': '+route['reason']]
    return value
