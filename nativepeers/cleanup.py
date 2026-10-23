"""Bounded, read-only cleanup plans. Nothing is removed or marked cleaned."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

from .artifacts import _selected_file_bytes
from .change_preview import _owned,_result,_workspace,_git,_inputs,_safe_paths
from .errors import PeerError,require
from .models import ARTIFACT_MAX
from .process import matches,owned_status
from .ownership import release_status


def _released(journal,row):
    require(row['state'] in ('RELEASED_RESUMABLE','FAILED'),'OWNERSHIP_CONFLICT','Worker has not released its writer')
    # Resumed histories keep their retained workspaces; this planner covers initial runs.
    require(row['generation']==1,'OWNERSHIP_CONFLICT','Resumed generations must be retained')
    path=journal.root/'workers'/(row['worker_id']+'.json')
    proof=journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='controller-not-created' ORDER BY sequence DESC LIMIT 1",(row['worker_id'],))
    proof=json.loads(proof['payload_json']) if proof else None
    if proof and proof.get('controller_created') is False and all(proof.get(k)==row[k] for k in ('worker_id','generation','native_id','cwd')):
        require(not path.exists(),'OWNERSHIP_UNKNOWN','A process manifest contradicts the never-created record')
        started=journal.one('SELECT 1 FROM tasks WHERE worker_id=? AND (native_turn_id IS NOT NULL OR result_artifact_id IS NOT NULL)',(row['worker_id'],))
        require(not started,'OWNERSHIP_UNKNOWN','Native execution evidence contradicts the prelaunch record')
        manifest={'controller_created':False,'proof':proof}
    else:
        manifest=json.loads(_selected_file_bytes(path,65536))
        require(all(manifest.get(key)==row[key] for key in ('worker_id','group_id','native_id','generation'))
                and manifest.get('owner'),'OWNERSHIP_UNKNOWN','Owned process manifest differs from this generation')
        closure=release_status(journal,row)
        require(closure['released'],'CLEANUP_UNCERTAIN','Owned process/output closure is unproved',details=closure)
    tasks=journal.one("SELECT COUNT(*) total,SUM(state NOT IN ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED')) unfinished FROM tasks WHERE worker_id=?",(row['worker_id'],))
    require(tasks['total'] and not tasks['unfinished'],'OWNERSHIP_CONFLICT','Worker has unfinished tasks')
    # Inspect only registry descriptors, without native discovery or metadata refresh writes.
    for peer in journal.all("SELECT descriptor_json FROM peer_instances WHERE provider=? AND native_id=? AND status='live'",(row['provider'],row['native_id'])):
        descriptor=json.loads(peer['descriptor_json'])
        require(owned_status(descriptor['owner'])=='DEAD','OWNERSHIP_CONFLICT','A registered runtime owns this native history')
    return manifest


def _storage(source,work,baseline,mode,deadline):
    top=_git(work,['rev-parse','--show-toplevel'],deadline)
    require(top.returncode==0 and Path(os.fsdecode(top.stdout).strip())==work,
            'OWNERSHIP_UNKNOWN','Retained directory is no longer its own Git workspace')
    head=_git(work,['rev-parse','HEAD'],deadline)
    require(head.returncode==0 and head.stdout.decode().strip()==baseline['head'],
            'UNIQUE_CHANGES','Retained workspace has commits beyond its input HEAD')
    reflog=_git(work,['reflog','show','--format=%H','HEAD'],deadline)
    require(reflog.returncode==0 and all(oid==baseline['head'] for oid in reflog.stdout.decode().splitlines()),
            'UNIQUE_CHANGES','Workspace reflog retains other commits')
    staged=_git(work,['diff','--cached','--quiet','--no-ext-diff','--no-textconv',baseline['input_tree'],'--'],deadline)
    require(staged.returncode==0,'UNIQUE_CHANGES','Workspace index differs from its frozen input tree')
    common=_git(work,['rev-parse','--git-common-dir'],deadline)
    require(common.returncode==0,'OWNERSHIP_UNKNOWN','Workspace Git storage is unavailable')
    common_path=Path(os.fsdecode(common.stdout).strip())
    common_path=(work/common_path).resolve() if not common_path.is_absolute() else common_path.resolve()
    listing=_git(work,['worktree','list','--porcelain','-z'],deadline)
    require(listing.returncode==0,'OWNERSHIP_UNKNOWN','Git worktree registration cannot be checked')
    entries=[entry.split(b'\0') for entry in listing.stdout.split(b'\0\0') if entry]
    if mode=='isolated-copy':
        require((work/'.git').is_dir() and not (work/'.git').is_symlink() and common_path==work/'.git'
                and len(entries)==1 and entries[0][0]==b'worktree '+os.fsencode(work),
                'OWNERSHIP_UNKNOWN','Selected copy is not a standalone owned repository')
        refs=_git(work,['for-each-ref','--format=%(objectname)'],deadline)
        require(refs.returncode==0 and all(oid==baseline['head'] for oid in refs.stdout.decode().splitlines()),
                'UNIQUE_CHANGES','Selected copy has additional retained refs')
        objects=_git(work,['fsck','--full','--unreachable','--no-reflogs'],deadline)
        require(objects.returncode==0 and not objects.stdout,'UNIQUE_CHANGES','Selected copy retains extra Git objects')
    else:
        original=_git(source,['rev-parse','--git-common-dir'],deadline)
        original_path=Path(os.fsdecode(original.stdout).strip())
        original_path=(source/original_path).resolve() if not original_path.is_absolute() else original_path.resolve()
        matching=[entry for entry in entries if entry[0]==b'worktree '+os.fsencode(work)]
        require(original.returncode==0 and common_path==original_path and len(matching)==1
                and b'branch refs/heads/npeer/'+work.name.encode() in matching[0]
                and not any(item.startswith((b'locked',b'prunable')) for item in matching[0]),
                'OWNERSHIP_UNKNOWN','Directory does not match its owned Git worktree registration')
        # Never recommend --force for uncommitted inherited inputs.
        dirty=_git(work,['diff','--no-ext-diff','--no-textconv','--quiet',baseline['head'],baseline['input_tree'],'--'],deadline)
        require(dirty.returncode==0,'UNIQUE_CHANGES','Git worktree has inherited dirty inputs; reconcile before normal removal')
    return common_path


def _inventory(work,deadline,max_entries,max_bytes,*,allow_internal_links=False):
    size=0;count=0;files={};directories=set();pending=[work]
    while pending:
        directory=pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                require(time.monotonic()<deadline,'CLEANUP_UNAVAILABLE','Workspace inspection time limit reached')
                count+=1;info=entry.stat(follow_symlinks=False)
                size+=info.st_size if not stat.S_ISDIR(info.st_mode) else 0
                require(count<=max_entries and size<=max_bytes,'CLEANUP_UNAVAILABLE','Workspace size or entry limit reached')
                path=Path(entry.path);relative=path.relative_to(work)
                if stat.S_ISLNK(info.st_mode) and allow_internal_links:
                    target=os.readlink(path)
                    require(not Path(target).is_absolute() and (path.parent/target).resolve().is_relative_to(work),
                            'OWNERSHIP_UNKNOWN','Workspace symlink escapes its owned directory')
                    files[str(relative)]={'sha256':hashlib.sha256(os.fsencode(target)).hexdigest(),
                                          'mode':stat.S_IMODE(info.st_mode),'link_target':target}
                    continue
                require(not stat.S_ISLNK(info.st_mode),'UNIQUE_CHANGES','Workspace contains a symlink; retain for inspection')
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                    if relative.parts[0]!='.git':directories.add(str(relative))
                else:
                    require(stat.S_ISREG(info.st_mode),'UNIQUE_CHANGES','Workspace contains a socket or other special file')
                    if relative.parts[0]=='.git':continue
                    raw=_selected_file_bytes(path,max_bytes)
                    files[str(relative)]={'sha256':hashlib.sha256(raw).hexdigest(),'mode':stat.S_IMODE(info.st_mode)}
    return {'size_bytes':size,'entry_count':count,'size_complete':True},files,directories


def _content(source,work,baseline,mode,deadline,files,directories):
    listing=_git(work,['ls-tree','-r','-z',baseline['input_tree']],deadline)
    require(listing.returncode==0,'CLEANUP_UNAVAILABLE','Frozen input tree is unavailable')
    names=[]
    for entry in listing.stdout.split(b'\0'):
        if not entry:continue
        info,name=entry.split(b'\t',1)
        require(info.split()[0] in (b'100644',b'100755'),'UNIQUE_CHANGES','Input contains symlinks or submodules')
        names.append(os.fsdecode(name))
    paths=_safe_paths(set(names)|set(files))
    expected_directories={str(parent) for name in names for parent in Path(name).parents if str(parent)!='.'}
    require(set(names)==set(files) and directories==expected_directories,'UNIQUE_CHANGES',
            'Workspace has extra or missing content: '+', '.join(sorted((set(names)^set(files))|(directories^expected_directories))[:20]))
    comparisons=_inputs(work,work,baseline['input_tree'],paths,mode,deadline)
    require(all(f['matches_input'] for f in comparisons),'UNIQUE_CHANGES',
            'Current workspace differs from its frozen input tree: '+', '.join(f['path'] for f in comparisons if not f['matches_input'])[:1000])
    if mode=='isolated-copy':
        require(all(files[name]['mode']==meta['mode'] for name,meta in baseline['copy_inputs'].items()),
                'UNIQUE_CHANGES','Selected copy permission bits differ from its input manifest')
        inherited=paths
    else:
        diff=_git(work,['diff','--no-ext-diff','--no-textconv','--no-renames','--name-only','-z',baseline['head'],baseline['input_tree']],deadline)
        require(diff.returncode==0,'CLEANUP_UNAVAILABLE','Inherited input comparison failed')
        inherited=_safe_paths({os.fsdecode(p) for p in diff.stdout.split(b'\0') if p})
    if inherited:
        source_inputs=_inputs(source,work,baseline['input_tree'],inherited,mode,deadline)
        require(all(f['matches_input'] for f in source_inputs),'UNIQUE_CHANGES','Original source no longer retains the captured inputs')
    return {'matches_input_tree':True,'file_count':len(paths),'captured_source_inputs_match':True}


def plan(journal,caller,worker_ids,*,timeout=5,max_entries=10000,max_bytes=ARTIFACT_MAX):
    """Plan exact initial released workers only; return procedures, never run them."""
    require(isinstance(worker_ids,list) and 0<len(worker_ids)<=100
            and all(isinstance(w,str) and w and Path(w).name==w and w not in ('.','..') for w in worker_ids)
            and len(set(worker_ids))==len(worker_ids),
            'PROTOCOL_ERROR','Supply 1 to 100 unique exact worker IDs')
    require(isinstance(timeout,(int,float)) and 0<timeout<=30 and type(max_entries) is int and 0<max_entries<=100000
            and type(max_bytes) is int and 0<max_bytes<=ARTIFACT_MAX,'PROTOCOL_ERROR','Invalid cleanup inspection bounds')
    # Authorize the complete explicit selection before inspecting any retained content.
    owned=[_owned(journal,caller,worker) for worker in worker_ids]
    deadline=time.monotonic()+timeout;candidates=[]
    for row,lease in owned:
        value={'worker_id':row['worker_id'],'title':row['role_title'],'provider':row['provider'],'generation':row['generation'],'safe_candidate':False,
               'status':'refused','size_bytes':None,'size_complete':False,'procedure':None}
        candidates.append(value)
        try:
            manifest=_released(journal,row)
            source,work,baseline,mode=_workspace(journal,row,lease)
            value.update(source=str(source),workspace=str(work),workspace_mode=mode)
            sizes,files,dirs=_inventory(work,deadline,max_entries,max_bytes);value.update(sizes)
            value['retained_results']=journal.all('SELECT task_id,result_artifact_id,generation,state FROM tasks WHERE worker_id=? AND result_artifact_id IS NOT NULL ORDER BY rowid LIMIT 101',(row['worker_id'],))
            require(len(value['retained_results'])<=100,'CLEANUP_UNAVAILABLE','Too many retained task results for one bounded plan')
            if manifest.get('controller_created') is False:
                value.update(never_launched=True,retained_result=None)
            else:
                task,record=_result(journal,caller,row)
                retained=record.get('changes',{})
                value['retained_result']={'task_id':task['task_id'],'result_artifact_id':task['result_artifact_id'],
                    'worker_delta_artifact':retained.get('worker_delta_artifact'),
                    'patch_artifact':retained.get('patch_artifact'),'worker_delta_base':retained.get('worker_delta_base')}
                require(retained.get('baseline')==baseline and retained.get('worktree')==str(work)
                    and retained.get('worker_delta_base')==baseline['input_tree'],
                    'OWNERSHIP_UNKNOWN','Retained result differs from the owned workspace')
                require(retained.get('worker_delta_empty') is True and not retained.get('worker_delta_artifact')
                    and not retained.get('worker_delta_unavailable_reason')
                    and not retained.get('excluded_sensitive_or_symlink_files')
                    and not retained.get('patch_unavailable_reason'),
                    'UNIQUE_CHANGES','Retained result has unique, excluded or unavailable changes')
            common=_storage(source,work,baseline,mode,deadline)
            value['current_changes']=_content(source,work,baseline,mode,deadline,files,dirs)
            # Recheck the retained directory and ownership after all potentially slow reads.
            sizes_after,files_after,dirs_after=_inventory(work,deadline,max_entries,max_bytes)
            require((sizes,files,dirs)==(sizes_after,files_after,dirs_after),'CLEANUP_UNAVAILABLE','Workspace content changed during inspection')
            require(_storage(source,work,baseline,mode,deadline)==common,'OWNERSHIP_UNKNOWN','Git storage changed during inspection')
            require(_owned(journal,caller,row['worker_id'])[0]==row and _released(journal,row)==manifest,
                    'OWNERSHIP_CONFLICT','Worker ownership or generation changed during inspection')
            if mode=='isolated-worktree':
                procedure={'kind':'git-worktree-remove','argv':['git','-C',str(source),'worktree','remove',str(work)],
                    'after_remove_argv':[['git','-C',str(source),'worktree','prune'],
                                         ['git','-C',str(source),'branch','-d','npeer/'+row['worker_id']]]}
            else:
                procedure={'kind':'selected-copy-manual-removal','path':str(work),
                    'instruction':'Recheck this plan, then manually remove only this exact selected copy directory.'}
            value.update(status='candidate',safe_candidate=True,procedure=procedure)
        except (PeerError,OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as error:
            value.update(unique_changes=isinstance(error,PeerError) and error.code=='UNIQUE_CHANGES',reason=error.message if isinstance(error,PeerError) else type(error).__name__,
                         reason_code=error.code if isinstance(error,PeerError) else 'CLEANUP_UNAVAILABLE')
    return {'planner_only':True,'removed':False,'candidates':candidates,
            'recheck_required':True,'instruction':'Rerun this read-only plan immediately before any separately authorized removal. Native histories and retained artifacts stay in place.'}
