"""Read-only applicability checks for an owning parent's retained worker delta."""
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import contextlib
import subprocess
import time

from .artifacts import Artifacts,_selected_file_bytes
from .errors import PeerError,require
from .isolated_copy import _private_roots,private_path
from .models import ARTIFACT_MAX
from .registry import Registry

TERMINAL={'COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED'}
MAX_PATHS=1024


def _owned(journal,caller,worker_id):
    require(caller.kind=='peer' and caller.incarnation,'CALLER_UNBOUND','A verified owning parent is required')
    Registry(journal).verify(caller.key,caller.incarnation)
    row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
    require(row,'PROTOCOL_ERROR','Unknown exact worker ID')
    group=journal.one('SELECT * FROM groups WHERE group_id=?',(row['group_id'],))
    require(group and group['parent_key']==caller.key,
            'APPROVAL_BLOCKED','Only the owning parent can inspect retained workspace changes')
    lease=json.loads(row['lease_json'])
    require(lease.get('parent_key')==caller.key,'OWNERSHIP_UNKNOWN','Worker lease parent differs from its group')
    return row,lease


def _artifact(journal,caller,handle):
    meta=Artifacts(journal).read(handle,caller.key,0,1)
    raw=_selected_file_bytes(journal.root/'artifacts'/handle,ARTIFACT_MAX)
    require(len(raw)==meta['byte_size'] and hashlib.sha256(raw).hexdigest()==meta['sha256'],
            'PROTOCOL_ERROR','Retained artifact changed during inspection')
    return raw


def _result(journal,caller,row,task_id=None):
    task=journal.one('SELECT * FROM tasks WHERE worker_id=? AND generation=?'+
        (' AND task_id=?' if task_id else '')+' ORDER BY rowid DESC LIMIT 1',
        (row['worker_id'],row['generation'])+((task_id,) if task_id else ()))
    require(task and task['state'] in TERMINAL and task['result_artifact_id'],
            'RESULT_UNAVAILABLE','No terminal result exists for this worker generation')
    record=json.loads(_artifact(journal,caller,task['result_artifact_id']))
    require(record.get('task_id')==task['task_id'] and record.get('group_id')==row['group_id']
            and record.get('worker',{}).get('native_session_id')==row['native_id'],
            'OWNERSHIP_UNKNOWN','Retained result differs from its worker/task identity')
    return task,record


def _workspace(journal,row,lease):
    baseline=lease.get('baseline',{})
    copying=baseline.get('workspace_mode')=='isolated-copy'
    require(row['worktree_id']==row['worker_id'] and baseline.get('input_tree') and baseline.get('head')
            and (copying and baseline.get('git') is False or not copying and baseline.get('git') is True),
            'OWNERSHIP_UNKNOWN','Worker has no recognized retained writing workspace')
    source=Path(baseline['cwd'] if copying else baseline['root'])
    work=Path(row['cwd']);expected=source/'.worktrees'/row['worker_id']
    require(source.is_absolute() and source.resolve(strict=True)==source and work==expected
            and work.resolve(strict=True)==expected and lease.get('spec',{}).get('cwd')==str(work)
            and not any(p.is_symlink() for p in (work,work.parent)),
            'OWNERSHIP_UNKNOWN','Retained directory is outside the exact owned workspace path')
    require(not any(source.is_relative_to(root) for root in _private_roots(journal)),
            'OWNERSHIP_UNKNOWN','Source directory overlaps private native state')
    return source,work,baseline,'isolated-copy' if copying else 'isolated-worktree'


def _git(cwd,args,deadline,*,raw=None):
    require(time.monotonic()<deadline,'PREVIEW_UNAVAILABLE','Inspection time limit reached')
    environment={k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0')
    command=['git','-C',str(cwd),'-c','core.fsmonitor=false','-c','core.hooksPath=/dev/null',*args]
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)
    output={process.stdout:bytearray(),process.stderr:bytearray()};sent=0;raw=raw or b''
    try:
        with selectors.DefaultSelector() as selector:
            for stream in output:
                os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ)
            if raw:
                os.set_blocking(process.stdin.fileno(),False);selector.register(process.stdin,selectors.EVENT_WRITE)
            else:process.stdin.close()
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(command,0)
                for key,_ in selector.select(remaining):
                    stream=key.fileobj
                    if stream is process.stdin:
                        try:sent+=os.write(stream.fileno(),raw[sent:sent+65536])
                        except BrokenPipeError:sent=len(raw)
                        if sent==len(raw):selector.unregister(stream);stream.close()
                    else:
                        chunk=os.read(stream.fileno(),65536)
                        if not chunk:selector.unregister(stream);continue
                        output[stream].extend(chunk)
                        require(sum(map(len,output.values()))<=ARTIFACT_MAX,'PREVIEW_UNAVAILABLE','Git inspection output exceeds its byte limit')
        code=process.wait(timeout=max(.001,deadline-time.monotonic()))
        return subprocess.CompletedProcess(command,code,bytes(output[process.stdout]),bytes(output[process.stderr]))
    finally:
        if process.poll() is None:
            with contextlib.suppress(ProcessLookupError):process.kill()
            process.wait(timeout=1)
        for stream in (process.stdin,process.stdout,process.stderr):stream.close()



def _safe_paths(paths):
    require(len(paths)<=MAX_PATHS,'PREVIEW_UNAVAILABLE','Delta has too many paths to inspect')
    for name in paths:
        path=Path(name)
        require(name and len(name)<=4096 and not path.is_absolute() and '..' not in path.parts
                and str(path)==name and name!='.' and not private_path(path)
                and '\\' not in name and not any(ord(c)<32 for c in name),
                'PREVIEW_UNAVAILABLE','Delta contains an unsafe, private or unsupported path')
    return sorted(paths)


def _patch_files(work,raw,deadline):
    paths=set();binary=set()
    # Reverse stats include the old side of renames as well as the new side.
    for reverse in ([],['--reverse']):
        result=_git(work,['apply','--numstat','-z',*reverse,'-'],deadline,raw=raw)
        require(result.returncode==0,'PREVIEW_UNAVAILABLE','Worker delta is not a valid Git patch')
        for entry in result.stdout.split(b'\0'):
            if not entry:continue
            added,removed,name=entry.split(b'\t',2);name=os.fsdecode(name)
            paths.add(name)
            if added==b'-' or removed==b'-':binary.add(name)
    return _safe_paths(paths),binary


def _inputs(source,work,base,paths,mode,deadline):
    require(re.fullmatch(r'[0-9a-f]{40,64}',base or ''),'PREVIEW_UNAVAILABLE','Frozen input tree ID is unavailable')
    listing=_git(work,['ls-tree','-z',base,'--',*paths],deadline)
    require(listing.returncode==0,'PREVIEW_UNAVAILABLE','Frozen input tree is no longer available')
    expected={}
    for entry in listing.stdout.split(b'\0'):
        if entry:
            info,name=entry.split(b'\t',1);bits,kind,oid=info.split()
            require(kind==b'blob' and bits in (b'100644',b'100755'),'PREVIEW_UNAVAILABLE','Submodules and symlinks require manual inspection')
            expected[os.fsdecode(name)]={'mode':bits.decode(),'oid':oid.decode()}
    current={};remaining=ARTIFACT_MAX
    for name in paths:
        path=source/name
        require(not any(p.is_symlink() for p in (path,*path.parents)),
                'PREVIEW_UNAVAILABLE','Source paths must not contain symlinks')
        try:raw=_selected_file_bytes(path,remaining)
        except FileNotFoundError:current[name]=None;continue
        remaining-=len(raw)
        conversion=['--no-filters']
        if mode=='isolated-worktree':
            attr=_git(source,['check-attr','-z','filter','--',name],deadline)
            require(attr.returncode==0 and attr.stdout.split(b'\0')[2] in (b'unspecified',b'unset'),
                    'PREVIEW_UNAVAILABLE','Custom source filters require manual inspection')
            filters=_git(source,['config','--null','--name-only','--get-regexp',r'^filter\..*\.(clean|smudge|process|required)$'],deadline)
            require(filters.returncode in (0,1),'PREVIEW_UNAVAILABLE','Source filter configuration could not be read')
            options=[]
            for key in filters.stdout.decode().split('\0'):
                if key:options.extend(['-c',key+'='+('false' if key.endswith('.required') else '')])
            conversion=[*options,'hash-object','--stdin','--path',name]
        oid=_git(source,conversion if mode=='isolated-worktree' else ['hash-object','--stdin',*conversion],deadline,raw=raw)
        require(oid.returncode==0,'PREVIEW_UNAVAILABLE','Current source content could not be compared')
        current[name]={'mode':'100755' if path.stat().st_mode&0o111 else '100644','oid':oid.stdout.decode().strip()}
    return [{'path':name,'expected':expected.get(name),'current':current[name],
             'matches_input':expected.get(name)==current[name]} for name in paths]


def preview(journal,caller,worker_id,*,task_id=None,timeout=5):
    """Inspect one immutable worker-only patch; never apply it or write an index."""
    require(isinstance(timeout,(int,float)) and 0<timeout<=30,'PROTOCOL_ERROR','Preview timeout must be between 0 and 30 seconds')
    row,lease=_owned(journal,caller,worker_id)
    task,record=_result(journal,caller,row,task_id)
    value=dict(worker_id=worker_id,task_id=task['task_id'],generation=row['generation'],
               result_artifact_id=task['result_artifact_id'],status='unavailable',applicable=False,
               applied=False,files=[])
    deadline=time.monotonic()+timeout
    try:
        source,work,baseline,mode=_workspace(journal,row,lease)
        changes=record.get('changes',{})
        require(changes.get('baseline')==baseline and changes.get('worktree')==str(work),
                'PREVIEW_UNAVAILABLE','Retained result workspace differs from its lease')
        base=changes.get('worker_delta_base');handle=changes.get('worker_delta_artifact')
        value.update(source=str(source),worktree=str(work),workspace_mode=mode,worker_delta_base=base,worker_delta_artifact=handle)
        require(base==baseline['input_tree'] and not changes.get('worker_delta_unavailable_reason'),
                'PREVIEW_UNAVAILABLE',changes.get('worker_delta_unavailable_reason') or 'Worker delta base is unavailable')
        if changes.get('worker_delta_empty') is True and not handle:
            value.update(status='no_changes',applicable=True,inputs_match=True,check_applicable=True)
            return value
        require(handle,'PREVIEW_UNAVAILABLE','Worker-only delta is unavailable; reconstruction patches are not substituted')
        raw=_artifact(journal,caller,handle)
        paths,binary=_patch_files(work,raw,deadline)
        require(paths,'PREVIEW_UNAVAILABLE','Worker delta has no inspectable file paths')
        source_git=_git(source,['rev-parse','--show-toplevel'],deadline)
        require((mode=='isolated-copy' and source_git.returncode!=0) or
                (mode=='isolated-worktree' and source_git.returncode==0 and Path(os.fsdecode(source_git.stdout).strip())==source),
                'PREVIEW_UNAVAILABLE','Source Git/copy mode no longer matches its baseline')
        files=_inputs(source,work,base,paths,mode,deadline)
        check=_git(source,['apply','--check','--binary','--whitespace=nowarn','-'],deadline,raw=raw)
        after=_inputs(source,work,base,paths,mode,deadline)
        require(files==after,'PREVIEW_UNAVAILABLE','Relevant source files changed during preview')
        require(journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))==row,
                'PREVIEW_UNAVAILABLE','Worker ownership or generation changed during preview')
        matches=all(f['matches_input'] for f in files)
        for f in files:f['binary']=f['path'] in binary
        value.update(files=files,inputs_match=matches,check_applicable=check.returncode==0,
                     applicable=matches and check.returncode==0,
                     status='conflict' if check.returncode else 'applicable' if matches else 'source_changed',
                     check_error=check.stderr.decode(errors='replace')[:1600])
    except (PeerError,OSError,ValueError,subprocess.TimeoutExpired) as error:
        value.update(status='unavailable',applicable=False,reason=error.message if isinstance(error,PeerError) else type(error).__name__)
    return value
