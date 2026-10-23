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
from .errors import ControllerNotStarted,PeerError,require
from .files import advisory_lock,async_advisory_lock,atomic_json,canonical
from .journal import Journal
from .models import PEER_FRAME,CONTROL_RESPONSE,INLINE_BODY,new_id
from .notifications import claim_wait,wait_generations,wait_output,release_wait,settle_deferred
from .process import refresh_captured,identity,matches,signal_owned,owned_status,signal_captured
from .profiles import ROOT,PYTHON
from .registry import Registry
from .receipts import delivery_error


CONTROL_WRITE_SECONDS=1
CONTROL_REPLY_SECONDS=1
CONTROL_CLOSE_SECONDS=1


def _abort_reply(writer):
    # Python 3.12 can clear a fully closed selector transport's loop. Re-abort
    # only an open transport or one still retaining buffered output.
    if not writer.is_closing() or writer.transport.get_write_buffer_size():writer.transport.abort()


class Group:
    def __init__(self,journal,group_id):
        self.journal,self.id=journal,group_id
        self.row=journal.one('SELECT * FROM groups WHERE group_id=?',(group_id,))
        self.policy=json.loads(self.row['policy_json']);self.owner=identity(os.getpid()).json()
        self.generation=self.policy.get('controller_generation',1);self.token=secrets.token_hex(32)
        self.endpoint=runtime_root()/('g-'+group_id[:24]+'.sock')
        self.children={};self.changed=asyncio.Event();self.closing=False;self.finished_at=None;self.shutdown_reason=None
        self.handoffs=set()
        self.connections={}
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
        try:
            child['process'].stdin.write(wire)
            await asyncio.wait_for(child['process'].stdin.drain(),CONTROL_WRITE_SECONDS)
        except Exception as error:
            if value.get('operation')=='task':
                raise PeerError('DELIVERY_UNKNOWN','Committed task handoff was not confirmed',may_have_received=True,
                    details={'worker_id':worker_id,'task_id':value['task_id'],'cause':type(error).__name__}) from error
            raise

    async def start_worker(self,row):
        read_fd=write_fd=log=None
        try:
            read_fd,write_fd=os.pipe()
            log=open(self.journal.root/'groups'/self.id/(row['worker_id']+'.stderr'),'ab',buffering=0)
            process=await asyncio.create_subprocess_exec(str(PYTHON),'-m','nativepeers.worker_process',
                '--worker-id',row['worker_id'],'--lease-fd',str(read_fd),cwd=ROOT,env=supervisor_environment(self.journal),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=log,
                pass_fds=(read_fd,),close_fds=True,start_new_session=True,limit=PEER_FRAME+1)
        except BaseException as error:
            if write_fd is not None:os.close(write_fd)
            if isinstance(error,OSError):
                failure=ControllerNotStarted(row,error)
                self.journal.event(row['worker_id'],'controller-not-created',failure.details)
                raise failure from error
            raise
        finally:
            if read_fd is not None:os.close(read_fd)
            if log is not None:log.close()
        # Successful creation owns a handle and lease even if birth inspection fails.
        child={'process':process,'owner':None,'lease_fd':write_fd,'ready':asyncio.get_running_loop().create_future(),'row':row}
        self.children[row['worker_id']]=child
        try:child['owner']=identity(process.pid).json()
        except (OSError,ValueError) as error:
            self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=? AND generation=?",(row['worker_id'],row['generation']))
            os.close(write_fd);child['lease_fd']=None
            process.stdin.close()
            try:await asyncio.wait_for(process.wait(),2)
            except asyncio.TimeoutError:pass
            details=dict(worker_id=row['worker_id'],generation=row['generation'],controller_created=True,
                         pid=process.pid,exit_observed=process.returncode is not None,cause=type(error).__name__)
            self.journal.event(row['worker_id'],'controller-inspection-failed',details)
            raise PeerError('OWNERSHIP_UNKNOWN','Created controller identity could not be inspected; its lease is closed and ownership remains reserved',details=details) from error
        child['reader']=asyncio.create_task(self.read_worker(row['worker_id']))
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
            if not self.closing and any(t['worker_id']==worker_id and t['state'] not in TERMINAL|{'FALLBACK_PENDING'} for t in self.tasks()):self.fail_worker(worker_id,{'code':'WORKER_CRASHED','message':'Owned worker closed its control stream'},child['row']['generation'])
        except Exception as error:
            self.fail_worker(worker_id,{'code':'WORKER_CRASHED','message':str(error)[:1000]},child['row']['generation'])
        finally:
            if not child['ready'].done():child['ready'].set_exception(PeerError('WORKER_CRASHED','Worker exited before readiness'))
            if not self.closing:
                await self.release_worker(worker_id,expected=child)
                pending=self.journal.all("SELECT * FROM tasks WHERE worker_id=? AND generation=? AND state='FALLBACK_PENDING'",(worker_id,child['row']['generation']))
                for task in pending:
                    handler=asyncio.current_task();self.handoffs.add(handler)
                    try:await self.advance_fallback(task)
                    except Exception as error:
                        self.finish_fallback(task,{'code':error.code if isinstance(error,PeerError) else 'FALLBACK_FAILED','message':str(error)[:1000]})
                    finally:self.handoffs.discard(handler)
            self.changed.set()

    def finish_fallback(self,task,error):
        """Settle a clean handoff failure without losing the original diagnostic."""
        if self.journal.one('SELECT state FROM tasks WHERE task_id=?',(task['task_id'],))['state']!='FALLBACK_PENDING':return
        from .fallback import attempts
        history=attempts(self.journal,task['task_id'])
        if not history:return
        last=history[-1]
        artifact=self.journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?',(last['result_artifact_id'],))
        record=json.loads((self.journal.root/'artifacts'/artifact['relative_path']).read_text())
        record['summary']+=' Fallback not completed: '+error['message']
        record['fallback_failure']=error
        result=Artifacts(self.journal).put(canonical(record),last['worker_id'],[self.row['parent_key']],media_type='application/json')
        self.journal.db.execute("UPDATE tasks SET state='FAILED',result_artifact_id=?,terminal_reason=? WHERE task_id=? AND state='FALLBACK_PENDING'",
                                (result,canonical(error).decode(),task['task_id']))
        self.changed.set()

    async def advance_fallback(self,task):
        """Run only after exact old writers exited. Never replay unknown input."""
        from .fallback import attempts,clone_workspace
        from .policy import load
        from .antigravity_install import verify_worker_ready
        old=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(task['worker_id'],))
        require(old['state']=='RELEASED_RESUMABLE','OWNERSHIP_CONFLICT','Original writer has not released its native history')
        lease=json.loads(old['lease_json']);history=attempts(self.journal,task['task_id']);latest=history[-1]
        artifact=self.journal.one('SELECT relative_path FROM artifacts WHERE artifact_id=?',(latest['result_artifact_id'],))
        record=json.loads((self.journal.root/'artifacts'/artifact['relative_path']).read_text());proof=record['fallback_eligibility']
        require(proof['definitive_no_execution'] and proof['workspace_unchanged'],'FALLBACK_UNSAFE','The original attempt did not establish a clean fallback boundary')
        task_spec=json.loads(task['task_json']);choices=task_spec.setdefault('fallback_choices',[])
        require(not self.closing and self.journal.one('SELECT state FROM tasks WHERE task_id=?',(task['task_id'],))['state']=='FALLBACK_PENDING',
                'USER_CANCELED','Fallback handoff was canceled')
        require(time.time()<min(task_spec['deadline'],self.lifetime_deadline()),'BUDGET_EXHAUSTED','Original task/group deadline elapsed before fallback')
        require(proof['remaining_task_turns']>0,'BUDGET_EXHAUSTED','Original task-turn limit leaves no fallback attempt')
        candidate=None
        for index,value in enumerate(lease.get('fallback_candidates',[])):
            item=dict(value);choice={'provider':item['provider'],'model':item['model']}
            try:
                require(not item.get('unavailable'),(item.get('unavailable') or {}).get('code','MODEL_UNAVAILABLE'),(item.get('unavailable') or {}).get('message','Fallback unavailable'))
                spec=dict(lease['spec'],provider=item['provider'],model=item['model'],model_display_name=item['model_display_name'])
                spec.pop('reasoning_effort',None)
                verify_worker_ready(self.journal,spec,item['executable'])
                candidate=item;choice['state']='SELECTED';choices.append(choice);break
            except (PeerError,OSError) as error:
                choice.update(state='UNAVAILABLE',error=error.json() if isinstance(error,PeerError) else {'code':'MODEL_UNAVAILABLE','message':str(error)[:1000]})
                choices.append(choice)
        self.journal.db.execute('UPDATE tasks SET task_json=? WHERE task_id=?',(canonical(task_spec).decode(),task['task_id']))
        require(candidate,'FALLBACK_UNAVAILABLE','Every explicit fallback choice is unavailable: '+('; '.join(c['model']+': '+(c.get('error') or {}).get('message','unavailable') for c in choices))[:1800])
        worker_id=new_id();binding=self.journal.root/'bindings'/new_id()
        spec.update(max_task_turns=proof['remaining_task_turns'],wall_time_seconds=max(1,int(task_spec['deadline']-time.time())))
        new_lease=dict(lease,provider_home=candidate['provider_home'],executable=candidate['executable'],spec=spec,binding=str(binding),
                      grants=dict(lease['grants'],worker_id=worker_id),fallback_candidates=lease['fallback_candidates'][index+1:])
        new_lease.pop('released_native',None)
        new_lease.pop('verified_runtime',None)
        new_lease.pop('usage_demand',None)
        new_lease.pop('logical_tasks_used',None);new_lease.pop('logical_tasks_generation',None)
        # One logical task/phase; a replacement is a different native history and worker.
        with self.journal.transaction() as db:
            current=db.execute('SELECT state FROM tasks WHERE task_id=?',(task['task_id'],)).fetchone()
            require(current['state']=='FALLBACK_PENDING','USER_CANCELED','Fallback was canceled during preflight')
            active=db.execute("SELECT COUNT(*) FROM workers WHERE state NOT IN ('RELEASED_RESUMABLE','FAILED','EXITED')").fetchone()[0]
            require(active<load(self.journal)['workers_total'],'CAPACITY_UNAVAILABLE','No released capacity is available for the explicit fallback')
            from .usage.service import Usage
            Usage(self.journal).admit_local_demand(new_lease)
            db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker_id,self.id,None,candidate['provider'],None,old['role_title'],candidate['model'],None,None,old['cwd'],None,'RESERVED',1,canonical(new_lease).decode()))
            task_spec['attempt_index']=latest['attempt_index']+1
            db.execute("UPDATE tasks SET worker_id=?,generation=1,state='QUEUED',native_turn_id=NULL,result_artifact_id=NULL,terminal_reason=NULL,task_json=? WHERE task_id=?",(worker_id,canonical(task_spec).decode(),task['task_id']))
            policy=json.loads(db.execute('SELECT policy_json FROM groups WHERE group_id=?',(self.id,)).fetchone()[0]);policy['workers'].append(worker_id)
            db.execute('UPDATE groups SET policy_json=? WHERE group_id=?',(canonical(policy).decode(),self.id))
        self.policy=policy
        try:
            if old['worktree_id']:
                cwd,worktree,baseline=await asyncio.to_thread(clone_workspace,self.journal,old,worker_id,proof['input_digest'])
                spec['cwd']=cwd;new_lease['baseline']=baseline
                self.journal.db.execute('UPDATE workers SET cwd=?,worktree_id=?,lease_json=? WHERE worker_id=?',(cwd,worktree,canonical(new_lease).decode(),worker_id))
            from .artifacts import Artifacts
            for ref in spec.get('context_refs',[]):Artifacts(self.journal).grant(ref,self.row['parent_key'],[worker_id])
            replacement=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
            require(not self.closing and time.time()<min(task_spec['deadline'],self.lifetime_deadline()),'BUDGET_EXHAUSTED','Original lifetime ended during fallback preparation')
            await self.start_worker(replacement)
            require(not self.closing and self.journal.one('SELECT state FROM tasks WHERE task_id=?',(task['task_id'],))['state']=='QUEUED','USER_CANCELED','Fallback was canceled during readiness')
            self.journal.event(task['task_id'],'fallback-admitted',{'worker_id':worker_id,'provider':candidate['provider'],'model':candidate['model'],'from_worker_id':old['worker_id']})
            await self.send_worker(worker_id,{'operation':'task','task_id':task['task_id'],'text':task_spec.get('input_text',task_spec['text'])})
        except Exception as error:
            self.fail_worker(worker_id,error.json() if isinstance(error,PeerError) else {'code':'FALLBACK_FAILED','message':str(error)[:1000]},1)
            if worker_id in self.children:await self.release_worker(worker_id,expected=self.children[worker_id])
            raise
        self.finished_at=None;self.changed.set()

    def fail_worker(self,worker_id,error,generation=None,*,before_admission=False):
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        if generation is not None and row['generation']!=generation:return
        self.journal.db.execute("UPDATE workers SET state=? WHERE worker_id=?",('RELEASING' if worker_id in self.children else 'FAILED',worker_id))
        for task in self.tasks():
            cleanup_cancel=before_admission and task['state']=='CANCELED' and task['terminal_reason']=='WORKER_RELEASED' and task['result_artifact_id'] is None and task['native_turn_id'] is None
            if task['worker_id']!=worker_id or task['state'] in TERMINAL and not cleanup_cancel:continue
            unstarted=(task['state']=='QUEUED' or cleanup_cancel) and task['native_turn_id'] is None
            task_error=error if unstarted else {'code':'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS','cause':error}
            state='FAILED' if unstarted else 'INTERRUPTED'
            record=dict(schema_version=1,group_id=self.id,task_id=task['task_id'],worker={'provider':row['provider'],
                'native_session_id':row['native_id'],'title':row['role_title'],'peer_key':row['peer_key']},status=state.lower(),summary=task_error.get('message') or task_error['code'],error=task_error,
                model_requested=row['model_requested'],model_observed=row['model_observed'],native_turn_ids=[],started_at=None,completed_at=time.time())
            artifact=Artifacts(self.journal).put(canonical(record),row['peer_key'] or worker_id,[self.row['parent_key']],media_type='application/json')
            changed=self.journal.db.execute("UPDATE tasks SET state=?,result_artifact_id=?,terminal_reason=? WHERE task_id=? AND generation=? AND (state NOT IN ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED') OR (? AND state='CANCELED' AND terminal_reason='WORKER_RELEASED' AND result_artifact_id IS NULL AND native_turn_id IS NULL))",(state,artifact,json.dumps(task_error),task['task_id'],row['generation'],cleanup_cancel)).rowcount
            if changed:self.journal.db.execute('INSERT OR IGNORE INTO task_attempts VALUES(?,?,?,?,?)',(task['task_id'],json.loads(task['task_json']).get('attempt_index',0),worker_id,state,artifact))
        self.changed.set()

    async def cancel_worker(self,worker_id,reason=None,*,expected=None):
        if worker_id not in self.children or (expected is not None and self.children[worker_id] is not expected):return
        try:await self.send_worker(worker_id,dict(operation='cancel',**({'reason':reason} if reason else {})))
        except (BrokenPipeError,ConnectionError,asyncio.TimeoutError):pass

    async def cleanup_native(self,worker_id,child):
        path=self.journal.root/'workers'/(worker_id+'.json')
        require(path.exists(),'OWNERSHIP_UNKNOWN','Owned provider process manifest is missing; capacity remains reserved')
        manifest=json.loads(path.read_text())
        row=self.journal.one('SELECT * FROM workers WHERE worker_id=?',(worker_id,))
        if not row or row['generation']!=child['row']['generation']:return
        require(manifest.get('worker_id')==worker_id and manifest.get('generation')==row['generation'] and manifest.get('native_id')==row['native_id'],'OWNERSHIP_UNKNOWN','Owned provider manifest differs from its generation; capacity remains reserved')
        owner=manifest.get('native_process')
        records=[r for r in [owner,*manifest.get('native_descendants',[])] if r]
        for sig,grace in ((signal.SIGTERM,1),(signal.SIGKILL,2)):
            for record in reversed(records):
                try:signal_captured(record,sig)
                except ProcessLookupError:pass
            deadline=time.monotonic()+grace
            while any(owned_status(r)!='DEAD' for r in records) and time.monotonic()<deadline:await asyncio.sleep(.02)
        require(not any(owned_status(r)!='DEAD' for r in records),'CLEANUP_UNCERTAIN','Owned native descendants did not stop; capacity remains reserved')
        require((manifest.get('cleanup') or {}).get('state')=='CLOSED' and (manifest.get('cleanup') or {}).get('output_eof') is True,
                'CLEANUP_UNCERTAIN','Native output closure was unproved; retained ownership requires inspection')
        if records:self.journal.event(worker_id,'orphan-provider-stopped',{'generation':row['generation'],'pids':[r['pid'] for r in records]})

    async def release_worker(self,worker_id,*,expected=None):
        child=self.children[worker_id]
        if expected is not None and child is not expected:return
        self.journal.db.execute("UPDATE workers SET state='RELEASING' WHERE worker_id=? AND generation=? AND state!='RELEASED_RESUMABLE'",(worker_id,child['row']['generation']))
        try:
            try:await self.send_worker(worker_id,dict(operation='release',**({'reason':self.shutdown_reason} if self.shutdown_reason else {})))
            except (BrokenPipeError,ConnectionError,asyncio.TimeoutError):pass
        except BaseException:
            if child.get('lease_fd') is not None:
                os.close(child['lease_fd']);child['lease_fd']=None
            raise
        # Lease closure does not depend on a writable control pipe or successful
        # process inspection. Post-write failures never establish nondelivery.
        try:
            try:await asyncio.wait_for(child['process'].wait(),2)
            finally:
                if child.get('lease_fd') is not None:
                    os.close(child['lease_fd']);child['lease_fd']=None
        except asyncio.TimeoutError:
            try:await asyncio.wait_for(child['process'].wait(),20)
            except asyncio.TimeoutError:
                await self.cleanup_native(worker_id,child)
                require(child.get('owner'),'OWNERSHIP_UNKNOWN','Created controller identity was not captured; lease is closed and ownership remains reserved')
                signal_captured(child['owner'],signal.SIGTERM)
                try:await asyncio.wait_for(child['process'].wait(),5)
                except asyncio.TimeoutError:
                    signal_captured(child['owner'],signal.SIGKILL)
                    await asyncio.wait_for(child['process'].wait(),5)
        # A dead controller returns immediately from wait; its separately grouped
        # native process still needs exact, generation-fenced cleanup.
        await self.cleanup_native(worker_id,child)
        require(child.get('owner') and owned_status(child['owner'])=='DEAD','OWNERSHIP_CONFLICT','Worker controller still owns its process')
        self.journal.db.execute("UPDATE workers SET state=CASE WHEN native_id IS NULL THEN 'FAILED' ELSE 'RELEASED_RESUMABLE' END WHERE worker_id=? AND generation=?",(worker_id,child['row']['generation']))

    async def initialize(self):
        self.server=await asyncio.start_unix_server(self.connection,path=str(self.endpoint),limit=PEER_FRAME+1)
        os.chmod(self.endpoint,0o600)
        atomic_json(self.journal.root/'groups'/self.id/'owner.json',dict(owner=self.owner,group_id=self.id,generation=self.generation,endpoint=str(self.endpoint),token=self.token))
        self.journal.db.execute("UPDATE groups SET state='STARTING',owner_manifest=? WHERE group_id=?",(canonical({'owner':self.owner,'endpoint':str(self.endpoint),'generation':self.generation}).decode(),self.id))
        rows=self.journal.all("SELECT * FROM workers WHERE group_id=? AND state='RESERVED'",(self.id,))
        results=await asyncio.gather(*[self.start_worker(row) for row in rows],return_exceptions=True)
        if any(isinstance(r,BaseException) for r in results):
            for row in rows:self.fail_worker(row['worker_id'],{'code':'CAPABILITY_MISMATCH','message':'Batch readiness failed before task admission'},row['generation'],before_admission=True)
            # No task has crossed the readiness barrier. End every started lifetime
            # now; ordinary follow-up grace is only for admitted work.
            started=[(row['worker_id'],self.children[row['worker_id']]) for row in rows if row['worker_id'] in self.children]
            cleanup=await asyncio.gather(*[self.release_worker(worker,expected=child) for worker,child in started],return_exceptions=True)
            require(not any(isinstance(r,BaseException) for r in cleanup),'OWNERSHIP_UNKNOWN','Batch startup failed and an owned writer could not be proved stopped; capacity remains reserved')
            return
        self.journal.db.execute("UPDATE groups SET state='RUNNING' WHERE group_id=?",(self.id,))
        # Readiness barrier is complete for both controllers before either native task is submitted.
        await asyncio.gather(*[self.send_worker(task['worker_id'],{'operation':'task','task_id':task['task_id'],
            'text':json.loads(task['task_json'])['text']+'\n\nNativePeers contract: work only on this focused task. Do not invoke vendor-native Agent/subagents. Use the bound NativePeers tools for permitted peer communication. Return your own final findings.'})
            for task in self.tasks() if task['state']=='QUEUED' and task['worker_id'] in self.children])

    async def connection(self,reader,writer):
        handler=asyncio.current_task()
        self.connections[handler]=writer
        try:
            await self.respond(reader,writer)
        except (ConnectionError,OSError,TimeoutError):
            # A lost reply does not reverse committed admission or authorize
            # replay. The caller retains its request/task identity as uncertain.
            self.journal.event(self.id,'control-reply-unconfirmed',{})
            _abort_reply(writer)
        except asyncio.CancelledError:
            _abort_reply(writer)
            raise
        finally:
            writer.close()
            try:await asyncio.wait_for(writer.wait_closed(),CONTROL_CLOSE_SECONDS)
            except (ConnectionError,OSError,TimeoutError):_abort_reply(writer)
            except asyncio.CancelledError:
                _abort_reply(writer);raise
            finally:self.connections.pop(handler,None)

    async def respond(self,reader,writer):
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
                require(not self.closing,'SUPERVISOR_RELEASING','Group has begun releasing ownership')
                require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Resume requires the owning parent')
                self.policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(self.id,))['policy_json'])
                row=self.journal.one("SELECT * FROM workers WHERE worker_id=? AND group_id=? AND state='RESERVED'",(payload['worker_id'],self.id))
                require(row,'OWNERSHIP_CONFLICT','Worker is not reserved for this resume')
                self.finished_at=None
                try:await self.start_worker(row)
                except ControllerNotStarted:
                    with self.journal.transaction() as db:
                        changed=db.execute("UPDATE tasks SET state='FAILED',terminal_reason='CONTROLLER_NOT_CREATED' WHERE task_id=? AND worker_id=? AND generation=? AND state='QUEUED' AND native_turn_id IS NULL",(payload['task_id'],row['worker_id'],row['generation'])).rowcount
                        if changed:
                            db.execute("UPDATE workers SET state='RELEASED_RESUMABLE' WHERE worker_id=? AND generation=? AND state='RESERVED'",(row['worker_id'],row['generation']))
                            for phase,tasks in self.policy['phases'].items():
                                if tasks==[payload['task_id']]:db.execute("UPDATE completion_routes SET state='RETAINED' WHERE group_id=? AND completion_generation=? AND state='UNCLAIMED'",(self.id,int(phase)))
                    self.changed.set()
                    raise
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
        wire=canonical(response)+b'\n';require(len(wire)<=CONTROL_RESPONSE,'PAYLOAD_TOO_LARGE','Result response exceeds 2 MiB; use artifact pages')
        writer.write(wire);await asyncio.wait_for(writer.drain(),CONTROL_REPLY_SECONDS)

    async def cancel(self,payload):
        require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Cancellation requires the owning parent')
        handle=payload['handle']
        with self.journal.transaction() as db:
            tasks=self.tasks();scoped=next((t for t in tasks if t['task_id']==handle),None)
            require(handle==self.id or scoped,'PROTOCOL_ERROR','Unknown task handle')
            for task in tasks:
                if (handle==self.id or task['task_id']==handle) and task['state'] in ('QUEUED','FALLBACK_PENDING'):
                    db.execute("UPDATE tasks SET state='CANCELED',terminal_reason='USER_CANCELED' WHERE task_id=? AND state IN ('QUEUED','FALLBACK_PENDING')",(task['task_id'],))
        self.changed.set()
        if scoped:
            if scoped['state'] in ('QUEUED','FALLBACK_PENDING') or scoped['state'] in TERMINAL:
                return {'state':'CANCELED' if scoped['state'] in ('QUEUED','FALLBACK_PENDING') else scoped['state'],'workers':[]}
            await self.send_worker(scoped['worker_id'],{'operation':'cancel-task','task_id':handle})
            return {'state':'CANCEL_REQUESTED','workers':[scoped['worker_id']],'task_id':handle}
        targets={t['worker_id']:self.children.get(t['worker_id']) for t in tasks}
        await asyncio.gather(*[self.cancel_worker(w,expected=child) for w,child in targets.items() if child is not None])
        for worker,child in targets.items():
            if child is not None:asyncio.create_task(self.cancel_and_release(worker,expected=child))
        return {'state':'CANCEL_REQUESTED','workers':list(targets)}

    async def cancel_and_release(self,worker_id,*,expected):
        generation=expected['row']['generation']
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if self.children.get(worker_id) is not expected:return
            if not any(t['worker_id']==worker_id and t['generation']==generation and t['state']=='RUNNING' for t in self.tasks()):break
            await asyncio.sleep(.05)
        await self.release_worker(worker_id,expected=expected)
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
        from .presentation import render
        input_text=render(message)
        with self.journal.transaction() as db:
            count=db.execute("SELECT COUNT(*) FROM tasks WHERE worker_id=? AND state='QUEUED'",(worker_id,)).fetchone()[0]
            require(count<32,'CAPACITY_UNAVAILABLE','Worker follow-up queue is full')
            from .usage.service import Usage
            lease=json.loads(row['lease_json'])
            Usage(self.journal).admit_local_demand(lease,worker_id=worker_id)
            db.execute('UPDATE workers SET lease_json=? WHERE worker_id=?',(canonical(lease).decode(),worker_id))
            db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task_id,worker_id,message['message_id'],None,'QUEUED',None,None,row['generation'],canonical({'text':input_text,'initial':False}).decode()))
            db.execute('UPDATE messages SET task_id=? WHERE message_id=?',(task_id,message['message_id']))
            policy=json.loads(db.execute('SELECT policy_json FROM groups WHERE group_id=?',(self.id,)).fetchone()[0])
            phases=policy.get('phases',{'1':policy['initial_tasks']})
            generation=max(int(n) for n in phases)+1
            policy['phases']=dict(phases,**{str(generation):[task_id]})
            db.execute('UPDATE groups SET policy_json=? WHERE group_id=?',(canonical(policy).decode(),self.id))
            db.execute('INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)',(self.id,generation,self.row['parent_key'],'wait','UNCLAIMED',None,None,new_id(),self.generation))
        self.policy=policy
        try:
            await self.send_worker(worker_id,{'operation':'task','task_id':task_id,'text':input_text})
        except Exception as error:
            raise PeerError('DELIVERY_UNKNOWN','Follow-up was committed but its controller handoff was not confirmed',
                may_have_received=True,details={'task_id':task_id,'message_id':message['message_id'],
                    'worker_id':worker_id,'cause':type(error).__name__}) from error
        self.finished_at=None;self.changed.set()
        return {'stage':'ACCEPTED_QUEUED','evidence_kind':'controller-queue-admission','external_submission_id':task_id,'native_expiry_enforced':False}

    async def wait(self,payload,reader):
        require(payload.get('caller_key')==self.row['parent_key'],'APPROVAL_BLOCKED','Wait requires the owning parent')
        timeout=payload.get('timeout_ms',30000)/1000;require(0<timeout<=30,'PROTOCOL_ERROR','Wait exceeds 30 seconds')
        mode=payload.get('on_timeout') or ('push' if self.policy['completion_mode']=='push' else 'retain')
        require(mode in ('push','retain'),'PROTOCOL_ERROR','Unknown wait fallback')
        waiter=new_id();deadline=time.monotonic()+timeout;output=False
        disconnected=asyncio.create_task(reader.read(1))
        try:
            while True:
                # A group read includes every phase. Enqueue/resume can add phases
                # while the waiter is suspended, so extend the claim before output.
                self.policy=json.loads(self.journal.one('SELECT policy_json FROM groups WHERE group_id=?',(self.id,))['policy_json'])
                generations=wait_generations(self.id,self.policy,payload['handle'])
                remaining=max(0,deadline-time.monotonic())
                claimed=[g for g in generations if claim_wait(self.journal,self.id,g,self.generation,waiter,remaining)]
                if not disconnected.done():
                    terminal=Delegation(self.journal,Registry(self.journal)).terminal_wait(self.parent,payload['handle'],max(1,int(remaining*1000)),waiter=waiter,controller_generation=self.generation)
                    if terminal is not None:
                        output=True
                        return terminal
                if remaining<=0 or disconnected.done():break
                changed=asyncio.create_task(self.changed.wait())
                try:await asyncio.wait([changed,disconnected],timeout=remaining,return_when=asyncio.FIRST_COMPLETED)
                finally:changed.cancel()
                self.changed.clear()
            return {'ready':False,'state':'WAIT_EXPIRED','notification_disposition':mode if generations else 'batch-notification-unchanged'}
        finally:
            if not output:release_wait(self.journal,self.id,self.generation,waiter,mode)
            disconnected.cancel();self.changed.set()

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
            from .presentation import completion
            text=completion(self.id,generation,phase_results,summary_limit,title_limit)
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
                async with async_advisory_lock(self.journal.root/'locks'/(self.id+'.resume.lock')):
                    deadline=self.lifetime_deadline()
                    parent_gone=not matches(self.policy['parent_owner']) and not self.policy['survive_parent']
                    if not parent_gone and time.time()<=deadline:continue
                    self.closing=True
                    self.journal.db.execute("UPDATE groups SET state='RELEASING' WHERE group_id=?",(self.id,))
                self.shutdown_reason=self.shutdown_reason or ('PARENT_LOST' if parent_gone else 'GROUP_DEADLINE_EXCEEDED')
                # The worker claims RUNNING before submitting native input. This compare-and-set
                # prevents buffered successors from starting after the lifetime has ended.
                self.stop_queued()
                await asyncio.gather(*[self.cancel_worker(w,self.shutdown_reason) for w in self.children])
                if not active:break
                if time.time()>deadline+20 or parent_gone:
                    self.journal.event(self.id,'lifetime-ended',{'parent_gone':parent_gone,'deadline':deadline});break
            pending_push=self.journal.one("SELECT 1 FROM completion_routes WHERE group_id=? AND mode='push' AND state='UNCLAIMED' LIMIT 1",(self.id,))
            if not active and self.finished_at is not None and not pending_push and time.time()-self.finished_at>=self.policy['followup_grace_seconds']:
                async with async_advisory_lock(self.journal.root/'locks'/(self.id+'.resume.lock')):
                    # An owning parent reserves and hands off resume under this lock.
                    # Recheck the journal after acquisition before announcing shutdown.
                    if any(t['state'] not in TERMINAL for t in self.tasks()):
                        self.finished_at=None;continue
                    self.closing=True
                    self.journal.db.execute("UPDATE groups SET state='RELEASING' WHERE group_id=?",(self.id,))
                    break
            self.changed.clear()
            try:await asyncio.wait_for(self.changed.wait(),1)
            except asyncio.TimeoutError:pass

    def stop_queued(self):
        self.journal.db.execute("UPDATE tasks SET state='CANCELED',terminal_reason=? WHERE (state='FALLBACK_PENDING' OR (state='QUEUED' AND native_turn_id IS NULL)) AND EXISTS (SELECT 1 FROM workers w WHERE w.worker_id=tasks.worker_id AND w.group_id=? AND w.generation=tasks.generation)",
                                (self.shutdown_reason or 'SUPERVISOR_RELEASED',self.id))

    async def close(self):
        self.closing=True
        self.server.close()
        self.stop_queued()
        if self.handoffs:await asyncio.gather(*list(self.handoffs),return_exceptions=True)
        self.journal.db.execute("UPDATE completion_routes SET state='RETAINED' WHERE group_id=? AND state='UNCLAIMED' AND mode='push'",(self.id,))
        for route in self.journal.all("SELECT delivery_id FROM completion_routes WHERE group_id=? AND state='RETAINED'",(self.id,)):
            settle_deferred(self.journal,self.id,route['delivery_id'])
        outcomes=await asyncio.gather(*[self.release_worker(w) for w in self.children],return_exceptions=True)
        unresolved=[{'worker_id':w,'reason':str(error)[:1000]} for w,error in zip(self.children,outcomes) if isinstance(error,BaseException)]
        if unresolved:self.journal.event(self.id,'worker-cleanup-unresolved',{'workers':unresolved})
        # Writer exit can precede consumption of its final buffered events.
        # Finish readers before the caller closes the shared journal.
        readers={c['reader'] for c in self.children.values() if c.get('reader') and c['reader'] is not asyncio.current_task()}
        if readers:
            _,pending=await asyncio.wait(readers,timeout=2)
            for reader in pending:reader.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            if pending:self.journal.event(self.id,'controller-reader-cleanup-uncertain',{'readers':len(pending)})
        # Accepted transports can retain queued output after the listener stops.
        # Abort them independently of a parent that may never read again.
        await asyncio.sleep(0)
        clients=list(self.connections.items())
        for task,writer in clients:
            _abort_reply(writer)
            if task is not asyncio.current_task():task.cancel()
        handlers={task for task,_ in clients if task is not asyncio.current_task()}
        if handlers:
            done,pending=await asyncio.wait(handlers,timeout=CONTROL_CLOSE_SECONDS)
            for task in done:
                if not task.cancelled():task.exception()
            if pending:unresolved.append({'control_handlers':len(pending),'reason':'Control handler cleanup remains unproved'})
        try:await asyncio.wait_for(self.server.wait_closed(),CONTROL_CLOSE_SECONDS)
        except TimeoutError:
            unresolved.append({'reason':'Control socket transport closure remains unproved'})
        self.endpoint.unlink(missing_ok=True)
        atomic_json(self.journal.root/'groups'/self.id/'owner.json',dict(owner=self.owner,group_id=self.id,generation=self.generation,endpoint=str(self.endpoint),token=self.token,released=not bool(unresolved),cleanup_unresolved=unresolved))
        final='COMPLETED' if all(t['state']=='COMPLETED' for t in self.tasks()) else 'PARTIAL'
        if unresolved:final='PARTIAL'
        self.journal.db.execute('UPDATE groups SET state=? WHERE group_id=?',(final,self.id))
        self.journal.event(self.id,'supervisor-released',{'owner':self.owner,'cleanup_unresolved':unresolved})


async def run(group_id):
    journal=Journal()
    with advisory_lock(journal.root/'locks'/(group_id+'.supervisor.lock'),timeout=.1):
        group=Group(journal,group_id)
        try:await group.initialize();await group.monitor()
        finally:await group.close();journal.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--group-id',required=True);args=parser.parse_args();asyncio.run(run(args.group_id))


if __name__=='__main__':main()
