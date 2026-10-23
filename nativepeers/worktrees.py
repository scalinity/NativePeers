"""Reproducible owned writing workspaces; the source checkout is never reset."""
import hashlib
import contextlib
from dataclasses import dataclass,field
from contextvars import ContextVar
import os
from pathlib import Path
import selectors
import stat
import subprocess
import tempfile
import time

from .errors import require, PeerError
from .files import sha256,advisory_lock,atomic_json,private_directory
from .profiles import ROOT
from .models import ARTIFACT_MAX

SECRET_NAMES={'.env','.credentials.json','auth.json','id_rsa','id_ed25519'}


PREPARATION_SECONDS=20
MAX_INPUT_FILES=1024
_preparation_deadline=ContextVar('nativepeers_preparation_deadline',default=None)


@contextlib.contextmanager
def preparation(deadline=None):
    current=_preparation_deadline.get()
    deadline=min(current,deadline) if current and deadline else current or deadline or time.monotonic()+PREPARATION_SECONDS
    require(time.monotonic()<deadline,'WORKSPACE_SNAPSHOT_REQUIRED','Input preparation deadline elapsed before materialization')
    token=_preparation_deadline.set(deadline)
    try:yield deadline
    finally:_preparation_deadline.reset(token)


def sensitive(path):
    return any(part in SECRET_NAMES or part.startswith('.env.') for part in Path(path).parts)


def worktree_parent(journal,root):
    directory=root/'.worktrees'
    require(not directory.is_symlink(),'WORKSPACE_SNAPSHOT_REQUIRED','Worktree storage must remain inside the source project')
    with advisory_lock(journal.root/'locks'/('worktree-ignore-'+sha256(str(root))+'.lock')):
        if git(root,'check-ignore','-q','.worktrees/.nativepeers-check',check=False).returncode:
            require(not git(root,'status','--porcelain','--untracked-files=all','--','.worktrees').stdout,
                    'WORKSPACE_SNAPSHOT_REQUIRED','Existing unignored .worktrees content must be reconciled before use')
            path=Path(git(root,'rev-parse','--git-path','info/exclude').stdout.decode().strip())
            if not path.is_absolute():path=root/path
            from .install import compare_write,file_hash
            before=path.read_bytes() if path.exists() else b'';expected=file_hash(path)
            added=(b'' if not before or before.endswith(b'\n') else b'\n')+b'/.worktrees/\n'
            backup=private_directory(ROOT/'backups'/'worktree-excludes')/(sha256(str(root))+'.json')
            if not backup.exists():atomic_json(backup,{'path':str(path),'before_sha256':expected,'before_size':len(before),'added_block':added.decode()})
            compare_write(path,before+added,expected)
        directory.mkdir(mode=0o700,exist_ok=True)
    return directory


def bounded_diff(cwd,args,limit,deadline,*,options=()):
    process=subprocess.Popen(['git','-C',str(cwd),*options,'diff','--no-ext-diff','--no-textconv',*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    data=bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0 or not selector.select(remaining):return None,'Diff capture timed out; inspect the retained worktree.'
                chunk=os.read(process.stdout.fileno(),min(65536,limit-len(data)+1))
                if not chunk:break
                data.extend(chunk)
                if len(data)>limit:return None,'Diff exceeds the local artifact limit; inspect the retained worktree.'
        code=process.wait(timeout=max(.001,deadline-time.monotonic()))
        if code not in (0,1):return None,'Git diff failed; inspect the retained worktree.'
        return data,None
    except subprocess.TimeoutExpired:return None,'Diff capture timed out; inspect the retained worktree.'
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):process.kill()
            process.wait(timeout=1)
        process.stdout.close()


def changes(cwd,baseline,*,max_bytes=ARTIFACT_MAX,timeout=10):
    deadline=time.monotonic()+timeout
    unsafe=sensitive
    if baseline.get('workspace_mode')=='isolated-copy':
        from .isolated_copy import private_path
        unsafe=private_path
    delta={'worker_delta':b'','worker_delta_base':baseline.get('input_tree')}
    try:
        # Disable configured external filters even during Git's file discovery and patch capture.
        filters=git(cwd,'config','--null','--name-only','--get-regexp',r'^filter\..*\.(clean|smudge|process|required)$',
                    check=False,timeout=max(.001,deadline-time.monotonic()))
        require(filters.returncode in (0,1),'WORKSPACE_SNAPSHOT_REQUIRED','Git filter configuration could not be read')
        options=[]
        for key in filters.stdout.decode().split('\0'):
            if key:options.extend(['-c',key+'='+('false' if key.endswith('.required') else '')])
        tracked=[p for p in git(cwd,*options,'diff','--no-ext-diff','--no-textconv','--no-renames','--name-only',baseline['head'],'-z',timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0') if p]
        untracked=[p for p in git(cwd,'ls-files','--others','--exclude-standard','-z',timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0') if p]
    except (OSError,subprocess.TimeoutExpired,PeerError):
        return dict(delta,patch=b'',changed_files=[],untracked_files=[],file_count=None,excluded_sensitive_or_symlink_files=[],
                    patch_unavailable_reason='File discovery failed or timed out; inspect the retained worktree.',
                    worker_delta_unavailable_reason='File discovery failed or timed out; inspect the retained worktree.')
    input_paths=[]
    if baseline.get('input_tree'):
        try:
            input_paths=[p for p in git(cwd,*options,'diff','--no-ext-diff','--no-textconv','--no-renames','--name-only',baseline['head'],baseline['input_tree'],'-z',
                                       timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0') if p]
            indexed=set(git(cwd,'ls-files','-z',timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0'))
            # Explicit inputs remain part of the reconstruction even if a worker changes ignore rules.
            untracked=list(dict.fromkeys(untracked+[p for p in input_paths if p not in indexed and (Path(cwd)/p).is_file()]))
        except (OSError,subprocess.TimeoutExpired,PeerError):
            delta['worker_delta_unavailable_reason']='Frozen input tree discovery failed or timed out; inspect the retained worktree.'
    excluded={p for p in tracked+untracked+input_paths if unsafe(p) or (Path(cwd)/p).is_symlink()}
    selected=[p for p in tracked if p not in excluded]
    result=dict(delta,changed_files=tracked[:100],untracked_files=untracked[:100],
                file_count=len(tracked)+len(untracked),excluded_sensitive_or_symlink_files=sorted(excluded)[:100])
    commands=([ [baseline['head'],'--binary','--',*selected] ] if selected else [])
    commands.extend(['--no-index','--binary','--','/dev/null',relative] for relative in untracked if relative not in excluded)
    data=bytearray()
    for args in commands:
        try:piece,reason=bounded_diff(cwd,args,max_bytes-len(data),deadline,options=options)
        except OSError:piece,reason=None,'Git diff could not start; inspect the retained worktree.'
        if reason:
            result['patch_unavailable_reason']=reason;data.clear();break
        data.extend(piece)
    result['patch']=bytes(data)
    if not baseline.get('input_tree'):
        return dict(result,worker_delta_unavailable_reason='Frozen input tree is unavailable; inspect the reconstruction patch or retained worktree.')
    if 'worker_delta_unavailable_reason' in result:return result
    try:
        paths=list(dict.fromkeys(p for p in tracked+untracked+input_paths if p not in excluded))
        tree=_working_tree(cwd,baseline['head'],paths,deadline,tracked_paths=set(tracked)|indexed,options=options)
        names=git(cwd,'diff','--no-ext-diff','--no-textconv','--no-renames','--name-only',baseline['input_tree'],tree,'-z',
                  timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0')
        paths=[p for p in names if p and p not in excluded and not unsafe(p) and not (Path(cwd)/p).is_symlink()]
        if paths:
            data,reason=bounded_diff(cwd,[baseline['input_tree'],tree,'--binary','--',*paths],max_bytes,deadline,options=options)
            if reason:result['worker_delta_unavailable_reason']=reason
            else:result['worker_delta']=bytes(data)
    except PeerError as error:
        result['worker_delta_unavailable_reason']='Worker delta unavailable: '+error.message
    except (OSError,subprocess.TimeoutExpired):
        result['worker_delta_unavailable_reason']='Worker delta capture failed or timed out; inspect the retained worktree.'
    return result


def git(cwd,*args,input=None,stdin=None,env=None,check=True,timeout=None):
    deadline=_preparation_deadline.get()
    if deadline is not None:
        require(time.monotonic()<deadline,'WORKSPACE_SNAPSHOT_REQUIRED','Input preparation deadline elapsed')
        timeout=min(timeout,deadline-time.monotonic()) if timeout else deadline-time.monotonic()
    result=subprocess.run(['git','-C',str(cwd),*args],input=input,stdin=stdin,env=env,capture_output=True,timeout=timeout)
    if check and result.returncode:
        raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED',result.stderr.decode(errors='replace')[:800])
    return result


def read_git(cwd,*args,**kwargs):
    """Inspect without index refreshes, hooks, monitors or external filters."""
    options=['--no-optional-locks','-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',
             '-c','diff.autoRefreshIndex=false']
    deadline=_preparation_deadline.get() or time.monotonic()+PREPARATION_SECONDS
    def bounded(*args,**kw):
        from .change_preview import _git
        try:result=_git(cwd,list(args),deadline,raw=kw.pop('input',None))
        except (subprocess.TimeoutExpired,PeerError) as error:
            raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED','Input inspection exceeded its shared time or byte limit') from error
        if kw.get('check',True) and result.returncode:raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED',result.stderr.decode(errors='replace')[:800])
        return result
    filters=bounded(*options,'config','--null','--name-only','--get-regexp',r'^filter\..*\.(clean|smudge|process|required)$',check=False)
    require(filters.returncode in (0,1),'WORKSPACE_SNAPSHOT_REQUIRED','Git filter configuration could not be read')
    for key in filters.stdout.decode().split('\0'):
        if key:options.extend(['-c',key+'='+('false' if key.endswith('.required') else '')])
    return bounded(*options,*args,**kwargs)


@contextlib.contextmanager
def _temporary_index(cwd,deadline,*,options=()):
    directory=git(cwd,'rev-parse','--absolute-git-dir',timeout=max(.001,deadline-time.monotonic())).stdout.decode().strip()
    with tempfile.TemporaryDirectory(prefix='npeer-index-',dir=directory) as temporary:
        environment=dict(os.environ,GIT_INDEX_FILE=str(Path(temporary)/'index'))
        def command(*args,**kwargs):
            return git(cwd,*options,*args,env=environment,timeout=max(.001,deadline-time.monotonic()),**kwargs)
        yield command


def _input_tree(cwd,frozen,selected):
    # Reapply the exact frozen inputs to a private index, never the source index.
    with _temporary_index(cwd,time.monotonic()+10) as command:
        command('read-tree',frozen.baseline['head'])
        if frozen.patch:command('apply','--binary','--cached',input=frozen.patch)
        entries=bytearray()
        for relative in selected:
            data,mode=frozen.untracked[relative]
            oid=command('hash-object','-w','--stdin','--no-filters',input=data).stdout.strip()
            entries.extend((b'100755' if mode&0o111 else b'100644')+b' '+oid+b'\t'+os.fsencode(relative)+b'\0')
        if entries:command('update-index','-z','--index-info',input=bytes(entries))
        return command('write-tree').stdout.decode().strip()


def _working_tree(cwd,head,paths,deadline,*,tracked_paths=(),options=()):
    # Normalize tracked blobs with built-in Git attributes, never external filters or the real index.
    with _temporary_index(cwd,deadline,options=options) as command:
        command('read-tree',head)
        normalized=[p for p in paths if p in tracked_paths]
        if normalized:
            attributes=command('check-attr','-z','--stdin','filter',input=b''.join(os.fsencode(p)+b'\0' for p in normalized)).stdout.split(b'\0')
            require(all(value in (b'unspecified',b'unset') for value in attributes[2::3]),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Custom Git filter normalization is unavailable without executing external filters; inspect the retained worktree.')
        if paths:command('update-index','--force-remove','-z','--stdin',input=b''.join(os.fsencode(p)+b'\0' for p in paths))
        entries=bytearray()
        for relative in paths:
            path=Path(cwd)/relative
            require(not any(part.is_symlink() for part in (path,*path.parents)),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Symlinked files are excluded from worker deltas')
            try:
                if path.is_dir():
                    require(not command('ls-tree',head,'--',relative).stdout.startswith(b'160000 '),
                            'WORKSPACE_SNAPSHOT_REQUIRED','Changed submodules require inspection in the retained worktree')
                    continue
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            except (FileNotFoundError,NotADirectoryError):continue
            with os.fdopen(fd,'rb') as stream:
                before=os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode),'WORKSPACE_SNAPSHOT_REQUIRED','Worker delta inputs must be regular files')
                conversion=['--path',relative] if relative in tracked_paths else ['--no-filters']
                oid=command('hash-object','-w','--stdin',*conversion,stdin=stream).stdout.strip()
                after=os.fstat(stream.fileno())
            require((before.st_mode,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
                    (after.st_mode,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Working file changed during worker delta capture')
            entries.extend((b'100755' if before.st_mode&0o111 else b'100644')+b' '+oid+b'\t'+os.fsencode(relative)+b'\0')
        if entries:command('update-index','-z','--index-info',input=bytes(entries))
        return command('write-tree').stdout.decode().strip()


def _capture_untracked(root,selected,*,journal=None):
    require(isinstance(selected,(list,tuple)) and all(isinstance(p,str) for p in selected),
            'WORKSPACE_SNAPSHOT_REQUIRED','Untracked-file selection must be an explicit list of paths')
    if not selected:return {}
    from .isolated_copy import _private_roots,private_path
    private_roots=_private_roots(journal)
    untracked=set(git(root,'ls-files','--others','--exclude-standard','-z').stdout.decode().split('\0'))-{''}
    require(len(set(selected))<=MAX_INPUT_FILES,'WORKSPACE_SNAPSHOT_REQUIRED','Too many selected input files')
    captured={};remaining=ARTIFACT_MAX
    for relative in sorted(set(selected)):
        require(time.monotonic()<(_preparation_deadline.get() or float('inf')),'WORKSPACE_SNAPSHOT_REQUIRED','Input preparation deadline elapsed')
        path=Path(relative)
        require(not path.is_absolute() and '..' not in path.parts and relative in untracked and not private_path(path)
                and not any((root/path).is_relative_to(home) for home in private_roots),
                'WORKSPACE_SNAPSHOT_REQUIRED','Untracked-file selection is not safe or explicit')
        try:
            require(not any((root/part).is_symlink() for part in (path,*path.parents)),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Symlinked untracked files require a separate approved snapshot')
            # Reject special files before reading; O_NOFOLLOW also covers a leaf replacement.
            require(stat.S_ISREG((root/path).lstat().st_mode),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Selected untracked inputs must be regular files')
            fd=os.open(root/path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                before=os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode),'WORKSPACE_SNAPSHOT_REQUIRED','Selected untracked inputs must be regular files')
                require(before.st_size<=remaining,'WORKSPACE_SNAPSHOT_REQUIRED','Selected inputs exceed the 10 MiB input limit')
                data=stream.read(remaining+1);after=os.fstat(stream.fileno())
                require(len(data)<=remaining,'WORKSPACE_SNAPSHOT_REQUIRED','Selected inputs exceed the 10 MiB input limit')
                remaining-=len(data)
            require((before.st_mode,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
                    (after.st_mode,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Selected untracked input changed during capture')
            captured[relative]=(data,stat.S_IMODE(before.st_mode))
        except OSError as error:
            raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED','Selected untracked input could not be captured safely') from error
    return captured


def _input_metadata(captured):
    return {path:{'sha256':hashlib.sha256(data).hexdigest(),'mode':mode} for path,(data,mode) in captured.items()}


def snapshot(cwd,*,untracked_allowlist=(),journal=None):
    with preparation():return _snapshot(cwd,untracked_allowlist=untracked_allowlist,journal=journal)


def _snapshot(cwd,*,untracked_allowlist=(),journal=None):
    root=read_git(cwd,'rev-parse','--show-toplevel',check=False)
    if root.returncode:return {'git':False,'cwd':str(Path(cwd).resolve())}
    root=Path(root.stdout.decode().strip())
    tracked=read_git(root,'ls-files','-z').stdout
    if tracked:
        attributes=read_git(root,'check-attr','-z','--stdin','filter',input=tracked).stdout.split(b'\0')
        require(all(value in (b'unspecified',b'unset') for value in attributes[2::3]),
                'WORKSPACE_SNAPSHOT_REQUIRED','Custom Git filter normalization is unavailable in a read-only snapshot')
    dirty=read_git(root,'diff','--no-ext-diff','--no-textconv','--no-renames','--name-only','HEAD','-z').stdout.split(b'\0')
    dirty=[os.fsdecode(name) for name in dirty if name]
    require(len(dirty)<=MAX_INPUT_FILES,'WORKSPACE_SNAPSHOT_REQUIRED','Too many dirty input files')
    size=0
    for name in dirty:
        require(time.monotonic()<_preparation_deadline.get(),'WORKSPACE_SNAPSHOT_REQUIRED','Input preparation deadline elapsed')
        path=root/name
        try:size+=path.lstat().st_size
        except FileNotFoundError:pass
        require(size<=ARTIFACT_MAX,'WORKSPACE_SNAPSHOT_REQUIRED','Dirty inputs exceed the 10 MiB input limit')
    result={'git':True,'dirty_input_files':len(dirty),'dirty_input_bytes':size,'root':str(root),'head':read_git(root,'rev-parse','HEAD').stdout.decode().strip(),
            'branch':read_git(root,'branch','--show-current').stdout.decode().strip(),
            'status_hex':read_git(root,'status','--porcelain=v1','-z','--untracked-files=all').stdout.hex(),
            'patch_sha256':hashlib.sha256(read_git(root,'diff','--no-ext-diff','--no-textconv','HEAD','--binary').stdout).hexdigest()}
    if untracked_allowlist:result['untracked_inputs']=_input_metadata(_capture_untracked(root,untracked_allowlist,journal=journal))
    return result


@dataclass(frozen=True)
class FrozenWorkspace:
    baseline:dict
    patch:bytes|None
    untracked:dict
    journal:object=field(default=None,repr=False,compare=False)
    deadline:float|None=field(default=None,repr=False,compare=False)


def freeze(cwd,workspaces,*,expected_baseline=None,journal=None,timeout=PREPARATION_SECONDS):
    with preparation(time.monotonic()+timeout):
        return _freeze(cwd,workspaces,expected_baseline=expected_baseline,journal=journal)


def _freeze(cwd,workspaces,*,expected_baseline=None,journal=None):
    """Capture the union of explicit writing inputs once, before admitting a batch.

    Keep this object in memory; only its JSON-safe baseline belongs in worker leases.
    """
    baseline=snapshot(cwd)
    copying=[workspace for workspace in workspaces if workspace.get('mode')=='isolated-copy']
    writing=[workspace for workspace in workspaces if workspace.get('mode','inherit-read-only')=='isolated-worktree']
    if copying:
        from .isolated_copy import capture
        require(not writing and not baseline['git'],'WORKSPACE_SNAPSHOT_REQUIRED','Isolated copies require a non-Git source; Git sources use isolated-worktree')
        baseline,captured=capture(cwd,copying,baseline,expected_baseline=expected_baseline,journal=journal)
        frozen=FrozenWorkspace(baseline,None,captured,journal,_preparation_deadline.get())
        validate_frozen(cwd,frozen)
        return frozen
    if not writing:return FrozenWorkspace(baseline,None,{},journal,_preparation_deadline.get())
    require(baseline['git'],'WORKSPACE_SNAPSHOT_REQUIRED','Concurrent writing in a non-Git directory requires an explicit isolated copy')
    root=Path(baseline['root']);selected=[]
    for workspace in writing:
        paths=workspace.get('untracked_allowlist',[])
        require(isinstance(paths,list) and all(isinstance(p,str) for p in paths),
                'WORKSPACE_SNAPSHOT_REQUIRED','Untracked-file selection must be an explicit list of paths')
        selected.extend(paths)
    names=read_git(root,'diff','--no-ext-diff','--no-textconv','--name-only','HEAD','-z').stdout.split(b'\0')
    require(not any(sensitive(n.decode()) for n in names if n),
            'WORKSPACE_SNAPSHOT_REQUIRED','Dirty secret files are not copied into worker worktrees')
    captured=_capture_untracked(root,selected,journal=journal)
    patch=read_git(root,'diff','--no-ext-diff','--no-textconv','HEAD','--binary').stdout
    expected=baseline
    if captured:baseline=dict(baseline,untracked_inputs=_input_metadata(captured))
    if expected_baseline is not None and 'untracked_inputs' in expected_baseline:expected=baseline
    require(expected_baseline is None or expected==expected_baseline,
            'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout differs from the shared writing-batch baseline')
    require(hashlib.sha256(patch).hexdigest()==baseline['patch_sha256'],
            'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed during patch capture')
    frozen=FrozenWorkspace(baseline,patch,captured,journal,_preparation_deadline.get())
    validate_frozen(cwd,frozen)
    return frozen


def validate_frozen(cwd,frozen,*,journal=None):
    with preparation(frozen.deadline):return _validate_frozen(cwd,frozen,journal=journal)


def _validate_frozen(cwd,frozen,*,journal=None):
    """Recheck all batch inputs immediately before native worker admission."""
    if 'copy_inputs' in frozen.baseline:
        from .isolated_copy import validate_source
        validate_source(cwd,frozen,journal=journal)
        return
    require(snapshot(cwd,untracked_allowlist=list(frozen.untracked),journal=journal or frozen.journal)==frozen.baseline,
            'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout differs from the frozen writing-batch inputs')


def prepare(journal,cwd,worker_id,workspace,*,expected_baseline=None,frozen=None):
    with preparation(frozen.deadline if frozen else None):
        return _prepare(journal,cwd,worker_id,workspace,expected_baseline=expected_baseline,frozen=frozen)


def _prepare(journal,cwd,worker_id,workspace,*,expected_baseline=None,frozen=None):
    mode=workspace.get('mode','inherit-read-only')
    if mode=='inherit-read-only':return str(Path(cwd).resolve()),None,snapshot(cwd)
    require(mode in ('isolated-worktree','isolated-copy'),'WORKSPACE_SNAPSHOT_REQUIRED','Writing workers require isolated-worktree or isolated-copy mode')
    if frozen is None:frozen=freeze(cwd,[workspace],expected_baseline=expected_baseline,journal=journal)
    if mode=='isolated-copy':
        from .isolated_copy import prepare_copy
        return prepare_copy(cwd,worker_id,workspace,frozen,expected_baseline=expected_baseline,journal=journal)
    baseline=frozen.baseline
    require(baseline['git'] and frozen.patch is not None,
            'WORKSPACE_SNAPSHOT_REQUIRED','Writing inputs were not frozen for this batch')
    expected=baseline if expected_baseline is None or 'untracked_inputs' in expected_baseline else {k:v for k,v in baseline.items() if k!='untracked_inputs'}
    require(expected_baseline is None or expected==expected_baseline,
            'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout differs from the shared writing-batch baseline')
    selected=workspace.get('untracked_allowlist',[])
    require(isinstance(selected,list) and all(isinstance(p,str) and p in frozen.untracked for p in selected),
            'WORKSPACE_SNAPSHOT_REQUIRED','Worker untracked-file selection was not frozen for this batch')
    validate_frozen(cwd,frozen)
    root=Path(baseline['root'])
    directory=worktree_parent(journal,root)/worker_id
    git(root,'worktree','add','-b','npeer/'+worker_id,str(directory),baseline['head'])
    if frozen.patch:git(directory,'apply','--binary','--index',input=frozen.patch)
    for relative in selected:
        data,mode=frozen.untracked[relative]
        destination=directory/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(data);destination.chmod(mode)
    baseline=dict(baseline,input_tree=_input_tree(directory,frozen,selected))
    validate_frozen(cwd,frozen)
    return str(directory),worker_id,baseline
