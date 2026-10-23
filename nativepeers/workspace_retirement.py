"""Archive and retire one exact stopped workspace, without certifying native EOF.

The resume lock fences admission. A durable prepared receipt blocks later resume
before any directory is removed; retry verifies that same archive rather than
repeating model work or trusting an interrupted deletion.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from .change_preview import _git, _owned, _workspace
from .cleanup import _inventory
from .errors import require
from .files import advisory_lock, atomic_json, canonical, private_directory
from .process import owned_status

TERMINAL = {'COMPLETED', 'FAILED', 'CANCELED', 'TIMED_OUT', 'PARTIAL', 'INTERRUPTED'}
MAX_BYTES = 512 * 1024 * 1024
MAX_ENTRIES = 30000


def retirement(journal, worker_id):
    row = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind IN ('workspace-retirement-prepared','workspace-retired') ORDER BY sequence DESC LIMIT 1", (worker_id,))
    return json.loads(row['payload_json']) if row else None


def summary(value):
    receipt = value['receipt']
    return {k: value[k] for k in ('state', 'removed', 'native_history_preserved')} | {
        'worker_id': receipt['worker_id'], 'generation': receipt['generation'],
        'archive': receipt['archive'], 'archive_sha256': receipt['archive_sha256'],
        'receipt_path': str(Path(receipt['archive']).parent/'receipt.json'),
        'file_count': len(receipt['snapshot']['files']), 'size_bytes': receipt['snapshot']['size']['size_bytes'],
        'native_output_closure_changed': False,
        'resume': 'Unchanged: a backup does not release ownership or prove native output closure.' if value['state'] == 'archived' else 'Refused after explicit workspace retirement; recover files from the verified archive.'}


def retain_processes(journal, worker_id, generation, native_id, records):
    """Keep all captured births before a worker manifest can reset or overwrite them."""
    prior = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='workspace-process-snapshot' AND json_extract(payload_json,'$.generation')=? ORDER BY sequence DESC LIMIT 1", (worker_id, generation))
    previous = json.loads(prior['payload_json']) if prior else {}
    merged = {(r['pid'], r['uid'], r['start']): r for r in previous.get('records', [])}
    for record in records:
        if record: merged[(record['pid'], record['uid'], record['start'])] = record
    value = {'worker_id': worker_id, 'generation': generation, 'native_id': native_id,
             'all_captured_births_retained': True, 'records': list(merged.values())}
    if value != previous: journal.event(worker_id, 'workspace-process-snapshot', value)


def _record(record):
    require(isinstance(record, dict) and type(record.get('pid')) is int and record['pid'] > 0
            and type(record.get('uid')) is int and record['uid'] == os.getuid()
            and isinstance(record.get('start'), str) and record['start'],
            'OWNERSHIP_UNKNOWN', 'A captured process birth is incomplete or belongs to another principal')
    state = owned_status(record)
    require(state == 'DEAD', 'OWNERSHIP_CONFLICT' if state == 'LIVE' else 'OWNERSHIP_UNKNOWN',
            'A captured workspace writer is live or cannot be inspected')
    return {'pid': record['pid'], 'uid': record['uid'], 'start': record['start'], 'state': state}


def _previous_boot(records):
    """An OS reboot ends every old process, including descendants not retained."""
    if sys.platform == 'darwin':
        try:
            value = subprocess.run(['/usr/sbin/sysctl', '-n', 'kern.boottime'], capture_output=True, timeout=2, check=True).stdout.decode()
            match = re.match(r'\{ sec = (\d+), usec = (\d+) \}', value)
            if not match: return None
            boot = int(match[1])+int(match[2])/1_000_000
            starts = [int(r['start'].split(':')[0])+int(r['start'].split(':')[1])/1_000_000 for r in records]
            return {'basis': 'previous-boot-ended', 'boot_started_at': boot} if starts and all(t < boot for t in starts) else None
        except (OSError, ValueError, subprocess.SubprocessError): return None
    if sys.platform.startswith('linux'):
        try:
            boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            valid = all(re.fullmatch(r'[0-9a-f-]{36}:\d+', r['start']) for r in records)
            return {'basis': 'previous-boot-ended', 'boot_id': boot} if records and valid and all(r['start'].split(':')[0] != boot for r in records) else None
        except OSError: return None
    return None


def _stopped(journal, row, *, allow_incomplete=False):
    require(row['state'] in ('RELEASED_RESUMABLE', 'FAILED'), 'OWNERSHIP_CONFLICT', 'Worker is not terminal')
    tasks = journal.all('SELECT state,generation FROM tasks WHERE worker_id=?', (row['worker_id'],))
    require(tasks and all(t['state'] in TERMINAL for t in tasks), 'OWNERSHIP_CONFLICT', 'Worker has unfinished tasks')
    records = []; generations = set()
    path = journal.root/'workers'/(row['worker_id']+'.json')
    require(path.is_file() and not path.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Current worker process manifest is missing or redirected')
    manifest = json.loads(path.read_bytes())
    require(all(manifest.get(k) == row[k] for k in ('worker_id', 'group_id', 'generation', 'native_id'))
            and manifest.get('owner') and 'native_process' in manifest,
            'OWNERSHIP_UNKNOWN', 'Current process manifest does not identify this worker generation')
    records.extend([manifest['owner'], *([manifest['native_process']] if manifest['native_process'] else []), *manifest.get('native_descendants', [])])
    missing = []
    for generation in range(1, row['generation']+1):
        retained = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='workspace-process-snapshot' AND json_extract(payload_json,'$.generation')=? ORDER BY sequence DESC LIMIT 1", (row['worker_id'], generation))
        if not retained:
            missing.append(generation); continue
        retained = json.loads(retained['payload_json'])
        require(retained.get('worker_id') == row['worker_id'] and retained.get('native_id') == row['native_id']
                and retained.get('all_captured_births_retained') is True and retained.get('records'),
                'OWNERSHIP_UNKNOWN', 'Historical captured-process retention is incomplete')
        records.extend(retained['records'])
    controllers = sorted((journal.root/'groups'/row['group_id']).glob('owner*.json'))
    require(controllers, 'OWNERSHIP_UNKNOWN', 'Group controller birth is missing')
    for path in controllers:
        require(path.is_file() and not path.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Group ownership path is redirected')
        controller = json.loads(path.read_bytes())
        require(controller.get('group_id') == row['group_id'] and controller.get('owner'), 'OWNERSHIP_UNKNOWN', 'Group controller identity differs')
        records.append(controller['owner'])
    for peer in journal.all('SELECT descriptor_json FROM peer_instances WHERE provider=? AND native_id=?', (row['provider'], row['native_id'])):
        descriptor = json.loads(peer['descriptor_json']); records.append(descriptor.get('owner'))
        transport = descriptor.get('transport', {})
        if transport.get('kind') == 'owned-worker' and transport.get('worker_id') == row['worker_id']:
            # Each resumed generation re-registers its exact controller birth.
            generations.add((descriptor.get('owner') or {}).get('start'))
    for event in journal.all("SELECT kind,payload_json FROM events WHERE subject_id=? AND kind IN ('native-hook-observed','controller-inspection-failed')", (row['worker_id'],)):
        value = json.loads(event['payload_json'])
        require(event['kind'] != 'controller-inspection-failed', 'OWNERSHIP_UNKNOWN', 'A created controller birth was never captured')
        require(value.get('native_id') == row['native_id'] and value.get('process'), 'OWNERSHIP_UNKNOWN', 'Native hook birth is incomplete')
        records.append(value['process'])
    require(row['generation'] == 1 or len(generations) >= row['generation'], 'OWNERSHIP_UNKNOWN', 'A resumed controller birth is missing')
    processes = {}
    for record in records:
        checked = _record(record)
        processes[checked['start']+':'+str(checked['pid'])] = checked
    cleanup = manifest.get('cleanup') or {}
    closed = cleanup.get('state') == 'CLOSED' and cleanup.get('output_eof') is True
    boot = _previous_boot(records) if missing or not closed else None
    require(not missing or boot or allow_incomplete, 'OWNERSHIP_UNKNOWN',
            'Historical captured-process sets were not retained; archive only now, then retire after a normal reboot ends all old descendants')
    require(closed or boot or allow_incomplete, 'CLEANUP_UNCERTAIN',
            'Native process/output closure is unproved; archive only now, then retire after a normal reboot ends all old descendants')
    return {'processes': list(processes.values()), 'historical_cleanup': manifest.get('cleanup'),
            'capture_history_complete': not missing, 'missing_capture_generations': missing, 'later_boot': boot,
            'native_output_eof_proved': (manifest.get('cleanup') or {}).get('output_eof') is True,
            'scope': 'Stopped captured births permit workspace retirement only; native output closure and resume eligibility are unchanged.'}


def _registration(source, work, baseline, deadline, *, missing=False):
    common = _git(source, ['rev-parse', '--git-common-dir'], deadline)
    require(common.returncode == 0, 'OWNERSHIP_UNKNOWN', 'Source Git storage is unavailable')
    gitroot = Path(os.fsdecode(common.stdout).strip())
    gitroot = (source/gitroot).resolve() if not gitroot.is_absolute() else gitroot.resolve()
    listing = _git(source, ['worktree', 'list', '--porcelain', '-z'], deadline)
    require(listing.returncode == 0, 'OWNERSHIP_UNKNOWN', 'Worktree registration cannot be inspected')
    entries = [v.split(b'\0') for v in listing.stdout.split(b'\0\0') if v]
    entry = [v for v in entries if v[0] == b'worktree '+os.fsencode(work)]
    if not entry and missing:
        require(not work.exists() and not work.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Unregistered workspace content remains')
        return None
    require(len(entry) == 1 and b'branch refs/heads/npeer/'+work.name.encode() in entry[0]
            and not any(v.startswith(b'locked') for v in entry[0]), 'OWNERSHIP_UNKNOWN', 'Exact owned branch/worktree registration differs')
    if work.exists():
        top = _git(work, ['rev-parse', '--show-toplevel'], deadline)
        branch = _git(work, ['symbolic-ref', 'HEAD'], deadline)
        require(top.returncode == branch.returncode == 0 and Path(os.fsdecode(top.stdout).strip()) == work
                and branch.stdout.decode().strip() == 'refs/heads/npeer/'+work.name,
                'OWNERSHIP_UNKNOWN', 'Git pointer does not belong to the exact owned branch/workspace')
        common = _git(work, ['rev-parse', '--git-common-dir'], deadline)
        value = Path(os.fsdecode(common.stdout).strip())
        value = (work/value).resolve() if not value.is_absolute() else value.resolve()
        require(common.returncode == 0 and value == gitroot, 'OWNERSHIP_UNKNOWN', 'Workspace Git storage differs from source')
        pointer = work/'.git'
        require(pointer.is_file() and not pointer.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Worktree Git pointer is redirected')
        admin = _git(work, ['rev-parse', '--absolute-git-dir'], deadline)
        admin_path = Path(os.fsdecode(admin.stdout).strip())
        require(admin.returncode == 0 and admin_path.parent == gitroot/'worktrees' and not admin_path.is_symlink()
                and (admin_path/'gitdir').read_text().strip() == str(pointer),
                'OWNERSHIP_UNKNOWN', 'Worktree administration directory differs from its recorded pointer')
    return entry[0]


def _snapshot(work, deadline):
    sizes, files, directories = _inventory(work, deadline, MAX_ENTRIES, MAX_BYTES, allow_internal_links=True)
    # _inventory deliberately omits .git; retain the exact worktree pointer too.
    raw = (work/'.git').read_bytes()
    files['.git'] = {'sha256': hashlib.sha256(raw).hexdigest(), 'mode': stat.S_IMODE((work/'.git').stat().st_mode)}
    refs = _git(work, ['for-each-ref', '--format=%(refname) %(objectname) %(symref)',
                       'refs/worktree/', 'refs/bisect/', 'refs/rewritten/'], deadline)
    require(refs.returncode == 0, 'BACKUP_UNAVAILABLE', 'Per-worktree references cannot be read')
    worktree_refs = {}
    for line in refs.stdout.decode().splitlines():
        name, oid, target = line.split(' ', 2)
        worktree_refs[name] = {'oid': oid, 'symbolic_target': target or None}
    git_state = {}
    names = ('index', 'HEAD', 'logs/HEAD', 'logs/refs/heads/npeer/'+work.name,
             *('logs/'+name for name in worktree_refs))
    for name in names:
        result = _git(work, ['rev-parse', '--git-path', name], deadline)
        require(result.returncode == 0, 'OWNERSHIP_UNKNOWN', 'Git metadata path cannot be read')
        path = Path(os.fsdecode(result.stdout).strip()); path = work/path if not path.is_absolute() else path
        require(not path.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Git metadata is redirected')
        git_state[name] = _hash(path) if path.exists() else None
    return {'files': files, 'directories': {p: stat.S_IMODE((work/p).stat().st_mode) for p in sorted(directories)},
            'root_mode': stat.S_IMODE(work.stat().st_mode), 'git_state': git_state,
            'worktree_refs': worktree_refs, 'size': sizes}


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while block := stream.read(1024*1024): digest.update(block)
    return digest.hexdigest()


def _verify(archive, receipt):
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= MAX_BYTES*2
            and _hash(archive) == receipt['archive_sha256'], 'BACKUP_CHANGED', 'Retirement archive checksum differs')
    expected = receipt['entries']; seen = set()
    with tarfile.open(archive, 'r:gz') as bundle:
        for entry in bundle:
            require(entry.name in expected and entry.name not in seen and entry.isfile(), 'BACKUP_CHANGED', 'Archive has an unexpected or unsafe entry')
            seen.add(entry.name); raw = bundle.extractfile(entry).read()
            require(hashlib.sha256(raw).hexdigest() == expected[entry.name]['sha256'] and entry.mode == expected[entry.name]['mode'], 'BACKUP_CHANGED', 'Archived content or mode differs')
    require(seen == set(expected), 'BACKUP_CHANGED', 'Retirement archive is incomplete')


def _archive(source, work, baseline, destination, row, stopped, deadline):
    snapshot = _snapshot(work, deadline)
    head = _git(work, ['rev-parse', 'HEAD'], deadline)
    require(head.returncode == 0, 'OWNERSHIP_UNKNOWN', 'Workspace HEAD is unavailable')
    oid = head.stdout.decode().strip()
    # A standalone bundle preserves unique commits; the pack additionally saves
    # frozen input trees and staged/conflict blobs that are not branch-reachable.
    roots = {oid, baseline['input_tree']}
    stages = _git(work, ['ls-files', '--stage', '-z'], deadline)
    require(stages.returncode == 0, 'BACKUP_UNAVAILABLE', 'Git recovery roots cannot be read')
    roots.update(v.split(b'\t', 1)[0].split()[1].decode() for v in stages.stdout.split(b'\0') if v)
    roots.update(value['oid'] for value in snapshot['worktree_refs'].values())
    metadata = {}
    for name, digest in snapshot['git_state'].items():
        if not name.startswith('logs/') or digest is None: continue
        location = _git(work, ['rev-parse', '--git-path', name], deadline)
        require(location.returncode == 0, 'BACKUP_UNAVAILABLE', 'Git reflog path cannot be read')
        path = Path(os.fsdecode(location.stdout).strip()); path = work/path if not path.is_absolute() else path
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == digest, 'WORKSPACE_CHANGED', 'Git reflog changed before backup')
        metadata['git/metadata/'+name] = raw
        for line in raw.splitlines():
            for reflog_oid in line.split(b' ', 2)[:2]:
                require(re.fullmatch(rb'[0-9a-f]{40}|[0-9a-f]{64}', reflog_oid), 'BACKUP_UNAVAILABLE', 'Git reflog object identity is malformed')
                if set(reflog_oid) != {ord('0')}: roots.add(reflog_oid.decode())
    packed = _git(work, ['pack-objects', '--stdout', '--revs'], deadline, raw=('\n'.join(sorted(roots))+'\n').encode(), max_output_bytes=MAX_BYTES)
    require(packed.returncode == 0, 'BACKUP_UNAVAILABLE', 'Git recovery object pack failed')
    index = _git(work, ['rev-parse', '--git-path', 'index'], deadline)
    require(index.returncode == 0, 'BACKUP_UNAVAILABLE', 'Workspace index path is unavailable')
    index_path = Path(os.fsdecode(index.stdout).strip()); index_path = work/index_path if not index_path.is_absolute() else index_path
    require(not index_path.is_symlink() and index_path.is_file(), 'OWNERSHIP_UNKNOWN', 'Workspace index is missing or redirected')
    index_bytes = index_path.read_bytes()
    require(hashlib.sha256(index_bytes).hexdigest() == snapshot['git_state']['index'], 'WORKSPACE_CHANGED', 'Workspace index changed before backup')
    with tempfile.TemporaryDirectory(prefix='.retirement-', dir=destination) as temporary:
        temporary = Path(temporary); branch = temporary/'branch.bundle'
        bundled = _git(work, ['bundle', 'create', str(branch), 'HEAD'], deadline)
        verified = _git(work, ['bundle', 'verify', str(branch)], deadline)
        require(bundled.returncode == verified.returncode == 0 and branch.is_file(), 'BACKUP_UNAVAILABLE', 'Unique commit bundle could not be verified')
        aux = {'git/branch.bundle': branch.read_bytes(), 'git/objects.pack': packed.stdout, 'git/index': index_bytes,
               'git/worktree-refs.json': canonical(snapshot['worktree_refs']), **metadata}
        require(sum(len(v) for v in aux.values())+snapshot['size']['size_bytes'] <= MAX_BYTES, 'BACKUP_UNAVAILABLE', 'Combined workspace and Git recovery backup exceeds the byte limit')
        entries = {'workspace/'+k: v for k, v in snapshot['files'].items()}
        entries.update({k: {'sha256': hashlib.sha256(v).hexdigest(), 'mode': 0o600} for k, v in aux.items()})
        archive = temporary/'workspace.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name, meta in entries.items():
                raw = (os.fsencode(meta['link_target']) if 'link_target' in meta else
                       (work/name.removeprefix('workspace/')).read_bytes()) if name.startswith('workspace/') else aux[name]
                require(hashlib.sha256(raw).hexdigest() == meta['sha256'], 'WORKSPACE_CHANGED', 'Workspace changed while being archived')
                entry = tarfile.TarInfo(name); entry.size = len(raw); entry.mode = meta['mode']; output.addfile(entry, io.BytesIO(raw))
        with archive.open('rb') as stream: os.fsync(stream.fileno())
        require(_snapshot(work, deadline) == snapshot and index_path.read_bytes() == index_bytes, 'WORKSPACE_CHANGED', 'Workspace changed during backup')
        receipt = {'version': 1, 'worker_id': row['worker_id'], 'generation': row['generation'], 'source': str(source), 'workspace': str(work),
                   'archive': str(destination/'workspace.tar.gz'), 'archive_sha256': _hash(archive), 'head': oid, 'baseline': baseline,
                   'entries': entries, 'snapshot': snapshot, 'stopped': stopped,
                   'recovery': 'Extract workspace files to a new folder, replace its .git pointer with git init, unpack git/objects.pack using git unpack-objects, set HEAD to the recorded head, then restore git/index. Recreate refs from git/worktree-refs.json using git update-ref or git symbolic-ref; raw owned-branch/HEAD/per-worktree reflogs are under git/metadata/logs. Create empty directories/modes from snapshot.directories. Internal symlinks are archived as inert regular files containing their targets: restore only snapshot.files entries with link_target as symlinks. The original source refs and native histories are not needed to recover the archived files.'}
        _verify(archive, receipt)
        os.link(archive, destination/'workspace.tar.gz')  # exclusive publication; never overwrite a prior backup
        atomic_json(destination/'receipt.json', receipt)
        return receipt


def archive_retire(journal, caller, worker_id, *, timeout=120, archive_only=False):
    require(isinstance(worker_id, str) and worker_id and Path(worker_id).name == worker_id and worker_id not in ('.', '..'), 'PROTOCOL_ERROR', 'Supply one exact worker ID')
    require(isinstance(timeout, (int, float)) and 0 < timeout <= 300, 'PROTOCOL_ERROR', 'Invalid retirement deadline')
    row, lease = _owned(journal, caller, worker_id)
    with advisory_lock(journal.root/'locks'/(row['group_id']+'.resume.lock')):
        row, lease = _owned(journal, caller, worker_id); prior = retirement(journal, worker_id)
        deadline = time.monotonic()+timeout
        stopped = _stopped(journal, row, allow_incomplete=archive_only)
        if prior:
            receipt = prior['receipt']
            require(receipt['generation'] == row['generation'] and receipt['worker_id'] == worker_id, 'OWNERSHIP_CONFLICT', 'Retirement generation changed')
            source = Path(receipt['source']); work = Path(receipt['workspace']); baseline = receipt['baseline']
            require(work == source/'.worktrees'/worker_id and source.resolve(strict=True) == source
                    and row['cwd'] == str(work) and lease.get('baseline') == baseline and lease.get('spec', {}).get('cwd') == str(work)
                    and not work.is_symlink() and not work.parent.is_symlink(), 'OWNERSHIP_UNKNOWN', 'Retired workspace path changed')
            require(Path(receipt['archive']) == source/'.worktrees'/'.archives'/worker_id/'workspace.tar.gz', 'OWNERSHIP_UNKNOWN', 'Retirement archive path differs')
            _verify(Path(receipt['archive']), receipt)
            require(json.loads((Path(receipt['archive']).parent/'receipt.json').read_bytes()) == receipt, 'BACKUP_CHANGED', 'Retirement receipt changed')
            if prior['state'] == 'retired' or archive_only: return summary(prior)
        else:
            source, work, baseline, mode = _workspace(journal, row, lease)
            require(mode == 'isolated-worktree', 'CLEANUP_UNAVAILABLE', 'Archive retirement currently supports owned Git worktrees only')
            _registration(source, work, baseline, deadline)
            destination = private_directory(private_directory(source/'.worktrees'/'.archives')/worker_id)
            if (destination/'receipt.json').exists():
                receipt = json.loads((destination/'receipt.json').read_bytes())
                require(receipt['worker_id'] == worker_id and receipt['generation'] == row['generation']
                        and receipt['source'] == str(source) and receipt['workspace'] == str(work) and receipt['baseline'] == baseline
                        and receipt['archive'] == str(destination/'workspace.tar.gz')
                        and receipt['snapshot'] == _snapshot(work, deadline), 'BACKUP_CHANGED', 'Unclaimed archive differs from the current worker')
                _verify(destination/'workspace.tar.gz', receipt)
            else:
                require(not (destination/'workspace.tar.gz').exists(), 'BACKUP_CHANGED', 'An incomplete archive publication must be preserved for inspection')
                receipt = _archive(source, work, baseline, destination, row, stopped, deadline)
            require(_owned(journal, caller, worker_id)[0] == row, 'OWNERSHIP_CONFLICT', 'Worker changed during backup')
            stopped = _stopped(journal, row, allow_incomplete=archive_only)
            if archive_only:
                return summary({'state': 'archived', 'receipt': receipt, 'removed': False, 'native_history_preserved': True})
            prepared = {'state': 'prepared', 'receipt': receipt, 'retirement_process_check': stopped, 'removed': False, 'native_history_preserved': True}
            journal.event(worker_id, 'workspace-retirement-prepared', prepared)
        # This durable event is checked by real resume admission while holding the
        # same group lock. Missing old EOF is deliberately not rewritten.
        _stopped(journal, row)
        if work.exists(): require(_snapshot(work, deadline) == receipt['snapshot'], 'WORKSPACE_CHANGED', 'Workspace changed after the archive was prepared')
        entry = _registration(source, work, baseline, deadline, missing=True)
        branch = 'refs/heads/npeer/'+worker_id
        ref = _git(source, ['rev-parse', '--verify', branch], deadline)
        require(ref.returncode != 0 or ref.stdout.decode().strip() == receipt['head'], 'WORKSPACE_CHANGED', 'Owned branch changed after backup')
        if entry:
            # The exact dirty/ignored tree is now fully backed up and byte-verified.
            # Git performs its own targeted registration cleanup; no broad prune.
            removed = _git(source, ['worktree', 'remove', '--force', str(work)], deadline)
            require(removed.returncode == 0, 'CLEANUP_UNAVAILABLE', 'Exact worktree removal failed; verified backup and prepared fence retained')
        require(not work.exists() and not work.is_symlink(), 'CLEANUP_UNAVAILABLE', 'Workspace content remains after removal')
        if ref.returncode == 0:
            deleted = _git(source, ['update-ref', '-d', branch, receipt['head']], deadline)
            require(deleted.returncode == 0, 'WORKSPACE_CHANGED', 'Owned branch changed before compare-and-delete')
        result = {'state': 'retired', 'receipt': receipt, 'removed': True, 'native_history_preserved': True,
                  'native_output_closure_changed': False, 'resume': 'Refused after explicit workspace retirement; recover files from the verified archive.'}
        with journal.transaction() as db:
            db.execute('UPDATE workers SET worktree_id=NULL WHERE worker_id=? AND generation=?', (worker_id, row['generation']))
            journal.event(worker_id, 'workspace-retired', result)
        return summary(result)
