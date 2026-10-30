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
from .errors import PeerError,SupervisorNotStarted,require
from .files import async_advisory_lock,atomic_json,canonical,private_directory,sha256
from .launch import launch_environment,provider_home,home_environment
from .models import WAIT_MAX_MS,bounded_text,new_id,title
from .notifications import claim_wait,wait_generations,wait_output,settle_deferred
from .process import matches
from .profiles import ROOT,PYTHON
from .worktrees import prepare,snapshot,freeze,validate_frozen

TERMINAL={'COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED'}
# Set by the Claude Code session bridge only. Claude Code stops a mod's process together with every
# descendant, so a supervisor the bridge starts is handed to launchd through a shell that exits at once,
# as a supervisor started by the CLI already is; it then ends by its own parent-lifetime policy.
REPARENT_SUPERVISORS=False


def supervisor_environment(journal):
    # This is a fresh independent application context, not a copy of a provider tool shell.
    # The allowlist is fixed before launch; there is no guard-deletion retry loop.
    names={'HOME','USER','LOGNAME','PATH','SHELL','TMPDIR','LANG','CODEX_HOME','CLAUDE_CONFIG_DIR','GROK_HOME',
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
        worker_handle=None
        if not group:
            row=self.journal.one('SELECT w.group_id FROM tasks t JOIN workers w USING(worker_id) WHERE task_id=?',(handle,))
            if not row:
                worker_handle=self.journal.one('SELECT group_id,worker_id FROM workers WHERE worker_id=?',(handle,))
                row=worker_handle
            require(row,'PROTOCOL_ERROR','Unknown task/group handle');group=self.journal.one('SELECT * FROM groups WHERE group_id=?',(row['group_id'],))
        require(caller.kind=='peer' and (caller.key==group['parent_key'] or caller.grants.get('group_id')==group['group_id']),
                'APPROVAL_BLOCKED','Task is outside the bound caller grant')
        require(not worker_handle,'PROTOCOL_ERROR','This is a worker ID; status/result require an exact task or group ID',
                details=dict(handle_kind='worker',group_id=group['group_id'],next_action='npeer status '+group['group_id']+' --compact --json'))
        return group

    async def spawn(self,caller,request,*,check=False,compact=False):
        require(not compact or check,'PROTOCOL_ERROR','Compact spawn output requires --check')
        require(caller.kind=='peer','CALLER_UNBOUND','Delegation requires a verified native caller')
        require(not caller.grants.get('worker_id') or caller.grants.get('can_spawn'), 'APPROVAL_BLOCKED','Worker cannot delegate at its current depth')
        require(isinstance(request,dict),'PROTOCOL_ERROR','Spawn specification must be an object')
        original=dict(request);request_id=request.get('client_request_id') or new_id();digest=sha256(original)
        require(len(canonical(original))<=65536,'PAYLOAD_TOO_LARGE','Spawn request exceeds 64 KiB')
        prior=self.journal.one('SELECT * FROM spawn_requests WHERE caller_key=? AND client_request_id=?',(caller.key,request_id))
        if prior:
            require(prior['spec_sha256']==digest,'IDEMPOTENCY_CONFLICT','Spawn request ID has different content')
            if not check:return dict(self.status(caller,prior['group_id']),client_request_id=request_id)
        from .protocol import validate
        validate(request,json.loads((ROOT/'schemas/spawn.v1.json').read_text()))
        for worker in request['workers']:
            if worker['provider']=='grok':require(not worker.get('fallbacks'),'PROTOCOL_ERROR','Grok workers do not support provider fallbacks')
            if 'model' in worker:
                require(worker['model'].strip() and not any(ord(c)<32 for c in worker['model']),
                        'PROTOCOL_ERROR','Worker model must be a nonempty native model ID or alias')
        from .policy import load,spawn_limits
        local=load(self.journal)
        workers=request.get('workers');require(isinstance(workers,list) and 0<len(workers)<=local['workers_per_group'],'CAPACITY_UNAVAILABLE','Batch exceeds configured worker limit')
        require(request.get('schema_version',1)==1,'PROTOCOL_ERROR','Unknown spawn schema version')
        require(request.get('completion_mode','push') in ('push','wait'),'PROTOCOL_ERROR','Invalid completion mode')
        require(request.get('failure_policy','collect-all')=='collect-all','PROTOCOL_ERROR','Only collect-all is supported')
        parent=self.registry.verify(caller.key,caller.incarnation)
        mirrored=[w['mirror'] for w in workers if w.get('mirror')]
        if mirrored:
            # A mirrored worker is its own wait-mode group, watched by the Claude session that is this parent (CLAUDE_LINEAGE_MOD §6.4).
            require(len(workers)==1 and request.get('completion_mode')=='wait','PROTOCOL_ERROR','A mirrored worker must be the only worker of a wait-mode group')
            require(parent['peer_key']['provider']=='claude' and mirrored[0]['claude_session']==parent['peer_key']['native_session_id'],
                    'APPROVAL_BLOCKED','The mirror session must be the bound Claude parent')
        limits=request.get('limits',{});depth=caller.grants.get('depth',0)+1
        ceiling=min(caller.grants.get('max_depth',1),local['max_depth'])
        require(depth<=ceiling and 1<=limits.get('max_depth',1)<=ceiling,'CAPACITY_UNAVAILABLE','Delegation depth ceiling reached')
        require(len(workers)<=limits.get('max_concurrent_workers',4)<=min(local['workers_per_group'],caller.grants.get('max_concurrent_workers',4)),
                'CAPACITY_UNAVAILABLE','Batch exceeds the caller concurrency ceiling')
        require(not request.get('survive_parent') or caller.grants.get('survive_parent'),'APPROVAL_BLOCKED','Detached survival requires an explicit operator grant')
        effective_limits=spawn_limits(local,limits)
        wall=effective_limits['wall_time_seconds']['effective'];turns=effective_limits['max_task_turns_per_worker']['effective']
        native_turns=effective_limits['max_native_turns_per_task']['effective']
        for field,requested,key in (('wall_time_seconds',wall,'task_wall_seconds'),('wall_time_seconds',wall,'group_wall_seconds'),
                                    ('max_task_turns_per_worker',turns,'max_task_turns'),('max_native_turns_per_task',native_turns,'native_max_turns')):
            budget_ceiling=local[key];source=local['sources'][key]
            mismatch='; task_wall_seconds must fit group_wall_seconds' if key=='group_wall_seconds' else ''
            require(budget_ceiling is None or requested<=budget_ceiling,'BUDGET_EXHAUSTED',
                    f'limits.{field} requested {requested} exceeds ceiling {budget_ceiling}; {key} from {source["source"]} ({source["policy_file"]})'+mismatch,
                    details=dict(field='limits.'+field,requested=requested,ceiling=budget_ceiling,**source))
        group_id=new_id();policy=dict(original_request=original,workers=[],initial_tasks=[],completion_mode=request.get('completion_mode','push'),
            followup_grace_seconds=min(local['followup_grace_seconds'],max(0,limits.get('followup_grace_seconds',local['followup_grace_seconds']))),wall_time_seconds=wall,group_wall_seconds=local['group_wall_seconds'],
            depth=depth,allow_sibling_messages=bool(request.get('allow_sibling_messages',False)),survive_parent=bool(request.get('survive_parent',False)),
            created_at=time.time(),parent_owner=parent['owner'],parent_incarnation=parent['incarnation_id'])
        budget_path=self.journal.root/'test-budget.json'
        test_budget=json.loads(budget_path.read_text()) if budget_path.exists() else {}
        homes={provider:provider_home(provider) for provider in {w['provider'] for w in workers}|{f['provider'] for w in workers for f in w.get('fallbacks',[])}}
        codex_settings=homes.get('codex',Path())/'config.toml'
        codex_config=tomllib.loads(codex_settings.read_text()) if 'codex' in homes and codex_settings.exists() else {}
        claude_settings=homes.get('claude',Path())/'settings.json';claude_config=json.loads(claude_settings.read_text()) if 'claude' in homes and claude_settings.exists() else {}
        agy_catalog={};agy_catalog_error=None
        agy_settings=homes.get('antigravity',Path())/'settings.json'
        if 'antigravity' in homes:
            from .antigravity import catalog
            try:agy_catalog=await catalog(Path.home()/'.local/bin/agy')
            except (PeerError,OSError) as error:
                if any(w['provider']=='antigravity' for w in workers):raise
                agy_catalog_error=error.json() if isinstance(error,PeerError) else {'code':'MODEL_UNAVAILABLE','message':'Antigravity native catalog is unavailable: '+type(error).__name__}
        grok_catalog=None
        if 'grok' in homes:
            from .grok import executable as grok_executable
            from .grok_worker import catalog as grok_models
            from .compatibility import verify
            grok_binary=Path(grok_executable()).resolve(strict=True)
            verify(self.journal,'grok',grok_binary)
            grok_catalog=await grok_models(grok_binary,home=homes['grok'])
        catalog=None
        if any(w.get('provider')=='codex' for w in workers):
            from .worker_visibility import FLAG
            client=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex',environment=home_environment('codex',homes['codex']),server_arguments=[FLAG])
            try:
                try:capability=await client.start()
                except Exception as error:
                    diagnostic=client.stderr_tail
                    unsupported=isinstance(diagnostic,(bytes,bytearray)) and b"unexpected argument '--nativepeers-worker' found" in diagnostic
                    raise PeerError('VISIBILITY_UNSUPPORTED' if unsupported else 'VISIBILITY_UNVERIFIED',
                        'Worker-only history capability could not be acknowledged during Codex startup; no worker was reserved or input submitted',
                        details={'cause':type(error).__name__}) from error
                require(capability.get('nativepeersWorkerSource') is True,'VISIBILITY_UNSUPPORTED',
                        'The selected Codex runtime did not acknowledge worker-only history classification; no worker was reserved or input submitted')
                catalog=(await client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            finally:await client.close()
        if any(w.get('workspace',{}).get('mode')=='isolated-copy' for w in workers):
            from .isolated_copy import identity_preflight
            identity_preflight(parent['cwd'])
        prepared=[];batch_baseline=snapshot(parent['cwd'])
        for source in workers:
            require(isinstance(source,dict) and source.get('provider') in ('claude','codex','antigravity','grok'),'PROTOCOL_ERROR','Unknown worker provider')
            spec=dict(source);spec['title']=title(spec['title']);spec['task']=bounded_text(spec['task'])
            profile=spec.get('permission_profile','analysis');require(profile in parent.get('policy',{}).get('profiles',['analysis']),
                'PERMISSION_CAPABILITY_UNKNOWN','Requested worker profile exceeds the verified parent ceiling')
            require(profile in ('analysis','workspace'),'PERMISSION_CAPABILITY_UNKNOWN','Trusted workers require an explicit operator profile')
            workspace=spec.get('workspace',{'mode':'inherit-read-only'})
            require(profile!='analysis' or workspace.get('mode','inherit-read-only')=='inherit-read-only','PROTOCOL_ERROR','Analysis uses a read-only workspace')
            require(profile!='workspace' or workspace.get('mode') in ('isolated-worktree','isolated-copy'),'WORKSPACE_SNAPSHOT_REQUIRED','Writing workers require an isolated worktree or explicit isolated copy')
            spec.update(permission_profile=profile,cwd=parent['cwd'],wall_time_seconds=wall,max_task_turns=turns,native_max_turns=native_turns)
            spec['model']=spec.get('model',codex_config.get('model') if spec['provider']=='codex' else claude_config.get('model','sonnet'))
            if spec['provider']=='antigravity':
                configured=json.loads(agy_settings.read_text()).get('model') if agy_settings.exists() else None
                default=configured if configured in agy_catalog else next((slug for slug,name in agy_catalog.items() if name==configured),None)
                spec['model']=source.get('model',default)
            if spec['provider']=='grok':
                spec['model']=source.get('model',grok_catalog['default'])
                spec['provider_home']=str(homes['grok'])
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
            if spec['provider']=='grok':
                from .grok_worker import arguments
                require(spec['model'] in grok_catalog['models'],'MODEL_UNAVAILABLE','Exact Grok model is absent from the native catalog')
                arguments(spec)
            refs=spec.get('context_refs',[])
            require(isinstance(refs,list) and len(refs)<=16 and all(isinstance(r,str) for r in refs),'PROTOCOL_ERROR','Context references must be registered artifact handles')
            from .artifacts import Artifacts
            for ref in refs:Artifacts(self.journal).read(ref,caller.key,0,1)
            if not check and spec['provider']=='claude' and profile=='analysis' and 'git_diff' in spec.get('requirements',{}).get('required_tools',[]):
                from .selected_context import packets
                require(packets(self.journal,caller,refs),'REVIEW_CONTEXT_REQUIRED','Analysis diff review requires a context-prepare artifact in context_refs; no input was submitted')
            if refs:spec['task']+='\nApproved context artifacts (read through artifact_read with text:true and limit:16000; follow next_offset until complete): '+', '.join(refs)
            worker_id,task_id=new_id(),new_id();binding=self.journal.root/'bindings'/new_id()
            grants=dict(worker_id=worker_id,group_id=group_id,depth=depth,max_depth=ceiling,profiles=[profile],
                can_spawn=depth<limits.get('max_depth',1),max_concurrent_workers=limits.get('max_concurrent_workers',4),
                message_targets=[caller.key],allow_sibling_messages=policy['allow_sibling_messages'])
            executable=grok_binary if spec['provider']=='grok' else (Path.home()/{'claude':'.local/bin/claude','codex':'.local/share/codex-mcp-manager/current/bin/codex','antigravity':'.local/bin/agy'}[spec['provider']]).resolve(strict=True)
            lease=dict(provider_home=str(homes[spec['provider']]),provider_home_explicit=('CLAUDE_CONFIG_DIR' in os.environ) if spec['provider']=='claude' else True,spec=spec,grants=grants,binding=str(binding),parent_key=caller.key,expires_at=time.time()+wall+600,
                executable=str(executable),live_budget_file=test_budget.get('path'),live_tranche=test_budget.get('tranche','first_cross_provider_delegations'))
            fallbacks=spec.get('fallbacks',[])
            require(not fallbacks or spec['provider']=='claude','PROTOCOL_ERROR','Explicit Gemini fallback is supported for Claude worker tasks')
            require(len({(f['provider'],f['model']) for f in fallbacks})==len(fallbacks),'PROTOCOL_ERROR','Fallback choices must be distinct')
            lease['fallback_candidates']=[]
            for fallback in fallbacks:
                selected=dict(fallback,provider_home=str(homes['antigravity']))
                path=Path.home()/'.local/bin/agy'
                selected['executable']=str(path.resolve())
                if fallback['model'] not in agy_catalog:
                    selected['unavailable']=agy_catalog_error or {'code':'MODEL_UNAVAILABLE','message':'Exact fallback model is absent from the signed-in Antigravity catalog.'}
                else:selected['model_display_name']=agy_catalog[fallback['model']]
                lease['fallback_candidates'].append(selected)
            prepared.append(dict(worker_id=worker_id,task_id=task_id,lease=lease,workspace=workspace))
            policy['workers'].append(worker_id);policy['initial_tasks'].append(task_id)
        policy['phases']={'1':policy['initial_tasks']}
        policy['controller_generation']=1
        from .usage.service import Usage
        intelligence=Usage(self.journal)
        if not check:
            # Reuse the already-read signed-in catalog. It does not renew quota
            # freshness and never turns metadata work into a second CLI launch.
            if agy_catalog:
                from .usage import store
                store.record(self.journal,'antigravity',homes['antigravity'],dict(source='antigravity-models',source_version=1,
                    models=[dict(model=k,display_name=v,support='NATIVE_CATALOG',capability_source='native-model-catalog',entitlement='UNVERIFIED') for k,v in list(agy_catalog.items())[:64]]),kind='catalog')
            remaining=policy['created_at']+wall-time.time()
            require(remaining>0,'BUDGET_EXHAUSTED','Worker deadline elapsed during planning')
            try:
                async with asyncio.timeout(remaining):
                    await asyncio.gather(*(intelligence.refresh(provider,home) for provider,home in homes.items()))
            except TimeoutError:
                raise PeerError('BUDGET_EXHAUSTED','Worker deadline elapsed during native quota collection') from None
        quota=intelligence.batch(prepared)
        for worker,assessment in zip(prepared,quota['workers']):
            worker['lease']['usage_at_spawn']=assessment
            worker['lease']['usage_demand']=[sha256([assessment['provider'],assessment['home'],assessment['scope'],p['pool_id']]) for p in assessment['pools']]
        policy['usage_at_spawn']=quota
        active=self.journal.one("SELECT COUNT(*) AS count FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')")['count']
        if check:
            # The shared normalization above does no reservation or materialization.
            # Catalog helpers are native metadata queries, never model inputs.
            from .compatibility import verify
            from .profiles import claude_arguments,codex_config
            failures=[];planned=[]
            for worker in prepared:
                lease=worker['lease'];spec=lease['spec'];provider=spec['provider']
                from .selected_context import packets
                context=packets(self.journal,caller,spec.get('context_refs',[]))
                tools=['Read','Glob','Grep','artifact_read'] if provider=='claude' and spec['permission_profile']=='analysis' else ['native '+spec['permission_profile']+' profile','artifact_read']
                required=spec.get('requirements',{}).get('required_tools',[])
                if 'git_diff' in required and provider=='claude' and spec['permission_profile']=='analysis' and not context:
                    failures.append(dict(provider=provider,title=spec['title'],error=dict(code='REVIEW_CONTEXT_REQUIRED',
                        message='Analysis has Read/Glob/Grep, without git diff. Use npeer context-prepare --path PATH --base BASE, then attach context_refs; keep the analysis profile.')))
                try:
                    verify(self.journal,provider,lease['executable'])
                    if provider=='antigravity':
                        from .antigravity_install import verify_worker_ready
                        verify_worker_ready(self.journal,spec,lease['executable'])
                    elif provider=='grok':
                        from .grok_install import verify_worker_ready
                        verify_worker_ready(self.journal,spec,lease['executable'])
                    elif provider=='claude':claude_arguments(self.journal,Path(lease['binding']),spec,new_id(),home=Path(lease['provider_home']))
                    else:codex_config(self.journal,Path(lease['binding']),spec['permission_profile'],home=Path(lease['provider_home']))
                except PeerError as error:failures.append(dict(provider=provider,title=spec['title'],error=error.json()))
                alternatives=[]
                for candidate in lease.get('fallback_candidates',[]):
                    alternative=dict(candidate)
                    try:
                        if candidate.get('unavailable'):raise PeerError(candidate['unavailable']['code'],candidate['unavailable']['message'])
                        from .antigravity_install import verify_worker_ready
                        verify_worker_ready(self.journal,dict(spec,provider='antigravity',model=candidate['model']),candidate['executable'])
                    except PeerError as error:alternative['unavailable']=error.json()
                    alternatives.append(alternative)
                planned.append(dict(spec,tools=dict(planned=tools,native_effective='NOT_OBSERVED before launch'),review_context=context,logical_task_slots={'limit':turns,'used':0,'remaining':turns},workspace=worker['workspace'],provider_home=lease['provider_home'],executable=lease['executable'],fallbacks=alternatives))
            frozen=await asyncio.to_thread(freeze,parent['cwd'],[w['workspace'] for w in prepared],expected_baseline=batch_baseline,journal=self.journal)
            quota_blocked=[item for item,worker in zip(quota['workers'],prepared) if item['blockable'] and worker['lease']['spec'].get('quota_policy','respect-known-limits')!='warn']
            pool_blocked=[d for d in quota['demands'] if d['ceiling'] is not None and d['active_workers']+d['proposed_workers']>d['ceiling']]
            from .completion_delivery import annotate,readiness
            plan=annotate(dict(state='CHECKED',read_only=True,ready=not failures and not quota_blocked and not pool_blocked and active+len(prepared)<=local['workers_total'],ready_scope='worker_execution',usage=quota,quota_blocked=quota_blocked,pool_concurrency_blocked=pool_blocked,
                workers=planned,baseline=frozen.baseline,failures=failures,limits=effective_limits,
                input_preparation=dict(selected_files=len(frozen.untracked)+frozen.baseline.get('dirty_input_files',0),selected_bytes=sum(len(data) for data,mode in frozen.untracked.values())+frozen.baseline.get('dirty_input_bytes',0),dirty_patch_bytes=len(frozen.patch or b''),time_limit_seconds=20,input_limit_bytes=10485760),
                capacity=dict(active=active,requested=len(prepared),ceiling=local['workers_total'],reservation_made=False),
                native_helpers=[dict(provider=provider,operation='model/list' if provider=='codex' else 'models',model_input=False)
                                for provider in ('codex','antigravity','grok') if provider in homes],
                client_request_id=request_id,existing_group_id=prior['group_id'] if prior else None,
                limitations=['A plan does not reserve capacity or guarantee later entitlement, runtime tools or unchanged files/configuration.']),
                readiness(self.journal,caller.key),push=policy['completion_mode']=='push')
            if compact:
                from .compact import preflight
                return preflight(caller,parent,plan)
            return plan
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
            else:
                active=db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')").fetchone()[0]
                require(active+len(prepared)<=local['workers_total'],'CAPACITY_UNAVAILABLE','Eight worker slots are occupied; no model work was submitted')
                # Optional local pool ceilings reserve only local demand, never vendor quota.
                for demand in intelligence.batch(prepared)['demands']:
                    require(demand['ceiling'] is None or demand['active_workers']+demand['proposed_workers']<=demand['ceiling'],
                        'CAPACITY_UNAVAILABLE','Configured local quota-pool concurrency ceiling reached',details=demand)
                db.execute('INSERT INTO spawn_requests VALUES(?,?,?,?)',(caller.key,request_id,digest,group_id))
                db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group_id,caller.key,canonical(policy).decode(),'RESERVED','{}'))
                from .mirror import grant,record
                db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(group_id,1,caller.key,policy['completion_mode'],'MIRROR_PENDING' if mirrored else 'UNCLAIMED',None,None,new_id(),1))
                for worker in prepared:
                    spec=worker['lease']['spec'];watch=spec.get('mirror')
                    record(db,worker['task_id'],group_id,1,'mirror' if watch else 'native',origin='parent-model',session=watch and watch['claude_session'],agent=watch and watch['claude_agent_id'])
                    if watch:grant(db,worker['worker_id'],watch['claude_session'],watch['claude_agent_id'],caller.key,display_only=False)
                    for ref in spec.get('context_refs',[]):Artifacts(self.journal).grant(ref,caller.key,[worker['worker_id']])
                    native_id=new_id() if spec['provider'] in ('claude','grok') else None
                    db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker['worker_id'],group_id,None,spec['provider'],native_id,spec['title'],spec['model'],None,spec.get('reasoning_effort'),spec['cwd'],None,'RESERVED',1,canonical(worker['lease']).decode()))
                    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(worker['task_id'],worker['worker_id'],None,None,'QUEUED',None,None,1,canonical({'text':spec['task'],'initial':True}).decode()))
        if raced:return dict(self.status(caller,raced['group_id']),client_request_id=request_id)
        directory=private_directory(self.journal.root/'groups'/group_id)
        launch_attempted=False
        try:
            batch_workspace=await asyncio.to_thread(freeze,parent['cwd'],[w['workspace'] for w in prepared],expected_baseline=batch_baseline,journal=self.journal)
            for worker in prepared:
                cwd,worktree_id,baseline=await asyncio.to_thread(prepare,self.journal,parent['cwd'],worker['worker_id'],worker['workspace'],frozen=batch_workspace)
                worker['lease']['spec']['cwd']=cwd;worker['lease']['baseline']=baseline
                self.journal.db.execute('UPDATE workers SET cwd=?,worktree_id=?,lease_json=? WHERE worker_id=?',(cwd,worktree_id,canonical(worker['lease']).decode(),worker['worker_id']))
            if any(w['workspace'].get('mode') in ('isolated-worktree','isolated-copy') for w in prepared):
                await asyncio.to_thread(validate_frozen,parent['cwd'],batch_workspace,journal=self.journal)
            launch_attempted=True
            await self.ensure_supervisor(group_id)
        except Exception as error:
            self.journal.event(group_id,'startup-error',{'type':type(error).__name__,'message':str(error)[:1000]})
            # A readiness timeout is uncertain; never launch another supervisor from this path.
            if not launch_attempted or isinstance(error,SupervisorNotStarted):
                for row in self.journal.all('SELECT worker_id,generation,native_id,cwd FROM workers WHERE group_id=?',(group_id,)):
                    self.journal.event(row['worker_id'],'controller-not-created',dict(row,controller_created=False,
                        cause=type(error).__name__,source='spawn-before-supervisor'))
                self.journal.db.execute("UPDATE groups SET state='FAILED' WHERE group_id=?",(group_id,))
                self.journal.db.execute("UPDATE workers SET state='FAILED' WHERE group_id=?",(group_id,))
                self.journal.db.execute("UPDATE tasks SET state='CANCELED',terminal_reason='STARTUP_FAILED_BEFORE_SUBMISSION' WHERE state='QUEUED' AND native_turn_id IS NULL AND worker_id IN (SELECT worker_id FROM workers WHERE group_id=?)",(group_id,))
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
                argv=[str(PYTHON),'-m','nativepeers.supervisor','--group-id',group_id]
                try:
                    process=subprocess.Popen(['/bin/sh','-c','"$@" &','sh',*argv] if REPARENT_SUPERVISORS else argv,
                        cwd=ROOT,env=supervisor_environment(self.journal),stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                        start_new_session=True,close_fds=True)
                    if REPARENT_SUPERVISORS:
                        require(process.wait(5)==0,'SUPERVISOR_LOST','Owned supervisor launch shell failed')
                except OSError as error:
                    raise SupervisorNotStarted(group_id,error) from error
            finally:log.close()
            deadline=time.monotonic()+8
            while not manifest.exists() and time.monotonic()<deadline:
                # A reparented supervisor is not this process's child: its readiness alone is awaited.
                require(REPARENT_SUPERVISORS or process.poll() is None,'SUPERVISOR_LOST','Owned supervisor exited before readiness; inspect its private log')
                await asyncio.sleep(.03)
            require(manifest.exists(),'OWNERSHIP_UNKNOWN','Supervisor readiness timed out; inspect the reserved group before any retry')

    def status(self,caller,handle,*,explain_selection=False,completion=True,compact=False):
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
        elif completion:
            from .completion_delivery import annotate,describe,readiness
            result['notifications']=[describe(self.journal,route) for route in result['notifications']]
            annotate(result,readiness(self.journal,group['parent_key']),
                     push=any(route['mode']=='push' for route in result['notifications']))
        from .fallback import result_details
        for task in result['tasks']:task.update(result_details(self.journal,task['task_id']))
        from .worker_process import task_slots
        for worker in result['workers']:
            row=self.journal.one('SELECT generation,provider,native_id,lease_json FROM workers WHERE worker_id=?',(worker['worker_id'],))
            worker['logical_task_slots']=task_slots(row)
            from .worker_visibility import status as visibility_status
            worker['history_visibility']=visibility_status(row)
            if explain_selection:
                from .launch import recorded_home
                from .usage.service import Usage
                from .usage.store import home_layout
                full=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker['worker_id'],))
                lease=json.loads(full['lease_json']);home=recorded_home(self.journal,full)
                with home_layout(lease.get('provider_home_explicit') if full['provider']=='claude' else None):
                    quota=Usage(self.journal).assessment(full['provider'],home,full['model_observed'] or lease['spec']['model'],full['effort_requested'])
                worker['selection']=dict(home=str(home),home_explicit=lease.get('provider_home_explicit'),
                    requested_model=full['model_requested'],observed_model=full['model_observed'],
                    reasoning_effort=full['effort_requested'],quota=quota,local_demand=lease.get('usage_demand',[]),
                    vendor_quota_reserved=False)
                from .ownership import release_status
                worker['release']=release_status(self.journal,full)
        result['usage_at_spawn']=json.loads(group['policy_json']).get('usage_at_spawn')
        result['progress']=self.progress(group,worker_id=caller.grants.get('worker_id') if caller.key!=group['parent_key'] else None)
        if compact:
            from .compact import result_view
            return result_view(self,result,json.loads(group['policy_json']),handle)
        return result

    def progress(self,group,*,worker_id=None):
        policy=json.loads(group['policy_json'])
        clause=' AND w.worker_id=?' if worker_id else ''
        params=(group['group_id'],)+((worker_id,) if worker_id else ())
        tasks=self.journal.all('SELECT t.*,w.generation current_generation FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?'+clause,params)
        current=[t for t in tasks if t['generation']==t['current_generation']]
        states={t['task_id']:t['state'] for t in tasks};initial=[t for t in policy.get('initial_tasks',[]) if t in states]
        phases=policy.get('phases',{})
        visible=[int(n) for n,ids in phases.items() if not worker_id or any(t in states for t in ids)]
        activity=self.journal.one("SELECT e.at,e.kind,e.subject_id FROM events e JOIN tasks t ON t.task_id=e.subject_id JOIN workers w USING(worker_id) WHERE w.group_id=? AND t.generation=w.generation"+clause+" AND e.kind IN ('native-task-started','native-task-terminal','provider-internal-rounds') ORDER BY e.sequence DESC LIMIT 1",params)
        return {'active_tasks':sum(t['state'] not in TERMINAL|{'QUEUED'} for t in current),
            'queued_tasks':sum(t['state']=='QUEUED' for t in current),
            'initial_batch_complete':all(states[t] in TERMINAL for t in initial) if initial else None,
            'latest_completion_phase':max(visible,default=None),
            'deadline':policy['created_at']+policy.get('group_wall_seconds',policy.get('wall_time_seconds',0)) if 'created_at' in policy and policy.get('group_wall_seconds',policy.get('wall_time_seconds')) is not None else None,
            'last_native_activity':dict(at=activity['at'],category=activity['kind'],task_id=activity['subject_id']) if activity else None,
            'activity_is_success':False}

    def groups(self,caller,limit=20,cursor=None):
        require(caller.kind=='peer' and caller.incarnation,'CALLER_UNBOUND','Retained groups require a verified native parent')
        self.registry.verify(caller.key,caller.incarnation)
        require(type(limit) is int and 1<=limit<=100,'PROTOCOL_ERROR','Group limit must be 1 to 100')
        from .pagination import encode,decode
        scope=['groups',caller.key]
        ceiling=self.journal.one('SELECT COALESCE(MAX(rowid),0) n FROM groups')['n']
        params=[caller.key]
        predicate=''
        if cursor is not None:
            position,ceiling=decode(cursor,scope)
            require(type(position) is int and 0<position<=ceiling,'PROTOCOL_ERROR','Invalid group position')
            predicate=' AND rowid<?';params.append(position)
        params.extend([ceiling,limit+1])
        rows=self.journal.all('SELECT rowid AS page_id,* FROM groups WHERE parent_key=?'+predicate+' AND rowid<=? ORDER BY rowid DESC LIMIT ?',params)
        groups=[]
        for row in rows[:limit]:
            workers=self.journal.all('SELECT worker_id,role_title,provider,native_id,state,generation FROM workers WHERE group_id=? ORDER BY worker_id',(row['group_id'],))
            counts=self.journal.one('SELECT COUNT(*) tasks,COUNT(result_artifact_id) results FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?',(row['group_id'],))
            
            from .worker_process import task_slots
            for worker in workers:
                worker['logical_task_slots']=task_slots(self.journal.one('SELECT generation,lease_json FROM workers WHERE worker_id=?',(worker['worker_id'],)))
            groups.append(dict(group_id=row['group_id'],state=row['state'],workers=workers,task_count=counts['tasks'],result_count=counts['results'],results_available=bool(counts['results']),progress=self.progress(row)))
        return {'groups':groups,'next_cursor':encode(scope,rows[limit-1]['page_id'],ceiling) if len(rows)>limit else None}

    def result(self,caller,handle,*,include_artifacts=False,compact=False):
        require(not compact or not include_artifacts,'PROTOCOL_ERROR','Choose compact previews or full artifact pages')
        status=self.status(caller,handle);policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(status['group_id'],))['policy_json'])
        if compact:
            from .compact import result_view
            return result_view(self,status,policy,handle)
        value=self._render_result(status,policy,handle)
        if include_artifacts:
            from .artifacts import Artifacts
            handles=[]
            for result in value['results']:
                handles.extend([result['result_artifact_id'],*result.get('artifacts',[])])
                changes=result.get('changes',{})
                handles.extend(changes.get(key) for key in ('patch_artifact','worker_delta_artifact'))
            handles=list(dict.fromkeys(h for h in handles if isinstance(h,str)))
            pages=[]
            for artifact in handles[:16]:
                try:page=Artifacts(self.journal).read(artifact,caller.key,0,16384)
                except PeerError as error:
                    if error.code!='APPROVAL_BLOCKED' or not caller.grants.get('worker_id'):raise
                    page=Artifacts(self.journal).read(artifact,caller.grants['worker_id'],0,16384)
                pages.append(page)
            value.update(artifact_pages=pages,artifact_pages_limit=16,artifact_page_bytes=16384,artifact_handles_omitted=max(0,len(handles)-16))
        return value

    def _render_result(self,status,policy,handle):
        # Only render the captured membership; concurrent admission belongs to a
        # later read, even if its immutable result becomes available meanwhile.
        results=[]
        for task in status['tasks']:
            if handle!=status['group_id'] and task['task_id']!=handle:continue
            if task['result_artifact_id']:
                artifact=self.journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?',(task['result_artifact_id'],))
                result=json.loads((self.journal.root/'artifacts'/artifact['relative_path']).read_text())
                raw=result['summary'].encode();result['summary']=raw[:12288].decode(errors='ignore');result['summary_truncated']=len(raw)>12288
                from .compact import phase
                result['completion_phase']=phase(policy,task['task_id'])
                result['result_artifact_id']=task['result_artifact_id'];result['initial_task']=task['task_id'] in policy['initial_tasks'];results.append(result)
                from .fallback import result_details
                result.update(result_details(self.journal,task['task_id']))
            elif task['state'] in TERMINAL-{'COMPLETED'}:
                # Never-started cancellation/recovery can have no model result.
                # Render its retained disposition, not an invented model answer.
                worker=next(w for w in status['workers'] if w['worker_id']==task['worker_id'])
                reason=task['terminal_reason'] or task['state']
                result=dict(task_id=task['task_id'],status=task['state'].lower(),summary='No final model result. '+reason,
                    worker={'provider':worker['provider'],'native_session_id':worker['native_id'],'title':worker['role_title']},
                    model_requested=worker['model_requested'],model_observed=worker['model_observed'],
                    result_artifact_id=None,initial_task=task['task_id'] in policy['initial_tasks'],retained_disposition=True,
                    error={'code':reason,'may_have_received':task['state']=='INTERRUPTED'})
                from .fallback import result_details
                result.update(result_details(self.journal,task['task_id']));results.append(result)
        return dict(status,results=results)

    def terminal_wait(self,caller,handle,timeout_ms,*,waiter=None,controller_generation=None):
        claimed=False;deliveries=[];waiter=waiter or new_id()
        with self.journal.transaction() as db:
            group=self.group(caller,handle)
            status=self.status(caller,handle,completion=False)
            tasks=[t for t in status['tasks'] if handle==group['group_id'] or t['task_id']==handle]
            if not tasks or any(t['state'] not in TERMINAL for t in tasks):return None
            policy=json.loads(group['policy_json']);generations=wait_generations(group['group_id'],policy,handle)
            controller_generation=controller_generation or policy.get('controller_generation',1)
            if caller.key==group['parent_key']:
                for generation in generations:
                    if claim_wait(self.journal,group['group_id'],generation,controller_generation,waiter,timeout_ms/1000,db=db):
                        emitted=wait_output(self.journal,group['group_id'],generation,controller_generation,waiter,settle=False)
                        claimed=emitted or claimed
                        if emitted:
                            deliveries.append(self.journal.one('SELECT delivery_id FROM completion_routes WHERE group_id=? AND completion_generation=?',(group['group_id'],generation))['delivery_id'])
                status['notifications']=self.journal.all('SELECT * FROM completion_routes WHERE group_id=? ORDER BY completion_generation',(group['group_id'],))
        # Artifact/budget I/O happens after committing the exact claims/membership.
        for delivery in deliveries:settle_deferred(self.journal,group['group_id'],delivery)
        if caller.key==group['parent_key']:
            from .completion_delivery import annotate,describe,readiness
            status['notifications']=[describe(self.journal,route) for route in status['notifications']]
            annotate(status,readiness(self.journal,group['parent_key']))
        return {'ready':True,'result':self._render_result(status,policy,handle),'notification_output_uncertain':claimed,
                'notification_disposition':('tool-result-output-pending-observation' if claimed else 'retained-result-read') if generations else 'batch-notification-unchanged'}

    async def wait(self,caller,handle,timeout_ms=WAIT_MAX_MS,on_timeout=None):
        require(type(timeout_ms) is int and 1<=timeout_ms<=WAIT_MAX_MS,'PROTOCOL_ERROR',
                f'timeout_ms requested {timeout_ms!r}; must be an integer from 1 to {WAIT_MAX_MS} ms (ceiling {WAIT_MAX_MS})',
                details={'field':'timeout_ms','requested':timeout_ms,'minimum':1,'ceiling':WAIT_MAX_MS})
        require(on_timeout in (None,'push','retain'),'PROTOCOL_ERROR','Unknown wait fallback')
        terminal=self.terminal_wait(caller,handle,timeout_ms)
        if terminal is not None:
            from .completion_delivery import annotate
            route=terminal['result'].get('completion_delivery')
            return annotate(terminal,route) if route is not None else terminal
        group=self.group(caller,handle)
        try:
            value=await group_call(self.journal,group['group_id'],'wait',{'caller_key':caller.key,'handle':handle,'timeout_ms':timeout_ms,'on_timeout':on_timeout},timeout_ms/1000+2)
            from .completion_delivery import annotate,readiness
            return annotate(value,readiness(self.journal,group['parent_key']),push=value.get('notification_disposition')=='push')
        except (PeerError,FileNotFoundError,ConnectionRefusedError) as error:
            if isinstance(error,PeerError) and error.code!='SUPERVISOR_LOST':raise
            latest=self.status(caller,handle)
            selected=[t for t in latest['tasks'] if handle==group['group_id'] or t['task_id']==handle]
            if selected and all(t['state'] in TERMINAL for t in selected):
                return await self.wait(caller,handle,timeout_ms,on_timeout)
            raise

    async def cancel(self,caller,handle,reason):
        group=self.group(caller,handle)
        require(caller.key==group['parent_key'],'APPROVAL_BLOCKED','Only the owning parent can cancel this work')
        return await group_call(self.journal,group['group_id'],'cancel',{'caller_key':caller.key,'handle':handle,'reason':bounded_text(reason)})
