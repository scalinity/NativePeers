"""Reproducible owned writing worktrees; the source checkout is never reset."""
import hashlib
import contextlib
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import time

from .errors import require, PeerError
from .files import sha256,advisory_lock,atomic_json,private_directory
from .profiles import ROOT
from .models import ARTIFACT_MAX

SECRET_NAMES={'.env','.credentials.json','auth.json','id_rsa','id_ed25519'}


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


def bounded_diff(cwd,args,limit,deadline):
    process=subprocess.Popen(['git','-C',str(cwd),'diff','--no-ext-diff','--no-textconv',*args],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
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
    try:
        tracked=[p for p in git(cwd,'diff','--name-only',baseline['head'],'-z',timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0') if p]
        untracked=[p for p in git(cwd,'ls-files','--others','--exclude-standard','-z',timeout=max(.001,deadline-time.monotonic())).stdout.decode().split('\0') if p]
    except (OSError,subprocess.TimeoutExpired,PeerError):
        return {'patch':b'','changed_files':[],'untracked_files':[],'file_count':None,'excluded_sensitive_or_symlink_files':[],
                'patch_unavailable_reason':'File discovery failed or timed out; inspect the retained worktree.'}
    excluded={p for p in tracked+untracked if sensitive(p) or (Path(cwd)/p).is_symlink()}
    selected=[p for p in tracked if p not in excluded]
    result={'changed_files':tracked[:100],'untracked_files':untracked[:100],
            'file_count':len(tracked)+len(untracked),'excluded_sensitive_or_symlink_files':sorted(excluded)[:100]}
    commands=([ [baseline['head'],'--binary','--',*selected] ] if selected else [])
    commands.extend(['--no-index','--binary','--','/dev/null',relative] for relative in untracked if relative not in excluded)
    data=bytearray()
    for args in commands:
        try:piece,reason=bounded_diff(cwd,args,max_bytes-len(data),deadline)
        except OSError:piece,reason=None,'Git diff could not start; inspect the retained worktree.'
        if reason:return dict(result,patch=b'',patch_unavailable_reason=reason)
        data.extend(piece)
    return dict(result,patch=bytes(data))


def git(cwd,*args,input=None,check=True,timeout=None):
    result=subprocess.run(['git','-C',str(cwd),*args],input=input,capture_output=True,timeout=timeout)
    if check and result.returncode:
        raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED',result.stderr.decode(errors='replace')[:800])
    return result


def snapshot(cwd):
    root=git(cwd,'rev-parse','--show-toplevel',check=False)
    if root.returncode:return {'git':False,'cwd':str(Path(cwd).resolve())}
    root=Path(root.stdout.decode().strip())
    return {'git':True,'root':str(root),'head':git(root,'rev-parse','HEAD').stdout.decode().strip(),
            'branch':git(root,'branch','--show-current').stdout.decode().strip(),
            'status_hex':git(root,'status','--porcelain=v1','-z','--untracked-files=all').stdout.hex(),
            'patch_sha256':hashlib.sha256(git(root,'diff','HEAD','--binary').stdout).hexdigest()}


def prepare(journal,cwd,worker_id,workspace,*,expected_baseline=None):
    baseline=snapshot(cwd)
    mode=workspace.get('mode','inherit-read-only')
    if mode=='inherit-read-only':return str(Path(cwd).resolve()),None,baseline
    require(mode=='isolated-worktree','WORKSPACE_SNAPSHOT_REQUIRED','Writing workers require isolated-worktree mode')
    require(expected_baseline is None or baseline==expected_baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout differs from the shared writing-batch baseline')
    require(baseline['git'],'WORKSPACE_SNAPSHOT_REQUIRED','Concurrent writing in a non-Git directory requires an explicit isolated copy')
    root=Path(baseline['root']);patch=git(root,'diff','HEAD','--binary').stdout
    names=git(root,'diff','--name-only','HEAD','-z').stdout.split(b'\0')
    require(not any(sensitive(n.decode()) for n in names if n),
            'WORKSPACE_SNAPSHOT_REQUIRED','Dirty secret files are not copied into worker worktrees')
    selected=workspace.get('untracked_allowlist',[])
    untracked=set(git(root,'ls-files','--others','--exclude-standard','-z').stdout.decode().split('\0'))-{''}
    for relative in selected:
        p=Path(relative)
        require(not p.is_absolute() and '..' not in p.parts and relative in untracked and not sensitive(p),
                'WORKSPACE_SNAPSHOT_REQUIRED','Untracked-file selection is not safe or explicit')
        require(not (root/p).is_symlink(),'WORKSPACE_SNAPSHOT_REQUIRED','Symlinked untracked files require a separate approved snapshot')
    require(snapshot(cwd)==baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed during baseline capture')
    directory=worktree_parent(journal,root)/worker_id
    git(root,'worktree','add','-b','npeer/'+worker_id,str(directory),baseline['head'])
    if patch:git(directory,'apply','--binary','--index',input=patch)
    for relative in selected:
        destination=directory/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/relative,destination)
    require(snapshot(cwd)==baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed while the isolated worktree was created; retained the worktree for review')
    return str(directory),worker_id,baseline
