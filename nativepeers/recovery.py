"""Exact owned-history release/resume. No peer is adopted or resumed for delivery."""
import json
from pathlib import Path
import shlex
import time

from .control import group_call
from .delegation import Delegation,TERMINAL
from .errors import require
from .files import async_advisory_lock,canonical
from .models import bounded_text,new_id
from .launch import recorded_home,home_environment
from .process import matches
from .registry import Registry


def owned_worker(journal,caller,worker_id):
    row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
    require(row,'PROTOCOL_ERROR','Unknown owned worker')
    group=journal.one('SELECT * FROM groups WHERE group_id=?',(row['group_id'],))
    require(caller.kind=='peer' and caller.key==group['parent_key'],'APPROVAL_BLOCKED','Only the owning parent can reclaim this worker')
    return row,group


def settle_stopped_tasks(db,worker_id,generation,reason):
    """Call only after the owned writer has stopped; never infer a submitted result."""
    db.execute("UPDATE tasks SET state='CANCELED',terminal_reason=? WHERE worker_id=? AND generation=? AND state='QUEUED' AND native_turn_id IS NULL",
               (reason,worker_id,generation))
    db.execute("UPDATE tasks SET state='INTERRUPTED',terminal_reason='DELIVERY_UNKNOWN_AFTER_OWNER_LOSS' WHERE worker_id=? AND generation=? AND state NOT IN ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED')",
               (worker_id,generation))


def assert_released(journal,row):
    require(row['state']=='RELEASED_RESUMABLE','OWNERSHIP_CONFLICT','Worker has not released its native writer')
    path=journal.root/'workers'/(row['worker_id']+'.json')
    require(path.exists(),'OWNERSHIP_UNKNOWN','Owned process manifest is missing')
    manifest=json.loads(path.read_text())
    require(manifest['worker_id']==row['worker_id'] and manifest['native_id']==row['native_id'],'OWNERSHIP_UNKNOWN','Owned process manifest differs from the native history')
    require(not matches(manifest['owner']) and (not manifest.get('native_process') or not matches(manifest['native_process'])),
            'OWNERSHIP_CONFLICT','Previous controller or provider writer is still alive')
    home=recorded_home(journal,row)
    for peer in Registry(journal).list():
        require(peer['peer_key']['native_session_id']!=row['native_id'] or peer['peer_key']['provider']!=row['provider'] or peer['home']!=str(home),
                'OWNERSHIP_CONFLICT','A registered native runtime currently owns this history')
    if row['provider']=='claude':
        from .adapters.metadata import claude_sessions
        require(not any(s.get('sessionId')==row['native_id'] for s in claude_sessions(home)),
                'OWNERSHIP_CONFLICT','Claude native discovery reports an active writer for this history')


def resume_command(journal,caller,worker_id):
    row,_=owned_worker(journal,caller,worker_id);assert_released(journal,row)
    command=['codex','resume',row['native_id']] if row['provider']=='codex' else ['agy','--conversation',row['native_id']] if row['provider']=='antigravity' else ['claude','--resume',row['native_id']]
    model=row['model_observed'] or row['model_requested']
    if model:command+=['--model',model]
    if row['provider']=='codex' and row['effort_requested']:
        command+=['--config','model_reasoning_effort='+json.dumps(row['effort_requested'])]
    elif row['effort_requested']:command+=['--effort',row['effort_requested']]
    environment=home_environment(row['provider'],recorded_home(journal,row))
    prefix=''.join(key+'='+shlex.quote(value)+' ' for key,value in environment.items())
    return {'worker_id':worker_id,'native_id':row['native_id'],'cwd':row['cwd'],'native_argv':command,'native_env':environment,
            'command':'cd '+shlex.quote(row['cwd'])+' && '+prefix+shlex.join(command),'launched':False,
            'ownership_scope':'Verified released NativePeers writers and registered eligible local native runtimes; no private-backend takeover.'}


async def resume_worker(journal,caller,worker_id,text,completion_mode=None):
    require(completion_mode in (None,'push','wait'),'PROTOCOL_ERROR','Invalid resume completion mode')
    text=bounded_text(text);row,group=owned_worker(journal,caller,worker_id)
    async with async_advisory_lock(journal.root/'locks'/(worker_id+'.resume.lock')):
        row,group=owned_worker(journal,caller,worker_id);assert_released(journal,row)
        registry=Registry(journal);parent=registry.verify(caller.key,caller.incarnation)
        from .policy import load
        local=load(journal)
        lease=json.loads(row['lease_json']);lease['provider_home']=str(recorded_home(journal,row))
        require(lease['grants'].get('depth',1)<=local['max_depth'],'CAPACITY_UNAVAILABLE','Current local depth ceiling is lower than this worker grant')
        lease['spec']['max_task_turns']=min(lease['spec']['max_task_turns'],local['max_task_turns'])
        lease['spec']['wall_time_seconds']=min(lease['spec']['wall_time_seconds'],local['task_wall_seconds'])
        profile=lease['spec']['permission_profile']
        if row['model_observed']:lease['spec']['model']=row['model_observed']
        require(profile in parent.get('policy',{}).get('profiles',['analysis']),'PERMISSION_CAPABILITY_UNKNOWN','Current parent does not retain the original permission ceiling')
        policy=json.loads(group['policy_json']);manifest_path=journal.root/'groups'/group['group_id']/'owner.json'
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        supervisor_alive=bool(manifest and matches(manifest['owner']))
        controller_generation=manifest['generation'] if supervisor_alive else policy.get('controller_generation',1)+1
        phases=policy.get('phases',{'1':policy['initial_tasks']});phase=max(int(n) for n in phases)+1;task_id=new_id()
        phases[str(phase)]=[task_id];policy.update(phases=phases,controller_generation=controller_generation,parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'],created_at=time.time())
        lease.update(released_native=True,expires_at=time.time()+lease['spec']['wall_time_seconds']+600)
        lease['grants']['message_targets']=[caller.key]
        with journal.transaction() as db:
            active=db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')").fetchone()[0]
            require(active<local["workers_total"],'CAPACITY_UNAVAILABLE','No safe native worker slot is available')
            db.execute("UPDATE workers SET state='RESERVED',generation=generation+1,lease_json=? WHERE worker_id=? AND state='RELEASED_RESUMABLE'",(canonical(lease).decode(),worker_id))
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,None,None,'QUEUED',None,None,row['generation']+1,canonical({'text':text,'initial':False}).decode()))
            db.execute('UPDATE groups SET state=?,policy_json=? WHERE group_id=?',('RESERVED',canonical(policy).decode(),group['group_id']))
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group['group_id'],phase,caller.key,completion_mode or policy['completion_mode'],'UNCLAIMED',None,None,new_id(),controller_generation))
        if supervisor_alive:
            await group_call(journal,group['group_id'],'resume',{'caller_key':caller.key,'worker_id':worker_id,'task_id':task_id,'text':text},timeout=35)
        else:
            if manifest_path.exists():manifest_path.rename(manifest_path.with_name('owner.generation-'+str(manifest['generation'])+'.json'))
            await Delegation(journal,registry).ensure_supervisor(group['group_id'])
        return {'worker_id':worker_id,'task_id':task_id,'group_id':group['group_id'],'native_id':row['native_id'],'completion_generation':phase,'state':'QUEUED'}


def reconcile_owned(journal,caller,group_id,*,dry_run=False):
    """Release only demonstrably dead owned writers; never replay their uncertain tasks."""
    group=Delegation(journal,Registry(journal)).group(caller,group_id)
    require(caller.key==group['parent_key'],'APPROVAL_BLOCKED','Only the parent may reconcile owned lifecycle state')
    owner_path=journal.root/'groups'/group_id/'owner.json'
    if owner_path.exists() and matches(json.loads(owner_path.read_text())['owner']):return []
    changes=[]
    for row in journal.all('SELECT * FROM workers WHERE group_id=?',(group_id,)):
        unfinished=journal.all('SELECT state FROM tasks WHERE worker_id=? AND generation=?',(row['worker_id'],row['generation']))
        if row['state']=='RELEASED_RESUMABLE' and all(t['state'] in TERMINAL for t in unfinished):continue
        path=journal.root/'workers'/(row['worker_id']+'.json')
        if not path.exists():continue
        manifest=json.loads(path.read_text())
        if manifest.get('worker_id')!=row['worker_id'] or manifest.get('generation')!=row['generation'] or manifest.get('native_id')!=row['native_id']:continue
        if matches(manifest['owner']) or (manifest.get('native_process') and matches(manifest['native_process'])):continue
        changes.append({'worker_id':row['worker_id'],'state':'RELEASED_RESUMABLE','unstarted_tasks':'CANCELED: OWNER_LOST_BEFORE_START','uncertain_tasks':'INTERRUPTED; no native input replayed'})
        if not dry_run:
            with journal.transaction() as db:
                settle_stopped_tasks(db,row['worker_id'],row['generation'],'OWNER_LOST_BEFORE_START')
                db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=? AND generation=?",(row['worker_id'],row['generation']))
                db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(row['peer_key'],))
            journal.event(row['worker_id'],'dead-owner-reconciled',changes[-1])
    return changes
