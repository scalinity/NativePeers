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


class Worker:
    def __init__(self,journal,worker_id):
        self.journal=journal;self.row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        self.lease=json.loads(self.row['lease_json']);self.spec=self.lease['spec'];self.binding=Path(self.lease['binding'])
        self.home=recorded_home(journal,self.row);self.lease['provider_home']=str(self.home)
        journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(self.lease).decode(),worker_id))
        self.queue=asyncio.Queue(maxsize=32);self.owner=identity(os.getpid()).json()
        self.provider=None;self.provider_identity=None;self.client=None;self.native=self.row['native_id'];self.turn=None
        self.stopping=False;self.shutdown_reason=None;self.running_task=None;self.tasks_started=0;self.retire_after_task=False
        self.canceling_task=None;self.turn_task=None
        self.expected_native=self.native;self.model_observed=self.row['model_observed']

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
            if self.row['provider'] in ('codex','antigravity') or requested.startswith('claude-'):
                require(model==requested.removesuffix('[1m]'),'MODEL_UNAVAILABLE','Provider selected a different model than the exact requested model',
                        details={'requested':requested,'observed':model})
            self.model_observed=model
            self.spec['model']=model
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
        elif self.row['provider']=='antigravity':
            from .antigravity_install import worker_profile,verify_tool_gate
            verify(self.journal,'antigravity',self.lease['executable'])
            path=Path.home()/'.gemini/config/agents'/('nativepeers-'+self.spec['permission_profile']+'.md')
            require(path.is_file() and path.read_text()==worker_profile(self.spec['permission_profile']),
                    'PERMISSION_CAPABILITY_UNKNOWN','The Antigravity worker profile is missing or modified; run npeer install --antigravity')
            verify_tool_gate(self.spec)
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
        self.running_task=task_id;started=time.time();self.turn=None
        text='NativePeers assigned worker title: '+self.row['role_title']+'. Provider: '+self.row['provider']+'.\n'+text+PROMPT
        self.journal.db.execute("UPDATE workers SET state='RUNNING' WHERE worker_id=?",(self.row['worker_id'],))
        reservation=None
        budget=None
        try:
            if self.lease.get('live_budget_file'):
                from .budget import LiveBudget
                budget=LiveBudget(Path(self.lease['live_budget_file']))
                try:reservation=budget.reserve('worker '+self.row['worker_id']+' task '+task_id,tranche=self.lease.get('live_tranche','first_cross_provider_delegations'))
                except RuntimeError as error:raise PeerError('BUDGET_EXHAUSTED',str(error)) from error
                budget.admit(reservation)
            async with asyncio.timeout(self.spec.get('wall_time_seconds',1800)):
                turn={'codex':self.codex_turn,'claude':self.claude_turn,'antigravity':self.antigravity_turn}[self.row['provider']]
                self.turn_task=asyncio.create_task(turn(text))
                result=await self.turn_task
        except BaseException as error:
            self.retire_after_task=True
            self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=?",(self.row['worker_id'],))
            await self.interrupt()
            code=classify(error.error) if isinstance(error,ProviderError) else 'BUDGET_EXHAUSTED' if isinstance(error,TimeoutError) else 'WORKER_CRASHED'
            result={'status':'canceled' if self.stopping else 'timed_out' if isinstance(error,TimeoutError) else 'failed','text':'','error':error.json() if isinstance(error,PeerError) else {'code':code,'message':str(error)[:1000]},'native_turn_ids':[self.turn] if self.turn else []}
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
            status=result['status'],summary=result.get('text',''),findings=findings,artifacts=[],
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
        artifact=Artifacts(self.journal).put(canonical(record),row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='application/json')
        with self.journal.transaction() as db:
            db.execute("UPDATE tasks SET state=?,result_artifact_id=?,native_turn_id=?,terminal_reason=? WHERE task_id=? AND generation=? AND state NOT IN ('COMPLETED','FAILED','CANCELED')",
                (result['status'].upper(),artifact,self.turn,json.dumps(result.get('error')),task_id,self.row['generation']))
            db.execute("UPDATE workers SET state=? WHERE worker_id=?",('RELEASING' if self.retire_after_task else 'IDLE_GRACE',self.row['worker_id']))
        self.journal.event(task_id,'native-task-terminal',record)
        self.running_task=None;self.emit('task_terminal',task_id=task_id,artifact_id=artifact,status=result['status'])
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
        args=claude_arguments(self.journal,self.binding,self.spec,self.expected_native,resume=self.tasks_started>1 or self.lease.get('released_native',False),home=self.home)
        env=launch_environment('claude');env.update(home_environment('claude',self.home));env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*args,cwd=self.row['cwd'],env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest()
        self.provider.stdin.write(text.encode());await self.provider.stdin.drain();self.provider.stdin.close()
        if self.canceling_task==self.running_task:await self.interrupt()
        errors=bytearray()
        async def stderr():
            while chunk:=await self.provider.stderr.read(4096):
                errors.extend(chunk);del errors[:-65536]
        drain=asyncio.create_task(stderr());terminal=None;assistant_rounds=0
        try:
            while line:=await self.provider.stdout.readline():
                event=json.loads(line)
                emitted=event.get('session_id')
                if emitted:require(emitted==self.expected_native,'OWNERSHIP_CONFLICT','Claude emitted another native ID')
                if event.get('type')=='system' and event.get('subtype')=='init':
                    self.bind(event['session_id'],model=event.get('model'))
                    if self.model_observed:self.spec['model']=self.model_observed
                    allowed={'Read','Glob','Grep','ToolSearch','ListMcpResourcesTool','ReadMcpResourceTool'}
                    if self.spec['permission_profile']=='workspace':allowed|={'Bash','Edit','Write'}
                    unsafe=[t for t in event.get('tools',[]) if t not in allowed and not t.startswith('mcp__nativepeers__')]
                    require(not unsafe,'PERMISSION_CAPABILITY_UNKNOWN','Claude worker inherited tools outside its profile',details=unsafe)
                    self.journal.event(self.row['worker_id'],'effective-tool-profile',{'tools':event.get('tools',[]),'model':self.model_observed,'profile':self.spec['permission_profile']})
                    self.turn=event.get('uuid') or new_id()
                    self.ingested()
                    self.journal.event(self.running_task,'native-task-started',{'native_id':self.native,'turn_id':self.turn,'at':time.time()})
                if event.get('type')=='assistant':
                    assistant_rounds+=1
                    if event.get('message',{}).get('model'):
                        self.bind(event['session_id'],model=event['message']['model'])
                if event.get('type')=='result':terminal=event
            await self.provider.wait();await drain
            require(terminal is not None,'WORKER_CRASHED','Claude exited without a terminal result')
            self.journal.event(self.running_task,'provider-internal-rounds',{'assistant_events':assistant_rounds,'num_turns':terminal.get('num_turns')})
            return dict(status='completed' if terminal.get('subtype')=='success' and not terminal.get('is_error') and self.provider.returncode==0 else 'failed',
                        text=terminal.get('result',''),native_turn_ids=[self.turn] if self.turn else [],
                        error=None if terminal.get('subtype')=='success' else {'code':classify(terminal),'subtype':terminal.get('subtype'),'errors':terminal.get('errors',[])},usage=terminal.get('usage'))
        finally:
            if not drain.done():drain.cancel()

    async def antigravity_turn(self,text):
        from .antigravity import arguments
        env=launch_environment('antigravity')
        env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*arguments(self.spec,self.native),
            cwd=self.row['cwd'],env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest()
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
            observed=self.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-hook-observed' AND json_extract(payload_json,'$.native_id')=? ORDER BY sequence DESC LIMIT 1",(self.row['worker_id'],self.native))
            require(observed,'PERMISSION_CAPABILITY_UNKNOWN','No verified native Antigravity worker hook observed this conversation')
            if observed:
                model=json.loads(observed['payload_json']).get('model')
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
            settle_stopped_tasks(db,self.row['worker_id'],self.row['generation'],self.shutdown_reason or 'WORKER_RELEASED')
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
