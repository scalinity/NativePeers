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
from .launch import launch_environment,recorded_home,home_environment
from .models import new_id
from .process import identity,matches,signal_owned
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


class Worker:
    def __init__(self,journal,worker_id):
        self.journal=journal;self.row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        self.lease=json.loads(self.row['lease_json']);self.spec=self.lease['spec'];self.binding=Path(self.lease['binding'])
        self.spec['model']=model_selection(self.row)
        self.home=recorded_home(journal,self.row);self.lease['provider_home']=str(self.home)
        journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(self.lease).decode(),worker_id))
        self.queue=asyncio.Queue(maxsize=32);self.owner=identity(os.getpid()).json()
        self.provider=None;self.provider_identity=None;self.client=None;self.native=self.row['native_id'];self.turn=None
        self.stopping=False;self.shutdown_reason=None;self.running_task=None;self.tasks_started=0;self.retire_after_task=False
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
        atomic_json(self.journal.root/'workers'/(self.row['worker_id']+'.json'),dict(owner=self.owner,
            native_process=self.provider_identity,native_id=self.native,worker_id=self.row['worker_id'],
            group_id=self.row['group_id'],generation=self.row['generation']))

    def bind(self,native_id,*,model=None):
        if self.expected_native:
            require(native_id==self.expected_native,'OWNERSHIP_CONFLICT','Provider emitted a different native session ID')
        require(self.native is None or self.native==native_id,'OWNERSHIP_CONFLICT','Worker identity cannot change')
        if model:
            requested=self.spec['model']
            if self.row['provider']=='antigravity' and model==self.spec.get('model_display_name'):model=requested
            if self.row['provider']=='claude':model=model.removesuffix('[1m]')
            expected=(self.model_observed or requested).removesuffix('[1m]') if self.row['provider']=='claude' else requested
            if self.row['provider'] in ('codex','antigravity') or expected.startswith('claude-'):
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
            owner_kind='owned-worker-controller',policy=self.lease['grants'])
        self.journal.db.execute('UPDATE workers SET peer_key=?,native_id=?,model_observed=? WHERE worker_id=?',
            (peer['handle'],native_id,self.model_observed,self.row['worker_id']))
        atomic_json(self.binding,dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],
            worker_id=self.row['worker_id'],owner=self.owner,expires_at=self.lease['expires_at'],grants=self.lease['grants']))
        self.manifest();self.emit('bound',native_id=native_id,peer_key=peer['handle'])

    async def prepare(self):
        self.bootstrap()
        if self.row['provider']=='codex':
            executable=Path(self.lease['executable']).resolve(strict=True)
            verify(self.journal,'codex',executable)
            config=codex_config(self.journal,self.binding,self.spec['permission_profile'],home=self.home)
            self.client=AppServer(executable,config_arguments(config),cwd=Path(self.row['cwd']),
                environment={**home_environment('codex',self.home),'NPEER_HOME':str(self.journal.root),'NPEER_BOOTSTRAP':str(self.binding),'PYTHONPATH':str(ROOT)})
            await self.client.start();self.provider=self.client.process;self.provider_identity=identity(self.provider.pid).json();self.manifest()
            catalog=(await self.client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            model=next((m for m in catalog if m['model']==self.spec['model']),None)
            require(model,'MODEL_UNAVAILABLE','The immutable worker executable does not advertise the exact model')
            require(self.spec.get('reasoning_effort') in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']],
                    'EFFORT_UNSUPPORTED','The immutable worker executable does not advertise the exact effort')
            if self.native:
                require(self.lease.get('released_native',False),'OWNERSHIP_CONFLICT','Existing native worker was not released')
                response=await self.client.call('thread/resume',{'threadId':self.native,'cwd':self.row['cwd'],'model':self.spec['model'],'approvalPolicy':'never','sandbox':'read-only' if self.spec['permission_profile']=='analysis' else 'workspace-write'})
            else:
                response=await self.client.call('thread/start',{'cwd':self.row['cwd'],'model':self.spec['model'],'approvalPolicy':'never',
                    'sandbox':'read-only' if self.spec['permission_profile']=='analysis' else 'workspace-write',
                    'ephemeral':False,'allowProviderModelFallback':False})
            self.bind(response['thread']['id'],model=response.get('model'))
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
        else:
            # Preflight validates argv/environment. Native initialization begins only after first input.
            require(Path(self.lease['executable']).is_file(),'AUTH_REQUIRED','Installed Claude executable is unavailable')
            verify(self.journal,'claude',self.lease['executable'])
            launch_environment('claude');claude_arguments(self.journal,self.binding,self.spec,self.expected_native,home=self.home)
        self.journal.db.execute("UPDATE workers SET state='READY' WHERE worker_id=?",(self.row['worker_id'],))
        self.emit('ready',native_id=self.native if self.row['provider']=='codex' else None)

    async def task(self,task_id,text):
        claimed=self.journal.db.execute("UPDATE tasks SET state='RUNNING' WHERE task_id=? AND worker_id=? AND generation=? AND state='QUEUED'",(task_id,self.row['worker_id'],self.row['generation'])).rowcount
        if not claimed:return
        self.tasks_started+=1
        require(self.tasks_started<=self.spec.get('max_task_turns',8),'BUDGET_EXHAUSTED','Worker task-turn limit reached')
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
        try:
            if self.row['worktree_id'] and self.lease.get('fallback_candidates'):
                from .fallback import workspace_image,image_digest
                try:clean_input=image_digest(await asyncio.to_thread(workspace_image,self.row['cwd']))
                except (PeerError,OSError):pass
            if self.lease.get('live_budget_file'):
                from .budget import LiveBudget
                budget=LiveBudget(Path(self.lease['live_budget_file']))
                try:reservation=budget.reserve('worker '+self.row['worker_id']+' task '+task_id,tranche=self.lease.get('live_tranche','first_cross_provider_delegations'))
                except RuntimeError as error:raise PeerError('BUDGET_EXHAUSTED',str(error)) from error
                budget.admit(reservation)
            require(time.time()<deadline,'BUDGET_EXHAUSTED','The original logical-task deadline has elapsed')
            async with asyncio.timeout(deadline-time.time()):
                turn={'codex':self.codex_turn,'claude':self.claude_turn,'antigravity':self.antigravity_turn}[self.row['provider']]
                self.turn_task=asyncio.create_task(turn(text))
                result=await self.turn_task
        except BaseException as error:
            self.retire_after_task=True
            self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=?",(self.row['worker_id'],))
            await self.interrupt()
            code=classify(error.error) if isinstance(error,ProviderError) else 'BUDGET_EXHAUSTED' if isinstance(error,TimeoutError) else 'WORKER_CRASHED'
            result={'status':'canceled' if self.stopping else 'timed_out' if isinstance(error,TimeoutError) else 'failed','text':'','error':error.json() if isinstance(error,PeerError) else {'code':code,'message':str(error)[:1000]},'native_turn_ids':[self.turn] if self.turn else []}
            result['error']['may_have_received']=self.input_submitted or result['error'].get('may_have_received',False)
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
        eligible=(result['status']=='failed' and error.get('code') in ('QUOTA_EXHAUSTED','AUTH_REQUIRED')
                  and error.get('execution',{}).get('definitive_no_execution') and not self.stopping and not scoped_cancel)
        workspace_clean=not self.row['worktree_id']
        if eligible and self.row['worktree_id'] and clean_input:
            try:workspace_clean=clean_input==image_digest(await asyncio.to_thread(workspace_image,self.row['cwd'])) and record['changes'].get('worker_delta_empty',False) and not record['changes'].get('excluded_sensitive_or_symlink_files')
            except (OSError,PeerError):workspace_clean=False
        record['fallback_eligibility']={'definitive_no_execution':bool(eligible),'workspace_unchanged':workspace_clean,
            'input_digest':clean_input,'remaining_task_turns':self.spec.get('max_task_turns',8)-self.tasks_started}
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

    async def codex_turn(self,text):
        require(not self.stopping,'USER_CANCELED','Worker is stopping')
        self.input_submitted=True
        response=await self.client.call('turn/start',{'threadId':self.native,'input':[{'type':'text','text':text}],
            'model':self.spec['model'],'effort':self.spec.get('reasoning_effort'),'approvalPolicy':'never'})
        self.turn=response['turn']['id'];self.ingested();self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
        if self.canceling_task==self.running_task:await self.interrupt()
        items={}
        while True:
            event=await asyncio.wait_for(self.client.next_event(),self.spec.get('wall_time_seconds',1800))
            params=event.get('params',{});method=event.get('method')
            if params.get('threadId')!=self.native:continue
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
                            text='\n'.join(final),native_turn_ids=[self.turn],error={'code':classify(error),'native_error':error} if error else None)

    async def claude_turn(self,text):
        from .fallback import scope_limit
        suppressed=scope_limit(self.journal,self.row,self.home,self.running_task)
        if suppressed:return dict(status='failed',text=suppressed['message'],error=suppressed,native_turn_ids=[])
        args=claude_arguments(self.journal,self.binding,self.spec,self.expected_native,resume=self.tasks_started>1 or self.lease.get('released_native',False),home=self.home)
        env=launch_environment('claude');env.update(home_environment('claude',self.home));env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*args,cwd=self.row['cwd'],env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest()
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

    def verify_antigravity(self):
        from .antigravity_install import verify_worker_ready
        verify_worker_ready(self.journal,self.spec,self.lease['executable'])

    async def antigravity_turn(self,text):
        from .antigravity import arguments
        require(not self.stopping,'USER_CANCELED','Worker is stopping')
        env=launch_environment('antigravity')
        env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.verify_antigravity()
        # A serialized follow-up launches a fresh process. Prior observations,
        # including those for the same worker/native UUID, cannot certify it.
        launch_sequence=self.journal.one('SELECT COALESCE(MAX(sequence),0) AS sequence FROM events')['sequence']
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*arguments(self.spec,self.native),
            cwd=self.row['cwd'],env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest()
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

    async def interrupt(self):
        if not self.provider or self.provider.returncode is not None:return
        if self.client:
            if self.turn:
                try:await self.client.call('turn/interrupt',{'threadId':self.native,'turnId':self.turn},timeout=5)
                except Exception:pass
        elif self.provider_identity:
            signal_owned(self.provider_identity,signal.SIGINT,group=True)

    async def close_provider(self):
        await self.interrupt()
        if self.client:await self.client.close()
        elif self.provider and self.provider.returncode is None:
            try:await asyncio.wait_for(self.provider.wait(),15)
            except asyncio.TimeoutError:
                signal_owned(self.provider_identity,signal.SIGTERM,group=True)
                try:await asyncio.wait_for(self.provider.wait(),5)
                except asyncio.TimeoutError:
                    signal_owned(self.provider_identity,signal.SIGKILL,group=True);await self.provider.wait()
        self.client=None;self.provider=None;self.provider_identity=None;self.turn=None
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
                worker.stopping=True;await worker.queue.put(None);return
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
