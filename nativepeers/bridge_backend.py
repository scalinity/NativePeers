"""The NativePeers side of the session bridge (docs/CLAUDE_LINEAGE_MOD.md §6.3-6.6).

`bind_session` binds to the live interactive Claude peer whose native ID is the session ID, with bind_caller's interactive
ancestry checks; nothing here is a new trust root. NativeBackend answers every bridge route with that parent's authority
through the existing paths (spawn_many, controller enqueue, owned resume, task and group cancel, results, artifacts), plus
the lease and route operations in `mirror`. `npeer input|interrupt|mirror` call the same handlers and return the same JSON.
Mutations are idempotent by client_request_id in `bridge_requests`; only definitive outcomes are remembered.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import signal
import time
import tomllib

from . import __version__, mirror
from .activity.events import SCHEMA, diff_stats
from .activity.reader import Reader
from .caller import Caller, native_ancestor, nearest_native
from .core import Core
from .delegation import TERMINAL
from .errors import PeerError, require
from .files import atomic_json, canonical, private_directory, sha256
from .models import bounded_text
from .process import ancestry, identity, matches

PROVIDERS = ('codex', 'claude', 'antigravity', 'grok')
STATUS = {'QUEUED': 'queued', 'RUNNING': 'running', 'WAITING': 'waiting', 'APPROVAL_BLOCKED': 'waiting', 'FALLBACK_PENDING': 'running',
          'COMPLETED': 'completed', 'FAILED': 'failed', 'CANCELED': 'canceled', 'INTERRUPTED': 'interrupted', 'PARTIAL': 'interrupted', 'TIMED_OUT': 'timed_out'}
ORIGINS = ('human-claude-view', 'parent-model')
DELIVERABLE = ('READY', 'RUNNING', 'IDLE_GRACE')
AGENT = re.compile(r'[A-Za-z0-9_-]{1,128}')
# Codex runtimes whose generated app-server schema lists turn/steer {threadId, expectedTurnId, input} (tests/fixtures/activity/codex/README.md).
STEER_RUNTIMES = {'codex': {'3998a0e51bb7e4a2c8da905e9ba2f9e149c81ae44c487de59cf1e7296c04dadc'}}
BACKFILL = ('claude', 'codex')  # Providers whose native histories nativepeers.activity.backfill reads; the others yield one gap.
CATALOG_SECONDS = 300
LINEAGE_GROUPS = 64
LINEAGE_DEPTH = 4


def bind_session(journal, registry, session):
    """The live interactive Claude peer for this session, owned by a process in this one's ancestry (bind_caller's interactive checks)."""
    require(isinstance(session, str) and 0 < len(session) <= 128, 'CALLER_UNBOUND', 'A Claude session ID is required')
    found = [p for p in registry.for_native('claude', session) if p['owner_kind'] == 'interactive-standalone']
    require(found, 'CALLER_UNBOUND', 'No live registered Claude session has this ID; NativePeers registers it at SessionStart', details={'claude_session': session})
    require(len(found) == 1, 'CALLER_UNBOUND', 'Several live registered Claude sessions have this ID', details={'claude_session': session, 'peers': [p['handle'] for p in found]})
    peer = found[0];ancestors = [p.json() for p in ancestry()]
    require(native_ancestor(peer['owner'], ancestors), 'CALLER_UNBOUND', 'Native owner is outside caller ancestry')
    nearest = nearest_native(ancestors, peer['owner']['executable'], journal=journal)
    require(nearest is not None and native_ancestor(peer['owner'], [nearest]), 'CALLER_UNBOUND', 'Interactive binding belongs to an enclosing native session')
    return peer


def runtimes(journal):
    """Providers whose installed executable holds a current runtime approval, with its digest. Never renews an approval.

    A patch update that no check has looked at yet stays listed: its first spawn renews it under the bounded update
    checks, or refuses, as any first use does. A checked, refused or revoked update is not listed."""
    from .compatibility import AUTO_RENEW, fingerprint, verify
    from .grok import executable as grok_executable
    from .runtime_updates import CONTRACTS
    paths = {'claude': '.local/bin/claude', 'codex': '.local/share/codex-mcp-manager/current/bin/codex', 'antigravity': '.local/bin/agy'}
    found = {}
    for provider in PROVIDERS:
        try:
            executable = grok_executable() if provider == 'grok' else Path.home() / paths[provider]
            found[provider] = verify(journal, provider, executable, renew=False)['sha256']
        except PeerError as error:
            if AUTO_RENEW and provider in CONTRACTS and (error.details or {}).get('compatibility_state') == 'UPDATE_UNTESTED':
                try:found[provider] = fingerprint(executable)['sha256']
                except OSError:continue
        except OSError:continue
    return found


def defaults(journal, provider):
    """`config.toml [claude_mod.defaults.<provider>]`: model and effort for np agents that name neither."""
    try:
        section = tomllib.loads((Path(journal.root) / 'config.toml').read_text()).get('claude_mod', {}).get('defaults', {}).get(provider, {})
    except (OSError, ValueError, AttributeError):
        section = {}
    return section if isinstance(section, dict) else {}


def refusal(plan):
    """Raise the first reason a checked plan is not ready, as the PeerError the mod shows."""
    if plan['ready']:return
    if plan['failures']:
        error = plan['failures'][0]['error'];raise PeerError(error['code'], error['message'], details=error.get('details'))
    if plan['quota_blocked']:
        blocked = plan['quota_blocked'][0]
        raise PeerError('QUOTA_EXHAUSTED', f"{blocked['provider']} {blocked.get('model') or ''} quota is exhausted".replace('  ', ' '), details={'pools': blocked.get('pools')})
    if plan['pool_concurrency_blocked']:
        raise PeerError('CAPACITY_UNAVAILABLE', 'Configured local quota-pool concurrency ceiling reached', details=plan['pool_concurrency_blocked'][0])
    raise PeerError('CAPACITY_UNAVAILABLE', 'Every worker slot is occupied', details=plan['capacity'])


def ms(at):
    return int(at * 1000) if at else None


class NativeBackend:
    def __init__(self, journal, peer, session, *, runtimes=None, manifest=None):
        self.journal, self.core = journal, Core(journal);self.registry = self.core.registry
        self.owner, self.peers, self.current = peer['owner'], {peer['handle']: peer}, peer['handle']
        self.session, self.sessions = session, [session]
        self.runtimes, self.manifest = dict(runtimes or {}), manifest
        self.inflight, self.agents, self.locks, self.catalog_cache, self.socket, self.times, self.backfills = {}, {}, {}, None, None, {}, {}
        self.changed, self.revision = asyncio.Event(), 0

    # --- identity and scope
    def caller(self, key=None):
        """The parent's live policy, so profile ceilings follow its observed permission mode; a replaced peer keeps its recorded one."""
        key = key or self.current;peer = self.peers[key]
        try:
            live = self.registry.get(key)
            if live['owner'] == self.owner:peer = self.peers[key] = live
        except PeerError:
            pass
        return Caller('peer', key, peer['incarnation_id'], dict(peer.get('policy') or {}))

    def worker(self, worker_id):
        row = self.journal.one('SELECT w.*,g.parent_key FROM workers w JOIN groups g USING(group_id) WHERE w.worker_id=?', (worker_id,))
        require(row, 'PROTOCOL_ERROR', 'Unknown worker')
        require(row['parent_key'] in self.peers, 'APPROVAL_BLOCKED', 'Worker is outside this Claude session\'s own delegations')
        return row

    def touch(self):
        self.revision += 1;self.changed.set();self.changed = asyncio.Event()

    async def changed_within(self, seconds):
        event = self.changed
        try:await asyncio.wait_for(event.wait(), max(0, seconds))
        except (TimeoutError, asyncio.TimeoutError):pass

    def remembered(self, caller, operation, request_id, payload):
        """(earlier response or None, digest). The same ID with different content is IDEMPOTENCY_CONFLICT."""
        require(isinstance(request_id, str) and 0 < len(request_id) <= 256, 'PROTOCOL_ERROR', 'client_request_id is required')
        digest = sha256(payload)
        row = self.journal.one('SELECT * FROM bridge_requests WHERE caller_key=? AND operation=? AND client_request_id=?', (caller.key, operation, request_id))
        require(not row or row['payload_sha256'] == digest, 'IDEMPOTENCY_CONFLICT', 'Request ID already has different content')
        return (json.loads(row['response_json']) if row and row['response_json'] else None), digest

    def intend(self, caller, operation, request_id, digest, request):
        self.journal.db.execute('INSERT OR IGNORE INTO bridge_requests VALUES(?,?,?,?,?,?,?)', (caller.key, operation, request_id, digest, canonical(request).decode(), None, time.time()))

    def settle(self, caller, operation, request_id, response):
        self.journal.db.execute('UPDATE bridge_requests SET response_json=? WHERE caller_key=? AND operation=? AND client_request_id=?',
                                (canonical(response).decode(), caller.key, operation, request_id))
        self.touch()
        return response

    def steers(self, provider):
        return self.runtimes.get(provider) in STEER_RUNTIMES.get(provider, ())

    # --- bridge lifecycle
    def hello(self):
        return dict(peer=self.current, version=__version__, providers=[p for p in PROVIDERS if p in self.runtimes],
                    capabilities=dict(steer={p: self.steers(p) for p in PROVIDERS}, activity_schema=SCHEMA, backfill=list(BACKFILL), lease_ttl_seconds=mirror.LEASE_TTL))

    async def owner_alive(self):
        return matches(self.owner)

    async def renew_leases(self):
        try:
            mirror.renew(self.journal, self.sessions)
            if mirror.expire(self.journal):self.touch()
        except Exception as error:  # A busy journal skips one renewal; the TTL covers several.
            self.journal.event(self.current, 'bridge-renewal-skipped', {'cause': type(error).__name__})

    def has_leases(self):
        return bool(mirror.held(self.journal, self.sessions))

    async def close(self, reason):
        # Leases are left to their TTL so a reloaded mod (and a restarted bridge for this session) can take them up again.
        # A spawn already handed to spawn_many finishes: abandoning it could strand a reserved group without its supervisor.
        if self.inflight:await asyncio.wait(list(self.inflight.values()), timeout=30)
        if self.manifest:Path(self.manifest).unlink(missing_ok=True)

    def record_manifest(self, socket=None):
        self.socket = socket or self.socket
        if self.manifest:
            atomic_json(Path(self.manifest), dict(process=identity(os.getpid()).json(), socket=str(self.socket), peer=self.current, peers=list(self.peers),
                                                  claude_sessions=self.sessions, protocol=1, started_at=time.time()))

    async def rebind(self, body):
        """After /clear the same Claude owner registers a new session; new spawns use it, existing workers keep their parent."""
        session = body.get('claude_session')
        if session in self.sessions:
            return dict(peer=self.current, claude_session=session, rebound=False)
        peer = bind_session(self.journal, self.registry, session)
        require(peer['owner'] == self.owner, 'CALLER_UNBOUND', 'The new Claude session belongs to another process')
        previous = self.current;self.peers[peer['handle']] = peer;self.current = peer['handle'];self.session = session;self.sessions.append(session)
        self.record_manifest();self.touch()
        return dict(peer=peer['handle'], previous=previous, claude_session=session, rebound=True)

    # --- catalog and lineage
    async def catalog(self):
        if not self.catalog_cache or time.monotonic() - self.catalog_cache[0] > CATALOG_SECONDS:
            self.catalog_cache = (time.monotonic(), await catalog(self.journal, self.caller(), [p for p in PROVIDERS if p in self.runtimes]))
        return self.catalog_cache[1]

    def worker_row(self, w, group, parent_worker):
        task = self.journal.one('SELECT * FROM tasks WHERE worker_id=? ORDER BY rowid DESC LIMIT 1', (w['worker_id'],))
        count = self.journal.one('SELECT COUNT(*) AS n FROM tasks WHERE worker_id=?', (w['worker_id'],))['n']
        lease = mirror.live_lease(self.journal, w['worker_id']);mine = bool(lease and lease['claude_session'] in self.sessions)
        state = task['state'] if task else w['state']
        status = 'starting' if state == 'QUEUED' and w['state'] == 'RESERVED' else STATUS.get(state, 'failed' if w['state'] == 'FAILED' else 'queued')
        started = ended = tokens = None
        if task:
            started, ended = self.task_times(task)
            tokens = sum(json.loads(r['payload_json']).get('tokens', {}).get('output_tokens') or 0 for r in self.journal.all('SELECT payload_json FROM task_usage WHERE task_id=?', (task['task_id'],))) or None
        return dict(workerId=w['worker_id'], groupId=w['group_id'], taskId=task['task_id'] if task else None, generation=count, provider=w['provider'],
                    modelRequested=w['model_requested'], modelObserved=w['model_observed'], effort=w['effort_requested'],
                    profile=json.loads(w['lease_json']).get('spec', {}).get('permission_profile'), title=w['role_title'], status=status,
                    startedAt=ms(started or json.loads(group['policy_json']).get('created_at')), endedAt=ms(ended), outputTokens=tokens,
                    agentId=lease['claude_agent_id'] if mine else None, parentWorkerId=parent_worker, mirrorOwner='this' if mine else 'other' if lease else None,
                    nativeId=w['native_id'], resumable=w['state'] == 'RELEASED_RESUMABLE' and bool(w['native_id']), continuedBy=self.continuation(w['worker_id']))

    def task_times(self, task):
        """A task's native start and terminal times; each is fixed once recorded, so the events scan runs once per task."""
        started, ended = self.times.get(task['task_id'], (None, None));final = task['state'] in TERMINAL
        if started is None:
            started = self.journal.one("SELECT MIN(at) AS at FROM events WHERE subject_id=? AND kind='native-task-started'", (task['task_id'],))['at'] or (False if final else None)
        if ended is None and final:
            ended = self.journal.one("SELECT MAX(at) AS at FROM events WHERE subject_id=? AND kind='native-task-terminal'", (task['task_id'],))['at'] or False
        self.times[task['task_id']] = (started, ended)
        return started or None, ended or None

    def lineage_rows(self):
        """This parent's groups (latest first, bounded), each worker followed by its own delegations, read-only, to a bounded depth."""
        out = []
        def walk(groups, parent_worker, depth):
            for group in groups:
                for w in self.journal.all('SELECT * FROM workers WHERE group_id=? ORDER BY rowid', (group['group_id'],)):
                    out.append(self.worker_row(w, group, parent_worker))
                    if w['peer_key'] and depth < LINEAGE_DEPTH:
                        walk(self.journal.all('SELECT * FROM groups WHERE parent_key=? ORDER BY rowid', (w['peer_key'],)), w['worker_id'], depth + 1)
        marks = ','.join('?' * len(self.peers))
        walk(self.journal.all(f'SELECT * FROM groups WHERE parent_key IN ({marks}) ORDER BY rowid DESC LIMIT ?', (*self.peers, LINEAGE_GROUPS))[::-1], None, 1)
        return out

    async def lineage(self, cursor, wait_ms):
        """Changes since `cursor` (a digest of the rows). Waits on the journal's data_version and local changes, never spinning."""
        deadline = time.monotonic() + wait_ms / 1000;delay = 0.1;stamp = None;computed = 0
        while True:
            mark = (self.journal.db.execute('PRAGMA data_version').fetchone()[0], self.revision)
            if mark != stamp or time.monotonic() - computed > 5:
                stamp, computed = mark, time.monotonic()
                rows = self.lineage_rows();digest = sha256(rows)[:24]
                if digest != cursor:return dict(cursor=digest, changed=True, workers=rows)
            remaining = deadline - time.monotonic()
            if remaining <= 0:return dict(cursor=digest, changed=False, workers=rows)
            await self.changed_within(min(delay, remaining));delay = min(delay * 1.5, 1.0)

    # --- spawn and binding (Path A)
    def worker_spec(self, body):
        worker = dict(body.get('worker') or {})
        require(worker.get('provider') in PROVIDERS, 'PROTOCOL_ERROR', 'A worker specification with a known provider is required')
        # A worker always starts in the parent's registered workspace, as with npeer spawn; a different directory is refused, not ignored.
        session_cwd = self.peers[self.current]['cwd'];asked = worker.pop('cwd', None)
        require(asked is None or isinstance(asked, str) and Path(session_cwd, asked).expanduser().resolve() == Path(session_cwd).resolve(),
                'PROTOCOL_ERROR', f"np workers run in the session's directory ({session_cwd})", details={'cwd': asked, 'session_cwd': session_cwd})
        chosen = defaults(self.journal, worker['provider'])
        if 'model' not in worker and isinstance(chosen.get('model'), str):worker['model'] = chosen['model']
        effort = chosen.get('effort', chosen.get('reasoning_effort'))
        if 'reasoning_effort' not in worker and isinstance(effort, str):worker['reasoning_effort'] = effort
        if worker.get('permission_profile') == 'workspace' and 'workspace' not in worker:worker['workspace'] = {'mode': 'isolated-worktree'}
        return worker

    async def spawn(self, body):
        caller = self.caller();worker = self.worker_spec(body)
        if body.get('check_only'):
            plan = await self.core.delegation.spawn(caller, {'schema_version': 1, 'workers': [worker], 'completion_mode': 'wait'}, check=True)
            refusal(plan);planned = plan['workers'][0]
            return dict(ok=True, model=planned.get('model'), effort=planned.get('reasoning_effort'), profile=planned.get('permission_profile'))
        agent, request_id, watch = body.get('claude_agent_id'), body.get('client_request_id'), dict(body.get('mirror') or {})
        require(isinstance(agent, str) and AGENT.fullmatch(agent), 'PROTOCOL_ERROR', 'claude_agent_id is required')
        require(watch.get('owner') == 'claude-mod' and watch.get('claude_agent_id', agent) == agent and watch.get('claude_session', self.session) == self.session,
                'PROTOCOL_ERROR', 'The mirror must name the claude-mod owner, this session and this agent')
        request = {'schema_version': 1, 'client_request_id': request_id, 'completion_mode': 'wait',
                   'workers': [dict(worker, mirror=dict(owner='claude-mod', claude_session=self.session, claude_agent_id=agent))]}
        prior, digest = self.remembered(caller, 'spawn', request_id, request)
        self.agents[agent] = (caller.key, request_id)
        if prior:return prior
        self.intend(caller, 'spawn', request_id, digest, dict(request=request, claude_agent_id=agent))
        self.start_spawn(caller, request, agent)
        return dict(state='pending', client_request_id=request_id, claude_agent_id=agent)

    def start_spawn(self, caller, request, agent):
        request_id = request['client_request_id']
        if request_id not in self.inflight:
            self.inflight[request_id] = asyncio.get_running_loop().create_task(self.run_spawn(caller, request, agent))

    async def run_spawn(self, caller, request, agent):
        request_id = request['client_request_id'];spec = request['workers'][0]
        try:
            try:
                status = await self.core.delegation.spawn(caller, request);error = None
            except Exception as failure:
                # Uncertain startup after reservation leaves a group that may still run: bind to it rather than end the shadow.
                error = dict(code=failure.code, message=failure.message) if isinstance(failure, PeerError) else dict(code='SPAWN_FAILED', message=type(failure).__name__)
                prior = self.journal.one('SELECT g.group_id FROM spawn_requests s JOIN groups g USING(group_id) WHERE s.caller_key=? AND s.client_request_id=? AND g.state!=?',
                                         (caller.key, request_id, 'FAILED'))
                status = self.core.delegation.status(caller, prior['group_id']) if prior else None
            if status:
                worker = status['workers'][0];task = next(t for t in status['tasks'] if t['worker_id'] == worker['worker_id'])
                outcome = dict(state='bound', worker_id=worker['worker_id'], group_id=status['group_id'], task_id=task['task_id'], generation=1,
                               model=worker['model_requested'], effort=worker['effort_requested'], profile=spec.get('permission_profile', 'analysis'),
                               **({'startup_error': error} if error else {}))
            else:
                outcome = dict(state='failed', error=error)
        except Exception as failure:
            outcome = dict(state='failed', error=dict(code='SPAWN_FAILED', message=type(failure).__name__))
        self.inflight.pop(request_id, None)
        return self.settle(caller, 'spawn', request_id, outcome)

    def find_binding(self, agent):
        known = self.agents.get(agent)
        if known:
            row = self.journal.one("SELECT * FROM bridge_requests WHERE caller_key=? AND operation='spawn' AND client_request_id=?", known)
        else:
            marks = ','.join('?' * len(self.peers))
            row = self.journal.one(f"SELECT * FROM bridge_requests WHERE operation='spawn' AND caller_key IN ({marks}) AND json_extract(request_json,'$.claude_agent_id')=? ORDER BY at DESC LIMIT 1",
                                   (*self.peers, agent))
        if not row:return None
        if row['response_json']:return json.loads(row['response_json'])
        if row['client_request_id'] not in self.inflight:
            # A bridge that restarted mid-spawn resumes it: spawn is idempotent by request ID and content.
            self.start_spawn(self.caller(row['caller_key']), json.loads(row['request_json'])['request'], agent)
        return dict(state='pending', client_request_id=row['client_request_id'])

    async def binding(self, agent, wait_ms):
        deadline = time.monotonic() + wait_ms / 1000
        while True:
            found = self.find_binding(agent);remaining = deadline - time.monotonic()
            if found and found['state'] != 'pending' or remaining <= 0:
                moved = self.continuation(found['worker_id']) if found and found['state'] == 'bound' else None
                return dict(found, **({'continued_by': moved} if moved else {})) if found else dict(state='pending')
            task = self.inflight.get(found['client_request_id']) if found else None
            if task:await asyncio.wait({task}, timeout=remaining)
            else:await self.changed_within(min(remaining, 1))

    # --- activity
    async def activity(self, worker_id, after, wait_ms, maximum, item):
        """The M1 reader. With `item`, also that item's generation's task.terminal, so a mirror call can report an interrupted tool."""
        row = self.worker(worker_id)
        reader = Reader(self.journal.root, worker_id);deadline = time.monotonic() + wait_ms / 1000
        # Only a finished worker that never wrote a log is rebuilt: a new one has no log yet either, and will write one.
        if not reader.files() and row['state'] in ('RELEASED_RESUMABLE', 'FAILED', 'EXITED') and not self.active_tasks(worker_id):
            return await self.backfilled(row, after, wait_ms, maximum, item)
        if item is None:
            page = await reader.wait(after, wait_ms=wait_ms, limit=maximum)
            return self.page(page, page['events'], self.continuation(worker_id))
        span = self.journal.one('SELECT generation FROM activity_index WHERE worker_id=? AND first_seq<=? ORDER BY first_seq DESC LIMIT 1', (worker_id, after))
        generation = span['generation'] if span else None;cursor = after
        while True:
            page = await reader.wait(cursor, wait_ms=max(0, int((deadline - time.monotonic()) * 1000)))
            events = [e for e in page['events'] if e.get('item') == item or e['kind'] == 'task.terminal' and generation in (None, e['generation'])][:maximum]
            cursor = page['next_seq'] if len(events) < maximum else events[-1]['seq']
            if events or page['gap'] or time.monotonic() >= deadline:
                return self.page(dict(page, next_seq=cursor), events, self.continuation(worker_id))

    async def backfilled(self, row, after, wait_ms, maximum, item):
        """A worker with no activity log at all: events rebuilt read-only from its native history, served like a log."""
        from .activity.backfill import backfill, history
        from .launch import recorded_home
        home = recorded_home(self.journal, row);found = history(row['provider'], home, row['native_id'])
        try:signature = (str(found), found.stat().st_size, found.stat().st_mtime_ns) if found else None
        except OSError:signature = None
        cached = self.backfills.get(row['worker_id'])
        if not cached or cached[0] != signature:
            cached = self.backfills[row['worker_id']] = (signature, backfill(self.journal, row, home))
        matching = [e for e in cached[1] if e['seq'] > after and (item is None or e.get('item') == item or e['kind'] == 'task.terminal')]
        if not matching:await self.changed_within(wait_ms / 1000)  # Nothing is appended to a rebuilt history: wait out the poll.
        last = cached[1][-1]['seq'] if cached[1] else 0;events = matching[:maximum]
        return dict(events=events, last_seq=last, next_seq=events[-1]['seq'] if events else max(after, last), more=len(matching) > maximum, gap=None, backfill=True)

    def continuation(self, worker_id):
        """A provider fallback moves the worker's task to a replacement worker: `pending` while it is prepared, then `moved`, with where to follow it."""
        latest = self.journal.one('SELECT task_id,state FROM tasks WHERE worker_id=? ORDER BY rowid DESC LIMIT 1', (worker_id,))
        if latest and latest['state'] == 'FALLBACK_PENDING':
            return dict(state='pending', from_generation=mirror.task_generation(self.journal, latest['task_id']), task_id=latest['task_id'])
        moved = self.journal.one('SELECT a.task_id,t.worker_id FROM task_attempts a JOIN tasks t USING(task_id) WHERE a.worker_id=? ORDER BY a.rowid DESC LIMIT 1', (worker_id,))
        if not moved or moved['worker_id'] == worker_id:return None
        span = self.journal.one('SELECT generation FROM activity_index WHERE worker_id=? AND task_id=? ORDER BY first_seq DESC LIMIT 1', (worker_id, moved['task_id']))
        return dict(state='moved', from_generation=span['generation'] if span else None, worker_id=moved['worker_id'], task_id=moved['task_id'],
                    generation=mirror.task_generation(self.journal, moved['task_id']))

    def page(self, page, events, continued=None):
        gap = page.get('gap')
        if gap:
            # Shown in the shadow as a dim marker; the seq sits just before the first event still kept.
            events = [dict(schema=SCHEMA, seq=gap['to_seq'], at=None, worker_id=None, group_id=None, task_id=None, generation=None, native_turn=None, item=None,
                           kind='gap', data=dict(from_seq=gap['from_seq'], reason=gap['reason']), source=dict(provider=None, event='bridge/gap'))] + list(events)
        return dict(events=events, last_seq=page.get('last_seq') or 0, next_seq=page.get('next_seq'), more=page.get('more', False), gap=gap,
                    **({'continued_by': continued} if continued else {}))

    # --- input, interrupt, cancel
    async def input(self, worker_id, body):
        row = self.worker(worker_id);caller = self.caller(row['parent_key'])
        text, origin, mode, owner = bounded_text(body.get('text')), body.get('origin'), body.get('mode', 'queue'), body.get('delivery_owner', 'native')
        require(text.strip(), 'PROTOCOL_ERROR', 'Input text is empty')
        require(origin in ORIGINS and mode in ('queue', 'steer') and owner in ('mirror', 'native'), 'PROTOCOL_ERROR', 'Unknown input origin, mode or delivery owner')
        payload = dict(worker_id=worker_id, text=text, origin=origin, mode=mode, delivery_owner=owner);request_id = body.get('client_request_id')
        async with self.locks.setdefault(worker_id, asyncio.Lock()):  # One worker's inputs are delivered in arrival order.
            prior, digest = self.remembered(caller, 'input', request_id, payload)
            if prior:return prior
            started = self.journal.one("SELECT json_extract(request_json,'$.steering') AS steering FROM bridge_requests WHERE caller_key=? AND operation='input' AND client_request_id=?",
                                       (caller.key, request_id))
            # A steer whose outcome was not observed is never sent again, nor queued as a second copy.
            require(not (started and started['steering']), 'DELIVERY_UNKNOWN', 'An earlier steer of this input may have reached the worker', may_have_received=True)
            lease = mirror.live_lease(self.journal, worker_id);held = lease if lease and lease['claude_session'] in self.sessions else None
            meta = dict(payload, client_request_id=request_id, claude_session=held['claude_session'] if held else self.session, claude_agent_id=held['claude_agent_id'] if held else None)
            self.intend(caller, 'input', request_id, digest, meta)
            return self.settle(caller, 'input', request_id, await self.deliver(caller, worker_id, meta))

    async def deliver(self, caller, worker_id, meta):
        deadline = time.monotonic() + 10
        while True:
            row = self.journal.one('SELECT * FROM workers WHERE worker_id=?', (worker_id,))
            latest = self.journal.one('SELECT * FROM tasks WHERE worker_id=? ORDER BY rowid DESC LIMIT 1', (worker_id,))
            if row['state'] in DELIVERABLE and row['peer_key'] and mirror.supervising(self.journal, row['group_id']) or row['state'] == 'RELEASED_RESUMABLE':break
            if row['state'] in ('FAILED', 'EXITED'):
                return dict(disposition='refused', reason='WORKER_' + row['state'], task_id=None, generation=None, delivery_owner=None)
            require(time.monotonic() < deadline, 'CALLER_NOT_READY', 'The worker is starting or releasing; retry the same request', details={'state': row['state']})
            await asyncio.sleep(0.2)
        reason = 'steer-unsupported' if meta['mode'] == 'steer' else None
        if meta['mode'] == 'steer' and latest and latest['state'] == 'RUNNING' and row['state'] != 'RELEASED_RESUMABLE' and self.steers(row['provider']):
            steered = await self.steer(caller, row, latest, meta)
            if steered:return steered
            reason = 'steer-refused'
        if row['state'] == 'RELEASED_RESUMABLE':
            from .recovery import resume_worker
            try:
                value = await resume_worker(self.journal, caller, worker_id, meta['text'], None, meta['client_request_id'], origin=meta['origin'],
                                            delivery=dict(owner=meta['delivery_owner'], claude_session=meta['claude_session'], claude_agent_id=meta['claude_agent_id']))
            except PeerError as error:
                if error.code != 'OWNERSHIP_CONFLICT':raise
                # Another runtime holds the released history: never a second writer, and never retried as one.
                return dict(disposition='refused', reason='OWNERSHIP_CONFLICT', message=error.message, task_id=None, generation=None, delivery_owner=None)
            task_id, disposition = value['task_id'], 'resumed'
        else:
            from .receipts import delivery_error
            outcome = await self.core.router.send(caller, row['peer_key'], meta['text'], client_request_id=meta['client_request_id'])
            self.journal.event(outcome['message_id'], 'input-origin', dict(origin=meta['origin'], client_request_id=meta['client_request_id']))
            task_id = self.journal.one('SELECT task_id FROM messages WHERE message_id=?', (outcome['message_id'],))['task_id']
            if not task_id:
                error = delivery_error(outcome) or PeerError('DELIVERY_UNKNOWN', 'The follow-up was not admitted', may_have_received=True)
                state = self.journal.one('SELECT state FROM workers WHERE worker_id=?', (worker_id,))['state']
                # Definitely not admitted because the controller was releasing: the released history is resumed instead.
                if error.may_have_received or meta.get('released') or state not in ('RELEASING', 'RELEASED_RESUMABLE'):raise error
                return await self.deliver(caller, worker_id, dict(meta, released=True))
            disposition = 'queued'
        owner = self.journal.one('SELECT owner FROM task_delivery WHERE task_id=?', (task_id,))
        return dict(disposition=disposition, task_id=task_id, generation=mirror.task_generation(self.journal, task_id),
                    delivery_owner=owner['owner'] if owner else 'native', **({'reason': reason} if reason else {}))

    async def steer(self, caller, row, task, meta):
        from .control import group_call
        mark = "UPDATE bridge_requests SET request_json=json_set(request_json,'$.steering',json(?)) WHERE caller_key=? AND operation='input' AND client_request_id=?"
        self.journal.db.execute(mark, ('true', caller.key, meta['client_request_id']))
        try:
            value = await group_call(self.journal, row['group_id'], 'steer', {'caller_key': caller.key, 'handle': task['task_id'], 'text': meta['text'],
                                                                             'client_request_id': meta['client_request_id'], 'origin': meta['origin']}, timeout=15)
        except PeerError as error:
            if not error.may_have_received:self.journal.db.execute(mark, ('false', caller.key, meta['client_request_id']))
            raise
        self.journal.db.execute(mark, ('false', caller.key, meta['client_request_id']))
        if not value.get('steered'):return None
        return dict(disposition='steered', task_id=task['task_id'], generation=mirror.task_generation(self.journal, task['task_id']),
                    delivery_owner=(self.journal.one('SELECT owner FROM task_delivery WHERE task_id=?', (task['task_id'],)) or {}).get('owner', 'native'))

    def active_tasks(self, worker_id):
        """The worker's unfinished tasks, the running one first, then queued follow-ups in the order they would run."""
        return self.journal.all("SELECT * FROM tasks WHERE worker_id=? AND state IN ('QUEUED','RUNNING','WAITING','APPROVAL_BLOCKED','FALLBACK_PENDING') "
                                "ORDER BY state='QUEUED',rowid", (worker_id,))

    async def interrupt(self, worker_id, body):
        row = self.worker(worker_id);caller = self.caller(row['parent_key']);request_id = body.get('client_request_id')
        prior, digest = self.remembered(caller, 'interrupt', request_id, dict(worker_id=worker_id))
        if prior:return prior
        task = next((t for t in self.active_tasks(worker_id) if t['state'] == 'RUNNING'), None)
        if not task:return dict(state='NOT_RUNNING', task_id=None, generation=None)
        self.intend(caller, 'interrupt', request_id, digest, dict(worker_id=worker_id, task_id=task['task_id']))
        from .control import group_call
        value = await group_call(self.journal, row['group_id'], 'interrupt', {'caller_key': caller.key, 'handle': task['task_id']}, timeout=10)
        return self.settle(caller, 'interrupt', request_id, dict(value, generation=mirror.task_generation(self.journal, task['task_id'])))

    async def cancel_worker(self, worker_id, body):
        row = self.worker(worker_id);caller = self.caller(row['parent_key']);request_id = body.get('client_request_id')
        reason = body.get('reason') or 'Canceled from Claude Code'
        prior, digest = self.remembered(caller, 'cancel', request_id, dict(worker_id=worker_id, reason=reason))
        if prior:return prior
        tasks = self.active_tasks(worker_id)
        if not tasks:return dict(state='NOT_RUNNING', task_id=None, generation=None, canceled=[])
        self.intend(caller, 'cancel', request_id, digest, dict(worker_id=worker_id, task_ids=[t['task_id'] for t in tasks]))
        # The existing per-task cancel, for the current task and the follow-ups queued behind it: a stopped shadow leaves nothing to run.
        values = [await self.core.delegation.cancel(caller, t['task_id'], reason) for t in tasks]
        return self.settle(caller, 'cancel', request_id, dict(values[0], task_id=tasks[0]['task_id'], generation=mirror.task_generation(self.journal, tasks[0]['task_id']),
                                                              canceled=[t['task_id'] for t in tasks]))

    async def cancel_group(self, group_id, body):
        group = self.journal.one('SELECT * FROM groups WHERE group_id=?', (group_id,))
        require(group and group['parent_key'] in self.peers, 'APPROVAL_BLOCKED', 'Group is outside this Claude session\'s own delegations')
        caller = self.caller(group['parent_key']);request_id = body.get('client_request_id');reason = body.get('reason') or 'Canceled from Claude Code'
        prior, digest = self.remembered(caller, 'cancel-group', request_id, dict(group_id=group_id, reason=reason))
        if prior:return prior
        self.intend(caller, 'cancel-group', request_id, digest, dict(group_id=group_id))
        return self.settle(caller, 'cancel-group', request_id, await self.core.delegation.cancel(caller, group_id, reason))

    # --- results, artifacts, resume command
    async def result(self, worker_id):
        row = self.worker(worker_id);caller = self.caller(row['parent_key'])
        task = self.journal.one('SELECT * FROM tasks WHERE worker_id=? AND (result_artifact_id IS NOT NULL OR state IN (%s)) ORDER BY rowid DESC LIMIT 1'
                                % ','.join('?' * len(TERMINAL)), (worker_id, *TERMINAL))
        require(task, 'PROTOCOL_ERROR', 'The worker has no task result yet')
        found = [r for r in self.core.delegation.result(caller, task['task_id'])['results'] if r['task_id'] == task['task_id']]
        require(found, 'PROTOCOL_ERROR', 'The worker has no task result yet')
        record = found[0];changes = record.get('changes') or {};patch = changes.get('patch_artifact')
        stats = dict(added=0, removed=0)
        if patch:
            try:
                from .artifacts import Artifacts
                Artifacts(self.journal).read(patch, caller.key, 0, 1)
                stats = diff_stats((self.journal.root / 'artifacts' / patch).read_bytes()[:4 << 20].decode(errors='replace'))
            except (PeerError, OSError):pass
        started, completed = record.get('started_at'), record.get('completed_at')
        return dict(task_id=task['task_id'], generation=mirror.task_generation(self.journal, task['task_id']), status=record.get('status'),
                    provider=(record.get('worker') or {}).get('provider') or row['provider'], model_requested=record.get('model_requested') or row['model_requested'],
                    effort=record.get('reasoning_effort_requested') or row['effort_requested'],
                    model_observed=record.get('model_observed'), native_id=(record.get('worker') or {}).get('native_session_id') or row['native_id'],
                    elapsed_ms=int((completed - started) * 1000) if started and completed else None, result_artifact_id=record.get('result_artifact_id'),
                    patch_artifact=patch, changes=dict(files=changes.get('file_count') or 0, worktree=changes.get('worktree'), **stats))

    async def resume_command(self, worker_id):
        from .recovery import resume_command
        row = self.worker(worker_id)
        return resume_command(self.journal, self.caller(row['parent_key']), worker_id)

    async def artifact(self, artifact_id, offset, limit):
        from .artifacts import Artifacts, RESULT_PAGE
        last = None
        for key in self.peers:
            try:return Artifacts(self.journal).read(artifact_id, key, offset, max(1, min(limit, RESULT_PAGE)))
            except PeerError as error:
                if error.code != 'APPROVAL_BLOCKED':raise
                last = error
        raise last

    # --- mirror lease and route
    async def mirror_status(self):
        mirror.expire(self.journal)
        return dict(claude_sessions=self.sessions, ttl_seconds=mirror.LEASE_TTL,
                    leases=[dict(worker_id=l['worker_id'], claude_agent_id=l['claude_agent_id'], claude_session=l['claude_session'],
                                 display_only=bool(l['display_only']), expires_at=l['expires_at']) for l in mirror.held(self.journal, self.sessions)])

    async def mirror(self, op, body):
        worker_id = body.get('worker_id')
        if op in ('fault', 'fallthrough'):
            # Recorded only: the worker is unaffected.
            agent = body.get('claude_agent_id')
            require(worker_id is None or self.worker(worker_id), 'PROTOCOL_ERROR', 'Unknown worker')
            details = {k: str(body[k])[:500] for k in ('kind', 'message', 'reason', 'claude_agent_id', 'worker_id') if body.get(k) is not None}
            self.journal.event(worker_id or agent or self.current, 'mirror-' + op, dict(details, claude_session=self.session))
            return dict(op=op, recorded=True)
        row = self.worker(worker_id)
        if op == 'attach':
            agent = body.get('claude_agent_id')
            require(body.get('check_only') or isinstance(agent, str) and AGENT.fullmatch(agent), 'PROTOCOL_ERROR', 'claude_agent_id is required')
            value = mirror.attach(self.journal, worker_id, self.session, agent, row['parent_key'], sessions=self.sessions,
                                  display_only=body.get('display_only', True), check_only=bool(body.get('check_only')))
        elif op == 'release':value = mirror.release(self.journal, worker_id, self.sessions)
        elif op == 'surfacing':value = mirror.surfacing(self.journal, worker_id, self.sessions, task_id=body.get('task_id'), generation=body.get('generation'))
        else:value = mirror.ack(self.journal, worker_id, self.sessions, task_id=body.get('task_id'), generation=body.get('generation'), outcome=body.get('outcome'))
        self.touch()
        return dict(value, op=op)


async def catalog(journal, caller, providers):
    """Exact native model IDs and efforts per usable provider, the caller's profiles and a cached quota summary. Metadata queries only."""
    from .launch import home_environment, provider_home
    entries = {}
    for provider in providers:
        entry = dict(defaults=defaults(journal, provider), models=[])
        try:
            home = provider_home(provider)
            if provider == 'codex':
                from .adapters.codex_app_server import AppServer
                from .worker_visibility import FLAG
                client = AppServer(Path.home() / '.local/share/codex-mcp-manager/current/bin/codex', environment=home_environment('codex', home), server_arguments=[FLAG])
                try:
                    await client.start()
                    listed = (await client.call('model/list', {'includeHidden': True, 'limit': 100}))['data']
                finally:
                    await client.close()
                entry['models'] = [dict(id=m['model'], efforts=[e['reasoningEffort'] for e in m.get('supportedReasoningEfforts', [])],
                                        default_effort=m.get('defaultReasoningEffort')) for m in listed]
            elif provider == 'antigravity':
                from .antigravity import catalog as agy_catalog
                entry['models'] = [dict(id=slug, name=name, efforts=[]) for slug, name in (await agy_catalog(Path.home() / '.local/bin/agy')).items()]
            elif provider == 'grok':
                from .grok import executable
                from .grok_worker import catalog as grok_catalog
                listed = await grok_catalog(executable(), home=home)
                entry.update(models=[dict(id=m, efforts=[]) for m in listed['models']], default_model=listed.get('default'))
            else:
                from .models import CLAUDE_ALIAS_FAMILIES
                settings = home / 'settings.json'
                entry.update(models=[dict(id=alias, efforts=[]) for alias in CLAUDE_ALIAS_FAMILIES], native_catalog=False,
                             default_model=json.loads(settings.read_text()).get('model', 'sonnet') if settings.exists() else 'sonnet')
        except (PeerError, OSError, ValueError, KeyError) as error:
            entry['error'] = error.json() if isinstance(error, PeerError) else dict(code='MODEL_UNAVAILABLE', message=type(error).__name__)
        entries[provider] = entry
    quota = {}
    try:
        from .usage.service import Usage
        for view in (await Usage(journal).status(caller))['providers']:
            quota[view.get('provider')] = dict(age_seconds=view.get('age_seconds'), source=view.get('source'),
                                               pools=[{k: p.get(k) for k in ('pool_id', 'label', 'state', 'used', 'remaining', 'resets_at') if k in p} for p in view.get('pools', [])])
    except (PeerError, OSError, ValueError) as error:
        quota['error'] = error.json() if isinstance(error, PeerError) else dict(code='UNKNOWN', message=type(error).__name__)
    return dict(providers=entries, profiles=caller.grants.get('profiles', ['analysis']), quota=quota)


def for_cli(journal, core, caller, session=None):
    """The backend for one `npeer input|interrupt|mirror`: bound by --claude-session like the bridge, or by the ordinary caller binding."""
    if session:
        return NativeBackend(journal, bind_session(journal, core.registry, session), session)
    require(caller.kind == 'peer' and caller.incarnation, 'CALLER_UNBOUND', 'This operation requires a verified native caller or --claude-session')
    peer = core.registry.verify(caller.key, caller.incarnation)
    return NativeBackend(journal, peer, peer['peer_key']['native_session_id'])


def bind_registered(journal, registry, session, wait=8.0):
    """bind_session, waiting while the session is not registered yet: the mod starts its bridge at session start, and on
    --resume NativePeers' own SessionStart hook registers the new incarnation a moment later. Other refusals are final."""
    deadline = time.monotonic() + wait;desktop = False
    while True:
        try:
            return bind_session(journal, registry, session)
        except PeerError as error:
            pending = error.code == 'CALLER_UNBOUND' and set((error.details or {}).keys()) == {'claude_session'}
            if not pending or time.monotonic() >= deadline:raise
            if not desktop:
                desktop = True;register_desktop(journal, session)
                continue
            time.sleep(0.25)


def register_desktop(journal, session):
    """A desktop engine's first session: its SessionStart hook refused because the engine has no approval yet, a check its
    short timeout leaves to the bridge. Approve it here and register through the hook's own path (§6.7). An approved engine
    is left to its hook."""
    from .caller import nearest_native
    from .desktop_host import approved, candidate
    from .hooks import observe
    nearest = nearest_native([p.json() for p in ancestry()[1:]], journal=journal)
    if not nearest or not candidate(nearest['executable']) or approved(journal, nearest['executable']):return
    observe('claude', {'hook_event_name': 'SessionStart', 'session_id': session, 'cwd': os.getcwd()}, journal, approve_desktop=True)


def claim_free(socket):
    """The session's socket, or a successor while another bridge for this session still serves it.

    A hot-reloaded mod starts its bridge before the previous module instance has finished its held steps; Claude Code
    stops that instance's bridge only when the instance unloads. Both bridges serve this session through the journal."""
    from .bridge import claim_socket
    for n in range(5):
        path = socket if n == 0 else socket.with_name(f'{socket.stem}.{n}{socket.suffix}')
        try:
            claim_socket(path);return path
        except PeerError as error:
            if error.code != 'BRIDGE_RUNNING' or n == 4:raise
    return socket


def manifest_path(journal, socket):
    return private_directory(Path(journal.root) / 'bridges') / (Path(socket).stem + '.json')


def live_bridges(journal):
    """Session bridges of this state root whose recorded process is still the same live process."""
    out = []
    for path in sorted((Path(journal.root) / 'bridges').glob('*.json')):
        try:value = json.loads(path.read_text())
        except (OSError, ValueError):continue
        if matches(value.get('process') or {}) and Path(value.get('socket', '')).exists():
            out.append(dict(socket=value['socket'], peer=value.get('peer'), claude_sessions=value.get('claude_sessions'), pid=value['process']['pid'], started_at=value.get('started_at')))
    return out


def run(args):
    """`npeer bridge --claude-session ID --protocol 1 --json`: one bridge for one Claude Code session. Prints one JSON line first."""
    from .bridge import Bridge, PROTOCOL, socket_path
    from .journal import Journal
    from .registry import Registry
    journal = manifest = None
    try:
        require(args.protocol == PROTOCOL, 'PROTOCOL_ERROR', f'Bridge protocol {args.protocol} is not supported; this NativePeers speaks {PROTOCOL}')
        journal = Journal()
        peer = bind_registered(journal, Registry(journal), args.claude_session)
        from . import delegation
        delegation.REPARENT_SUPERVISORS = True
        socket = claim_free(socket_path(args.claude_session))
        manifest = manifest_path(journal, socket)
        backend = NativeBackend(journal, peer, args.claude_session, runtimes=runtimes(journal), manifest=manifest)
        backend.record_manifest(socket)
        async def serve():
            bridge = Bridge(backend, socket)
            asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, bridge.stop, 'terminated')
            return await bridge.run()
        return dict(stopped=asyncio.run(serve()))
    except (PeerError, OSError) as error:
        error = error if isinstance(error, PeerError) else PeerError('BRIDGE_UNSAFE', str(error)[:300])
        print(json.dumps(dict(ready=False, error=error.json()), separators=(',', ':')), flush=True)
        return None
    finally:
        if manifest:manifest.unlink(missing_ok=True)
        if journal:journal.close()
