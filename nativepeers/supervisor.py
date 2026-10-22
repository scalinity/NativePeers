"""A finite, non-model supervisor for one owned delegation group."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import secrets
import signal
import time

from .artifacts import Artifacts
from .caller import Caller
from .control import runtime_root
from .delegation import Delegation,TERMINAL,supervisor_environment
from .errors import PeerError,require
from .files import advisory_lock,atomic_json,canonical
from .journal import Journal
from .models import PEER_FRAME,CONTROL_RESPONSE,INLINE_BODY,new_id
from .notifications import claim_wait,wait_generation,wait_output
from .process import identity,matches,signal_owned
from .profiles import ROOT,PYTHON
from .registry import Registry
from .receipts import delivery_error


class Group:
    def __init__(self,journal,group_id):
        self.journal,self.id=journal,group_id
        self.row=journal.one('SELECT * FROM groups WHERE group_id=?',(group_id,))
        self.policy=json.loads(self.row['policy_json']);self.owner=identity(os.getpid()).json()
        self.generation=self.policy.get('controller_generation',1);self.token=secrets.token_hex(32)
        self.endpoint=runtime_root()/('g-'+group_id[:24]+'.sock')
        self.children={};self.changed=asyncio.Event();self.closing=False;self.finished_at=None;self.shutdown_reason=None
        self.controller=Caller('controller','controller:'+group_id+':'+str(self.generation),grants={'group_id':group_id,'generation':self.generation,'parent_key':self.row['parent_key']})
        self.parent=Caller('peer',self.row['parent_key'],self.policy['parent_incarnation'])

    def tasks(self):
        return self.journal.all('SELECT t.* FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?',(self.id,))

    def initial_done(self):
        return all(t['state'] in TERMINAL for t in self.tasks() if t['task_id'] in self.policy['initial_tasks'])

    def phases(self):return self.policy.get('phases',{'1':self.policy['initial_tasks']})

    def phase_done(self,generation):
        wanted=self.phases()[str(generation)];rows={t['task_id']:t for t in self.tasks()}
        return all(task in rows and rows[task]['state'] in TERMINAL for task in wanted)

    def target_done(self,handle):
        if handle==self.id:return all(t['state'] in TERMINAL for t in self.tasks())
        return any(t['task_id']==handle and t['state'] in TERMINAL for t in self.tasks())

    async def send_worker(self,worker_id,value):
        child=self.children[worker_id];wire=canonical(value)+b'\n'
        require(len(wire)<=PEER_FRAME,'PAYLOAD_TOO_LARGE','Worker control frame exceeds 64 KiB')
        child['process'].stdin.write(wire);await child['process'].stdin.drain()

    async def start_worker(self,row):
        read_fd,write_fd=os.pipe()
        log=open(self.journal.root/'groups'/self.id/(row['worker_id']+'.stderr'),'ab',buffering=0)
        try:
            process=await asyncio.create_subprocess_exec(str(PYTHON),'-m','nativepeers.worker_process',
                '--worker-id',row['worker_id'],'--lease-fd',str(read_fd),cwd=ROOT,env=supervisor_environment(self.journal),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=log,
                pass_fds=(read_fd,),close_fds=True,start_new_session=True,limit=PEER_FRAME+1)
        except BaseException:
            os.close(write_fd);raise
        finally:os.close(read_fd);log.close()
        child={'process':process,'owner':identity(process.pid).json(),'lease_fd':write_fd,'ready':asyncio.get_running_loop().create_future(),'row':row}
        self.children[row['worker_id']]=child;child['reader']=asyncio.create_task(self.read_worker(row['worker_id']))
        await asyncio.wait_for(asyncio.shield(child['ready']),30)
        # macOS Python may exec its application binary during startup. Pin the ready
        # controller, retaining the exact birth identity used for the child we launched.
        ready_owner=identity(process.pid).json()
        require(all(ready_owner[k]==child['owner'][k] for k in ('pid','pgid','uid','start')),
                'OWNERSHIP_UNKNOWN','Worker identity changed before readiness')
        child['owner']=ready_owner

    async def read_worker(self,worker_id):
        child=self.children[worker_id]
        try:
            while raw:=await child['process'].stdout.readline():
                require(len(raw)<=PEER_FRAME,'PROTOCOL_ERROR','Worker event exceeds 64 KiB')
                event=json.loads(raw)
                if event.get('generation')!=child['row']['generation']:continue
                self.journal.event(worker_id,'controller-event',event)
                if event['event']=='ready' and not child['ready'].done():child['ready'].set_result(event)
                if event['event']=='failed':
                    if not child['ready'].done():child['ready'].set_exception(PeerError('WORKER_CRASHED',str(event['error'])))
                    self.fail_worker(worker_id,event['error'],child['row']['generation'])
                if event['event']=='task_terminal':self.changed.set()
            if not self.closing and any(t['worker_id']==worker_id and t['state'] not in TERMINAL for t in self.tasks()):self.fail_worker(worker_id,{'code':'WORKER_CRASHED','message':'Owned worker closed its control stream'},child['row']['generation'])
        except Exception as error:
            self.fail_worker(worker_id,{'code':'WORKER_CRASHED','message':str(error)[:1000]},child['row']['generation'])
        finally:
            if not child['ready'].done():child['ready'].set_exception(PeerError('WORKER_CRASHED','Worker exited before readiness'))
            if not self.closing:
                await self.release_worker(worker_id,expected=child)
            self.changed.set()

    def fail_worker(self,worker_id,error,generation=None):
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        if generation is not None and row['generation']!=generation:return
        self.journal.db.execute("UPDATE workers SET state=? WHERE worker_id=?",('RELEASING' if worker_id in self.children else 'FAILED',worker_id))
        for task in self.tasks():
            if task['worker_id']!=worker_id or task['state'] in TERMINAL:continue
            unstarted=task['state']=='QUEUED' and task['native_turn_id'] is None
            task_error=error if unstarted else {'code':'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','cause':error}
            state='FAILED' if unstarted else 'INTERRUPTED'
            record=dict(schema_version=1,group_id=self.id,task_id=task['task_id'],worker={'provider':row['provider'],
                'native_session_id':row['native_id'],'title':row['role_title'],'peer_key':row['peer_key']},status=state.lower(),summary='',error=task_error,
                model_requested=row['model_requested'],model_observed=row['model_observed'],native_turn_ids=[],started_at=None,completed_at=time.time())
            artifact=Artifacts(self.journal).put(canonical(record),row['peer_key'] or worker_id,[self.row['parent_key']],media_type='application/json')
            self.journal.db.execute("UPDATE tasks SET state=?,result_artifact_id=?,terminal_reason=? WHERE task_id=? AND generation=? AND state NOT IN ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED')",(state,artifact,json.dumps(task_error),task['task_id'],row['generation']))
        self.changed.set()

    async def cancel_worker(self,worker_id,reason=None):
        if worker_id not in self.children:return
        try:await self.send_worker(worker_id,dict(operation='cancel',**({'reason':reason} if reason else {})))
        except (BrokenPipeError,ConnectionError):pass

    async def cleanup_native(self,worker_id,child):
        path=self.journal.root/'workers'/(worker_id+'.json')
        if not path.exists():return
        manifest=json.loads(path.read_text())
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        if not row or row['generation']!=child['row']['generation']:return
        if manifest.get('worker_id')!=worker_id or manifest.get('generation')!=row['generation'] or manifest.get('native_id')!=row['native_id']:return
        owner=manifest.get('native_process')
        if not owner or not matches(owner):return
        signal_owned(owner,signal.SIGTERM,group=True)
        deadline=time.monotonic()+1
        while matches(owner) and time.monotonic()<deadline:await asyncio.sleep(.02)
        if matches(owner):signal_owned(owner,signal.SIGKILL,group=True)
        deadline=time.monotonic()+2
        while matches(owner) and time.monotonic()<deadline:await asyncio.sleep(.02)
        require(not matches(owner),'OWNERSHIP_CONFLICT','Owned native provider did not stop; worker capacity remains reserved')
        self.journal.event(worker_id,'orphan-provider-stopped',{'generation':row['generation'],'pid':owner['pid']})

    async def release_worker(self,worker_id,*,expected=None):
        child=self.children[worker_id]
        if expected is not None and child is not expected:return
        self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=? AND generation=? AND state!='RELEASED_RESUMABLE'",(worker_id,child['row']['generation']))
        try:await self.send_worker(worker_id,dict(operation='release',**({'reason':self.shutdown_reason} if self.shutdown_reason else {})))
        except (BrokenPipeError,ConnectionError):pass
        if child.get('lease_fd') is not None:
            os.close(child['lease_fd']);child['lease_fd']=None
        try:await asyncio.wait_for(child['process'].wait(),22)
        except asyncio.TimeoutError:
            await self.cleanup_native(worker_id,child)
            signal_owned(child['owner'],signal.SIGTERM,group=True)
            try:await asyncio.wait_for(child['process'].wait(),5)
            except asyncio.TimeoutError:
                signal_owned(child['owner'],signal.SIGKILL,group=True)
                await asyncio.wait_for(child['process'].wait(),5)
        # A dead controller returns immediately from wait; its separately grouped
        # native process still needs exact, generation-fenced cleanup.
        await self.cleanup_native(worker_id,child)
        require(not matches(child['owner']),'OWNERSHIP_CONFLICT','Worker controller still owns its process')
        self.journal.db.execute("UPDATE workers SET state=CASE WHEN native_id IS NULL THEN 'FAILED' ELSE 'RELEASED_RESUMABLE' END WHERE worker_id=? AND generation=?",(worker_id,child['row']['generation']))

    async def initialize(self):
        self.server=await asyncio.start_unix_server(self.connection,path=str(self.endpoint),limit=PEER_FRAME+1)
        os.chmod(self.endpoint,0o600)
        atomic_json(self.journal.root/'groups'/self.id/'owner.json',dict(owner=self.owner,group_id=self.id,generation=self.generation,endpoint=str(self.endpoint),token=self.token))
        self.journal.db.execute("UPDATE groups SET state='STARTING',owner_manifest=? WHERE group_id=?",(canonical({'owner':self.owner,'endpoint':str(self.endpoint),'generation':self.generation}).decode(),self.id))
        rows=self.journal.all("SELECT * FROM workers WHERE group_id=? AND state='RESERVED'",(self.id,))
        results=await asyncio.gather(*[self.start_worker(row) for row in rows],return_exceptions=True)
        if any(isinstance(r,BaseException) for r in results):
            for row in rows:self.fail_worker(row['worker_id'],{'code':'CAPABILITY_MISMATCH','message':'Batch readiness failed before task admission'})
            return
        self.journal.db.execute("UPDATE groups SET state='RUNNING' WHERE group_id=?",(self.id,))
        # Readiness barrier is complete for both controllers before either native task is submitted.
        await asyncio.gather(*[self.send_worker(task['worker_id'],{'operation':'task','task_id':task['task_id'],
            'text':json.loads(task['task_json'])['text']+'\n\nNativePeers contract: work only on this focused task. Do not invoke vendor-native Agent/subagents. Use the bound NativePeers tools for permitted peer communication. Return your own final findings.'})
            for task in self.tasks() if task['state']=='QUEUED' and task['worker_id'] in self.children])

    async def connection(self,reader,writer):
        uncertain=False
        try:
            raw=await asyncio.wait_for(reader.readline(),3)
            require(len(raw)<=PEER_FRAME,'PAYLOAD_TOO_LARGE','Group frame exceeds 64 KiB')
            request=json.loads(raw)
            require(secrets.compare_digest(request.get('token',''),self.token),'CALLER_UNBOUND','Invalid group capability')
            op=request.get('operation');payload=request.get('payload',{})
            require(op in ('hello','status','enqueue','wait','cancel','result','release-idle','resume'),'PROTOCOL_ERROR','Unknown scoped group operation')
            if op=='enqueue':value=await self.enqueue(payload)
            elif op=='wait':value=await self.wait(payload,reader);uncertain=value.get('notification_output_uncertain',False)
            elif op=='cancel':
                value=await self.cancel(payload)
            elif op=='resume':
                require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Resume requires the owning parent')
                self.policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(self.id,))['policy_json'])
                row=self.journal.one("SELECT * FROM workers WHERE worker_id=? AND group_id=? AND state='RESERVED'",(payload['worker_id'],self.id))
                require(row,'OWNERSHIP_CONFLICT','Worker is not reserved for this resume')
                self.finished_at=None
                await self.start_worker(row)
                await self.send_worker(row['worker_id'],{'operation':'task','task_id':payload['task_id'],'text':payload['text']})
                self.finished_at=None;value={'worker_id':row['worker_id'],'task_id':payload['task_id'],'state':'QUEUED'}
            elif op=='release-idle':
                row=self.journal.one('SELECT state FROM workers WHERE worker_id=?',(payload['worker_id'],))
                require(row and row['state']=='IDLE_GRACE' and not any(t['worker_id']==payload['worker_id'] and t['state'] not in TERMINAL for t in self.tasks()),'CAPACITY_UNAVAILABLE','Worker is not safely idle')
                await self.release_worker(payload['worker_id']);value={'released':True}
            else:
                require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Group read requires its parent')
                value=Delegation(self.journal,Registry(self.journal)).result(self.parent,payload.get('handle',self.id))
            response={'ok':True,'data':value}
        except Exception as error:
            response={'ok':False,'error':error.json() if isinstance(error,PeerError) else {'code':'PROTOCOL_ERROR','message':type(error).__name__}}
        try:
            wire=canonical(response)+b'\n';require(len(wire)<=CONTROL_RESPONSE,'PAYLOAD_TOO_LARGE','Result response exceeds 2 MiB; use artifact pages')
            writer.write(wire);await writer.drain()
        finally:
            writer.close();await writer.wait_closed()

    async def cancel(self,payload):
        require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Cancellation requires the owning parent')
        handle=payload['handle']
        with self.journal.transaction() as db:
            tasks=self.tasks();scoped=next((t for t in tasks if t['task_id']==handle),None)
            require(handle==self.id or scoped,'PROTOCOL_ERROR','Unknown task handle')
            for task in tasks:
                if (handle==self.id or task['task_id']==handle) and task['state']=='QUEUED':
                    db.execute("UPDATE tasks SET state='CANCELED',terminal_reason='USER_CANCELED' WHERE task_id=? AND state='QUEUED'",(task['task_id'],))
        self.changed.set()
        if scoped:
            if scoped['state']=='QUEUED' or scoped['state'] in TERMINAL:
                return {'state':'CANCELED' if scoped['state']=='QUEUED' else scoped['state'],'workers':[]}
            await self.send_worker(scoped['worker_id'],{'operation':'cancel-task','task_id':handle})
            return {'state':'CANCEL_REQUESTED','workers':[scoped['worker_id']],'task_id':handle}
        targets={t['worker_id'] for t in tasks}
        await asyncio.gather(*[self.cancel_worker(w) for w in targets])
        for worker in targets:asyncio.create_task(self.cancel_and_release(worker))
        return {'state':'CANCEL_REQUESTED','workers':list(targets)}

    async def cancel_and_release(self,worker_id):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if not any(t['worker_id']==worker_id and t['state']=='RUNNING' for t in self.tasks()):break
            await asyncio.sleep(.05)
        await self.release_worker(worker_id)
        self.changed.set()

    async def enqueue(self,payload):
        require(not self.closing and not self.shutdown_reason,'TARGET_OFFLINE','Group controller is shutting down')
        message=payload['message'];worker_id=payload['worker_id']
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=? AND group_id=?',(worker_id,self.id))
        require(row and row['peer_key']==message['to']['peer_key'],'TARGET_REBOUND','Worker identity differs from the recorded recipient')
        stored=self.journal.one('SELECT payload_json,task_id FROM messages WHERE message_id=?',(message['message_id'],))
        require(stored and canonical(json.loads(stored['payload_json']))==canonical(message),'CALLER_UNBOUND','Follow-up must reference the immutable routing journal')
        if stored['task_id']:return {'stage':'ACCEPTED_QUEUED','evidence_kind':'controller-queue-admission','external_submission_id':stored['task_id']}
        require(row['state'] in ('READY','RUNNING','IDLE_GRACE'),'TARGET_OFFLINE','Worker controller has released ownership')
        task_id=new_id()
        with self.journal.transaction() as db:
            count=db.execute("SELECT COUNT(*) FROM tasks WHERE worker_id=? AND state='QUEUED'",(worker_id,)).fetchone()[0]
            require(count<32,'CAPACITY_UNAVAILABLE','Worker follow-up queue is full')
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,message['message_id'],None,'QUEUED',None,None,row['generation'],canonical({'text':canonical(message).decode(),'initial':False}).decode()))
            db.execute('UPDATE messages SET task_id=? WHERE message_id=?',(task_id,message['message_id']))
            generation=max(int(n) for n in self.phases())+1
            self.policy['phases']=dict(self.phases(),**{str(generation):[task_id]})
            db.execute('UPDATE groups SET policy_json=? WHERE group_id=?',(canonical(self.policy).decode(),self.id))
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,generation,self.row['parent_key'],'wait','UNCLAIMED',None,None,new_id(),self.generation))
        await self.send_worker(worker_id,{'operation':'task','task_id':task_id,'text':canonical(message).decode()})
        self.finished_at=None;self.changed.set()
        return {'stage':'ACCEPTED_QUEUED','evidence_kind':'controller-queue-admission','external_submission_id':task_id,'native_expiry_enforced':False}

    async def wait(self,payload,reader):
        require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Wait requires the owning parent')
        timeout=payload.get('timeout_ms',30000)/1000;require(0<timeout<=30,'PROTOCOL_ERROR','Wait exceeds 30 seconds')
        mode=payload.get('on_timeout') or ('push' if self.policy['completion_mode']=='push' else 'retain')
        require(mode in ('push','retain'),'PROTOCOL_ERROR','Unknown wait fallback')
        waiter=new_id();generation=wait_generation(self.id,self.policy,payload['handle'])
        owns_notification=generation is not None
        claimed=claim_wait(self.journal,self.id,generation,self.generation,waiter,timeout)
        deadline=time.monotonic()+timeout
        disconnected=asyncio.create_task(reader.read(1))
        try:
            while not self.target_done(payload['handle']):
                remaining=deadline-time.monotonic()
                if remaining<=0 or disconnected.done():break
                changed=asyncio.create_task(self.changed.wait())
                try:await asyncio.wait([changed,disconnected],timeout=remaining,return_when=asyncio.FIRST_COMPLETED)
                finally:changed.cancel()
                self.changed.clear()
            if self.target_done(payload['handle']) and not disconnected.done():
                if claimed:
                    claimed=wait_output(self.journal,self.id,generation,self.generation,waiter)
                return {'ready':True,'result':Delegation(self.journal,Registry(self.journal)).result(self.parent,payload['handle']),
                        'notification_output_uncertain':claimed,'notification_disposition':('tool-result-output-pending-observation' if claimed else 'retained-result-read') if owns_notification else 'batch-notification-unchanged'}
            if claimed:
                self.journal.db.execute("UPDATE completion_routes SET state='UNCLAIMED',mode=?,waiter_id=NULL,lease_deadline=NULL WHERE group_id=? AND completion_generation=? AND waiter_id=? AND claimed_generation=?",('push' if mode=='push' else 'wait',self.id,generation,waiter,self.generation))
            return {'ready':False,'state':'WAIT_EXPIRED','notification_disposition':mode if owns_notification else 'batch-notification-unchanged'}
        finally:disconnected.cancel();self.changed.set()

    async def notify(self,generation=1):
        if time.time()>=self.lifetime_deadline():return
        if not self.phase_done(generation):return
        with self.journal.transaction() as db:
            row=db.execute('SELECT * FROM completion_routes WHERE group_id=? AND completion_generation=?',(self.id,generation)).fetchone()
            if row['state']=='WAIT_LEASED' and row['lease_deadline']<time.time():
                db.execute("UPDATE completion_routes SET state='UNCLAIMED',waiter_id=NULL WHERE group_id=? AND completion_generation=?",(self.id,generation))
                row=dict(row);row['state']='UNCLAIMED'
            if row['state']!='UNCLAIMED' or row['mode']!='push':return
            db.execute("UPDATE completion_routes SET state='PUSH_SUBMITTING',claimed_generation=? WHERE group_id=? AND completion_generation=?",(self.generation,self.id,generation))
        from .core import Core
        result=Delegation(self.journal,Registry(self.journal)).result(self.parent,self.id)
        phase_results=[r for r in result['results'] if r['task_id'] in self.phases()[str(generation)]]
        summary_limit,title_limit=2000,512
        while True:
            summaries=[dict(title=r['worker']['title'][:title_limit],title_truncated=len(r['worker']['title'])>title_limit,
                provider=r['worker']['provider'],native_session_id=r['worker']['native_session_id'],status=r['status'],
                summary=r['summary'][:summary_limit],summary_truncated=r.get('summary_truncated',False) or len(r['summary'])>summary_limit,
                result_artifact_id=r['result_artifact_id']) for r in phase_results]
            text='NativePeers group '+self.id+' completion generation '+str(generation)+'. These are independently produced worker results.\n'+json.dumps(summaries,ensure_ascii=False)
            if len(text.encode('utf-8'))<=INLINE_BODY:break
            if summary_limit:summary_limit//=2
            else:
                title_limit//=2
                require(title_limit>=16,'PAYLOAD_TOO_LARGE','Completion metadata exceeds the peer body limit; retained artifact reads remain available')
        budget=None;reservation=None
        test=self.journal.root/'test-budget.json'
        try:
            if test.exists():
                from .budget import LiveBudget
                spec=json.loads(test.read_text());budget=LiveBudget(Path(spec['path']))
                prior=self.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='completion-budget-reserved' ORDER BY sequence LIMIT 1",(row['delivery_id'],))
                if prior:reservation=json.loads(prior['payload_json'])['reservation']
                else:
                    try:reservation=budget.reserve('parent completion '+self.id,tranche=spec.get('tranche','first_cross_provider_delegations'))
                    except RuntimeError as error:raise PeerError('BUDGET_EXHAUSTED',str(error)) from error
                    budget.admit(reservation)
                    self.journal.event(row['delivery_id'],'completion-budget-reserved',{'reservation':reservation})
            receipt=await Core(self.journal).router.send(self.controller,self.row['parent_key'],text,kind='completion',client_request_id=row['delivery_id'],admission_deadline=self.lifetime_deadline())
            deferred=receipt['state'] in ('OUTBOX_COMMITTED','ADMISSION_DEFERRED')
            error=delivery_error(receipt)
            state=('UNCLAIMED' if deferred else 'PUSH_ACCEPTED' if receipt['state'] in ('INGESTED','ACCEPTED_QUEUED')
                   else 'HELD' if receipt['state']=='HELD' else 'RETAINED' if error else 'NOTIFICATION_UNKNOWN')
            self.journal.db.execute('UPDATE completion_routes SET state=? WHERE group_id=? AND completion_generation=? AND claimed_generation=?',(state,self.id,generation,self.generation))
            event='parent-completion-deferred' if deferred else 'parent-completion-retained' if error else 'parent-completion-submitted'
            self.journal.event(self.id,event,{'receipt':receipt,'live_reservation':reservation})
            if error and not deferred and budget and reservation:budget.settle(reservation,observed_turns=0)
            # Accepted/uncertain model work remains reserved until the live collector observes it.
        except PeerError as error:
            self.journal.db.execute("UPDATE completion_routes SET state=? WHERE group_id=? AND completion_generation=? AND claimed_generation=?",('NOTIFICATION_UNKNOWN' if error.may_have_received else 'RETAINED',self.id,generation,self.generation))
            self.journal.event(self.id,'parent-completion-retained',error.json())
            if not error.may_have_received and budget and reservation:budget.settle(reservation,observed_turns=0)

    def lifetime_deadline(self):
        return self.policy['created_at']+self.policy.get('group_wall_seconds',self.policy['wall_time_seconds'])

    async def monitor(self):
        while True:
            expired=time.time()>=self.lifetime_deadline()
            tasks=self.tasks();active=any(t['state'] not in TERMINAL for t in tasks)
            if self.initial_done():
                state='COMPLETED' if all(t['state']=='COMPLETED' for t in tasks if t['task_id'] in self.policy['initial_tasks']) else 'PARTIAL'
                self.journal.db.execute('UPDATE groups SET state=? WHERE group_id=?',(state,self.id))
                if not expired:
                    for phase in self.phases():await self.notify(int(phase))
                # Notification awaits can admit a successor; never start idle grace
                # from the activity snapshot taken before that await.
                active=any(t['state'] not in TERMINAL for t in self.tasks())
                if active:self.finished_at=None
                elif self.finished_at is None:self.finished_at=time.time()
            # Owned resume reloads the persisted policy, including renewed lifetime.
            deadline=self.lifetime_deadline()
            parent_gone=not matches(self.policy['parent_owner']) and not self.policy['survive_parent']
            if parent_gone or time.time()>deadline:
                self.shutdown_reason=self.shutdown_reason or ('PARENT_LOST' if parent_gone else 'GROUP_DEADLINE_EXCEEDED')
                # The worker claims RUNNING before submitting native input. This compare-and-set
                # prevents buffered successors from starting after the lifetime has ended.
                self.stop_queued()
                await asyncio.gather(*[self.cancel_worker(w,self.shutdown_reason) for w in self.children])
                if not active:break
                if time.time()>deadline+20 or parent_gone:
                    self.journal.event(self.id,'lifetime-ended',{'parent_gone':parent_gone,'deadline':deadline});break
            pending_push=self.journal.one("SELECT 1 FROM completion_routes WHERE group_id=? AND mode='push' AND state='UNCLAIMED' LIMIT 1",(self.id,))
            if not active and self.finished_at is not None and not pending_push and time.time()-self.finished_at>=self.policy['followup_grace_seconds']:break
            self.changed.clear()
            try:await asyncio.wait_for(self.changed.wait(),1)
            except asyncio.TimeoutError:pass

    def stop_queued(self):
        self.journal.db.execute("UPDATE tasks SET state='CANCELED',terminal_reason=? WHERE state='QUEUED' AND native_turn_id IS NULL AND EXISTS (SELECT 1 FROM workers w WHERE w.worker_id=tasks.worker_id AND w.group_id=? AND w.generation=tasks.generation)",
                                (self.shutdown_reason or 'SUPERVISOR_RELEASED',self.id))

    async def close(self):
        self.closing=True
        self.stop_queued()
        self.journal.db.execute("UPDATE completion_routes SET state='RETAINED' WHERE group_id=? AND state='UNCLAIMED' AND mode='push'",(self.id,))
        await asyncio.gather(*[self.release_worker(w) for w in self.children],return_exceptions=True)
        self.server.close();await self.server.wait_closed();self.endpoint.unlink(missing_ok=True)
        self.journal.event(self.id,'supervisor-released',{'owner':self.owner})


async def run(group_id):
    journal=Journal()
    with advisory_lock(journal.root/'locks'/(group_id+'.supervisor.lock'),timeout=.1):
        group=Group(journal,group_id)
        try:await group.initialize();await group.monitor()
        finally:await group.close();journal.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--group-id',required=True);args=parser.parse_args();asyncio.run(run(args.group_id))


if __name__=='__main__':main()
