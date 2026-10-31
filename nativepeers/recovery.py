"""Exact owned-history release/resume. No peer is adopted or resumed for delivery."""
import json
import asyncio
from pathlib import Path
import shlex
import time

from .control import group_call
from .delegation import Delegation,TERMINAL
from .errors import SupervisorNotStarted,require
from .files import async_advisory_lock,canonical,sha256
from .models import bounded_text,new_id
from .launch import recorded_home,home_environment
from .process import matches,owned_status
from .ownership import release_status
from .registry import Registry
from .worker_process import model_selection


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
    from .workspace_retirement import retirement
    require(not retirement(journal,row['worker_id']), 'WORKSPACE_RETIRED', 'Workspace retirement was prepared; recover its verified archive instead of resuming this writer')
    require(row['state']=='RELEASED_RESUMABLE','OWNERSHIP_CONFLICT','Worker has not released its native writer',
            details={'worker_id':row['worker_id'],'group_id':row['group_id'],'state':row['state'],
                     'next_action':'Send a serialized live follow-up to the exact peer; do not start a second native writer.',
                     'command':'npeer send --to '+row['peer_key']+' --message TEXT --request-reply --client-request-id NEW_ID --json' if row.get('peer_key') else 'npeer status '+row['group_id']+' --json'})
    closure=release_status(journal,row)
    require(closure['released'],'CLEANUP_UNCERTAIN','Previous process/output closure is unproved; inspect the retained release details',details=closure)
    home=recorded_home(journal,row)
    for peer in Registry(journal).list():
        require(peer['peer_key']['native_session_id']!=row['native_id'] or peer['peer_key']['provider']!=row['provider'] or peer['home']!=str(home),
                'OWNERSHIP_CONFLICT','A registered native runtime currently owns this history')
    if row['provider']=='claude':
        from .adapters.metadata import claude_sessions
        require(not any(s.get('sessionId')==row['native_id'] for s in claude_sessions(home,explicit=json.loads(row.get('lease_json') or '{}').get('provider_home_explicit'))),
                'OWNERSHIP_CONFLICT','Claude native discovery reports an active writer for this history')


def resume_command(journal,caller,worker_id,*,explain=False):
    row,_=owned_worker(journal,caller,worker_id)
    from .worker_visibility import status as visibility_status
    visibility=visibility_status(row)
    if explain:
        from .workspace_retirement import retirement,summary
        retired=retirement(journal,worker_id)
        closure=release_status(journal,row)
        return dict(worker_id=worker_id,native_id=row['native_id'],state=row['state'],closure=closure,history_visibility=visibility,
                    workspace_retirement=summary(retired) if retired else None,
                    resume_eligible=not retired and row['state']=='RELEASED_RESUMABLE' and closure['released'],launched=False,
                    next_step='Recover the verified workspace archive; normal resume is refused after retirement' if retired else 'Use normal exact-history resume only after closure and native ownership checks pass' if closure['released'] else 'Retain ownership; inspect the listed live/unknown process births and pipe closure')
    assert_released(journal,row)
    archive_state={'archived':None,'checked':False}
    if row['provider']=='codex':
        from .history_cleanup import resume_archive_state
        archive_state=resume_archive_state(journal,row)
    command=['codex','resume',row['native_id']] if row['provider']=='codex' else ['agy','--conversation',row['native_id']] if row['provider']=='antigravity' else ['claude','--resume',row['native_id']]
    if row['provider']=='grok':
        from .grok_worker import arguments
        lease=json.loads(row['lease_json']);args=arguments(dict(lease['spec'],model=model_selection(row)),row['native_id'])
        for flag in ('--prompt-file','--output-format'):
            index=args.index(flag);del args[index:index+2]
        command=[str(Path(lease['executable']).resolve(strict=True)),*args]
    model=model_selection(row)
    if model and row['provider']!='grok':command+=['--model',model]
    if row['provider']=='codex' and row['effort_requested']:
        command+=['--config','model_reasoning_effort='+json.dumps(row['effort_requested'])]
    elif row['effort_requested']:command+=['--effort',row['effort_requested']]
    environment=home_environment(row['provider'],recorded_home(journal,row),explicit=json.loads(row.get('lease_json') or '{}').get('provider_home_explicit'))
    if row['provider']=='grok':
        from .launch import launch_environment
        environment.update({k:v for k,v in launch_environment('grok').items() if k.startswith('GROK_') and k!='GROK_HOME'})
    prefix=('env -u CLAUDE_CONFIG_DIR ' if row['provider']=='claude' and not environment else '')+''.join(key+'='+shlex.quote(value)+' ' for key,value in environment.items())
    unarchive=['codex','unarchive',row['native_id']] if archive_state['archived'] is True else None
    launch=(prefix+shlex.join(unarchive)+' && ' if unarchive else '')+prefix+shlex.join(command)
    return {'worker_id':worker_id,'native_id':row['native_id'],'cwd':row['cwd'],'native_argv':command,'native_env':environment,'history_visibility':visibility,
            'command':'cd '+shlex.quote(row['cwd'])+' && '+launch,'launched':False,
            'archive_state':archive_state,'unarchive_argv':unarchive,
            'resume_visibility_note':'Native resume retains persisted classification; legacy unclassified histories can reappear in ordinary recents after unarchive.' if row['provider']=='codex' else None,
            'ownership_scope':'Verified released NativePeers writers and registered eligible local native runtimes; no private-backend takeover.'}


def _resume_request(journal,caller,request_id,digest):
    prior=journal.one('SELECT r.*,t.state FROM resume_requests r JOIN tasks t USING(task_id) WHERE caller_key=? AND client_request_id=?',
                      (caller.key,request_id))
    if prior:
        require(prior['payload_sha256']==digest,'IDEMPOTENCY_CONFLICT','Resume request ID has different content')
        return dict(json.loads(prior['response_json']),state=prior['state'])


async def resume_worker(journal,caller,worker_id,text,completion_mode=None,client_request_id=None,*,origin=None,delivery=None):
    require(completion_mode in (None,'push','wait'),'PROTOCOL_ERROR','Invalid resume completion mode')
    require(client_request_id is None or isinstance(client_request_id,str) and bool(client_request_id),
            'PROTOCOL_ERROR','Resume request ID must be a nonempty string')
    request_id=bounded_text(client_request_id) if client_request_id is not None else new_id()
    text=bounded_text(text);row,group=owned_worker(journal,caller,worker_id)
    digest=sha256({'worker_id':worker_id,'text':text,'completion_mode':completion_mode})
    async with async_advisory_lock(journal.root/'locks'/(group['group_id']+'.resume.lock')):
        prior=_resume_request(journal,caller,request_id,digest)
        if prior:return prior
        row,group=owned_worker(journal,caller,worker_id);assert_released(journal,row)
        registry=Registry(journal);parent=registry.verify(caller.key,caller.incarnation)
        from .policy import load
        local=load(journal)
        lease=json.loads(row['lease_json']);lease['provider_home']=str(recorded_home(journal,row))
        require(lease['grants'].get('depth',1)<=local['max_depth'],'CAPACITY_UNAVAILABLE','Current local depth ceiling is lower than this worker grant')
        lease['spec'].setdefault('native_max_turns',local['native_max_turns_default'])
        for field,key in (('max_task_turns','max_task_turns'),('wall_time_seconds','task_wall_seconds'),
                          ('wall_time_seconds','group_wall_seconds'),('native_max_turns','native_max_turns')):
            if local[key] is not None:
                lease['spec'][field]=min(lease['spec'].get(field,local.get(key+'_default',local[key])),local[key])
        profile=lease['spec']['permission_profile']
        lease['spec']['model']=model_selection(row)
        require(profile in parent.get('policy',{}).get('profiles',['analysis']),'PERMISSION_CAPABILITY_UNKNOWN','Current parent does not retain the original permission ceiling')
        policy=json.loads(group['policy_json']);manifest_path=journal.root/'groups'/group['group_id']/'owner.json'
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        if manifest and (group['state']=='RELEASING' or manifest.get('released')):
            # Idle shutdown claimed this lifetime before resume acquired the lock.
            # No new task is reserved until that exact finite owner has exited.
            deadline=time.monotonic()+30
            while owned_status(manifest['owner'])!='DEAD' and time.monotonic()<deadline:await asyncio.sleep(.03)
            require(owned_status(manifest['owner'])=='DEAD','OWNERSHIP_CONFLICT','Previous supervisor is still releasing; no resume input admitted')
        supervisor_alive=bool(manifest and owned_status(manifest['owner'])!='DEAD')
        controller_generation=manifest['generation'] if supervisor_alive else policy.get('controller_generation',1)+1
        task_id=new_id()
        lease.update(released_native=True,expires_at=time.time()+lease['spec']['wall_time_seconds']+600)
        lease['grants']['message_targets']=[caller.key]
        with journal.transaction() as db:
            # Different groups have different resume locks but share the caller's ID space.
            raced=_resume_request(journal,caller,request_id,digest)
            if raced:return raced
            # Enqueue uses a separate process/connection and does not take the
            # resume lock. Allocate and merge against the current durable policy.
            policy=json.loads(db.execute('SELECT policy_json FROM groups WHERE group_id=?',(group['group_id'],)).fetchone()[0])
            phases=policy.get('phases',{'1':policy['initial_tasks']});phase=max(int(n) for n in phases)+1
            phases[str(phase)]=[task_id]
            policy.update(phases=phases,controller_generation=controller_generation,parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'],created_at=time.time(),
                          wall_time_seconds=lease['spec']['wall_time_seconds'],group_wall_seconds=local['group_wall_seconds'])
            response={'worker_id':worker_id,'task_id':task_id,'group_id':group['group_id'],'native_id':row['native_id'],
                      'completion_generation':phase,'state':'QUEUED','client_request_id':request_id}
            active=db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')").fetchone()[0]
            require(active<local["workers_total"],'CAPACITY_UNAVAILABLE','No safe native worker slot is available')
            from .usage.service import Usage
            Usage(journal).admit_local_demand(lease,worker_id=worker_id)
            db.execute("UPDATE workers SET state='RESERVED',generation=generation+1,lease_json=? WHERE worker_id=? AND state='RELEASED_RESUMABLE'",(canonical(lease).decode(),worker_id))
            from .mirror import owner_for,record,route_state
            owner=owner_for(journal,worker_id,(delivery or {}).get('claude_session'),(delivery or {}).get('owner'))
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,None,None,'QUEUED',None,None,row['generation']+1,
                canonical(dict(text=text,initial=False,**({'origin':origin,'client_request_id':request_id} if origin else {}))).decode()))
            db.execute('UPDATE groups SET state=?,policy_json=? WHERE group_id=?',('RESERVED',canonical(policy).decode(),group['group_id']))
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group['group_id'],phase,caller.key,completion_mode or policy['completion_mode'],route_state(owner),None,None,new_id(),controller_generation))
            record(db,task_id,group['group_id'],phase,owner,origin=origin,session=(delivery or {}).get('claude_session'),agent=(delivery or {}).get('claude_agent_id'))
            if delivery:response['delivery_owner']=owner
            db.execute('INSERT INTO resume_requests VALUES(?,?,?,?,?)',(caller.key,request_id,digest,task_id,canonical(response).decode()))
        if supervisor_alive:
            await group_call(journal,group['group_id'],'resume',{'caller_key':caller.key,'worker_id':worker_id,'task_id':task_id,'text':text},timeout=35)
        else:
            if manifest_path.exists():manifest_path.rename(manifest_path.with_name('owner.generation-'+str(manifest['generation'])+'.json'))
            try:
                await Delegation(journal,registry).ensure_supervisor(group['group_id'])
            except SupervisorNotStarted as error:
                # This positive process-creation failure is stronger than a
                # missing manifest. The resume admission lock is still held.
                with journal.transaction() as db:
                    changed=db.execute("UPDATE tasks SET state='FAILED',terminal_reason='SUPERVISOR_LAUNCH_FAILED_BEFORE_START' WHERE task_id=? AND generation=? AND state='QUEUED' AND native_turn_id IS NULL",(task_id,row['generation']+1)).rowcount
                    if changed:
                        db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=? AND generation=? AND state='RESERVED'",(worker_id,row['generation']+1))
                        db.execute("UPDATE completion_routes SET state='RETAINED' WHERE group_id=? AND completion_generation=? AND state IN ('UNCLAIMED','MIRROR_PENDING')",(group['group_id'],phase))
                        db.execute("UPDATE groups SET state='FAILED' WHERE group_id=?",(group['group_id'],))
                journal.event(worker_id,'controller-not-created',dict(worker_id=worker_id,generation=row['generation']+1,native_id=row['native_id'],cwd=row['cwd'],controller_created=False,supervisor_created=False))
                journal.event(task_id,'resume-not-launched',error.json())
                error.details.update(task_id=task_id,worker_id=worker_id,client_request_id=request_id)
                raise
        return response


def reconcile_owned(journal,caller,group_id,*,dry_run=False):
    """Release only demonstrably dead owned writers; never replay their uncertain tasks."""
    group=Delegation(journal,Registry(journal)).group(caller,group_id)
    require(caller.key==group['parent_key'],'APPROVAL_BLOCKED','Only the parent may reconcile owned lifecycle state')
    owner_path=journal.root/'groups'/group_id/'owner.json'
    if owner_path.exists() and owned_status(json.loads(owner_path.read_text())['owner'])!='DEAD':return []
    changes=[]
    for row in journal.all('SELECT * FROM workers WHERE group_id=?',(group_id,)):
        unfinished=journal.all('SELECT state FROM tasks WHERE worker_id=? AND generation=?',(row['worker_id'],row['generation']))
        if row['state']=='RELEASED_RESUMABLE' and all(t['state'] in TERMINAL for t in unfinished):continue
        if not release_status(journal,row)['released']:continue
        changes.append({'worker_id':row['worker_id'],'state':'RELEASED_RESUMABLE','unstarted_tasks':'CANCELED: OWNER_LOST_BEFORE_START','uncertain_tasks':'INTERRUPTED; no native input replayed'})
        if not dry_run:
            with journal.transaction() as db:
                settle_stopped_tasks(db,row['worker_id'],row['generation'],'OWNER_LOST_BEFORE_START')
                db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=? AND generation=?",(row['worker_id'],row['generation']))
                db.execute("UPDATE peer_instances SET status='offline' WHERE peer_key=?",(row['peer_key'],))
            journal.event(row['worker_id'],'dead-owner-reconciled',changes[-1])
    return changes
