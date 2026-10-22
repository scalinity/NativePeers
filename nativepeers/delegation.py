"""Idempotent batch intent, bounded capacity, and one finite group supervisor."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import time
import tomllib

from .adapters.codex_app_server import AppServer
from .control import group_call
from .errors import PeerError,require
from .files import async_advisory_lock,atomic_json,canonical,private_directory,sha256
from .launch import launch_environment
from .models import bounded_text,new_id,title
from .process import matches
from .profiles import ROOT,PYTHON
from .worktrees import prepare,snapshot

TERMINAL={'COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED'}


def supervisor_environment(journal):
    # This is a fresh independent application context, not a copy of a provider tool shell.
    # The allowlist is fixed before launch; there is no guard-deletion retry loop.
    names={'HOME','USER','LOGNAME','PATH','SHELL','TMPDIR','LANG','CODEX_HOME','CLAUDE_CONFIG_DIR',
           'OPENAI_API_KEY','OPENAI_BASE_URL','ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL',
           'CLAUDE_CODE_USE_BEDROCK','CLAUDE_CODE_USE_VERTEX','CLAUDE_CODE_USE_FOUNDRY',
           'AWS_PROFILE','AWS_REGION','GOOGLE_CLOUD_PROJECT','SSL_CERT_FILE','HTTPS_PROXY','HTTP_PROXY','NO_PROXY'}
    result={k:v for k,v in os.environ.items() if k in names or k.startswith('LC_')}
    result.update(NPEER_HOME=str(journal.root),PYTHONPATH=str(ROOT))
    return result


class Delegation:
    def __init__(self,journal,registry):self.journal,self.registry=journal,registry

    def group(self,caller,handle):
        group=self.journal.one('SELECT * FROM groups WHERE group_id=?',(handle,))
        if not group:
            row=self.journal.one('SELECT w.group_id FROM tasks t JOIN workers w USING(worker_id) WHERE task_id=?',(handle,))
            require(row,'PROTOCOL_ERROR','Unknown task/group handle');group=self.journal.one('SELECT * FROM groups WHERE group_id=?',(row['group_id'],))
        require(caller.kind=='peer' and (caller.key==group['parent_key'] or caller.grants.get('group_id')==group['group_id']),
                'APPROVAL_BLOCKED','Task is outside the bound caller grant')
        return group

    async def spawn(self,caller,request):
        require(caller.kind=='peer','CALLER_UNBOUND','Delegation requires a verified native caller')
        require(not caller.grants.get('worker_id') or caller.grants.get('can_spawn'), 'APPROVAL_BLOCKED','Worker cannot delegate at its current depth')
        require(isinstance(request,dict),'PROTOCOL_ERROR','Spawn specification must be an object')
        original=dict(request);request_id=request.get('client_request_id') or new_id();digest=sha256(original)
        require(len(canonical(original))<=65536,'PAYLOAD_TOO_LARGE','Spawn request exceeds 64 KiB')
        prior=self.journal.one('SELECT * FROM spawn_requests WHERE caller_key=? AND client_request_id=?',(caller.key,request_id))
        if prior:
            require(prior['spec_sha256']==digest,'IDEMPOTENCY_CONFLICT','Spawn request ID has different content')
            return dict(self.status(caller,prior['group_id']),client_request_id=request_id)
        from .protocol import validate
        validate(request,json.loads((ROOT/'schemas/spawn.v1.json').read_text()))
        for worker in request['workers']:
            if 'model' in worker:
                require(worker['model'].strip() and not any(ord(c)<32 for c in worker['model']),
                        'PROTOCOL_ERROR','Worker model must be a nonempty native model ID or alias')
        from .policy import load
        local=load(self.journal)
        workers=request.get('workers');require(isinstance(workers,list) and 0<len(workers)<=local['workers_per_group'],'CAPACITY_UNAVAILABLE','Batch exceeds configured worker limit')
        require(request.get('schema_version',1)==1,'PROTOCOL_ERROR','Unknown spawn schema version')
        require(request.get('completion_mode','push') in ('push','wait'),'PROTOCOL_ERROR','Invalid completion mode')
        require(request.get('failure_policy','collect-all')=='collect-all','PROTOCOL_ERROR','Only collect-all is supported')
        parent=self.registry.verify(caller.key,caller.incarnation)
        limits=request.get('limits',{});depth=caller.grants.get('depth',0)+1
        ceiling=min(caller.grants.get('max_depth',1),local['max_depth'])
        require(depth<=ceiling and 1<=limits.get('max_depth',1)<=ceiling,'CAPACITY_UNAVAILABLE','Delegation depth ceiling reached')
        require(len(workers)<=limits.get('max_concurrent_workers',4)<=min(local['workers_per_group'],caller.grants.get('max_concurrent_workers',4)),
                'CAPACITY_UNAVAILABLE','Batch exceeds the caller concurrency ceiling')
        require(not request.get('survive_parent') or caller.grants.get('survive_parent'),'APPROVAL_BLOCKED','Detached survival requires an explicit operator grant')
        wall=limits.get('wall_time_seconds',local['task_wall_seconds']);turns=limits.get('max_task_turns_per_worker',local['max_task_turns'])
        require(1<=wall<=local['task_wall_seconds'] and 1<=turns<=local['max_task_turns'],'BUDGET_EXHAUSTED','Worker time/turn ceilings exceeded')
        group_id=new_id();policy=dict(original_request=original,workers=[],initial_tasks=[],completion_mode=request.get('completion_mode','push'),
            followup_grace_seconds=min(local['followup_grace_seconds'],max(0,limits.get('followup_grace_seconds',local['followup_grace_seconds']))),wall_time_seconds=wall,group_wall_seconds=local['group_wall_seconds'],
            depth=depth,allow_sibling_messages=bool(request.get('allow_sibling_messages',False)),survive_parent=bool(request.get('survive_parent',False)),
            created_at=time.time(),parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'])
        budget_path=self.journal.root/'test-budget.json'
        test_budget=json.loads(budget_path.read_text()) if budget_path.exists() else {}
        codex_config=tomllib.loads((Path.home()/'.codex/config.toml').read_text())
        claude_settings=Path.home()/'.claude/settings.json';claude_config=json.loads(claude_settings.read_text()) if claude_settings.exists() else {}
        agy_catalog={}
        agy_settings=Path.home()/'.gemini/antigravity-cli/settings.json'
        if any(w.get('provider')=='antigravity' for w in workers):
            from .antigravity import catalog
            agy_catalog=await catalog(Path.home()/'.local/bin/agy')
        catalog=None
        if any(w.get('provider')=='codex' for w in workers):
            client=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex')
            try:await client.start();catalog=(await client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            finally:await client.close()
        prepared=[];batch_baseline=snapshot(parent['cwd'])
        for source in workers:
            require(isinstance(source,dict) and source.get('provider') in ('claude','codex','antigravity'),'PROTOCOL_ERROR','Unknown worker provider')
            spec=dict(source);spec['title']=title(spec['title']);spec['task']=bounded_text(spec['task'])
            profile=spec.get('permission_profile','analysis');require(profile in parent.get('policy',{}).get('profiles',['analysis']),
                'PERMISSION_CAPABILITY_UNKNOWN','Requested worker profile exceeds the verified parent ceiling')
            require(profile in ('analysis','workspace'),'PERMISSION_CAPABILITY_UNKNOWN','Trusted workers require an explicit operator profile')
            workspace=spec.get('workspace',{'mode':'inherit-read-only'})
            require(profile!='analysis' or workspace.get('mode','inherit-read-only')=='inherit-read-only','PROTOCOL_ERROR','Analysis uses a read-only workspace')
            require(profile!='workspace' or workspace.get('mode')=='isolated-worktree','WORKSPACE_SNAPSHOT_REQUIRED','Writing workers require isolated worktrees')
            spec.update(permission_profile=profile,cwd=parent['cwd'],wall_time_seconds=wall,max_task_turns=turns)
            spec['model']=spec.get('model',codex_config.get('model') if spec['provider']=='codex' else claude_config.get('model','sonnet'))
            if spec['provider']=='antigravity':
                configured=json.loads(agy_settings.read_text()).get('model') if agy_settings.exists() else None
                default=configured if configured in agy_catalog else next((slug for slug,name in agy_catalog.items() if name==configured),None)
                spec['model']=source.get('model',default)
            require(isinstance(spec['model'],str) and spec['model'].strip() and not any(ord(c)<32 for c in spec['model']),
                    'PROTOCOL_ERROR','Worker model must be a nonempty native model ID or alias')
            if spec['provider']=='codex':
                spec['reasoning_effort']=spec.get('reasoning_effort') or codex_config.get('model_reasoning_effort','medium')
                model=next((m for m in catalog if m['model']==spec['model']),None)
                require(model,'MODEL_UNAVAILABLE','Exact requested Codex model is absent from the installed catalog')
                require(spec['reasoning_effort'] in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']],
                        'EFFORT_UNSUPPORTED','Exact requested effort is absent from the installed catalog')
            if spec['provider']=='antigravity':
                require(spec['model'] in agy_catalog,'MODEL_UNAVAILABLE','Exact Antigravity model is absent from the signed-in native catalog')
                spec['model_display_name']=agy_catalog[spec['model']]
                effort=spec.get('reasoning_effort')
                require(effort is None or spec['model'].endswith('-'+effort),'EFFORT_UNSUPPORTED','Antigravity effort must match the pinned model slug')
            refs=spec.get('context_refs',[])
            require(isinstance(refs,list) and len(refs)<=16 and all(isinstance(r,str) for r in refs),'PROTOCOL_ERROR','Context references must be registered artifact handles')
            from .artifacts import Artifacts
            for ref in refs:Artifacts(self.journal).read(ref,caller.key,0,1)
            if refs:spec['task']+='\nApproved context artifacts (read through artifact_read): '+', '.join(refs)
            worker_id,task_id=new_id(),new_id();binding=self.journal.root/'bindings'/new_id()
            grants=dict(worker_id=worker_id,group_id=group_id,depth=depth,max_depth=ceiling,profiles=[profile],
                can_spawn=depth<limits.get('max_depth',1),max_concurrent_workers=limits.get('max_concurrent_workers',4),
                message_targets=[caller.key],allow_sibling_messages=policy['allow_sibling_messages'])
            executable=(Path.home()/{'claude':'.local/bin/claude','codex':'.local/share/codex-mcp-manager/current/bin/codex','antigravity':'.local/bin/agy'}[spec['provider']]).resolve(strict=True)
            lease=dict(spec=spec,grants=grants,binding=str(binding),parent_key=caller.key,expires_at=time.time()+wall+600,
                executable=str(executable),live_budget_file=test_budget.get('path'),live_tranche=test_budget.get('tranche','first_cross_provider_delegations'))
            prepared.append(dict(worker_id=worker_id,task_id=task_id,lease=lease,workspace=workspace))
            policy['workers'].append(worker_id);policy['initial_tasks'].append(task_id)
        policy['phases']={'1':policy['initial_tasks']}
        policy['controller_generation']=1
        active=self.journal.one("SELECT COUNT(*) AS count FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')")['count']
        if active+len(prepared)>local['workers_total']:
            candidates=self.journal.all("SELECT worker_id,group_id FROM workers w WHERE state='IDLE_GRACE' AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.worker_id=w.worker_id AND t.state IN ('QUEUED','RUNNING','WAITING','APPROVAL_BLOCKED'))")
            for idle in candidates:
                try:await group_call(self.journal,idle['group_id'],'release-idle',{'worker_id':idle['worker_id']},timeout=25)
                except (PeerError,OSError,TimeoutError):continue
                active=self.journal.one("SELECT COUNT(*) AS count FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')")['count']
                if active+len(prepared)<=local['workers_total']:break
        with self.journal.transaction() as db:
            raced=db.execute('SELECT * FROM spawn_requests WHERE caller_key=? AND client_request_id=?',(caller.key,request_id)).fetchone()
            if raced:
                require(raced['spec_sha256']==digest,'IDEMPOTENCY_CONFLICT','Concurrent spawn has different content')
                return dict(self.status(caller,raced['group_id']),client_request_id=request_id)
            active=db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')").fetchone()[0]
            require(active+len(prepared)<=local['workers_total'],'CAPACITY_UNAVAILABLE','Eight worker slots are occupied; no model work was submitted')
            db.execute('INSERT INTO spawn_requests VALUES(?,?,?,?)',(caller.key,request_id,digest,group_id))
            db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group_id,caller.key,canonical(policy).decode(),'RESERVED','{}'))
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group_id,1,caller.key,policy['completion_mode'],'UNCLAIMED',None,None,new_id(),1))
            for worker in prepared:
                spec=worker['lease']['spec']
                for ref in spec.get('context_refs',[]):Artifacts(self.journal).grant(ref,caller.key,[worker['worker_id']])
                native_id=new_id() if spec['provider']=='claude' else None
                db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker['worker_id'],group_id,None,spec['provider'],native_id,spec['title'],spec['model'],None,spec.get('reasoning_effort'),spec['cwd'],None,'RESERVED',1,canonical(worker['lease']).decode()))
                db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(worker['task_id'],worker['worker_id'],None,None,'QUEUED',None,None,1,canonical({'text':spec['task'],'initial':True}).decode()))
        directory=private_directory(self.journal.root/'groups'/group_id)
        try:
            for worker in prepared:
                cwd,worktree_id,baseline=await asyncio.to_thread(prepare,self.journal,parent['cwd'],worker['worker_id'],worker['workspace'],expected_baseline=batch_baseline)
                worker['lease']['spec']['cwd']=cwd;worker['lease']['baseline']=baseline
                self.journal.db.execute('UPDATE workers SET cwd=?,worktree_id=?,lease_json=? WHERE worker_id=?',(cwd,worktree_id,canonical(worker['lease']).decode(),worker['worker_id']))
            if any(w['workspace'].get('mode')=='isolated-worktree' for w in prepared):
                require(snapshot(parent['cwd'])==batch_baseline,'WORKSPACE_SNAPSHOT_REQUIRED','Original checkout changed before writing-batch startup')
            await self.ensure_supervisor(group_id)
        except Exception as error:
            self.journal.event(group_id,'startup-error',{'type':type(error).__name__,'message':str(error)[:1000]})
            # A readiness timeout is uncertain; never launch another supervisor from this path.
            if not (directory/'owner.json').exists():
                self.journal.db.execute("UPDATE groups SET state='FAILED' WHERE group_id=?",(group_id,))
                self.journal.db.execute("UPDATE workers SET state='FAILED' WHERE group_id=?",(group_id,))
            raise
        return dict(self.status(caller,group_id),client_request_id=request_id)

    async def ensure_supervisor(self,group_id):
        directory=self.journal.root/'groups'/group_id
        async with async_advisory_lock(self.journal.root/'locks'/(group_id+'.launch.lock')):
            manifest=directory/'owner.json'
            if manifest.exists():
                value=json.loads(manifest.read_text())
                require(matches(value['owner']),'OWNERSHIP_UNKNOWN','Previous supervisor requires explicit recovery; it is not replayed')
                return
            log=open(directory/'supervisor.log','ab',buffering=0);os.chmod(log.name,0o600)
            try:
                process=subprocess.Popen([str(PYTHON),'-m','nativepeers.supervisor','--group-id',group_id],
                    cwd=ROOT,env=supervisor_environment(self.journal),stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                    start_new_session=True,close_fds=True)
            finally:log.close()
            deadline=time.monotonic()+8
            while not manifest.exists() and time.monotonic()<deadline:
                require(process.poll() is None,'SUPERVISOR_LOST','Owned supervisor exited before readiness; inspect its private log')
                await asyncio.sleep(.03)
            require(manifest.exists(),'OWNERSHIP_UNKNOWN','Supervisor readiness timed out; inspect the reserved group before any retry')

    def status(self,caller,handle):
        group=self.group(caller,handle)
        result=dict(group_id=group['group_id'],state=group['state'],workers=self.journal.all(
            'SELECT worker_id,peer_key,provider,native_id,role_title,model_requested,model_observed,effort_requested,state,generation FROM workers WHERE group_id=?',(group['group_id'],)),
            tasks=self.journal.all('SELECT t.task_id,t.worker_id,t.native_turn_id,t.state,t.result_artifact_id,t.terminal_reason FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?',(group['group_id'],)),
            notifications=self.journal.all('SELECT * FROM completion_routes WHERE group_id=? ORDER BY completion_generation',(group['group_id'],)))
        if caller.key!=group['parent_key']:
            worker=caller.grants.get('worker_id')
            require(handle==group['group_id'] or any(t['task_id']==handle and t['worker_id']==worker for t in result['tasks']),
                    'APPROVAL_BLOCKED','A worker can read only its own task results')
            result['workers']=[w for w in result['workers'] if w['worker_id']==worker]
            result['tasks']=[t for t in result['tasks'] if t['worker_id']==worker]
            result['notifications']=[]
        return result

    def result(self,caller,handle):
        status=self.status(caller,handle);policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(status['group_id'],))['policy_json'])
        results=[]
        for task in status['tasks']:
            if handle!=status['group_id'] and task['task_id']!=handle:continue
            if task['result_artifact_id']:
                artifact=self.journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?',(task['result_artifact_id'],))
                result=json.loads((self.journal.root/'artifacts'/artifact['relative_path']).read_text())
                raw=result['summary'].encode();result['summary']=raw[:12288].decode(errors='ignore');result['summary_truncated']=len(raw)>12288
                result['result_artifact_id']=task['result_artifact_id'];result['initial_task']=task['task_id'] in policy['initial_tasks'];results.append(result)
        return dict(status,results=results)

    async def wait(self,caller,handle,timeout_ms=30000,on_timeout=None):
        require(type(timeout_ms) is int and 1<=timeout_ms<=30000,'PROTOCOL_ERROR','Wait is bounded to 30 seconds')
        require(on_timeout in (None,'push','retain'),'PROTOCOL_ERROR','Unknown wait fallback')
        group=self.group(caller,handle)
        status=self.status(caller,handle)
        tasks=[t for t in status['tasks'] if handle==group['group_id'] or t['task_id']==handle]
        if tasks and all(t['state'] in TERMINAL for t in tasks):
            return {'ready':True,'result':self.result(caller,handle),'notification_output_uncertain':False,'notification_disposition':'retained-result-read'}
        return await group_call(self.journal,group['group_id'],'wait',{'caller_key':caller.key,'handle':handle,'timeout_ms':timeout_ms,'on_timeout':on_timeout},timeout_ms/1000+2)

    async def cancel(self,caller,handle,reason):
        group=self.group(caller,handle)
        require(caller.key==group['parent_key'],'APPROVAL_BLOCKED','Only the owning parent can cancel this work')
        return await group_call(self.journal,group['group_id'],'cancel',{'caller_key':caller.key,'handle':handle,'reason':bounded_text(reason)})
