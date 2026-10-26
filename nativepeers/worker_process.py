"""One owned logical worker, serialized native turns, and a supervisor lease."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time

from .adapters.codex_app_server import AppServer,ProviderError
from .artifacts import Artifacts
from .errors import PeerError,require
from .files import atomic_json,canonical
from .journal import Journal
from .launch import launch_environment,recorded_home,home_environment,apply_home
from .models import new_id,CLAUDE_ALIAS_FAMILIES,claude_model_matches
from .process import identity,matches,signal_owned,descendants,refresh_captured,owned_status,signal_captured
from .profiles import ROOT,claude_arguments,codex_config,config_arguments
from .registry import Registry
from .compatibility import verify
from .provider_errors import classify
from .worker_report import PROMPT,extract


def model_selection(row):
    """Keep Claude's launch-only context suffix alongside its observed identity."""
    selected=json.loads(row.get('lease_json') or '{}').get('spec',{}).get('model') or row['model_requested']
    observed=row['model_observed']
    if row['provider']=='claude' and observed:
        extended=any(value and value.endswith('[1m]') for value in (selected,row['model_requested']))
        return observed.removesuffix('[1m]')+('[1m]' if extended else '')
    return observed or selected


def task_slots(row):
    lease=json.loads(row.get('lease_json') or '{}');limit=lease.get('spec',{}).get('max_task_turns',8)
    used=lease.get('logical_tasks_used',0) if lease.get('logical_tasks_generation')==row['generation'] else 0
    used=min(limit,max(0,used))
    return dict(limit=limit,used=used,remaining=max(0,limit-used),generation=row['generation'],
                scope='logical task starts in this controller generation; native model/tool rounds are separate')


class Worker:
    def __init__(self,journal,worker_id):
        self.journal=journal;self.row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        self.lease=json.loads(self.row['lease_json']);self.spec=self.lease['spec'];self.binding=Path(self.lease['binding'])
        self.spec['model']=model_selection(self.row)
        self.home=recorded_home(journal,self.row);self.lease['provider_home']=str(self.home)
        if self.row['provider']=='grok':self.spec['provider_home']=str(self.home)
        from .worker_visibility import requested
        requested(self.row,self.lease)
        journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(self.lease).decode(),worker_id))
        self.queue=asyncio.Queue(maxsize=32);self.owner=identity(os.getpid()).json()
        self.provider=None;self.provider_identity=None;self.provider_descendants={};self.provider_monitor=None;self.provider_cleanup=None;self.client=None;self.native=self.row['native_id'];self.turn=None
        self.stopping=False;self.shutdown_reason=None;self.running_task=None;self.tasks_started=task_slots(self.row)['used'];self.retire_after_task=False
        self.canceling_task=None;self.turn_task=None
        self.expected_native=self.native;self.model_observed=self.row['model_observed']
        self.fallback_pending=False;self.input_submitted=False

    def emit(self,event,**payload):
        print(canonical(dict(event=event,worker_id=self.row['worker_id'],generation=self.row['generation'],**payload)).decode(),flush=True)

    def bootstrap(self):
        atomic_json(self.binding,dict(state='RESERVED_UNBOUND',kind='peer',provider=self.row['provider'],worker_id=self.row['worker_id'],
            owner=self.owner,expires_at=self.lease['expires_at'],grants=self.lease['grants']))
        self.manifest()

    def manifest(self):
        from .workspace_retirement import retain_processes
        retain_processes(self.journal,self.row['worker_id'],self.row['generation'],self.native,
                         [self.owner,self.provider_identity,*self.provider_descendants.values()])
        atomic_json(self.journal.root/'workers'/(self.row['worker_id']+'.json'),dict(owner=self.owner,
            native_process=self.provider_identity,native_descendants=list(self.provider_descendants.values()),cleanup=self.provider_cleanup,native_id=self.native,worker_id=self.row['worker_id'],
            group_id=self.row['group_id'],generation=self.row['generation'],history_visibility=self.lease.get('history_visibility')))

    def bind(self,native_id,*,model=None):
        if self.expected_native:
            require(native_id==self.expected_native,'OWNERSHIP_CONFLICT','Provider emitted a different native session ID')
        require(self.native is None or self.native==native_id,'OWNERSHIP_CONFLICT','Worker identity cannot change')
        if model:
            requested=self.spec['model']
            if self.row['provider']=='antigravity' and model==self.spec.get('model_display_name'):model=requested
            if self.row['provider']=='claude':model=model.removesuffix('[1m]')
            expected=(self.model_observed or requested).removesuffix('[1m]') if self.row['provider']=='claude' else requested
            if self.row['provider']=='claude' and not self.model_observed and not expected.startswith('claude-'):
                # Owned turns run outside plan mode. Default is intentionally
                # account-selected; unknown alias semantics are not verified.
                if expected!='default':
                    require(expected in CLAUDE_ALIAS_FAMILIES or model==expected,'MODEL_UNAVAILABLE','Claude alias semantics are unverified; select an exact native model',details={'requested':requested})
                    require(claude_model_matches(expected,model,allow_multiple=True),
                            'MODEL_UNAVAILABLE','Provider selected a different Claude model family',details={'requested':requested,'observed':model})
            if self.model_observed or self.row['provider'] in ('codex','antigravity','grok') or expected.startswith('claude-'):
                require(model==expected,'MODEL_UNAVAILABLE','Provider selected a different model than the exact requested model',
                        details={'requested':requested,'observed':model})
            self.model_observed=model
            self.spec['model']=model+('[1m]' if self.row['provider']=='claude' and requested.endswith('[1m]') else '')
            self.journal.db.execute('UPDATE workers SET model_observed=?,lease_json=? WHERE worker_id=?',
                (model,canonical(self.lease).decode(),self.row['worker_id']))
        self.native=native_id
        self.expected_native=native_id
        existing=json.loads(self.binding.read_text())
        if existing.get('state')=='BOUND':return
        peer=Registry(self.journal).register(self.row['provider'],self.home,native_id,self.row['role_title'],self.owner,
            self.row['cwd'],{'kind':'owned-worker','group_id':self.row['group_id'],'worker_id':self.row['worker_id']},
            owner_kind='owned-worker-controller',policy=self.lease['grants'],home_explicit=self.lease.get('provider_home_explicit'))
        self.journal.db.execute('UPDATE workers SET peer_key=?,native_id=?,model_observed=? WHERE worker_id=?',
            (peer['handle'],native_id,self.model_observed,self.row['worker_id']))
        atomic_json(self.binding,dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],
            worker_id=self.row['worker_id'],owner=self.owner,expires_at=self.lease['expires_at'],grants=self.lease['grants']))
        self.manifest();self.emit('bound',native_id=native_id,peer_key=peer['handle'])

    async def prepare(self):
        self.check_submission()
        self.bootstrap()
        actual=verify(self.journal,self.row['provider'],self.lease['executable'],worker_id=self.row['worker_id'])
        self.lease['verified_runtime']=actual
        self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(self.lease).decode(),self.row['worker_id']))
        if self.row['provider']=='codex':
            from .worker_visibility import server_arguments,initialized,persisted
            executable=Path(self.lease['executable']).resolve(strict=True)
            history_arguments=server_arguments(self.row,self.lease)
            config=codex_config(self.journal,self.binding,self.spec['permission_profile'],home=self.home)
            self.client=AppServer(executable,config_arguments(config),cwd=Path(self.row['cwd']),
                server_arguments=history_arguments,
                ownership_changed=self.app_server_ownership,
                environment={**home_environment('codex',self.home),'NPEER_HOME':str(self.journal.root),'NPEER_BOOTSTRAP':str(self.binding),'PYTHONPATH':str(ROOT)})
            capability=await self.client.start();self.provider=self.client.process;self.provider_identity=identity(self.provider.pid).json();self.manifest()
            initialized(self.journal,self.row,self.lease,capability,required=bool(history_arguments))
            catalog=(await self.client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            model=next((m for m in catalog if m['model']==self.spec['model']),None)
            require(model,'MODEL_UNAVAILABLE','The immutable worker executable does not advertise the exact model')
            require(self.spec.get('reasoning_effort') in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']],
                    'EFFORT_UNSUPPORTED','The immutable worker executable does not advertise the exact effort')
            self.check_submission()
            if self.native:
                require(self.lease.get('released_native',False),'OWNERSHIP_CONFLICT','Existing native worker was not released')
                from .history_cleanup import resume_archive_state
                archived=resume_archive_state(self.journal,dict(self.row,native_id=self.native))
                if archived['archived'] is True:
                    self.check_submission()
                    await self.client.call('thread/unarchive',{'threadId':self.native})
                    self.journal.event(self.row['worker_id'],'history-unarchived',dict(archived,archived=False,generation=self.row['generation'],operation='owned-resume'))
                self.check_submission()
                response=await self.client.call('thread/resume',{'threadId':self.native,'cwd':self.row['cwd'],'model':self.spec['model'],'approvalPolicy':'never','sandbox':'read-only' if self.spec['permission_profile']=='analysis' else 'workspace-write'})
            else:
                response=await self.client.call('thread/start',{'cwd':self.row['cwd'],'model':self.spec['model'],'approvalPolicy':'never',
                    'sandbox':'read-only' if self.spec['permission_profile']=='analysis' else 'workspace-write',
                    'ephemeral':False,'allowProviderModelFallback':False})
            self.bind(response['thread']['id'],model=response.get('model'))
            persisted(self.journal,self.row,self.lease,response['thread'],required=bool(history_arguments))
            await self.client.call('thread/name/set',{'threadId':self.native,'name':self.row['role_title']})
            # A real inventory, including inherited MCP/plugin tools, is checked before model input.
            deadline=time.monotonic()+5
            while True:
                inventory=await self.client.call('mcpServerStatus/list',{'threadId':self.native})
                unsafe=[s['name'] for s in inventory['data'] if s['name']!='nativepeers' and s.get('tools')]
                require(not unsafe,'PERMISSION_CAPABILITY_UNKNOWN','Worker inherited unscoped MCP tools',details=unsafe)
                own=next((s for s in inventory['data'] if s['name']=='nativepeers'),{})
                if 'peer_send' in own.get('tools',{}):break
                require(time.monotonic()<deadline,'CALLER_NOT_READY','Native provider did not refresh bound MCP tools')
                await asyncio.sleep(.1)
            self.journal.event(self.row['worker_id'],'effective-tool-profile',{'profile':self.spec['permission_profile'],
                'servers':[{ 'name':s['name'],'tools':list(s['tools']),'runtime_status':s.get('runtimeStatus')} for s in inventory['data']],
                'sandbox':'read-only' if self.spec['permission_profile']=='analysis' else 'workspace-write'})
        elif self.row['provider']=='antigravity':self.verify_antigravity()
        elif self.row['provider']=='grok':
            from .grok_worker import catalog,arguments
            self.verify_grok()
            models=await catalog(self.lease['executable'],home=self.home)
            require(self.spec['model'] in models['models'],'MODEL_UNAVAILABLE','The immutable Grok worker executable does not advertise the exact model')
            arguments(self.spec,self.native)
        else:
            # Preflight validates argv/environment. Native initialization begins only after first input.
            require(Path(self.lease['executable']).is_file(),'EXECUTABLE_NOT_FOUND','Installed Claude executable is unavailable')
            launch_environment('claude');claude_arguments(self.journal,self.binding,self.spec,self.expected_native,home=self.home)
        self.check_submission()
        self.journal.db.execute("UPDATE workers SET state='READY' WHERE worker_id=?",(self.row['worker_id'],))
        self.emit('ready',native_id=self.native if self.row['provider']=='codex' else None)

    async def task(self,task_id,text):
        from .usage.store import home_layout
        with home_layout(self.lease.get('provider_home_explicit') if self.row['provider']=='claude' else None):
            return await self._task(task_id,text)

    async def _task(self,task_id,text):
        claimed=self.journal.db.execute("UPDATE tasks SET state='RUNNING' WHERE task_id=? AND worker_id=? AND generation=? AND state='QUEUED'",(task_id,self.row['worker_id'],self.row['generation'])).rowcount
        if not claimed:return
        from .worker_visibility import requested
        requested(self.row,self.lease)
        self.tasks_started+=1
        self.lease.update(logical_tasks_used=min(self.tasks_started,self.spec.get('max_task_turns',8)),logical_tasks_generation=self.row['generation'])
        self.journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=? AND generation=?',(canonical(self.lease).decode(),self.row['worker_id'],self.row['generation']))
        self.running_task=task_id;started=time.time();self.turn=None;self.input_submitted=False
        task_row=self.journal.one('SELECT task_json FROM tasks WHERE task_id=?',(task_id,))
        task_spec=json.loads(task_row['task_json']);deadline=task_spec.setdefault('deadline',started+self.spec.get('wall_time_seconds',1800))
        task_spec.setdefault('input_text',text)
        self.journal.db.execute('UPDATE tasks SET task_json=? WHERE task_id=?',(canonical(task_spec).decode(),task_id))
        text='NativePeers assigned worker title: '+self.row['role_title']+'. Provider: '+self.row['provider']+'.\n'+text+PROMPT
        self.journal.db.execute("UPDATE workers SET state='RUNNING' WHERE worker_id=?",(self.row['worker_id'],))
        reservation=None
        budget=None
        clean_input=None
        usage_assessment=None
        try:
            require(self.tasks_started<=self.spec.get('max_task_turns',8),'BUDGET_EXHAUSTED','Worker logical task-turn limit reached',details={'input_submitted':False})
            if self.row['worktree_id'] and self.lease.get('fallback_candidates'):
                from .fallback import workspace_image,image_digest
                try:clean_input=image_digest(await asyncio.to_thread(workspace_image,self.row['cwd']))
                except (PeerError,OSError):pass
            from .usage.service import Usage
            intelligence=Usage(self.journal)
            require(time.time()<deadline,'BUDGET_EXHAUSTED','The original logical-task deadline has elapsed')
            try:
                # Native acquisition is outside the admission transaction.
                async with asyncio.timeout(deadline-time.time()):
                    await intelligence.refresh(self.row['provider'],self.home)
            except PeerError as usage_error:
                if usage_error.code=='BUDGET_EXHAUSTED':raise
            except Exception:pass
            with self.journal.transaction() as db:
                usage_assessment=intelligence.guard(self.row['provider'],self.home,self.spec)
                intelligence.admit_local_demand(self.lease,worker_id=self.row['worker_id'])
                db.execute('UPDATE workers SET lease_json=? WHERE worker_id=? AND generation=?',
                           (canonical(self.lease).decode(),self.row['worker_id'],self.row['generation']))
            if self.lease.get('live_budget_file'):
                from .budget import LiveBudget
                budget=LiveBudget(Path(self.lease['live_budget_file']))
                try:reservation=budget.reserve('worker '+self.row['worker_id']+' task '+task_id,tranche=self.lease.get('live_tranche','first_cross_provider_delegations'))
                except RuntimeError as error:raise PeerError('BUDGET_EXHAUSTED',str(error)) from error
                budget.admit(reservation)
            require(time.time()<deadline,'BUDGET_EXHAUSTED','The original logical-task deadline has elapsed')
            async with asyncio.timeout(deadline-time.time()):
                turn={'codex':self.codex_turn,'claude':self.claude_turn,'antigravity':self.antigravity_turn,'grok':self.grok_turn}[self.row['provider']]
                self.turn_task=asyncio.create_task(turn(text))
                result=await self.turn_task
        except BaseException as error:
            self.retire_after_task=True
            self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=?",(self.row['worker_id'],))
            await self.interrupt()
            code=classify(error.error) if isinstance(error,ProviderError) else 'BUDGET_EXHAUSTED' if isinstance(error,TimeoutError) else 'WORKER_CRASHED'
            result={'status':'canceled' if self.stopping else 'timed_out' if isinstance(error,TimeoutError) else 'failed','text':'','error':error.json() if isinstance(error,PeerError) else {'code':code,'message':str(error)[:1000]},'native_turn_ids':[self.turn] if self.turn else []}
            result['error']['may_have_received']=self.input_submitted or result['error'].get('may_have_received',False)
            if isinstance(error,PeerError) and isinstance(error.details,dict) and error.details.get('execution'):
                result['error']['execution']=error.details['execution']
            if self.stopping and self.shutdown_reason not in (None,'USER_CANCELED'):
                result.update(status='interrupted',error={'code':'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','cause':self.shutdown_reason})
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
        scoped_cancel=self.canceling_task==task_id and not self.stopping
        if (self.stopping or scoped_cancel) and result['status'] in ('failed','canceled'):
            result['status']='canceled';result['error']={'code':self.shutdown_reason or 'USER_CANCELED','native_error':result.get('error')}
        row=self.journal.one('SELECT peer_key FROM workers WHERE worker_id=?',(self.row['worker_id'],))
        findings,checks,report_limits=extract(result.get('text',''))
        record=dict(schema_version=1,group_id=self.row['group_id'],task_id=task_id,
            worker={'provider':self.row['provider'],'native_session_id':self.native,'title':self.row['role_title'],'peer_key':row['peer_key']},
            model_requested=self.row['model_requested'],model_observed=self.model_observed,
            reasoning_effort_requested=self.spec.get('reasoning_effort'),reasoning_effort_observed=None,
            status=result['status'],summary=result.get('text') or (result.get('error') or {}).get('message') or (result.get('error') or {}).get('code') or 'No final text was returned.',findings=findings,artifacts=[],
            verification={'tests_run':checks,'limitations':report_limits+['Worker-supplied checks are not independently verified.','Reasoning effort independently echoed by inference: unverified.']},
            started_at=started,completed_at=time.time(),native_turn_ids=result.get('native_turn_ids',[]),
            error=result.get('error'),usage=result.get('usage'),changes={'worktree':self.row['cwd'] if self.row['worktree_id'] else None,'commit':None,'patch_artifact':None})
        record['usage_intelligence']={'at_admission':usage_assessment,'account_usage_is_local_task_usage':False}
        if self.row['provider']=='grok':
            record['native_model_usage']=result.get('model_usage')
            record['native_request_id']=result.get('native_request_id')
            record['native_headless_usage']=result.get('native_headless_usage')
            if result.get('usage_error'):record['verification']['limitations'].append(result['usage_error'])
        if self.row['provider']=='codex':record['history_visibility']=dict(self.lease['history_visibility'])
        if self.row['worktree_id']:
            from .worktrees import git,changes
            from .models import ARTIFACT_MAX
            baseline=self.lease['baseline'];captured=changes(self.row['cwd'],baseline);patch=captured.pop('patch');delta=captured.pop('worker_delta',b'')
            record['changes'].update(worker_delta_artifact=None,worker_delta_empty=not delta and not captured.get('worker_delta_unavailable_reason'))
            record['changes'].update(captured)
            record['changes']['commit']=git(self.row['cwd'],'rev-parse','HEAD').stdout.decode().strip()
            record['changes']['baseline']=baseline
            if patch and len(patch)<=ARTIFACT_MAX:
                record['changes']['patch_artifact']=Artifacts(self.journal).put(patch,row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='text/x-diff')
            elif patch:record['changes']['patch_unavailable_reason']='Diff exceeds the local artifact limit; inspect the retained worktree.'
            if delta:
                record['changes']['worker_delta_artifact']=Artifacts(self.journal).put(delta,row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='text/x-diff')
                self.journal.event(record['changes']['worker_delta_artifact'],'worker-delta-artifact',{'input_tree':captured['worker_delta_base']})
        from .fallback import remember_limit,workspace_image,image_digest
        error=result.get('error') or {}
        remember_limit(self.journal,self.row,self.home,error)
        # Observability cannot discard an otherwise retained task result.
        try:
            from .usage.store import native_error
            from .usage.forecast import record_task
            native_error(self.journal,self.row,self.home,error)
            record_task(self.journal,self.row,self.spec,record,task_spec.get('attempt_index',0),self.home,
                before=usage_assessment.get('observation_id') if usage_assessment else None)
        except Exception as observation_error:
            record['verification']['limitations'].append('Local usage observation unavailable: '+type(observation_error).__name__)
        eligible=(result['status']=='failed' and error.get('code') in ('QUOTA_EXHAUSTED','AUTH_REQUIRED')
                  and error.get('execution',{}).get('definitive_no_execution') and not self.stopping and not scoped_cancel)
        workspace_clean=not self.row['worktree_id']
        if eligible and self.row['worktree_id'] and clean_input:
            try:workspace_clean=clean_input==image_digest(await asyncio.to_thread(workspace_image,self.row['cwd'])) and record['changes'].get('worker_delta_empty',False) and not record['changes'].get('excluded_sensitive_or_symlink_files')
            except (OSError,PeerError):workspace_clean=False
        record['fallback_eligibility']={'definitive_no_execution':bool(eligible),'workspace_unchanged':workspace_clean,
            'input_digest':clean_input,'remaining_task_turns':max(0,self.spec.get('max_task_turns',8)-self.tasks_started)}
        pending=eligible and workspace_clean and bool(self.lease.get('fallback_candidates'))
        if eligible and not workspace_clean:
            record['summary']+=' Fallback was not attempted: unchanged frozen workspace input could not be established.'
        artifact=Artifacts(self.journal).put(canonical(record),row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='application/json')
        with self.journal.transaction() as db:
            db.execute('INSERT INTO task_attempts VALUES(?,?,?,?,?)',(task_id,task_spec.get('attempt_index',0),self.row['worker_id'],result['status'].upper(),artifact))
            db.execute("UPDATE tasks SET state=?,result_artifact_id=?,native_turn_id=?,terminal_reason=? WHERE task_id=? AND generation=? AND state NOT IN ('COMPLETED','FAILED','CANCELED')",
                ('FALLBACK_PENDING' if pending else result['status'].upper(),None if pending else artifact,self.turn,json.dumps(result.get('error')),task_id,self.row['generation']))
            if pending:self.retire_after_task=True;self.fallback_pending=True
            db.execute("UPDATE workers SET state=? WHERE worker_id=?",('RELEASING' if self.retire_after_task else 'IDLE_GRACE',self.row['worker_id']))
        self.journal.event(task_id,'native-task-terminal',record)
        self.running_task=None;self.emit('fallback_pending' if pending else 'task_terminal',task_id=task_id,artifact_id=artifact,status=result['status'])
        self.turn_task=None
        if scoped_cancel:
            await self.close_provider()
            self.canceling_task=None
            if not self.stopping:
                self.retire_after_task=False
                if self.row['provider']=='codex':
                    self.lease['released_native']=True
                    await self.prepare()
                self.journal.db.execute("UPDATE workers SET state='IDLE_GRACE' WHERE worker_id=?",(self.row['worker_id'],))
        elif self.retire_after_task:await self.close()
        if reservation:budget.settle(reservation,observed_turns=1,native_id=self.native)

    async def cancel_task(self,task_id):
        if self.running_task!=task_id:return
        self.canceling_task=task_id
        await self.interrupt()
        turn=self.turn_task
        if self.running_task==task_id and turn and not turn.done():
            try:await asyncio.wait_for(asyncio.shield(turn),1)
            except asyncio.TimeoutError:
                if self.running_task==task_id:turn.cancel()
            except Exception:pass

    def ingested(self):
        task=self.journal.one('SELECT request_message_id FROM tasks WHERE task_id=?',(self.running_task,))
        if task and task['request_message_id']:
            from .routing import Router
            router=Router(self.journal,Registry(self.journal));outcome=router.outcome(task['request_message_id'])
            peer=Registry(self.journal).get(self.journal.one('SELECT peer_key FROM workers WHERE worker_id=?',(self.row['worker_id'],))['peer_key'])
            router.record(task['request_message_id'],outcome['attempt']['attempt_id'],{'stage':'INGESTED','evidence_kind':'owned-native-turn-start','native_turn_id':self.turn},peer)

    def check_submission(self):
        canceled=self.stopping or self.canceling_task is not None and self.canceling_task==self.running_task
        if canceled and self.provider and not self.client and self.provider.stdin:
            self.provider.stdin.close()
        require(not canceled,'USER_CANCELED','Task canceled before native input submission')

    async def codex_turn(self,text):
        self.check_submission()
        self.input_submitted=True
        response=await self.client.call('turn/start',{'threadId':self.native,'input':[{'type':'text','text':text}],
            'model':self.spec['model'],'effort':self.spec.get('reasoning_effort'),'approvalPolicy':'never'})
        self.turn=response['turn']['id'];self.ingested();self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
        if self.canceling_task==self.running_task:await self.interrupt()
        items={};usage=None
        while True:
            event=await asyncio.wait_for(self.client.next_event(),self.spec.get('wall_time_seconds',1800))
            params=event.get('params',{});method=event.get('method')
            if method=='account/rateLimits/updated':
                try:
                    from .usage.events import codex_update
                    codex_update(self.journal,self.home,params,self.lease.get('verified_runtime'))
                except Exception:pass
                continue
            if params.get('threadId')!=self.native:continue
            if method=='thread/tokenUsage/updated' and params.get('turnId')==self.turn:
                from .usage.events import codex_tokens
                usage=codex_tokens(params)
            if method=='item/completed' and params.get('turnId')==self.turn:
                item=params['item']
                if item.get('type')=='agentMessage':items[item['id']]=item
            if method=='turn/completed' and params['turn']['id']==self.turn:
                terminal=params['turn']
                for item in terminal.get('items',[]):
                    if item.get('type')=='agentMessage':items[item['id']]=item
                final=[i['text'] for i in items.values() if i.get('phase') in (None,'final_answer')]
                require(final or terminal['status']!='completed','PROTOCOL_ERROR','Terminal native turn has no final assistant result')
                error=terminal.get('error')
                return dict(status={'completed':'completed','interrupted':'canceled','failed':'failed'}.get(terminal['status'],'failed'),
                            text='\n'.join(final),native_turn_ids=[self.turn],usage=usage,error={'code':classify(error),'native_error':error} if error else None)

    async def claude_turn(self,text):
        self.check_submission()
        from .fallback import scope_limit
        suppressed=scope_limit(self.journal,self.row,self.home,self.running_task)
        if suppressed:return dict(status='failed',text=suppressed['message'],error=suppressed,native_turn_ids=[])
        if self.provider:await self.close_provider()
        args=claude_arguments(self.journal,self.binding,self.spec,self.expected_native,resume=self.tasks_started>1 or self.lease.get('released_native',False),home=self.home)
        env=apply_home('claude',self.home,launch_environment('claude'),explicit=self.lease.get('provider_home_explicit'));env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*args,cwd=self.row['cwd'],env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest();self.track_provider()
        self.check_submission()
        suppressed=scope_limit(self.journal,self.row,self.home,self.running_task)
        if suppressed:
            self.provider.stdin.close();await self.close_provider()
            return dict(status='failed',text=suppressed['message'],error=suppressed,native_turn_ids=[])
        self.input_submitted=True
        self.provider.stdin.write(text.encode());await self.provider.stdin.drain();self.provider.stdin.close()
        if self.canceling_task==self.running_task:await self.interrupt()
        errors=bytearray()
        async def stderr():
            while chunk:=await self.provider.stderr.read(4096):
                errors.extend(chunk);del errors[:-65536]
        from .claude_stream import Evidence
        evidence=Evidence()
        drain=asyncio.create_task(stderr());terminal=None
        try:
            while line:=await self.provider.stdout.readline():
                event=json.loads(line)
                synthetic=evidence.observe(event)
                emitted=event.get('session_id')
                if emitted:require(emitted==self.expected_native,'OWNERSHIP_CONFLICT','Claude emitted another native ID')
                if event.get('type')=='system' and event.get('subtype')=='init':
                    self.bind(event['session_id'],model=event.get('model'))
                    allowed={'Read','Glob','Grep','ToolSearch','ListMcpResourcesTool','ReadMcpResourceTool'}
                    if self.spec['permission_profile']=='workspace':allowed|={'Bash','Edit','Write'}
                    unsafe=[t for t in event.get('tools',[]) if t not in allowed and not t.startswith('mcp__nativepeers__')]
                    require(not unsafe,'PERMISSION_CAPABILITY_UNKNOWN','Claude worker inherited tools outside its profile',details=unsafe)
                    self.journal.event(self.row['worker_id'],'effective-tool-profile',{'tools':event.get('tools',[]),'model':self.model_observed,'profile':self.spec['permission_profile']})
                    self.turn=event.get('uuid') or new_id()
                    self.ingested()
                    self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
                if event.get('type')=='assistant':
                    if not synthetic and event.get('message',{}).get('model'):
                        self.bind(event['session_id'],model=event['message']['model'])
                if event.get('type')=='result':terminal=event
            await self.provider.wait();await drain
            require(terminal is not None or evidence.synthetic,'WORKER_CRASHED','Claude exited without a terminal result')
            self.journal.event(self.running_task,'provider-internal-rounds',{'assistant_events':evidence.rounds,'num_turns':(terminal or {}).get('num_turns')})
            error=evidence.failure(terminal,self.provider.returncode)
            return dict(status='failed' if error else 'completed',text=terminal.get('result','') if not error else error['message'],
                        native_turn_ids=[self.turn] if self.turn else [],error=error,usage=(terminal or {}).get('usage'))
        finally:
            if not drain.done():drain.cancel()
            await asyncio.gather(drain,return_exceptions=True)

    def verify_antigravity(self):
        from .antigravity_install import verify_worker_ready
        verify_worker_ready(self.journal,self.spec,self.lease['executable'],worker_id=self.row['worker_id'])

    def verify_grok(self):
        from .grok_install import verify_worker_ready
        verify_worker_ready(self.journal,self.spec,self.lease['executable'],worker_id=self.row['worker_id'])

    def grok_observation(self,launch):
        """The controller verifies the native birth, kernel and fresh hook record."""
        from .grok import summaries
        from .grok_hooks import kernel_sandbox
        self.refresh_provider_identity()
        process=launch['process'];state=owned_status(process);cached=False
        if state=='LIVE':
            try:
                current=identity(process['pid']).json()
                require(all(current[k]==process[k] for k in ('pid','start','uid'))
                        and current['executable']==str(Path(self.lease['executable']).resolve(strict=True)),
                        'OWNERSHIP_CONFLICT','Grok native process birth or executable changed during this launch')
                kernel=kernel_sandbox(dict(self.spec,state_root=str(self.journal.root),binding_path=str(self.binding)),pid=current['pid'])
                after=identity(current['pid']).json()
                require(after==current,'OWNERSHIP_CONFLICT','Grok native process changed during its kernel permission check')
                require(kernel.get('verified') is True and kernel.get('profile')==self.spec['permission_profile'] and kernel.get('checked_pid')==current['pid'],
                        'PERMISSION_CAPABILITY_UNKNOWN','The controller could not prove Grok native kernel write restrictions')
                launch['kernel']=kernel;launch['checked_process']=current
            except ProcessLookupError:state='DEAD'
        if state!='LIVE':
            require(state=='DEAD' and launch.get('kernel') and launch.get('checked_process')
                    and all(launch['checked_process'][k]==process[k] for k in ('pid','start','uid')),
                    'PERMISSION_CAPABILITY_UNKNOWN','Grok exited before its exact native process kernel permissions were proved')
            require(self.provider.returncode is not None,'CALLER_NOT_READY','Waiting for the verified Grok native process to be reaped')
            kernel=launch['kernel'];current=launch['checked_process'];cached=True
        native=summaries(native_id=self.expected_native,home=self.home)
        require(native,'CALLER_NOT_READY','Grok has not persisted its owned session summary yet')
        require(len(native)==1 and Path(native[0]['cwd']).resolve()==Path(self.row['cwd']).resolve(),
                'OWNERSHIP_CONFLICT','Grok did not persist the exact owned native session and workspace')
        require(native[0].get('model'),'CALLER_NOT_READY','Grok exact native summary has not reported its selected model yet')
        require(native[0].get('sandbox_profile')=='nativepeers-'+self.spec['permission_profile'],
                'PERMISSION_CAPABILITY_UNKNOWN','Grok native session persisted a different sandbox profile')
        hook=launch['hook'].read()
        require(hook,'CALLER_NOT_READY','A fresh successful owned Grok SessionStart hook record has not been flushed yet')
        self.journal.event(self.row['worker_id'],'grok-kernel-check',dict(kernel,native_id=self.expected_native,process=current,
            evidence_kind='parent-native-kernel-check',reused_after_native_exit=cached))
        observed=dict(native_id=self.expected_native,model=native[0]['model'],event='SessionStart',process=current,
            evidence_kind='parent-native-hook-execution-read',hook_execution=hook)
        self.journal.event(self.row['worker_id'],'native-hook-observed',observed)
        self.lease['grok_native_created']=True
        self.bind(self.expected_native,model=native[0]['model'])
        return observed

    async def grok_turn(self,text):
        from .grok_worker import arguments,Stream,HookEvidence
        from .usage.grok_native import session_usage
        self.check_submission()
        if self.provider:await self.close_provider()
        self.verify_grok()
        self.expected_native=self.native or new_id()
        spec=dict(self.spec,native_session_id=self.expected_native)
        env=apply_home('grok',self.home,launch_environment('grok'))
        env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        resume_id=self.native if self.lease.get('grok_native_created') or self.lease.get('released_native') else None
        previous_usage_turn=0
        if resume_id:
            try:previous_usage_turn=(await session_usage(self.journal,self.home,resume_id,self.lease['executable'],self.row['cwd']))['native_turn_number']
            except Exception:previous_usage_turn=None
        hook=HookEvidence(self.home,self.row['cwd'],self.expected_native)
        self.provider=await asyncio.create_subprocess_exec(str(Path(self.lease['executable']).resolve(strict=True)),*arguments(spec,resume_id),
            cwd=self.row['cwd'],env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest();self.track_provider()
        launch=dict(process=dict(self.provider_identity),hook=hook)
        self.check_submission();self.input_submitted=True
        self.provider.stdin.write(text.encode());await self.provider.stdin.drain();self.provider.stdin.close()
        if self.canceling_task==self.running_task:await self.interrupt()
        errors=bytearray()
        async def stderr():
            while chunk:=await self.provider.stderr.read(4096):
                errors.extend(chunk);del errors[:-65536]
        drain=asyncio.create_task(stderr());stream=Stream(self.expected_native,self.spec['permission_profile'])
        try:
            while line:=await self.provider.stdout.readline():
                kind=stream.observe(json.loads(line))
                if kind!='error' and not self.turn:
                    proof_deadline=time.monotonic()+min(2,self.spec.get('wall_time_seconds',1800))
                    while True:
                        try:self.grok_observation(launch);break
                        except PeerError as error:
                            if error.code!='CALLER_NOT_READY':raise
                            require(self.provider.returncode is None and time.monotonic()<proof_deadline,
                                'PERMISSION_CAPABILITY_UNKNOWN',error.message)
                            await asyncio.sleep(.02)
                    self.turn=new_id();self.ingested()
                    self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
                if kind=='available_commands':
                    self.journal.event(self.row['worker_id'],'effective-tool-profile',{'tools':stream.tools,'advertised_catalog':stream.advertised_tools,
                        'denied_advertised_tools':stream.denied_advertised_tools,'profile':self.spec['permission_profile'],'sandbox':'nativepeers-'+self.spec['permission_profile'],
                        'enforcement':'Fixed built-in allowlist, native MCPTool(*) denial, and the exact owned PreToolUse hook with inherited kernel checks; advertised MCP names are not grants.'})
            await self.provider.wait();await drain
            result=stream.result(self.provider.returncode)
            if result['status']=='completed':
                self.grok_observation(launch)
                require(stream.tools is not None,'PERMISSION_CAPABILITY_UNKNOWN','Grok never reported its effective tool inventory')
            self.journal.event(self.running_task,'provider-internal-rounds',{'num_turns':result.pop('native_internal_rounds'),'counter_scope':'native prompt model rounds; logical worker task starts are separate'})
            result['native_turn_ids']=[self.turn] if self.turn else []
            result['native_headless_usage']=result.pop('usage')
            try:
                require(type(previous_usage_turn) is int and previous_usage_turn>=0,
                        'SOURCE_SCHEMA_MISMATCH','Grok prior owned turn usage counter was unavailable')
                expected_usage_turn=previous_usage_turn+1
                usage=await session_usage(self.journal,self.home,self.expected_native,self.lease['executable'],self.row['cwd'],turn=expected_usage_turn)
                require(usage.get('native_turn_number')==expected_usage_turn,
                        'SOURCE_SCHEMA_MISMATCH','Grok persisted usage did not identify a new turn after this owned input')
                result['usage']=usage
            except (Exception,asyncio.CancelledError) as error:
                # Only optional telemetry follows the validated terminal result.
                # A deadline here cannot erase the completed native response.
                code=error.code if isinstance(error,PeerError) else type(error).__name__
                result['usage']={'status':'UNKNOWN','source':'grok-usage-session','counter_scope':'unknown','error':code}
                result['usage_error']='Grok native persisted task usage unavailable: '+code
            return result
        finally:
            if not drain.done():drain.cancel()
            await asyncio.gather(drain,return_exceptions=True)

    async def antigravity_turn(self,text):
        from .antigravity import arguments
        self.check_submission()
        if self.provider:await self.close_provider()
        env=launch_environment('antigravity')
        env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.verify_antigravity()
        # A serialized follow-up launches a fresh process. Prior observations,
        # including those for the same worker/native UUID, cannot certify it.
        launch_sequence=self.journal.one('SELECT COALESCE(MAX(sequence),0) AS sequence FROM events')['sequence']
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*arguments(self.spec,self.native),
            cwd=self.row['cwd'],env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest();self.track_provider()
        self.check_submission()
        self.input_submitted=True
        self.provider.stdin.write(canonical({'event':'user','message':{'content':text}})+b'\n')
        await self.provider.stdin.drain();self.provider.stdin.close()
        errors=bytearray()
        async def stderr():
            while chunk:=await self.provider.stderr.read(4096):
                errors.extend(chunk);del errors[:-65536]
        drain=asyncio.create_task(stderr());terminal=None
        try:
            while line:=await self.provider.stdout.readline():
                event=json.loads(line);body=event.get('result',event.get('step_update',{}))
                native=event.get('conversation_id') or body.get('conversation_id')
                if native:
                    require(self.expected_native is None or native==self.expected_native,'OWNERSHIP_CONFLICT','Antigravity emitted a different owned native ID')
                if event.get('event')=='init':
                    self.bind(native)
                    tools=event.get('init',{}).get('tools',[])
                    self.turn=new_id();self.ingested()
                    self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
                    self.journal.event(self.row['worker_id'],'effective-tool-profile',{'advertised_catalog':tools,'profile':self.spec['permission_profile'],
                        'enforcement':'Native PreToolUse hook denies every tool outside the fixed owned worker profile; the advertised catalog is not treated as an effective grant.'})
                if event.get('event')=='result':terminal=event['result']
            await self.provider.wait();await drain
            require(terminal is not None,'WORKER_CRASHED','Antigravity exited without a terminal result',details=errors.decode(errors='replace')[-1000:])
            observations=self.journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-hook-observed' AND sequence>? AND json_extract(payload_json,'$.native_id')=? ORDER BY sequence DESC",(self.row['worker_id'],launch_sequence,self.native))
            observed=next((payload for row in observations if (payload:=json.loads(row['payload_json'])).get('process')==self.provider_identity),None)
            require(observed,'PERMISSION_CAPABILITY_UNKNOWN','No verified native Antigravity worker hook observed this turn process after launch')
            model=observed.get('model')
            if model:self.bind(self.native,model=model)
            status=terminal.get('status')
            self.journal.event(self.running_task,'provider-internal-rounds',{'num_turns':terminal.get('num_turns'),'counter_scope':'native conversation cumulative; worker inputs are counted separately'})
            return {'status':'completed' if status=='SUCCESS' and self.provider.returncode==0 else 'canceled' if status in ('CANCELED','INTERRUPTED') else 'failed',
                    'text':terminal.get('response',''),'native_turn_ids':[self.turn] if self.turn else [],'usage':terminal.get('usage'),
                    'error':None if status=='SUCCESS' and self.provider.returncode==0 else {'code':classify(terminal),'native_status':status,'message':terminal.get('error')}}
        finally:
            if not drain.done():drain.cancel()
            await asyncio.gather(drain,return_exceptions=True)

    async def interrupt(self):
        if not self.provider or self.provider.returncode is not None:return
        if self.client:
            if self.turn:
                try:await self.client.call('turn/interrupt',{'threadId':self.native,'turnId':self.turn},timeout=5)
                except Exception:pass
        elif self.provider_identity:
            signal_captured(self.provider_identity,signal.SIGINT)

    def app_server_ownership(self, client):
        self.provider = client.process
        self.provider_identity = client.owner
        self.provider_descendants = dict(client.descendants)
        self.provider_cleanup = client.cleanup
        self.manifest()

    def refresh_provider_identity(self):
        for pid,record in list(self.provider_descendants.items()):
            current=refresh_captured(record)
            if current:self.provider_descendants[pid]=current
        if self.provider and self.provider.returncode is None and self.provider_identity and self.provider_identity['pid']!=os.getpid():
            try:
                current=identity(self.provider.pid).json()
                if current['ppid']==os.getpid() and all(current[k]==self.provider_identity[k] for k in ('pid','start','uid')):
                    self.provider_identity=current
            except ProcessLookupError:pass

    def track_provider(self):
        self.provider_cleanup=None
        if self.provider_identity['pid']==os.getpid():return
        async def monitor():
            while self.provider is not None:
                try:
                    self.refresh_provider_identity()
                    captured=await asyncio.to_thread(descendants,[self.provider_identity,*self.provider_descendants.values()])
                    if captured:
                        self.provider_descendants.update({r['pid']:r for r in captured});self.manifest()
                except (OSError,TimeoutError):pass
                await asyncio.sleep(.05)
        self.provider_monitor=asyncio.create_task(monitor())

    async def close_provider(self):
        if self.client:
            await self.interrupt()
            try:self.provider_cleanup=await self.client.close()
            except PeerError:
                self.provider_cleanup=getattr(self.client,'cleanup',None) or {'state':'UNCERTAIN','output_eof':False}
                self.manifest();raise
        elif self.provider:
            process=self.provider
            # Capture before interruption; after reparenting ancestry alone cannot
            # establish ownership of a process that was never observed.
            try:
                self.refresh_provider_identity()
                captured=await asyncio.to_thread(descendants,[self.provider_identity,*self.provider_descendants.values()])
                self.provider_descendants.update({r['pid']:r for r in captured})
            except (OSError,TimeoutError):pass
            if process.stdin:process.stdin.close()
            if self.provider_monitor:
                self.provider_monitor.cancel();await asyncio.gather(self.provider_monitor,return_exceptions=True)
                self.provider_monitor=None
            for sig,grace in ((signal.SIGTERM,.5),(signal.SIGKILL,1)):
                for record in [*reversed(list(self.provider_descendants.values())),self.provider_identity if process.returncode is None else None]:
                    if record and record['pid']!=os.getpid():
                        try:signal_captured(record,sig)
                        except ProcessLookupError:pass
                deadline=time.monotonic()+grace
                while time.monotonic()<deadline and (process.returncode is None or any(owned_status(r)!='DEAD' for r in self.provider_descendants.values())):
                    await asyncio.sleep(.02)
            async def drain(stream):
                if stream:
                    while await stream.read(65536):pass
            readers=[asyncio.create_task(drain(stream)) for stream in (process.stdout,process.stderr)]
            _,pending=await asyncio.wait(readers,timeout=1)
            failures=[r for r in readers if r.done() and not r.cancelled() and r.exception()]
            for reader in pending:reader.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            uncertain=process.returncode is None or bool(pending or failures) or any(owned_status(r)!='DEAD' for r in self.provider_descendants.values())
            self.provider_cleanup={'state':'UNCERTAIN' if uncertain else 'CLOSED','output_eof':not bool(pending or failures),
                                   'provider_exited':process.returncode is not None,'captured_descendants':len(self.provider_descendants)}
            self.manifest()
            if process.returncode is not None and hasattr(process,'_transport'):process._transport.close()
            require(not uncertain,'CLEANUP_UNCERTAIN','Owned native process/output closure is unproved; capacity remains reserved',details=self.provider_cleanup)
        if not self.provider and not self.client and self.provider_cleanup is None:
            self.provider_cleanup={'state':'CLOSED','output_eof':True,'provider_exited':True,'native_process_created':False}
        self.client=None;self.provider=None;self.provider_identity=None;self.provider_descendants={};self.turn=None
        self.manifest()

    async def close(self):
        self.stopping=True;await self.close_provider()
        from .recovery import settle_stopped_tasks
        with self.journal.transaction() as db:
            if not self.fallback_pending or self.shutdown_reason not in (None,'WORKER_RELEASED'):
                settle_stopped_tasks(db,self.row['worker_id'],self.row['generation'],self.shutdown_reason or 'WORKER_RELEASED')
            else:
                # A clean handoff protects only its logical task, never queued
                # peer follow-ups pinned to the old native identity.
                db.execute("UPDATE tasks SET state='CANCELED',terminal_reason='WORKER_REPLACED_BEFORE_START' WHERE worker_id=? AND generation=? AND state='QUEUED'",(self.row['worker_id'],self.row['generation']))
            db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=? AND generation=?",(self.row['worker_id'],self.row['generation']))
        self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE pid=? AND process_start=?",(self.owner['pid'],self.owner['start']))
        self.emit('released',native_id=self.native)


async def run(worker_id,lease_fd):
    journal=Journal();worker=Worker(journal,worker_id)
    main_task=asyncio.current_task()
    async def commands():
        while line:=await asyncio.to_thread(sys.stdin.buffer.readline,65537):
            require(len(line)<=65536,'PAYLOAD_TOO_LARGE','Worker control request too large')
            event=json.loads(line)
            if event['operation']=='cancel':
                worker.shutdown_reason=event.get('reason','USER_CANCELED');worker.stopping=True;await worker.interrupt()
            elif event['operation']=='cancel-task':await worker.cancel_task(event['task_id'])
            elif event['operation']=='release':
                worker.shutdown_reason=worker.shutdown_reason or event.get('reason','WORKER_RELEASED')
                worker.stopping=True;await worker.interrupt();await worker.queue.put(None);return
            elif event['operation']=='task':await worker.queue.put(event)
        worker.shutdown_reason=worker.shutdown_reason or 'CONTROL_STREAM_CLOSED'
        worker.stopping=True;await worker.interrupt();await worker.queue.put(None)
    async def lease():
        await asyncio.to_thread(os.read,lease_fd,1);os.close(lease_fd)
        worker.shutdown_reason=worker.shutdown_reason or 'SUPERVISOR_LEASE_LOST'
        worker.stopping=True;await worker.interrupt()
        main_task.cancel()
    readers=[]
    try:
        readers=[asyncio.create_task(commands()),asyncio.create_task(lease())]
        await worker.prepare()
        while (event:=await worker.queue.get()) is not None:
            if worker.stopping:break
            await worker.task(event['task_id'],event['text'])
            if worker.retire_after_task:break
    except Exception as error:
        worker.emit('failed',error=error.json() if isinstance(error,PeerError) else {'code':'WORKER_CRASHED','message':str(error)[:1000]})
    finally:
        for reader in readers:reader.cancel()
        await worker.close()
        journal.close()
        # A blocked stdin thread must not keep a finite controller alive after lease EOF.
        os._exit(0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker-id',required=True);parser.add_argument('--lease-fd',type=int,required=True);args=parser.parse_args()
    asyncio.run(run(args.worker_id,args.lease_fd))


if __name__=='__main__':main()
