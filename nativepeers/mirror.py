"""Delivery ownership per task generation and the per-worker mirror lease (docs/CLAUDE_LINEAGE_MOD.md §6.4, §8.3).

A task generation is the task's 1-based place among its worker's tasks. Each one gets a delivery owner when it is created:
`mirror` when the Claude session that created it holds the worker's live lease (the engine's own notification reaches the
parent), `native` otherwise. A mirror-owned generation's completion route starts in MIRROR_PENDING, which the supervisor
never pushes and no waiter claims; surfacing moves it to MIRROR_SURFACING and ack to MIRROR_DELIVERED. When the lease
lapses first the route falls back: MIRROR_PENDING returns to UNCLAIMED with its recorded mode (a push route re-arms, a
wait route is retained), and MIRROR_SURFACING becomes NOTIFICATION_UNKNOWN, which never pushes. Every change is a
compare-and-set inside an immediate transaction, so expiry, surfacing and ack race to one outcome.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .errors import require
from .process import matches

LEASE_TTL = 30
PENDING, SURFACING, DELIVERED = 'MIRROR_PENDING', 'MIRROR_SURFACING', 'MIRROR_DELIVERED'


def task_generation(journal, task_id):
    row = journal.one('SELECT COUNT(*) AS n FROM tasks a JOIN tasks b ON a.worker_id=b.worker_id WHERE b.task_id=? AND a.rowid<=b.rowid', (task_id,))
    return row['n'] or None


def task_at(journal, worker_id, generation):
    return journal.one('SELECT * FROM tasks WHERE worker_id=? ORDER BY rowid LIMIT 1 OFFSET ?', (worker_id, generation - 1)) if generation >= 1 else None


def live_lease(journal, worker_id, now=None):
    return journal.one('SELECT * FROM mirror_leases WHERE worker_id=? AND expires_at>?', (worker_id, time.time() if now is None else now))


def owner_for(journal, worker_id, session, requested):
    """`mirror` only where that session holds the worker's live lease; ownership requires a lease."""
    lease = live_lease(journal, worker_id)
    return 'mirror' if requested == 'mirror' and lease and lease['claude_session'] == session else 'native'


def record(db, task_id, group_id, phase, owner, *, origin=None, session=None, agent=None):
    db.execute('INSERT OR IGNORE INTO task_delivery VALUES(?,?,?,?,?,?,?,?)', (task_id, group_id, phase, owner, origin, session, agent, time.time()))


def route_state(owner):
    return PENDING if owner == 'mirror' else 'UNCLAIMED'


def supervising(journal, group_id):
    try:
        manifest = json.loads((Path(journal.root) / 'groups' / group_id / 'owner.json').read_text())
    except (OSError, ValueError):
        return False
    return not manifest.get('released') and matches(manifest['owner'])


def expire(journal, *, group_id=None, supervised=None, worker_id=None, handback=False):
    """Fall back every mirror-owned route whose lease has lapsed. A push route with no live supervisor is retained, as close-out does.

    `handback` falls back one worker's mirror-owned routes whatever its lease: a display-only re-attach takes over a
    worker whose earlier shadow can no longer surface, so those generations go back to NativePeers delivery (§6.4)."""
    now = time.time();moved = []
    lapsed = ("SELECT r.group_id,r.completion_generation,r.state,r.mode,d.task_id,t.worker_id FROM completion_routes r "
              "JOIN task_delivery d ON d.group_id=r.group_id AND d.completion_generation=r.completion_generation AND d.owner='mirror' "
              "JOIN tasks t ON t.task_id=d.task_id WHERE r.state IN (?,?)" + (' AND r.group_id=?' if group_id else '') + (' AND t.worker_id=?' if worker_id else '') +
              ('' if handback else ' AND NOT EXISTS (SELECT 1 FROM mirror_leases l WHERE l.worker_id=t.worker_id AND l.claude_session=d.claude_session AND l.expires_at>?)'))
    params = (PENDING, SURFACING) + ((group_id,) if group_id else ()) + ((worker_id,) if worker_id else ()) + (() if handback else (now,))
    if not journal.one(lapsed + ' LIMIT 1', params):return moved  # The common case takes no write lock.
    with journal.transaction() as db:
        for row in db.execute(lapsed, params).fetchall():
            target = 'NOTIFICATION_UNKNOWN' if row['state'] == SURFACING else 'UNCLAIMED'
            if target == 'UNCLAIMED' and row['mode'] == 'push' and not (supervised if supervised is not None else supervising(journal, row['group_id'])):
                target = 'RETAINED'
            if db.execute('UPDATE completion_routes SET state=? WHERE group_id=? AND completion_generation=? AND state=?',
                          (target, row['group_id'], row['completion_generation'], row['state'])).rowcount:
                moved.append(dict(task_id=row['task_id'], worker_id=row['worker_id'], group_id=row['group_id'], completion_generation=row['completion_generation'],
                                  **{'from': row['state'], 'to': target, 'mode': row['mode']}))
        for item in moved:journal.event(item['task_id'], 'mirror-route-fallback', item)
    return moved


def attach(journal, worker_id, session, agent, parent_key, *, sessions, display_only=True, check_only=False):
    """The worker's exclusive lease for this session. A live lease held by another session refuses with MIRROR_OWNED."""
    if not check_only:expire(journal)
    now = time.time()
    with journal.transaction() as db:
        held = db.execute('SELECT * FROM mirror_leases WHERE worker_id=? AND expires_at>?', (worker_id, now)).fetchone()
        require(not held or held['claude_session'] in sessions, 'MIRROR_OWNED', 'Another Claude Code session mirrors this worker; read-only viewing stays available',
                details={'worker_id': worker_id})
        if check_only:
            return dict(worker_id=worker_id, mirror_owner='this' if held else None, attached=False)
        expires = grant(db, worker_id, session, agent, parent_key, display_only=display_only)
    # Only once admitted: an earlier shadow of this worker can no longer surface.
    if display_only:expire(journal, worker_id=worker_id, handback=True)
    return dict(worker_id=worker_id, mirror_owner='this', attached=True, claude_agent_id=agent, display_only=bool(display_only), expires_at=expires)


def grant(db, worker_id, session, agent, parent_key, *, display_only):
    now = time.time()
    db.execute('INSERT INTO mirror_leases VALUES(?,?,?,?,?,?,?) ON CONFLICT(worker_id) DO UPDATE SET claude_session=excluded.claude_session,'
               'claude_agent_id=excluded.claude_agent_id,parent_key=excluded.parent_key,display_only=excluded.display_only,attached_at=excluded.attached_at,'
               'expires_at=excluded.expires_at', (worker_id, session, agent, parent_key, int(bool(display_only)), now, now + LEASE_TTL))
    return now + LEASE_TTL


def renew(journal, sessions):
    """Extend only unexpired leases: a lapsed lease has already handed its routes back."""
    if not sessions:return 0
    now = time.time();marks = ','.join('?' * len(sessions))
    return journal.db.execute(f'UPDATE mirror_leases SET expires_at=? WHERE claude_session IN ({marks}) AND expires_at>?', (now + LEASE_TTL, *sessions, now)).rowcount


def release(journal, worker_id, sessions):
    now = time.time();marks = ','.join('?' * len(sessions))
    released = journal.db.execute(f'UPDATE mirror_leases SET expires_at=? WHERE worker_id=? AND claude_session IN ({marks}) AND expires_at>?',
                                  (now, worker_id, *sessions, now)).rowcount
    return dict(worker_id=worker_id, released=bool(released), fallback=expire(journal))


def held(journal, sessions):
    marks = ','.join('?' * len(sessions))
    return journal.all(f'SELECT * FROM mirror_leases WHERE claude_session IN ({marks}) AND expires_at>? ORDER BY attached_at', (*sessions, time.time())) if sessions else []


def route(journal, worker_id, task_id=None, generation=None):
    task = journal.one('SELECT * FROM tasks WHERE task_id=? AND worker_id=?', (task_id, worker_id)) if task_id else task_at(journal, worker_id, int(generation or 0))
    require(task, 'PROTOCOL_ERROR', 'Unknown task generation for this worker')
    actual = task_generation(journal, task['task_id'])
    require(generation is None or int(generation) == actual, 'PROTOCOL_ERROR', 'Task and generation disagree', details={'task_id': task['task_id'], 'generation': actual})
    delivery = journal.one('SELECT * FROM task_delivery WHERE task_id=?', (task['task_id'],)) or {}
    state = journal.one('SELECT state,mode FROM completion_routes WHERE group_id=? AND completion_generation=?', (delivery.get('group_id'), delivery.get('completion_generation'))) if delivery else None
    return dict(worker_id=worker_id, task_id=task['task_id'], generation=actual, owner=delivery.get('owner', 'native'), claude_session=delivery.get('claude_session'),
                group_id=delivery.get('group_id'), completion_generation=delivery.get('completion_generation'), route=state['state'] if state else None,
                mode=state['mode'] if state else None)


def transition(journal, current, sessions, allowed, target, *, lease=False, event=None, details=None):
    """One compare-and-set on a mirror-owned route; any other route is reported unchanged."""
    if current['owner'] != 'mirror':
        return dict(current, changed=False)
    require(current['claude_session'] in sessions, 'APPROVAL_BLOCKED', 'This generation is mirrored by another Claude Code session')
    marks = ','.join('?' * len(allowed));now = time.time()
    with journal.transaction() as db:
        guard = ' AND EXISTS (SELECT 1 FROM mirror_leases WHERE worker_id=? AND claude_session=? AND expires_at>?)' if lease else ''
        changed = bool(db.execute(f'UPDATE completion_routes SET state=? WHERE group_id=? AND completion_generation=? AND state IN ({marks})' + guard,
                                  (target, current['group_id'], current['completion_generation'], *allowed) + ((current['worker_id'], current['claude_session'], now) if lease else ())).rowcount)
        state = db.execute('SELECT state FROM completion_routes WHERE group_id=? AND completion_generation=?', (current['group_id'], current['completion_generation'])).fetchone()['state']
        journal.event(current['task_id'], event, dict(details or {}, worker_id=current['worker_id'], changed=changed, route=state))
    return dict(current, route=state, changed=changed)


def surfacing(journal, worker_id, sessions, *, task_id=None, generation=None):
    return transition(journal, route(journal, worker_id, task_id, generation), sessions, (PENDING,), SURFACING, lease=True, event='mirror-surfacing')


def ack(journal, worker_id, sessions, *, task_id=None, generation=None, outcome=None):
    """The shadow completed and the parent was told natively. A killed shadow acks without surfacing; a late ack settles an unknown route."""
    require(outcome in (None, 'killed'), 'PROTOCOL_ERROR', 'Unknown mirror ack outcome')
    allowed = (SURFACING, 'NOTIFICATION_UNKNOWN') + ((PENDING,) if outcome == 'killed' else ())
    return transition(journal, route(journal, worker_id, task_id, generation), sessions, allowed, DELIVERED, event='mirror-ack', details={'outcome': outcome})
