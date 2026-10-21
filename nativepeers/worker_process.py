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
from .launch import launch_environment
from .models import new_id
from .process import identity,matches,signal_owned
from .profiles import ROOT,claude_arguments,codex_config,config_arguments
from .registry import Registry
from .compatibility import verify
from .provider_errors import classify


class Worker:
    def __init__(self,journal,worker_id):
        self.journal=journal;self.row=journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        self.lease=json.loads(self.row['lease_json']);self.spec=self.lease['spec'];self.binding=Path(self.lease['binding'])
        self.queue=asyncio.Queue(maxsize=32);self.owner=identity(os.getpid()).json()
        self.provider=None;self.provider_identity=None;self.client=None;self.native=self.row['native_id'];self.turn=None
        self.stopping=False;self.running_task=None;self.tasks_started=0;self.retire_after_task=False
        self.expected_native=self.native;self.model_observed=None

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
        self.native=native_id;self.model_observed=model or self.model_observed
        existing=json.loads(self.binding.read_text())
        if existing.get('state')=='BOUND':return
        home=Path(os.getenv('CODEX_HOME' if self.row['provider']=='codex' else 'CLAUDE_CONFIG_DIR',Path.home()/('.codex' if self.row['provider']=='codex' else '.claude'))).resolve()
        peer=Registry(self.journal).register(self.row['provider'],home,native_id,self.row['role_title'],self.owner,
            self.row['cwd'],{'kind':'owned-worker','group_id':self.row['group_id'],'worker_id':self.row['worker_id']},
            owner_kind='owned-worker-controller',policy=self.lease['grants'])
        self.journal.db.execute('UPDATE workers SET peer_key=?,native_id=?,model_observed=? WHERE worker_id=?',
            (peer['handle'],native_id,self.model_observed,self.row['worker_id']))
        atomic_json(self.binding,dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],
            owner=self.owner,expires_at=self.lease['expires_at'],grants=self.lease['grants']))
        self.manifest();self.emit('bound',native_id=native_id,peer_key=peer['handle'])

    async def prepare(self):
        self.bootstrap()
        if self.row['provider']=='codex':
            executable=Path(self.lease['executable']).resolve(strict=True)
            verify(self.journal,'codex',executable)
            config=codex_config(self.journal,self.binding,self.spec['permission_profile'])
            self.client=AppServer(executable,config_arguments(config),cwd=Path(self.row['cwd']),
                environment={'NPEER_HOME':str(self.journal.root),'NPEER_BOOTSTRAP':str(self.binding),'PYTHONPATH':str(ROOT)})
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
        else:
            # Preflight validates argv/environment. Native initialization begins only after first input.
            require(Path(self.lease['executable']).is_file(),'AUTH_REQUIRED','Installed Claude executable is unavailable')
            verify(self.journal,'claude',self.lease['executable'])
            launch_environment('claude');claude_arguments(self.journal,self.binding,self.spec,self.expected_native)
        self.journal.db.execute("UPDATE workers SET state='READY' WHERE worker_id=?",(self.row['worker_id'],))
        self.emit('ready',native_id=self.native if self.row['provider']=='codex' else None)

    async def task(self,task_id,text):
        self.tasks_started+=1
        require(self.tasks_started<=self.spec.get('max_task_turns',8),'BUDGET_EXHAUSTED','Worker task-turn limit reached')
        self.running_task=task_id;started=time.time();self.turn=None
        text='NativePeers assigned worker title: '+self.row['role_title']+'. Provider: '+self.row['provider']+'.\n'+text
        self.journal.db.execute("UPDATE tasks SET state='RUNNING' WHERE task_id=? AND generation=?",(task_id,self.row['generation']))
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
                result=await (self.codex_turn(text) if self.row['provider']=='codex' else self.claude_turn(text))
        except BaseException as error:
            self.retire_after_task=True
            self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=?",(self.row['worker_id'],))
            await self.interrupt()
            code=classify(error.error) if isinstance(error,ProviderError) else 'BUDGET_EXHAUSTED' if isinstance(error,TimeoutError) else 'WORKER_CRASHED'
            result={'status':'canceled' if self.stopping else 'timed_out' if isinstance(error,TimeoutError) else 'failed','text':'','error':error.json() if isinstance(error,PeerError) else {'code':code,'message':str(error)[:1000]},'native_turn_ids':[self.turn] if self.turn else []}
            if isinstance(error,(KeyboardInterrupt,SystemExit)):raise
        if self.stopping and result['status'] in ('failed','canceled'):
            result['status']='canceled';result['error']={'code':'USER_CANCELED','native_error':result.get('error')}
        row=self.journal.one('SELECT peer_key FROM workers WHERE worker_id=?',(self.row['worker_id'],))
        record=dict(schema_version=1,group_id=self.row['group_id'],task_id=task_id,
            worker={'provider':self.row['provider'],'native_session_id':self.native,'title':self.row['role_title'],'peer_key':row['peer_key']},
            model_requested=self.row['model_requested'],model_observed=self.model_observed,
            reasoning_effort_requested=self.spec.get('reasoning_effort'),reasoning_effort_observed=None,
            status=result['status'],summary=result.get('text',''),findings=[],artifacts=[],
            verification={'tests_run':[],'limitations':['Provider final text retained; structured findings were not inferred.','Reasoning effort independently echoed by inference: unverified.']},
            started_at=started,completed_at=time.time(),native_turn_ids=result.get('native_turn_ids',[]),
            error=result.get('error'),usage=result.get('usage'),changes={'worktree':self.row['cwd'] if self.row['worktree_id'] else None,'commit':None,'patch_artifact':None})
        if self.row['worktree_id']:
            from .worktrees import git
            baseline=self.lease['baseline'];patch=git(self.row['cwd'],'diff',baseline['head'],'--binary').stdout
            record['changes']['commit']=git(self.row['cwd'],'rev-parse','HEAD').stdout.decode().strip()
            record['changes']['baseline']=baseline
            if patch:record['changes']['patch_artifact']=Artifacts(self.journal).put(patch,row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='text/x-diff')
        artifact=Artifacts(self.journal).put(canonical(record),row['peer_key'] or self.row['worker_id'],[self.lease['parent_key']],media_type='application/json')
        with self.journal.transaction() as db:
            db.execute("UPDATE tasks SET state=?,result_artifact_id=?,native_turn_id=?,terminal_reason=? WHERE task_id=? AND generation=? AND state NOT IN ('COMPLETED','FAILED','CANCELED')",
                (result['status'].upper(),artifact,self.turn,json.dumps(result.get('error')),task_id,self.row['generation']))
            db.execute("UPDATE workers SET state=? WHERE worker_id=?",('RELEASING' if self.retire_after_task else 'IDLE_GRACE',self.row['worker_id']))
        self.journal.event(task_id,'native-task-terminal',record)
        self.running_task=None;self.emit('task_terminal',task_id=task_id,artifact_id=artifact,status=result['status'])
        if self.retire_after_task:await self.close()
        if reservation:budget.settle(reservation,observed_turns=1,native_id=self.native)

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
        args=claude_arguments(self.journal,self.binding,self.spec,self.expected_native,resume=self.tasks_started>1 or self.lease.get('released_native',False))
        env=launch_environment('claude');env.update(NPEER_HOME=str(self.journal.root),NPEER_BOOTSTRAP=str(self.binding),PYTHONPATH=str(ROOT))
        self.provider=await asyncio.create_subprocess_exec(self.lease['executable'],*args,cwd=self.row['cwd'],env=env,
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            start_new_session=True,close_fds=True,limit=8*1024*1024)
        self.provider_identity=identity(self.provider.pid).json();self.manifest()
        self.provider.stdin.write(text.encode());await self.provider.stdin.drain();self.provider.stdin.close()
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
                if event.get('type')=='assistant':assistant_rounds+=1
                if event.get('type')=='result':terminal=event
            await self.provider.wait();await drain
            require(terminal is not None,'WORKER_CRASHED','Claude exited without a terminal result')
            self.journal.event(self.running_task,'provider-internal-rounds',{'assistant_events':assistant_rounds,'num_turns':terminal.get('num_turns')})
            return dict(status='completed' if terminal.get('subtype')=='success' and not terminal.get('is_error') and self.provider.returncode==0 else 'failed',
                        text=terminal.get('result',''),native_turn_ids=[self.turn] if self.turn else [],
                        error=None if terminal.get('subtype')=='success' else {'code':classify(terminal),'subtype':terminal.get('subtype'),'errors':terminal.get('errors',[])},usage=terminal.get('usage'))
        finally:
            if not drain.done():drain.cancel()

    async def interrupt(self):
        if not self.provider or self.provider.returncode is not None:return
        if self.client and self.turn:
            try:await self.client.call('turn/interrupt',{'threadId':self.native,'turnId':self.turn},timeout=5)
            except Exception:pass
        elif self.provider_identity:
            signal_owned(self.provider_identity,signal.SIGINT,group=True)

    async def close(self):
        self.stopping=True;await self.interrupt()
        if self.client:await self.client.close()
        elif self.provider and self.provider.returncode is None:
            try:await asyncio.wait_for(self.provider.wait(),15)
            except asyncio.TimeoutError:
                signal_owned(self.provider_identity,signal.SIGTERM,group=True)
                try:await asyncio.wait_for(self.provider.wait(),5)
                except asyncio.TimeoutError:
                    signal_owned(self.provider_identity,signal.SIGKILL,group=True);await self.provider.wait()
        self.journal.db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=?",(self.row['worker_id'],))
        self.journal.db.execute("UPDATE peer_instances SET status='offline' WHERE pid=? AND process_start=?",(self.owner['pid'],self.owner['start']))
        self.emit('released',native_id=self.native)


async def run(worker_id,lease_fd):
    journal=Journal();worker=Worker(journal,worker_id)
    main_task=asyncio.current_task()
    async def commands():
        while line:=await asyncio.to_thread(sys.stdin.buffer.readline,65537):
            require(len(line)<=65536,'PAYLOAD_TOO_LARGE','Worker control request too large')
            event=json.loads(line)
            if event['operation']=='cancel':worker.stopping=True;await worker.interrupt()
            elif event['operation']=='release':worker.stopping=True;await worker.queue.put(None);return
            elif event['operation']=='task':await worker.queue.put(event)
        worker.stopping=True;await worker.interrupt();await worker.queue.put(None)
    async def lease():
        await asyncio.to_thread(os.read,lease_fd,1);os.close(lease_fd)
        worker.stopping=True;await worker.interrupt();await worker.queue.put(None)
        await worker.close();main_task.cancel()
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
        await worker.close()
        for reader in readers:reader.cancel()
        journal.close()
        # A blocked stdin thread must not keep a finite controller alive after lease EOF.
        os._exit(0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--worker-id',required=True);parser.add_argument('--lease-fd',type=int,required=True);args=parser.parse_args()
    asyncio.run(run(args.worker_id,args.lease_fd))


if __name__=='__main__':main()
