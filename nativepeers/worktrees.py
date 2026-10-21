"""Reproducible owned writing worktrees; the source checkout is never reset."""
import hashlib
from pathlib import Path
import shutil
import subprocess

from .errors import require, PeerError
from .files import sha256


def git(cwd,*args,input=None,check=True):
    result=subprocess.run(['git','-C',str(cwd),*args],input=input,capture_output=True)
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
    secret_names={'.env','.credentials.json','auth.json','id_rsa','id_ed25519'}
    require(not any(Path(n.decode()).name in secret_names or Path(n.decode()).name.startswith('.env.') for n in names if n),
            'WORKSPACE_SNAPSHOT_REQUIRED','Dirty secret files are not copied into worker worktrees')
    selected=workspace.get('untracked_allowlist',[])
    untracked=set(git(root,'ls-files','--others','--exclude-standard','-z').stdout.decode().split('\0'))-{''}
    for relative in selected:
        p=Path(relative)
        require(not p.is_absolute() and '..' not in p.parts and relative in untracked and p.name not in secret_names and not p.name.startswith('.env'),
                'WORKSPACE_SNAPSHOT_REQUIRED','Untracked-file selection is not safe or explicit')
        require(not (root/p).is_symlink(),'WORKSPACE_SNAPSHOT_REQUIRED','Symlinked untracked files require a separate approved snapshot')
    require(snapshot(cwd)==baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed during baseline capture')
    directory=journal.root/'worktrees'/worker_id;directory.parent.mkdir(mode=0o700,exist_ok=True)
    git(root,'worktree','add','-b','npeer/'+worker_id,str(directory),baseline['head'])
    if patch:git(directory,'apply','--binary','--index',input=patch)
    for relative in selected:
        destination=directory/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/relative,destination)
    require(snapshot(cwd)==baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed while the isolated worktree was created; retained the worktree for review')
    return str(directory),worker_id,baseline
