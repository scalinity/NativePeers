"""Explicit, bounded file copies for writing workers with a non-Git source."""
from contextlib import closing
import os
from pathlib import Path
import shutil
import stat
import sqlite3

from .errors import PeerError,require
from .files import private_directory
from .models import ARTIFACT_MAX
from .worktrees import git,sensitive,snapshot,_input_metadata

MAX_FILES=256
PRIVATE_NAMES={'.git','.hg','.svn','.worktrees','.nativepeers','.codex','.claude','.gemini',
               '.ssh','.aws','.azure','.gnupg','gcloud','.netrc','.npmrc','.pypirc',
               '.git-credentials','.pgpass','credentials','credentials.json','token.json',
               'tokens.json','transcripts','id_dsa','id_ecdsa','.secrets','secrets.json',
               'secrets.yaml','secrets.yml'}


def private_path(path):
    path=Path(str(path).lower())
    return (sensitive(path) or any(part in PRIVATE_NAMES for part in path.parts)
            or path.suffix in ('.pem','.key','.p12','.pfx','.kdbx')
            or (path.suffix=='.jsonl' and (path.stem in ('transcript','transcripts') or path.name.startswith('rollout-'))))


def selection(workspace):
    paths=workspace.get('file_allowlist')
    require(isinstance(paths,list) and 0<len(paths)<=MAX_FILES and all(isinstance(p,str) for p in paths),
            'WORKSPACE_SNAPSHOT_REQUIRED','Isolated copies require an explicit file_allowlist of 1 to 256 relative files')
    require(len(set(paths))==len(paths) and not workspace.get('untracked_allowlist'),
            'WORKSPACE_SNAPSHOT_REQUIRED','Copy selections must be unique; use file_allowlist for non-Git sources')
    for relative in paths:
        path=Path(relative)
        require(relative and len(relative)<=4096 and not path.is_absolute() and '..' not in path.parts
                and str(path)==relative and relative!='.' and '\\' not in relative
                and not any(ord(c)<32 for c in relative) and not private_path(path),
                'WORKSPACE_SNAPSHOT_REQUIRED','Copy selection contains an unsafe or private path')
    return paths


def _capture(root,selected,*,journal=None):
    require(len(selected)<=MAX_FILES,'WORKSPACE_SNAPSHOT_REQUIRED','Copy batch exceeds 256 selected files')
    captured={};remaining=ARTIFACT_MAX;private_roots=_private_roots(journal)
    for relative in sorted(selected):
        from .worktrees import _preparation_deadline
        import time
        require(time.monotonic()<(_preparation_deadline.get() or float('inf')),'WORKSPACE_SNAPSHOT_REQUIRED','Input preparation deadline elapsed')
        require(not any((root/relative).is_relative_to(home) for home in private_roots),
                'WORKSPACE_SNAPSHOT_REQUIRED','Private native state cannot be selected for copying')
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            parts=Path(relative).parts
            for part in parts[:-1]:
                next_directory=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=next_directory
            fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
            with os.fdopen(fd,'rb') as stream:
                before=os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and not before.st_mode&0o7000,
                        'WORKSPACE_SNAPSHOT_REQUIRED','Selected copy inputs must be regular files with ordinary permission bits')
                require(before.st_size<=remaining,'WORKSPACE_SNAPSHOT_REQUIRED','Copy batch exceeds the 10 MiB input limit')
                data=stream.read(remaining+1);after=os.fstat(stream.fileno())
            require(len(data)<=remaining and len(data)==after.st_size
                    and (before.st_dev,before.st_ino,before.st_mode,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
                        (after.st_dev,after.st_ino,after.st_mode,after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                    'WORKSPACE_SNAPSHOT_REQUIRED','Selected copy input changed during capture or exceeds the input limit')
            captured[relative]=(data,stat.S_IMODE(before.st_mode));remaining-=len(data)
        except OSError as error:
            raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED','Selected copy input could not be captured safely') from error
        finally:os.close(directory)
    return captured


def _private_roots(journal=None):
    # Match artifact import's known-private boundary, including offline registrations.
    roots=[(Path.home()/'.local/state/nativepeers').resolve(),
           *(Path(os.environ[name]).expanduser().resolve() for name in
             ('CODEX_HOME','CLAUDE_CONFIG_DIR','NPEER_HOME') if os.environ.get(name))]
    if journal is not None:
        roots.append(journal.root.resolve())
        # freeze/prepare also run in executor threads; never share the journal connection.
        try:
            with closing(sqlite3.connect((journal.root/'journal.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                homes=db.execute("SELECT DISTINCT json_extract(descriptor_json,'$.home') FROM peer_instances").fetchall()
            roots.extend(Path(home).expanduser().resolve() for (home,) in homes)
        except (sqlite3.Error,TypeError,ValueError) as error:
            raise PeerError('WORKSPACE_SNAPSHOT_REQUIRED','Known private native roots could not be verified') from error
    return roots


def _root(cwd,*,journal=None):
    root=Path(cwd).resolve(strict=True)
    require(root.is_dir() and not private_path(root) and not any(root.is_relative_to(home) for home in _private_roots(journal)),
            'WORKSPACE_SNAPSHOT_REQUIRED','Copy source must be a project directory outside private native state')
    return root


def capture(cwd,workspaces,baseline,*,expected_baseline=None,journal=None):
    root=_root(cwd,journal=journal);selected=set()
    for workspace in workspaces:selected.update(selection(workspace))
    captured=_capture(root,selected,journal=journal)
    baseline=dict(baseline,copy_inputs=_input_metadata(captured))
    expected=baseline if expected_baseline is not None and 'copy_inputs' in expected_baseline else {k:v for k,v in baseline.items() if k!='copy_inputs'}
    require(expected_baseline is None or expected==expected_baseline,
            'WORKSPACE_SNAPSHOT_REQUIRED','Original directory differs from the shared copy-batch baseline')
    return baseline,captured


def validate_source(cwd,frozen,*,journal=None):
    journal=journal or frozen.journal
    observed=dict(snapshot(cwd),copy_inputs=_input_metadata(_capture(_root(cwd,journal=journal),frozen.untracked,journal=journal)))
    require(observed==frozen.baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original directory differs from the frozen copy-batch inputs')



def identity_preflight(cwd):
    """Use the configured identity; never invent or change commit attribution."""
    import subprocess
    for variable in ('GIT_AUTHOR_IDENT','GIT_COMMITTER_IDENT'):
        result=subprocess.run(['git','-C',str(cwd),'var',variable],capture_output=True,timeout=3)
        require(result.returncode==0,'GIT_IDENTITY_REQUIRED',
                'Isolated-copy baselines require an existing Git author and committer identity; configure Git before spawning this editing task',
                details={'missing_identity':variable,'source_changed':False})
    return {'state':'READY','basis':'existing Git identity','configuration_changed':False}

def prepare_copy(cwd,worker_id,workspace,frozen,*,expected_baseline=None,journal=None):
    identity_preflight(cwd)
    baseline=frozen.baseline;selected=selection(workspace)
    require(not baseline.get('git') and 'copy_inputs' in baseline and frozen.patch is None
            and all(p in frozen.untracked for p in selected),
            'WORKSPACE_SNAPSHOT_REQUIRED','Copy inputs were not frozen for this worker')
    expected=baseline if expected_baseline is not None and 'copy_inputs' in expected_baseline else {k:v for k,v in baseline.items() if k!='copy_inputs'}
    require(expected_baseline is None or expected==expected_baseline,
            'WORKSPACE_SNAPSHOT_REQUIRED','Original directory differs from the shared copy-batch baseline')
    require(isinstance(worker_id,str) and worker_id not in ('','.','..') and Path(worker_id).name==worker_id,
            'WORKSPACE_SNAPSHOT_REQUIRED','Copy worker ID must be a single directory name')
    validate_source(cwd,frozen,journal=journal)
    root=_root(cwd,journal=journal);parent=private_directory(root/'.worktrees');directory=parent/worker_id
    directory.mkdir(mode=0o700)
    try:
        for relative in selected:
            data,mode=frozen.untracked[relative]
            destination=directory/relative;destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            destination.write_bytes(data);destination.chmod(mode)
        # Only this owned copy is initialized. Raw blobs avoid filters and preserve selected bytes.
        git(directory,'-c','core.hooksPath=/dev/null','init','-q','--template=')
        (directory/'.git/info').mkdir(exist_ok=True)
        (directory/'.git/info/attributes').write_text('* -text -ident -filter -working-tree-encoding\n')
        entries=bytearray()
        for relative in sorted(selected):
            data,mode=frozen.untracked[relative]
            oid=git(directory,'hash-object','-w','--stdin','--no-filters',input=data).stdout.strip()
            entries.extend((b'100755' if mode&0o111 else b'100644')+b' '+oid+b'\t'+os.fsencode(relative)+b'\0')
        git(directory,'update-index','-z','--index-info',input=bytes(entries))
        tree=git(directory,'write-tree').stdout.decode().strip()
        head=git(directory,'commit-tree',tree,input=b'Create isolated copy baseline\n').stdout.decode().strip()
        git(directory,'-c','core.hooksPath=/dev/null','update-ref','HEAD',head)
        validate_source(cwd,frozen,journal=journal)
        return str(directory),worker_id,dict(baseline,workspace_mode='isolated-copy',
            copy_inputs={p:baseline['copy_inputs'][p] for p in selected},head=head,input_tree=tree)
    except BaseException:
        shutil.rmtree(directory)
        raise
