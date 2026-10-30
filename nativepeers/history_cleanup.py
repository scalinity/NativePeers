"""History-preserving cleanup of exact, released NativePeers Codex workers.

Only NativePeers' journal is written here. Provider metadata is opened read-only;
the native archive/unarchive API owns every provider-history mutation.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
import json
from pathlib import Path
import shlex
import sqlite3
import time
import uuid

from .adapters.codex_app_server import AppServer
from .compatibility import verify
from .errors import PeerError, require
from .files import async_advisory_lock, atomic_json, sha256
from .launch import recorded_home
from .models import home_id, new_id
from .ownership import release_status
from .process import owned_status


TERMINAL = ('COMPLETED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'PARTIAL', 'INTERRUPTED')
UNRESOLVED = ('OUTBOX_COMMITTED', 'ADMISSION_DEFERRED', 'SUBMITTING', 'DELIVERY_UNKNOWN', 'HELD', 'SUBMITTED_UNCONFIRMED')


def _uuid(value):
    require(isinstance(value, str), 'PROTOCOL_ERROR', 'Expected an exact UUID')
    try:
        require(str(uuid.UUID(value)) == value, 'PROTOCOL_ERROR', 'Expected a canonical UUID')
    except ValueError:
        raise PeerError('PROTOCOL_ERROR', 'Expected an exact UUID') from None
    return value


def native_metadata(home, native_id, *, subtree=False):
    """Read only selected IDs, pin/archive state and native spawn relationships.

    This schema is the installed Codex state-v5 contract. Refuse other schemas
    rather than infer that missing relationship/pin metadata means safe cleanup.
    No title, preview, message, rollout or credential is read.
    """
    home = Path(home)
    require(home.is_absolute() and home.resolve() == home, 'OWNERSHIP_UNKNOWN', 'Provider home is not an exact canonical path')
    database = home/'state_5.sqlite'
    require(database.is_file() and not database.is_symlink(), 'CAPABILITY_MISMATCH', 'Codex state-v5 metadata is unavailable')
    with sqlite3.connect(database.as_uri()+'?mode=ro', uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        columns = {r[1] for r in db.execute('PRAGMA table_info(threads)')}
        edges = {r[1] for r in db.execute('PRAGMA table_info(thread_spawn_edges)')}
        require({'id', 'archived', 'is_pinned'} <= columns and {'parent_thread_id', 'child_thread_id'} <= edges,
                'CAPABILITY_MISMATCH', 'Codex archive/pin/relationship metadata schema differs')
        ids = [_uuid(native_id)]
        if subtree:
            # UNION bounds cycles; a second bound refuses unexpectedly large trees.
            rows = db.execute('WITH RECURSIVE subtree(id) AS (SELECT ? UNION SELECT child_thread_id FROM thread_spawn_edges JOIN subtree ON parent_thread_id=subtree.id) SELECT id FROM subtree LIMIT 257', (native_id,)).fetchall()
            require(len(rows) <= 256, 'OWNERSHIP_UNKNOWN', 'Native archive subtree exceeds the cleanup bound')
            ids = [native_id] + sorted(r['id'] for r in rows if r['id'] != native_id)
        result = []
        for selected in ids:
            _uuid(selected)
            row = db.execute('SELECT id,archived,is_pinned FROM threads WHERE id=?', (selected,)).fetchone()
            require(row is not None, 'OWNERSHIP_UNKNOWN', 'Selected native history metadata is missing', details={'native_id': selected})
            result.append(dict(native_id=row['id'], archived=bool(row['archived']), pinned=bool(row['is_pinned'])))
        return result


def _home(journal, row):
    lease = json.loads(row['lease_json'])
    # recorded_home's ambient fallback is useful for legacy launch, but is not
    # provenance sufficient for an operation that can hide existing histories.
    require(lease.get('provider_home') or row.get('peer_key'), 'OWNERSHIP_UNKNOWN', 'Recorded provider-home provenance is missing')
    return recorded_home(journal, row)


def resume_archive_state(journal, row):
    """Exact metadata prerequisite; unknown is an explicit failure, never false."""
    receipt = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind IN ('history-archived','history-archive-intent') AND json_extract(payload_json,'$.native_id')=? ORDER BY sequence DESC LIMIT 1", (row['worker_id'], row['native_id']))
    if receipt is None:
        return dict(archived=None, checked=False, native_id=row['native_id'])
    home = _home(journal, row)
    require(json.loads(receipt['payload_json'])['provider_home'] == str(home), 'OWNERSHIP_CONFLICT', 'Cleanup receipt provider home differs from this worker')
    metadata = native_metadata(home, row['native_id'])[0]
    return dict(archived=metadata['archived'], checked=True, provider_home=str(home), native_id=row['native_id'])


def _released(journal, row):
    require(row['state'] == 'RELEASED_RESUMABLE', 'OWNERSHIP_CONFLICT', 'Worker has not released its writer')
    closure = release_status(journal, row)
    require(closure['released'], 'CLEANUP_UNCERTAIN', 'Current-generation process/output closure is unproved', details=closure)
    tasks = journal.all('SELECT task_id,state,terminal_reason FROM tasks WHERE worker_id=?', (row['worker_id'],))
    require(tasks and all(t['state'] in TERMINAL for t in tasks), 'OWNERSHIP_CONFLICT', 'Worker has active or queued tasks')
    require(not any('DELIVERY_UNKNOWN' in (t['terminal_reason'] or '') for t in tasks), 'DELIVERY_UNKNOWN', 'Worker retains unresolved native-input delivery')
    group = journal.one('SELECT * FROM groups WHERE group_id=?', (row['group_id'],))
    require(group and group['state'] in ('COMPLETED', 'PARTIAL', 'FAILED'), 'OWNERSHIP_CONFLICT', 'Group lifetime is not terminal')
    owner_path = journal.root/'groups'/row['group_id']/'owner.json'
    owner = json.loads(owner_path.read_text()) if owner_path.exists() else json.loads(group['owner_manifest'])
    require(owner.get('owner') and owned_status(owner['owner']) == 'DEAD', 'OWNERSHIP_CONFLICT', 'Group supervisor is live or its ownership is unknown')
    require(not owner.get('cleanup_unresolved'), 'CLEANUP_UNCERTAIN', 'Group retains unresolved process cleanup')
    routes = journal.all('SELECT state,mode,waiter_id,lease_deadline FROM completion_routes WHERE group_id=?', (row['group_id'],))
    require(not any(r['state'] in ('WAIT_LEASED', 'PUSH_SUBMITTING', 'HELD', 'MIRROR_PENDING', 'MIRROR_SURFACING') or
                    r['state'] == 'UNCLAIMED' and r['mode'] == 'push' for r in routes),
            'OWNERSHIP_CONFLICT', 'Group has a waiter or unresolved completion ownership')
    return dict(generation=row['generation'], process_closure=closure, task_count=len(tasks), supervisor='DEAD')


def _inspect(journal, worker_id, *, subtree=True):
    row = journal.one('SELECT * FROM workers WHERE worker_id=?', (_uuid(worker_id),))
    require(row is not None and row['provider'] == 'codex' and row['native_id'], 'OWNERSHIP_UNKNOWN', 'No exact owned Codex history for this worker')
    home = _home(journal, row)
    executable = json.loads(row['lease_json']).get('executable')
    require(isinstance(executable, str) and Path(executable).is_absolute(), 'CAPABILITY_MISMATCH', 'Worker has no exact pinned native executable')
    receipt = journal.root/'compat/runtime.json'
    approved = json.loads(receipt.read_text()).get('providers', {}).get('codex', {}) if receipt.exists() else {}
    metadata_executable = approved.get('path')
    require(isinstance(metadata_executable, str) and Path(metadata_executable).is_absolute(), 'CAPABILITY_MISMATCH', 'No currently approved native metadata runtime is installed')
    selected = native_metadata(home, row['native_id'], subtree=subtree)
    affected = []
    for metadata in selected:
        require(not metadata['pinned'], 'OWNERSHIP_CONFLICT', 'Pinned history is preserved', details={'native_id': metadata['native_id']})
        owners = [w for w in journal.all('SELECT * FROM workers WHERE provider=? AND native_id=?', ('codex', metadata['native_id'])) if _home(journal, w) == home]
        require(owners, 'OWNERSHIP_UNKNOWN', 'Native archive would affect a history without NativePeers worker provenance', details={'native_id': metadata['native_id']})
        evidence = []
        for worker in owners:
            peers = journal.all('SELECT owner_kind,descriptor_json,status FROM peer_instances WHERE provider=? AND native_id=? AND home_id=?', ('codex', metadata['native_id'], home_id(home)))
            require(not any(p['owner_kind'] not in ('owned-worker-controller',) for p in peers), 'OWNERSHIP_CONFLICT', 'History has an ordinary or promoted native registration')
            require(not any(owned_status(json.loads(p['descriptor_json'])['owner']) != 'DEAD' for p in peers if p['status'] == 'live'),
                    'OWNERSHIP_CONFLICT', 'A registered native writer is live or unknown')
            proof = _released(journal, worker)
            keys = {worker['peer_key']} if worker.get('peer_key') else set()
            keys.update(json.loads(p['descriptor_json']).get('handle') for p in peers)
            for key in keys - {None}:
                pending = journal.one('SELECT 1 FROM messages m JOIN attempts a USING(message_id) WHERE (m.to_key=? OR m.from_actor_key=?) AND a.state IN (?,?,?,?,?,?) LIMIT 1', (key, key, *UNRESOLVED))
                require(not pending, 'DELIVERY_UNKNOWN', 'Worker has unresolved routed delivery')
            pending = journal.one('SELECT 1 FROM messages m JOIN attempts a USING(message_id) WHERE m.from_actor_key LIKE ? AND a.state IN (?,?,?,?,?,?) LIMIT 1', ('controller:'+worker['group_id']+':%', *UNRESOLVED))
            require(not pending, 'DELIVERY_UNKNOWN', 'Group has unresolved completion delivery')
            evidence.append(dict(worker_id=worker['worker_id'], group_id=worker['group_id'], peer_key=worker['peer_key'], **proof))
        affected.append(dict(**metadata, ownership=evidence))
    return dict(worker_id=worker_id, native_id=row['native_id'], provider='codex', provider_home=str(home), affected=affected,
                eligible=True, operation='thread/archive', worker_executable=executable, metadata_executable=metadata_executable)


def _candidate(journal, worker_id, **kwargs):
    try:
        return _inspect(journal, worker_id, **kwargs)
    except (PeerError, OSError, ValueError, sqlite3.Error) as error:
        return dict(worker_id=worker_id, eligible=False, operation=None, reason_code=error.code if isinstance(error, PeerError) else 'OWNERSHIP_UNKNOWN',
                    reason=str(error), details=error.details if isinstance(error, PeerError) else {})


def _automation(journal, path):
    """Recognize the two completed, owned pro-review harness record schemas.

    The record is a selected input, corroborated with its sibling launch/prepared
    manifests and this exact journal. A title never establishes test provenance.
    """
    path = Path(path).resolve()
    require(path == journal.root.parent/'codex-case.json' and journal.root.name == 'state',
            'OWNERSHIP_UNKNOWN', 'Automation record must be the exact sibling codex-case.json for this state root')
    record = json.loads(path.read_text()); launch = json.loads(path.with_name('codex-launch.json').read_text())
    prepared = json.loads(path.with_name('prepared.json').read_text())
    require(record.get('provider') == 'codex' and record.get('status') in ('PASS', 'FAIL') and record.get('source') == prepared.get('source') and record.get('source'),
            'OWNERSHIP_UNKNOWN', 'Completed automation record and prepared source do not agree')
    native_id = _uuid(record.get('parent_native_id')); caller = record.get('caller')
    owner = launch.get('owner_expected_after_exec')
    require(owner and owned_status(owner) == 'DEAD', 'OWNERSHIP_CONFLICT', 'Automation parent launch is live or unknown')
    peers = journal.all('SELECT * FROM peer_instances WHERE peer_key=? AND provider=? AND native_id=?', (caller, 'codex', native_id))
    require(peers, 'OWNERSHIP_UNKNOWN', 'Automation parent is absent from the exact fixture registry')
    descriptors = [json.loads(p['descriptor_json']) for p in peers]
    homes = {p.get('home') for p in descriptors}
    require(len(homes) == 1 and None not in homes, 'OWNERSHIP_UNKNOWN', 'Automation parent home is ambiguous')
    home = Path(homes.pop())
    require(all(all(p['owner'].get(k) == owner.get(k) for k in ('pid', 'start', 'uid')) and owned_status(p['owner']) == 'DEAD' for p in descriptors),
            'OWNERSHIP_CONFLICT', 'Parent has a later, active or promoted native incarnation')
    groups = record.get('groups') or [record.get('group_id')]
    require(isinstance(groups, list) and 0 < len(groups) <= 100 and len(groups) == len(set(groups)), 'OWNERSHIP_UNKNOWN', 'Automation group provenance is incomplete')
    ownership = []
    for group_id in groups:
        _uuid(group_id)
        group = journal.one('SELECT parent_key FROM groups WHERE group_id=?', (group_id,))
        require(group and group['parent_key'] == caller, 'OWNERSHIP_UNKNOWN', 'Automation groups belong to a different parent')
        rows = journal.all('SELECT * FROM workers WHERE group_id=?', (group_id,))
        require(rows, 'OWNERSHIP_UNKNOWN', 'Automation group contains no retained worker provenance')
        for row in rows:
            proof = _released(journal, row)
            ownership.append(dict(worker_id=row['worker_id'], group_id=group_id, peer_key=row['peer_key'], **proof))
    pending = journal.one('SELECT 1 FROM messages m JOIN attempts a USING(message_id) WHERE (m.to_key=? OR m.from_actor_key=?) AND a.state IN (?,?,?,?,?,?) LIMIT 1', (caller, caller, *UNRESOLVED))
    require(not pending, 'DELIVERY_UNKNOWN', 'Automation parent has unresolved delivery')
    metadata = native_metadata(home, native_id, subtree=True)
    require(not metadata[0]['pinned'], 'OWNERSHIP_CONFLICT', 'Pinned automation parent is preserved')
    affected = [dict(**metadata[0], ownership=ownership)]
    # Any vendor-private descendant must independently have ordinary NativePeers
    # worker provenance. Unknown children block the whole parent archive.
    for child in metadata[1:]:
        rows = [w for w in journal.all("SELECT * FROM workers WHERE provider='codex' AND native_id=?", (child['native_id'],)) if _home(journal, w) == home]
        require(rows, 'OWNERSHIP_UNKNOWN', 'Automation archive would affect an unrelated native descendant')
        checked = _inspect(journal, rows[0]['worker_id'])
        affected.append(next(m for m in checked['affected'] if m['native_id'] == child['native_id']))
    approved = json.loads((journal.root/'compat/runtime.json').read_text()).get('providers', {}).get('codex', {})
    executable = approved.get('path')
    require(isinstance(executable, str) and Path(executable).is_absolute(), 'CAPABILITY_MISMATCH', 'No approved metadata runtime')
    return dict(worker_id=native_id, automation_manifest=str(path), provenance_sha256=sha256(dict(record=record, launch=launch, prepared=prepared)),
                native_id=native_id, provider='codex', provider_home=str(home), affected=affected, eligible=True,
                operation='thread/archive', metadata_executable=executable)


def _inspect_selection(journal, selection):
    return _automation(journal, selection['automation_manifest']) if selection.get('automation_manifest') else _inspect(journal, selection['worker_id'])


def preview(journal, *, worker_ids=None, automation_manifests=(), limit=100):
    require(type(limit) is int and 1 <= limit <= 100, 'PROTOCOL_ERROR', 'History selection limit must be 1 to 100')
    workers = worker_ids if worker_ids is not None else [r['worker_id'] for r in journal.all("SELECT worker_id FROM workers WHERE provider='codex' AND native_id IS NOT NULL ORDER BY rowid DESC LIMIT ?", (limit,))]
    require(isinstance(workers, list) and len(workers) <= 100 and len(set(workers)) == len(workers), 'PROTOCOL_ERROR', 'Select at most 100 unique worker UUIDs')
    for worker in workers: _uuid(worker)
    candidates = [_candidate(journal, worker) for worker in workers]
    require(len(candidates)+len(automation_manifests) <= 100, 'PROTOCOL_ERROR', 'History selection exceeds 100 records')
    for selected in automation_manifests:
        try:
            candidates.append(_automation(journal, selected))
        except (PeerError, OSError, ValueError, sqlite3.Error) as error:
            candidates.append(dict(automation_manifest=str(selected), eligible=False, operation=None, reason_code=error.code if isinstance(error, PeerError) else 'OWNERSHIP_UNKNOWN', reason=str(error)))
    from .activity import listing
    for candidate in candidates:
        if candidate.get('worker_id') and journal.one('SELECT 1 FROM workers WHERE worker_id=?', (candidate['worker_id'],)):
            candidate['activity_logs'] = listing(journal, candidate['worker_id'])
    return dict(schema_version=1, kind='nativepeers-history-cleanup', operation_id=new_id(), journal_root=str(journal.root),
                created_at=time.time(), candidates=candidates, applied=False, histories_deleted=False, activity_logs_deleted=False,
                recheck_required=True, desktop_verified=False)


def save_manifest(path, manifest):
    path = Path(path)
    require(not path.exists(), 'PROTOCOL_ERROR', 'Cleanup manifest already exists; choose a new private path')
    atomic_json(path, manifest)


def _record(journal, selection, current, native_ids, kind, operation_id, *, evidence):
    for native_id in native_ids:
        affected = next(m for m in current['affected'] if m['native_id'] == native_id)
        subjects = {selection['worker_id']: None}
        if not selection.get('automation_manifest') or native_id != selection['native_id']:
            subjects.update({p['worker_id']: p['generation'] for p in affected['ownership']})
        for subject, generation in subjects.items():
            journal.event(subject, kind, dict(operation_id=operation_id, provider_home=current['provider_home'], native_id=native_id,
                                             generation=generation, evidence=evidence))


async def apply(journal, manifest, *, reverse=False, client_factory=AppServer):
    require(manifest.get('schema_version') == 1 and manifest.get('kind') == 'nativepeers-history-cleanup' and manifest.get('journal_root') == str(journal.root),
            'PROTOCOL_ERROR', 'Cleanup manifest does not belong to this journal')
    _uuid(manifest.get('operation_id'))
    candidates = manifest.get('candidates')
    require(isinstance(candidates, list) and len(candidates) <= 100, 'PROTOCOL_ERROR', 'Invalid cleanup manifest selection')
    outcomes = []
    helper_unresolved = False
    for previous in candidates:
        if helper_unresolved:
            outcomes.append(dict(worker_id=previous.get('worker_id'), status='skipped', reason='Previous native metadata helper has unresolved process/output cleanup'))
            continue
        if not previous.get('eligible'):
            outcomes.append(dict(worker_id=previous.get('worker_id'), automation_manifest=previous.get('automation_manifest'), status='skipped', reason=previous.get('reason', 'Preview was ineligible')))
            continue
        worker_id = _uuid(previous.get('worker_id'))
        # Reversal changes exact archived identities only. Native unarchive does
        # not cascade, so an independently changed descendant is never restored.
        identities = previous.get('affected', []) if reverse else [None]
        for identity in identities:
            outcome = dict(worker_id=worker_id, native_id=identity['native_id'] if identity else previous['native_id'])
            if helper_unresolved:
                outcome.update(status='skipped', reason='Previous native metadata helper has unresolved process/output cleanup')
                outcomes.append(outcome)
                continue
            client = None
            submitted = False
            changed = []
            try:
                current = _inspect_selection(journal, previous)
                require(current['provider_home'] == previous['provider_home'] and current['native_id'] == previous['native_id'], 'OWNERSHIP_CONFLICT', 'Worker identity/home changed after preview')
                require(current.get('provenance_sha256') == previous.get('provenance_sha256'), 'OWNERSHIP_CONFLICT', 'Automation provenance changed after preview')
                if reverse:
                    receipt = journal.one("SELECT 1 FROM events WHERE subject_id=? AND kind IN ('history-archived','history-archive-intent') AND json_extract(payload_json,'$.operation_id')=? AND json_extract(payload_json,'$.native_id')=? AND json_extract(payload_json,'$.provider_home')=?", (worker_id, manifest['operation_id'], identity['native_id'], previous['provider_home']))
                    require(receipt, 'APPROVAL_BLOCKED', 'Only identities archived by this exact cleanup can be reversed')
                else:
                    old = [{k:v for k,v in m.items() if k!='archived'} for m in previous['affected']]
                    now = [{k:v for k,v in m.items() if k!='archived'} for m in current['affected']]
                    require(sha256(now) == sha256(old), 'OWNERSHIP_CONFLICT', 'Affected identities or ownership changed after preview')
                groups = sorted({p['group_id'] for m in current['affected'] for p in m['ownership']})
                async with AsyncExitStack() as locks:
                    for group in groups:
                        await locks.enter_async_context(async_advisory_lock(journal.root/'locks'/(group+'.resume.lock')))
                    for group in groups:
                        await locks.enter_async_context(async_advisory_lock(journal.root/'locks'/(group+'.supervisor.lock'), timeout=.1))
                    current = _inspect_selection(journal, previous)
                    require(current['provider_home'] == previous['provider_home'] and current['native_id'] == previous['native_id'], 'OWNERSHIP_CONFLICT', 'Worker changed during cleanup admission')
                    if not reverse:
                        # Archive-state changes from an earlier successful apply
                        # are harmless; every other ownership fact must be exact.
                        old = [{k:v for k,v in m.items() if k!='archived'} for m in previous['affected']]
                        now = [{k:v for k,v in m.items() if k!='archived'} for m in current['affected']]
                        require(sha256(old) == sha256(now), 'OWNERSHIP_CONFLICT', 'Archive subtree or release proof changed during admission')
                    metadata = native_metadata(Path(current['provider_home']), outcome['native_id'])[0]
                    if metadata['archived'] != reverse:
                        remaining = [m['native_id'] for m in current['affected'] if not m['archived']] if not reverse else []
                        if remaining:
                            outcome.update(status='failed', reason_code='PARTIAL_NATIVE_ARCHIVE', reason='Root is archived but descendants remain unarchived; select their exact worker IDs for a new preview', remaining_native_ids=remaining)
                        else:
                            outcome['status'] = 'already-unarchived' if reverse else 'already-archived'
                    else:
                        executable = Path(current['metadata_executable'])
                        # Metadata maintenance selects the current approval; a
                        # released worker's immutable execution lease is untouched.
                        verify(journal, 'codex', executable)
                        client = client_factory(executable, environment={'CODEX_HOME':current['provider_home']})
                        await client.start()
                        # Initialize may run native metadata maintenance. Recheck
                        # safety and subtree immediately before the only mutation.
                        final = _inspect_selection(journal, previous)
                        require(sha256(final) == sha256(current), 'OWNERSHIP_CONFLICT', 'Cleanup eligibility changed during native initialization')
                        changed = [outcome['native_id']] if reverse else [m['native_id'] for m in current['affected'] if not m['archived']]
                        # Persist exact pre-mutation authorization before native
                        # input. A lost response must never strand reversal or
                        # require replaying an uncertain archive request.
                        _record(journal, previous, current, changed, 'history-unarchive-intent' if reverse else 'history-archive-intent',
                                manifest['operation_id'], evidence='exact-eligible-pre-mutation-state')
                        submitted = True
                        await client.call('thread/unarchive' if reverse else 'thread/archive', {'threadId':outcome['native_id']})
                        for native_id in changed:
                            require(native_metadata(Path(current['provider_home']), native_id)[0]['archived'] != reverse,
                                    'CAPABILITY_MISMATCH', 'Native operation did not persist the expected archive state')
                        _record(journal, previous, current, changed, 'history-unarchived' if reverse else 'history-archived',
                                manifest['operation_id'], evidence='native-response-and-selected-metadata')
                        outcome.update(status='unarchived' if reverse else 'archived', affected_native_ids=changed)
            except (PeerError, OSError, ValueError, RuntimeError, TimeoutError, sqlite3.Error) as error:
                outcome.update(status='skipped' if isinstance(error, PeerError) and error.code in ('OWNERSHIP_CONFLICT', 'CLEANUP_UNCERTAIN', 'OWNERSHIP_UNKNOWN', 'DELIVERY_UNKNOWN', 'APPROVAL_BLOCKED') else 'failed',
                               reason_code=error.code if isinstance(error, PeerError) else type(error).__name__, reason=str(error))
                if submitted:
                    observed = []
                    for native_id in changed:
                        try:
                            if native_metadata(Path(current['provider_home']), native_id)[0]['archived'] != reverse: observed.append(native_id)
                        except (PeerError, OSError, ValueError, sqlite3.Error): pass
                    _record(journal, previous, current, observed, 'history-unarchived' if reverse else 'history-archived',
                            manifest['operation_id'], evidence='selected-metadata-after-uncertain-native-result')
                    outcome.update(affected_native_ids=observed, native_response_verified=False, mutation_replayed=False)
                    if observed == changed: outcome['status'] = 'unarchived' if reverse else 'archived'
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except (PeerError, OSError, RuntimeError, TimeoutError) as error:
                        helper_unresolved = True
                        outcome['helper_cleanup'] = dict(state='uncertain', reason_code=error.code if isinstance(error, PeerError) else type(error).__name__, reason=str(error))
            outcomes.append(outcome)
    return dict(operation_id=manifest['operation_id'], reverse=reverse, outcomes=outcomes, histories_deleted=False, activity_logs_deleted=False,
                desktop_verified=False, counts={state:sum(o['status']==state for o in outcomes) for state in ('archived', 'already-archived', 'unarchived', 'already-unarchived', 'skipped', 'failed')},
                reversal='npeer history-cleanup --unarchive --manifest '+shlex.quote(str(manifest.get('manifest_path', '<exact-preview-manifest>')))+' --json')
