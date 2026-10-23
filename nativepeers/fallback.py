"""Explicit clean-attempt handoff within an existing finite delegation group."""
import json
import hashlib
import os
from pathlib import Path
import stat

from .errors import require
from .files import canonical,sha256
from .models import ARTIFACT_MAX


def workspace_image(cwd):
    """Bounded materialized bytes, including ignored files; no symlinks/devices."""
    root=Path(cwd);files={};size=0
    require(not root.is_symlink() and root.is_dir(),'FALLBACK_UNSAFE','Owned input directory is missing or replaced')
    for directory,names,leaves in os.walk(root,followlinks=False):
        require(not Path(directory).is_symlink(),'FALLBACK_UNSAFE','Workspace directory changed during inspection')
        files[str(Path(directory).relative_to(root))+'/']=(None,stat.S_IMODE(Path(directory).stat().st_mode))
        require(len(files)<=10000,'FALLBACK_UNSAFE','Workspace exceeds the bounded clean-fallback check')
        if Path(directory)==root:
            names[:]=[name for name in names if name!='.git']
            leaves=[name for name in leaves if name!='.git']
        for name in names:
            require(not (Path(directory)/name).is_symlink(),'FALLBACK_UNSAFE','Workspace contains a symlink; clean fallback cannot be established')
        for name in leaves:
            path=Path(directory)/name;info=path.lstat()
            require(stat.S_ISREG(info.st_mode),'FALLBACK_UNSAFE','Workspace contains a nonregular file')
            require(info.st_size<=ARTIFACT_MAX-size and len(files)<10000,'FALLBACK_UNSAFE','Workspace exceeds the bounded clean-fallback check')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                opened=os.fstat(stream.fileno())
                require(stat.S_ISREG(opened.st_mode) and (opened.st_dev,opened.st_ino)==(info.st_dev,info.st_ino),'FALLBACK_UNSAFE','Workspace file was replaced during inspection')
                data=stream.read(ARTIFACT_MAX-size+1);after=os.fstat(stream.fileno())
            size+=len(data)
            require(size<=ARTIFACT_MAX and len(data)==after.st_size and
                    (info.st_size,info.st_mtime_ns,info.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns),
                    'FALLBACK_UNSAFE','Workspace changed during fallback inspection')
            files[str(path.relative_to(root))]=(data,stat.S_IMODE(info.st_mode))
    return files


def image_digest(files):
    return sha256({name:{'sha256':hashlib.sha256(data).hexdigest() if data is not None else None,'mode':mode} for name,(data,mode) in files.items()})


def materialize(directory,files):
    for relative,(data,mode) in files.items():
        path=directory/relative
        if data is None:path.mkdir(parents=True,exist_ok=True)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        path.chmod(mode)


def attempts(journal,task_id):
    return journal.all('SELECT a.*,w.provider,w.native_id,w.model_requested,w.model_observed,w.cwd FROM task_attempts a JOIN workers w USING(worker_id) WHERE task_id=? ORDER BY attempt_index',(task_id,))


def scope_limit(journal,row,home,task_id=None):
    import time
    from .usage.service import Usage,same_model,supersedes_refusal
    from .usage import store
    spec=json.loads(row['lease_json']).get('spec',{})
    if spec.get('quota_policy')=='warn':return None
    if task_id:
        task=journal.one('SELECT task_json FROM tasks WHERE task_id=?',(task_id,))
        if not task or not json.loads(task['task_json']).get('initial'):return None
    value=journal.one('SELECT error_json FROM group_provider_limits WHERE group_id=? AND provider=? AND provider_home=?',
                      (row['group_id'],row['provider'],str(home)))
    if not value:return None
    intelligence=Usage(journal);now=time.time();model=spec.get('model') or row['model_requested']
    view=intelligence.snapshot(row['provider'],home,model=model)
    ttl=intelligence.settings['provider_freshness'].get(row['provider'],intelligence.settings['freshness_seconds'])
    for proof in json.loads(value['error_json']).get('limits',[]):
        if proof['scope']!=store.scope(row['provider'],home) or not 0<=now-proof['at']<=ttl:continue
        if proof.get('reset') is not None and now>=proof['reset']:continue
        if not any(m=='*' or same_model(row['provider'],model,m) for m in proof['models']):continue
        if any(supersedes_refusal(row['provider'],p,proof.get('pool_id'),proof['models'],proof['at'],states=('AVAILABLE','LIMITED')) for p in view['pools']):continue
        error=proof['error']
        return dict(error,may_have_received=False,suppressed=True,
                    message=error.get('message','Native quota is exhausted.')+' A matching limit was already established in this batch.',
                    execution={'input_submitted':False,'definitive_no_execution':True})


def remember_limit(journal,row,home,error):
    import time
    from .usage import store
    from .usage.normalize import timestamp
    if not error or error.get('suppressed') or error.get('code')!='QUOTA_EXHAUSTED' or not error.get('execution',{}).get('definitive_no_execution'):return
    assessment=(error.get('details') or {}).get('usage',{})
    pools=[p for p in assessment.get('pools',[]) if p['state']=='EXHAUSTED']
    if not pools:pools=[dict(pool_id=error.get('limiting_scope') or error.get('pool_id'),
        models=['*'] if error.get('scope')=='provider-wide' else [row['model_requested']],
        resets_at=timestamp(error.get('resets_at') or error.get('reset_at')))]
    proofs=[dict(pool_id=p.get('pool_id'),models=p.get('models') or [row['model_requested']],reset=p.get('resets_at'),
        scope=store.scope(row['provider'],home),at=p.get('observed_at',assessment.get('observed_at',time.time())),error=error) for p in pools]
    key=(row['group_id'],row['provider'],str(home))
    with journal.transaction() as db:
        previous=journal.one('SELECT error_json FROM group_provider_limits WHERE group_id=? AND provider=? AND provider_home=?',key)
        limits=json.loads(previous['error_json']).get('limits',[]) if previous else []
        identities={(p['pool_id'],tuple(p['models'])) for p in proofs}
        limits=[p for p in limits if (p['pool_id'],tuple(p['models'])) not in identities]+proofs
        db.execute('INSERT OR REPLACE INTO group_provider_limits VALUES(?,?,?,?)',(*key,canonical({'limits':limits[-64:]}).decode()))


def clone_workspace(journal,old,new_id_value,expected):
    """Use the failed attempt's verified frozen input, never recapture the parent."""
    from .worktrees import git,worktree_parent,changes
    lease=json.loads(old['lease_json']);baseline=lease['baseline'];source=Path(old['cwd'])
    files=workspace_image(source)
    require(image_digest(files)==expected,'FALLBACK_UNSAFE','Workspace changed after the failed attempt; no replay was admitted')
    delta=changes(source,baseline)
    require(not delta.get('worker_delta') and not delta.get('worker_delta_unavailable_reason') and not delta.get('excluded_sensitive_or_symlink_files'),
            'FALLBACK_UNSAFE','The failed workspace no longer matches its frozen input')
    parent=source.parent
    require(parent.name=='.worktrees' and source.name==old['worker_id'],'OWNERSHIP_UNKNOWN','Fallback workspace is outside the owned project location')
    destination=parent/new_id_value
    if baseline.get('workspace_mode')=='isolated-copy':
        # This Git directory was created solely for the owned isolated copy.
        # Copy its local object/index metadata, never a provider home or parent repo.
        require((source/'.git').is_dir(),'OWNERSHIP_UNKNOWN','Owned copy metadata is unavailable')
        metadata=workspace_image(source/'.git')
        destination.mkdir(mode=0o700)
        # Snapshot only bounded regular metadata bytes; do not follow a replaced
        # metadata symlink or copy a path that changes during traversal.
        materialize(destination/'.git',metadata)
    else:
        root=Path(baseline['root'])
        require(worktree_parent(journal,root)==parent,'OWNERSHIP_UNKNOWN','Owned worktree source differs from its baseline')
        git(root,'worktree','add','-b','npeer/'+new_id_value,str(destination),baseline['head'],timeout=15)
        patch=git(source,'diff','--no-ext-diff','--no-textconv','--binary',baseline['head'],baseline['input_tree'],timeout=10).stdout
        if patch:git(destination,'apply','--binary','--index',input=patch,timeout=10)
    materialize(destination,files)
    require(image_digest(workspace_image(destination))==expected,'FALLBACK_UNSAFE','Replacement input differs from the frozen failed attempt')
    return str(destination),new_id_value,baseline


def result_details(journal,task_id):
    history=attempts(journal,task_id)
    task=journal.one('SELECT task_json,state FROM tasks WHERE task_id=?',(task_id,))
    choices=json.loads(task['task_json']).get('fallback_choices',[]) if task else []
    if not history:return {'attempts':history}
    primary=journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(history[0]['worker_id'],))
    configured=json.loads(primary['lease_json']).get('fallback_candidates',[])
    if len(history)<2 and not choices and (not configured or history[0]['state']=='COMPLETED'):return {'attempts':history}
    first=history[0]
    artifact=journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?',(first['result_artifact_id'],))
    record=json.loads((journal.root/'artifacts'/artifact['relative_path']).read_text())
    considered={(c['provider'],c['model']) for c in choices}
    choices=[*choices,*[dict(provider=c['provider'],model=c['model'],state='NOT_TRIED',
        reason='An earlier choice completed the task.' if task['state']=='COMPLETED' else
               'No further clean admission within the original task lifetime/limits was established.' if task['state'] in ('FAILED','TIMED_OUT','INTERRUPTED','CANCELED') else 'Awaiting the clean handoff.')
        for c in configured if (c['provider'],c['model']) not in considered]]
    return {'attempts':history,'fallback':{'primary_failure':record.get('error'),
            'reason':(record.get('error') or {}).get('message') or 'Confirmed provider failure before task execution.',
            'primary':{'provider':first['provider'],'model':first['model_requested']},
            'choices':choices,
            'state':task['state'],
            'selected':{'provider':history[-1]['provider'],'model':history[-1]['model_observed'] or history[-1]['model_requested']}}}
