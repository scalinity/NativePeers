"""Explicit selected source packets; no shell grant or recursive context import."""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .artifacts import Artifacts, _selected_file_bytes
from .change_preview import _safe_paths
from .errors import PeerError, require
from .files import canonical
from .isolated_copy import _private_roots
from .models import ARTIFACT_MAX
from .registry import Registry
from .worktrees import preparation, read_git

MEDIA = 'application/vnd.nativepeers.selected-context+json'


def content(raw):
    try:return dict(text=raw.decode('utf-8'))
    except UnicodeDecodeError:return dict(base64=base64.b64encode(raw).decode())


def prepare(journal, caller, paths, *, project=None, base='HEAD', untracked=(), runtimes=(), max_bytes=262144):
    require(caller.kind == 'peer' and caller.incarnation and not caller.grants.get('worker_id'),
            'APPROVAL_BLOCKED', 'Selected context requires a verified parent')
    peer = Registry(journal).verify(caller.key, caller.incarnation)
    require(type(max_bytes) is int and 0 < max_bytes <= ARTIFACT_MAX, 'PROTOCOL_ERROR', 'Packet limit must be 1 to 10485760 bytes')
    require(isinstance(paths, (list, tuple)) and paths and len(paths) <= 64,
            'PROTOCOL_ERROR', 'Select 1 to 64 exact repository paths')
    paths = _safe_paths(paths)
    require(set(untracked) <= set(paths), 'PROTOCOL_ERROR', 'Untracked inputs must also be selected paths')
    require(isinstance(base, str) and base and len(base) <= 256, 'PROTOCOL_ERROR', 'Select one Git base revision')
    require(len(runtimes) <= 4 and all(p in ('claude', 'codex', 'antigravity', 'grok') for p in runtimes),
            'PROTOCOL_ERROR', 'Unknown selected runtime')
    with preparation():
        root = Path(project or peer['cwd']).expanduser().resolve(strict=True)
        root = Path(read_git(root, 'rev-parse', '--show-toplevel').stdout.decode().strip())
        require(not any(root.is_relative_to(p) for p in _private_roots(journal)),
                'APPROVAL_BLOCKED', 'Private native state cannot supply selected context')
        def git(*args):return read_git(root, '--literal-pathspecs', *args).stdout
        base_oid = git('rev-parse', '--verify', '--end-of-options', base+'^{commit}').decode().strip()
        head = git('rev-parse', 'HEAD').decode().strip()
        tracked = set(git('ls-files', '-z', '--', *paths).decode().split('\0')) - {''}
        before = git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--', *paths)
        attrs = git('check-attr', '-z', 'filter', '--', *paths).split(b'\0')
        require(all(v in (b'unspecified', b'unset') for v in attrs[2::3]),
                'WORKSPACE_SNAPSHOT_REQUIRED', 'Selected custom filters require a separately prepared snapshot; no filter was executed')
        files = []
        for name in paths:
            path = root/name
            require(not any(path.is_relative_to(p) for p in _private_roots(journal)), 'APPROVAL_BLOCKED', 'Selected path overlaps private native state')
            # ls-tree distinguishes a tracked deletion from an explicitly selected new file.
            entry = git('ls-tree', base_oid, '--', name).decode().strip()
            require(not entry or entry.split()[0] in ('100644', '100755'), 'PROTOCOL_ERROR', 'Select regular files, not symlinks, submodules or directories')
            require(name in tracked or bool(entry) or name in untracked, 'PROTOCOL_ERROR', 'Untracked input needs an explicit --untracked selection: '+name)
            original = git('show', base_oid+':'+name) if entry else None
            current, mode = _selected_file_bytes(path, max_bytes, metadata=True) if path.exists() or path.is_symlink() else (None, None)
            require(current is not None or original is not None, 'PROTOCOL_ERROR', 'Selected path is absent: '+name)
            files.append(dict(path=name, untracked=name not in tracked and not entry,
                              current_mode=mode,
                              base_sha256=hashlib.sha256(original).hexdigest() if original is not None else None,
                              current_sha256=hashlib.sha256(current).hexdigest() if current is not None else None,
                              base=content(original) if original is not None else None,
                              current=content(current) if current is not None else None))
        diff = git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary', base_oid, '--', *paths)
        require(head == git('rev-parse', 'HEAD').decode().strip() and before == git('status', '--porcelain=v1', '-z', '--untracked-files=all', '--', *paths),
                'WORKSPACE_SNAPSHOT_REQUIRED', 'Selected source changed while preparing context')
        for item in files:
            path = root/item['path']
            now, mode = _selected_file_bytes(path, max_bytes, metadata=True) if path.exists() or path.is_symlink() else (None, None)
            require(item['current_sha256'] == (hashlib.sha256(now).hexdigest() if now is not None else None) and item['current_mode'] == mode,
                    'WORKSPACE_SNAPSHOT_REQUIRED', 'Selected content changed while preparing context')
    runtime_rows = []
    if runtimes:
        from .compatibility import diagnose
        runtime_rows = [r for r in diagnose(journal) if r['provider'] in runtimes]
    packet = dict(schema='nativepeers.selected-context.v1', observed_at=datetime.now(ZoneInfo('America/New_York')).isoformat(),
                  sender=caller.key, source=dict(root=str(root), base=base_oid, head=head,
                  selected_status_hex=before.hex(), observation='frozen at preparation; recheck before integration'),
                  files=files, diff=content(diff), runtimes=runtime_rows,
                  boundaries=dict(selected_paths_only=True, full_selected_content=True, worker_delta=None,
                  untracked='Explicit new inputs have base:null; they are outside Git diff.',
                  tools='Read through artifact_read; analysis needs no Bash or write grant.',
                  delivery='This packet records no admission, native ingestion, model acknowledgment or integration.'))
    raw = canonical(packet)
    require(len(raw) <= max_bytes, 'PAYLOAD_TOO_LARGE', 'Selected packet exceeds its byte limit; select fewer paths or explicitly increase --max-bytes', details=dict(byte_size=len(raw), ceiling=max_bytes))
    handle = Artifacts(journal).put(raw, caller.key, media_type=MEDIA)
    return dict(artifact_id=handle, sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw),
                source=packet['source'], paths=paths, omitted='All unselected paths and histories',
                context_refs=[handle], next_action='Use context_refs on spawn or --artifact on send; artifact-read pages retain the complete packet')


def packets(journal, caller, refs):
    found = []
    for ref in refs:
        meta = Artifacts(journal).read(ref, caller.key, 0, 1)
        if meta['media_type'] == MEDIA:
            raw = _selected_file_bytes(journal.root/'artifacts'/ref, ARTIFACT_MAX)
            require(hashlib.sha256(raw).hexdigest() == meta['sha256'], 'PROTOCOL_ERROR', 'Selected context changed')
            value = json.loads(raw)
            found.append(dict(artifact_id=ref, byte_size=meta['byte_size'], source=value['source'], paths=[f['path'] for f in value['files']]))
    return found


def compare(journal, caller, handle, *, project=None):
    """Compare a granted frozen handoff with current selected paths; change nothing."""
    require(caller.kind == 'peer' and caller.incarnation and not caller.grants.get('worker_id'),
            'APPROVAL_BLOCKED', 'Context comparison requires a verified parent')
    Registry(journal).verify(caller.key, caller.incarnation)
    meta = Artifacts(journal).read(handle, caller.key, 0, 1)
    require(meta['media_type'] == MEDIA, 'PROTOCOL_ERROR', 'Select a prepared source-context artifact')
    raw = _selected_file_bytes(journal.root/'artifacts'/handle, ARTIFACT_MAX)
    require(hashlib.sha256(raw).hexdigest() == meta['sha256'], 'PROTOCOL_ERROR', 'Selected context changed')
    frozen = json.loads(raw)
    try:root = Path(project or frozen['source']['root']).expanduser().resolve(strict=True)
    except OSError as error:raise PeerError('PROTOCOL_ERROR','Frozen source is unavailable; pass --project with the current repository path') from error
    require(not any(root.is_relative_to(p) for p in _private_roots(journal)), 'APPROVAL_BLOCKED', 'Private native state is excluded')
    rows = []
    with preparation():
        head = read_git(root, 'rev-parse', 'HEAD').stdout.decode().strip()
        for item in frozen['files']:
            name = _safe_paths([item['path']])[0];path = root/name
            require(not any(path.is_relative_to(p) for p in _private_roots(journal)), 'APPROVAL_BLOCKED', 'Selected path overlaps private native state')
            try:
                current, mode = _selected_file_bytes(path, ARTIFACT_MAX, metadata=True) if path.exists() or path.is_symlink() else (None, None)
                digest = hashlib.sha256(current).hexdigest() if current is not None else None
                rows.append(dict(path=name, frozen_sha256=item['current_sha256'], current_sha256=digest,
                                 matches=digest == item['current_sha256'] and mode == item.get('current_mode')))
            except (OSError, PeerError) as error:
                rows.append(dict(path=name, frozen_sha256=item['current_sha256'], current_sha256=None, matches=False, unavailable=type(error).__name__))
    runtime_rows = []
    if frozen['runtimes']:
        from .compatibility import diagnose
        current = {r['provider']:r for r in diagnose(journal)}
        for old in frozen['runtimes']:
            now = current.get(old['provider'])
            runtime_rows.append(dict(provider=old['provider'], matches=bool(now) and old['current'] == now['current'] and old['expected'] == now['expected'],
                                     frozen=old, current=now))
    return dict(artifact_id=handle, frozen_observed_at=frozen['observed_at'],
                checked_at=datetime.now(ZoneInfo('America/New_York')).isoformat(),
                source=dict(frozen_head=frozen['source']['head'], current_head=head, head_matches=head == frozen['source']['head']),
                files=rows, runtimes=runtime_rows, changed_paths=[r['path'] for r in rows if not r['matches']],
                mutation=False, delivery='No admission, ingestion, model acknowledgment or integration inferred')
