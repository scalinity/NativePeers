"""Recipient-owned close-out policy. Retention is decided before submission and never rewrites transport evidence."""
import contextlib
import json
import time

from .errors import require
from .files import advisory_lock, async_advisory_lock, canonical, sha256

EVIDENCE='notification-policy'
FOOTER='\n— NativePeers references —\n'


def lock(journal,peer_key):
    # The same recipient advisory lock Router.admit holds before any adapter side effect.
    return async_advisory_lock(journal.root/'locks'/(sha256(peer_key)+'.lock'))


@contextlib.contextmanager
def recipient_lock(journal,peer_key,timeout):
    manager=advisory_lock(journal.root/'locks'/(sha256(peer_key)+'.lock'),timeout)
    try:manager.__enter__()
    except TimeoutError:
        # An in-flight admission may hold this lock while its native queue dispatches the frame.
        # The immediate journal transaction still serializes the decision with close/open.
        yield False;return
    try:yield True
    finally:manager.__exit__(None,None,None)


def row(db,peer_key):
    # Effective state belongs to the stable peer key, so a resumed incarnation keeps closed exchanges quiet.
    value=db.execute('SELECT * FROM notification_policies WHERE peer_key=?',(peer_key,)).fetchone()
    return dict(value) if value else None


def describe(value):
    if value is None:return dict(state='OPEN',configured=False,message_cutoff=0,route_cutoff=0,closed_at=None,opened_at=None,written_by_incarnation=None)
    return dict(state=value['state'],configured=True,message_cutoff=value['message_cutoff'],route_cutoff=value['route_cutoff'],
                closed_at=value['closed_at'],opened_at=value['opened_at'],written_by_incarnation=value['incarnation_id'])


def status(journal,peer_key):
    return describe(row(journal.db,peer_key))


def reopen(db,peer_key,incarnation,now,cause):
    # Opening never clears cutoffs: closed exchanges and completion phases stay quiet.
    if db.execute("UPDATE notification_policies SET state='OPEN',opened_at=?,incarnation_id=? WHERE peer_key=? AND state='QUIET'",(now,incarnation,peer_key)).rowcount:
        db.execute('INSERT INTO events(subject_id,kind,payload_json,at) VALUES(?,?,?,?)',(peer_key,'notification-policy-opened',canonical({'cause':cause,'incarnation_id':incarnation}).decode(),now))


def current_owner(db,peer_key,incarnation,native_id,pid,start):
    """Inside the caller's transaction: is this still the stable peer's exact current live owner and incarnation?"""
    current=db.execute('SELECT incarnation_id,native_id,pid,process_start,status FROM peer_instances WHERE peer_key=? ORDER BY last_seen DESC LIMIT 1',(peer_key,)).fetchone()
    return bool(current) and tuple(current)==(incarnation,native_id,pid,start,'live')


async def apply(journal,registry,caller,action):
    require(action in ('open','close','status'),'PROTOCOL_ERROR','Notification action must be open, close or status')
    require(caller.kind=='peer' and caller.incarnation,'CALLER_UNBOUND','Notification policy requires a verified native caller')
    peer=registry.verify(caller.key,caller.incarnation)
    if action=='status':return status(journal,caller.key)
    # Owned workers report tasks normally; their controller owns completions and follow-ups.
    require(not caller.grants.get('worker_id') and peer.get('owner_kind')!='owned-worker-controller' and peer.get('transport',{}).get('kind')!='owned-worker',
            'APPROVAL_BLOCKED','Only interactive sessions may open or close notifications')
    async with lock(journal,caller.key):
        with journal.transaction() as db:
            # Only the verified current incarnation may write; a resume during the lock wait is refused.
            registry.verify(caller.key,caller.incarnation)
            current=row(db,caller.key);now=time.time()
            if action=='close' and (current is None or current['state']!='QUIET'):
                # Only OPEN->QUIET advances cutoffs; a repeated close never absorbs later fresh requests.
                messages=db.execute('SELECT COALESCE(MAX(rowid),0) FROM messages').fetchone()[0]
                routes=db.execute('SELECT COALESCE(MAX(rowid),0) FROM completion_routes').fetchone()[0]
                db.execute("INSERT INTO notification_policies VALUES(?,'QUIET',?,?,?,NULL,?) ON CONFLICT(peer_key) DO UPDATE SET state='QUIET',"
                           "message_cutoff=MAX(message_cutoff,excluded.message_cutoff),route_cutoff=MAX(route_cutoff,excluded.route_cutoff),"
                           "closed_at=excluded.closed_at,incarnation_id=excluded.incarnation_id",(caller.key,messages,routes,now,caller.incarnation))
            elif action=='open':reopen(db,caller.key,caller.incarnation,now,'explicit-open')
            return describe(row(db,caller.key))


def retention(db,message_id,envelope,now):
    """Why input to this recipient stays retained, or None to admit. Expired input is never admitted."""
    # The journal row, not frame text, is authoritative for recipient, exchange, correlation and expiry.
    message=db.execute('SELECT to_key,exchange_id,in_reply_to,expires_at FROM messages WHERE message_id=?',(message_id,)).fetchone()
    recipient,exchange,reply,expires=(tuple(message) if message else
        (envelope['to']['peer_key'],envelope['exchange_id'],envelope.get('in_reply_to'),envelope['expires_at']))
    if now>=expires:return 'expired'
    policy=row(db,recipient)
    if policy is None:return None
    kind=envelope.get('kind')
    if kind in ('completion','failure'):
        route=db.execute('SELECT c.rowid FROM completion_routes c JOIN send_requests s ON s.client_request_id=c.delivery_id WHERE s.message_id=?',(message_id,)).fetchone()
        if route and route[0]<=policy['route_cutoff']:return 'completion-phase-closed'
    root=db.execute('SELECT MIN(rowid) FROM messages WHERE exchange_id=?',(exchange,)).fetchone()[0]
    if root is not None and root<=policy['message_cutoff']:return 'exchange-closed'
    if policy['state']=='QUIET' and (kind!='request' or reply):return 'quiet-informational'
    return None


def suppress(journal,message_id,envelope,disposition,*,now=None):
    """Shared consumption filter: retained input gets separate disposition evidence, never a transport rewrite."""
    reason=retention(journal.db,message_id,envelope,time.time() if now is None else now)
    if reason and not journal.one("SELECT 1 n FROM events WHERE subject_id=? AND kind='notification-suppressed'",(message_id,)):
        journal.event(message_id,'notification-suppressed',dict(disposition,reason=reason))
    return reason


def frame(journal,prompt):
    """The lookup ID comes only from the final references section; the complete deterministic frame must be equal."""
    from .presentation import render
    if not isinstance(prompt,str) or FOOTER not in prompt:return None
    # render() ends with exactly one references section whose second line is the message ID; body text precedes it.
    lines=prompt.rpartition(FOOTER)[2].split('\n')
    if len(lines)<2 or not lines[1].startswith('Message: '):return None
    found=journal.one('SELECT payload_json FROM messages WHERE message_id=?',(lines[1][len('Message: '):],))
    return json.loads(found['payload_json']) if found and render(json.loads(found['payload_json']))==prompt else None


def guard(journal,peer,prompt,*,timeout=1):
    """Consumption-time decision for one prompt to an exact registered native owner; returns hook output."""
    # A malformed hook without prompt text is not a new human task: no decision and no reopen.
    if not isinstance(prompt,str):return {}
    envelope=frame(journal,prompt)
    # Another recipient's frame neither blocks nor reopens this peer.
    if envelope is not None and envelope['to']['peer_key']!=peer['peer_key']:return {}
    with recipient_lock(journal,peer['peer_key'],timeout) as locked,journal.transaction() as db:
        # Revalidate after the lock wait: a superseded owner's input must not mutate the resumed peer's policy.
        if not current_owner(db,peer['peer_key'],peer['incarnation_id'],peer['native_id'],peer['pid'],peer['process_start']):
            return {'guarded':False,'reason':'peer-superseded'}
        now=time.time();policy=row(db,peer['peer_key'])
        if envelope is not None:
            attempt=db.execute('SELECT attempt_id,state,target_incarnation FROM attempts WHERE message_id=? ORDER BY started_at DESC LIMIT 1',(envelope['message_id'],)).fetchone()
            attempt=dict(attempt) if attempt else {}
            reason=suppress(journal,envelope['message_id'],envelope,dict(evidence='exact-native-frame',attempt_id=attempt.get('attempt_id'),attempt_state=attempt.get('state'),
                target_incarnation=attempt.get('target_incarnation'),observed_incarnation=peer['incarnation_id'],native_id=peer['native_id'],
                owner=[peer['pid'],peer['process_start']],recipient_lock=locked),now=now)
            if reason:return {'decision':'block','reason':'NativePeers kept this late notice out of the conversation ('+reason+'). Read it with npeer inbox --retained --json.'}
        # Only a fresh root request passes QUIET; ordinary human text passes unchanged. Either opens and keeps cutoffs.
        if policy and policy['state']=='QUIET':reopen(db,peer['peer_key'],peer['incarnation_id'],now,'fresh-root-request' if envelope else 'human-prompt')
        return {}
