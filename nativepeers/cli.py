"""Namespaced command-line interface; model and operator origins are never conflated."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import shlex
import sys

from .caller import bind_caller,Caller
from .core import Core
from .errors import PeerError,require
from .files import canonical
from .journal import Journal
from .models import CONTROL_RESPONSE,new_id
from .process import matches
from .receipts import delivery_error


def parser():
    p=argparse.ArgumentParser(prog='npeer',description='Native local peer messaging and owned workers')
    p.add_argument('--json',action='store_true');subs=p.add_subparsers(dest='command',required=True)
    def command(name):
        child=subs.add_parser(name);child.add_argument('--json',action='store_true',default=argparse.SUPPRESS);return child
    command('whoami')
    q=command('list');q.add_argument('--provider',choices=['claude','codex','antigravity']);q.add_argument('--include-offline',action='store_true')
    q=command('resolve');q.add_argument('--to',required=True)
    q=command('groups');q.add_argument('--mine',action='store_true',required=True);q.add_argument('--limit',type=int,default=20);q.add_argument('--cursor')
    for name in ['send','reply']:
        q=command(name)
        if name=='send':q.add_argument('--to',required=True)
        else:q.add_argument('message_id')
        source=q.add_mutually_exclusive_group(required=True);source.add_argument('--message');source.add_argument('--message-file',type=Path)
        q.add_argument('--request-reply',action='store_true');q.add_argument('--client-request-id')
    q=command('inbox');q.add_argument('--unread',action='store_true');q.add_argument('--limit',type=int,default=20);q.add_argument('--page',action='store_true');q.add_argument('--cursor')
    q=command('ack');q.add_argument('message_id')
    q=command('spawn');q.add_argument('--spec',type=Path,required=True)
    for name in ['status','result','wait','cancel']:
        q=command(name);q.add_argument('handle')
        if name=='status':q.add_argument('--refresh',action='store_true')
        if name=='result':q.add_argument('--include-artifacts',action='store_true')
        if name=='wait':q.add_argument('--timeout-ms',type=int,default=30000);q.add_argument('--on-timeout',choices=['push','retain'])
        if name=='cancel':q.add_argument('--reason',required=True)
    q=command('artifact-add');q.add_argument('--file',type=Path,required=True);q.add_argument('--max-bytes',type=int,default=10485760)
    q=command('artifact-export');q.add_argument('handle');q.add_argument('--output',type=Path,required=True)
    q=command('artifact-read');q.add_argument('handle');q.add_argument('--offset',type=int,default=0);q.add_argument('--limit',type=int,default=262144)
    q=command('resume-command');q.add_argument('worker_id')
    q=command('resume-worker');q.add_argument('worker_id');q.add_argument('--task-file',type=Path,required=True);q.add_argument('--completion-mode',choices=['push','wait']);q.add_argument('--client-request-id')
    q=command('recover');q.add_argument('--group',required=True);q.add_argument('--dry-run',action='store_true')
    q=command('doctor');q.add_argument('--live',action='store_true');q.add_argument('--budget-file',type=Path)
    for name in ['install','uninstall']:
        q=command(name);q.add_argument('--dry-run',action='store_true');q.add_argument('--antigravity',action='store_true')
    return p


def read_file(path,limit=65536):
    with path.open('rb') as stream:raw=stream.read(limit+1)
    require(len(raw)<=limit,'PAYLOAD_TOO_LARGE',f'Input file exceeds {limit} bytes')
    return raw.decode('utf-8')


async def dispatch(args,journal):
    core=Core(journal)
    try:caller=bind_caller(journal,core.registry)
    except PeerError:
        if args.command not in ('list','resolve','doctor','install','uninstall'):raise
        caller=Caller('external-local','external-local')
    name=args.command
    if name=='whoami':return dict(kind=caller.kind,key=caller.key,incarnation_id=caller.incarnation,grants=caller.grants)
    if name=='list':return core.registry.list(args.provider,args.include_offline)
    if name=='resolve':return core.registry.resolve(args.to)
    if name in ('send','reply'):
        return await core.router.send(caller,args.to if name=='send' else 'reply-author',
            args.message if args.message is not None else read_file(args.message_file),expect_reply=args.request_reply,
            client_request_id=args.client_request_id,in_reply_to=args.message_id if name=='reply' else None)
    if name=='inbox':return core.router.inbox(caller,args.limit,args.unread,cursor=args.cursor,paged=args.page)
    if name=='ack':return core.router.ack(caller,args.message_id)
    if name=='groups':return core.delegation.groups(caller,args.limit,args.cursor)
    if name=='spawn':return await core.delegation.spawn(caller,json.loads(read_file(args.spec)))
    if name=='status':return await core.status(caller,args.handle,refresh=args.refresh)
    if name=='result':return core.delegation.result(caller,args.handle)
    if name=='wait':return await core.tool(caller,'task_wait',dict(handle=args.handle,timeout_ms=args.timeout_ms,on_timeout=args.on_timeout))
    if name=='cancel':return await core.delegation.cancel(caller,args.handle,args.reason)
    if name=='artifact-add':
        from .artifacts import Artifacts
        return Artifacts(journal).add_file(args.file,caller,max_bytes=args.max_bytes)
    if name=='artifact-export':
        from .artifacts import Artifacts
        return Artifacts(journal).export(args.handle,args.output,caller)
    if name=='artifact-read':return await core.tool(caller,'artifact_read',dict(handle=args.handle,offset=args.offset,limit=args.limit))
    if name in ('resume-worker','resume-command'):
        from .recovery import resume_command,resume_worker
        return resume_command(journal,caller,args.worker_id) if name=='resume-command' else await resume_worker(journal,caller,args.worker_id,read_file(args.task_file),args.completion_mode,args.client_request_id)
    if name=='recover':
        group=core.delegation.group(caller,args.group)
        workers=journal.all('SELECT worker_id,native_id,state FROM workers WHERE group_id=?',(group['group_id'],))
        messages=journal.all('SELECT message_id FROM messages WHERE from_actor_key LIKE ?',('controller:'+group['group_id']+':%',))
        from .reconcile import observe
        observations=[]
        for message in messages:
            if args.dry_run:observations.append(core.router.outcome(message['message_id']))
            else:observations.append(await observe(journal,core.registry,core.router,message['message_id']))
        from .recovery import reconcile_owned
        lifecycle=reconcile_owned(journal,caller,group['group_id'],dry_run=args.dry_run)
        return {'group_id':group['group_id'],'workers':workers,'message_observations':observations,'lifecycle':lifecycle,'replayed':False,
                'next_action':'Read retained results; uncertain native input was not resubmitted.'}
    if name=='doctor':
        from .compatibility import diagnose
        from .readiness import readiness
        require(not args.live,'BUDGET_EXHAUSTED','Live doctor never submits models automatically; use the explicit approved live harness and durable budget')
        return dict(compatibility=diagnose(journal),integrations=readiness(journal),registered_peers=core.registry.list(include_offline=True),
            stale_workers=[r for r in journal.all('SELECT worker_id,state FROM workers') if r['state'] not in ('RELEASED_RESUMABLE','FAILED') and not (journal.root/'workers'/(r['worker_id']+'.json')).exists()],
            permanent_service_installed=False,unresolved_messages=journal.all("SELECT message_id,state FROM attempts WHERE state IN ('OUTBOX_COMMITTED','ADMISSION_DEFERRED','SUBMITTING','DELIVERY_UNKNOWN','HELD','SUBMITTED_UNCONFIRMED')"))
    if name in ('install','uninstall'):
        if args.antigravity:
            from .antigravity_install import install as install_antigravity
            return install_antigravity(journal,dry_run=args.dry_run,remove=name=='uninstall')
        from .install import install,uninstall
        return await (install if name=='install' else uninstall)(journal,dry_run=args.dry_run)
    raise PeerError('PROTOCOL_ERROR','Unknown command')


def error_exit(error):
    if error.may_have_received:return 6
    code=error.code
    if code.startswith(('TARGET_','AMBIGUOUS')):return 3
    if code in ('CALLER_UNBOUND','CALLER_NOT_READY','APPROVAL_BLOCKED','AUTH_REQUIRED','PROVIDER_POLICY_REFUSED'):return 4
    if code in ('BUDGET_EXHAUSTED','WAIT_EXPIRED','CAPACITY_UNAVAILABLE','EXPIRED_BEFORE_SUBMISSION','CANCELED_BEFORE_SUBMISSION'):return 8
    if code=='CAPABILITY_MISMATCH' or code.startswith(('UNSUPPORTED','PERMISSION_','OWNERSHIP_')):return 5
    return 7


def main():
    args=parser().parse_args();journal=None;request_id=new_id();exit_code=0
    try:
        journal=Journal();value=asyncio.run(dispatch(args,journal))
        response=dict(schema_version=1,ok=True,data=value,error=None,request_id=request_id)
        if isinstance(value,dict) and value.get('state') in ('DELIVERY_UNKNOWN','SUBMITTING','SUBMITTED_UNCONFIRMED','OUTBOX_COMMITTED'):
            exit_code=6
        if isinstance(value,dict) and value.get('state')=='WAIT_EXPIRED':exit_code=8
        if args.command in ('send','reply') and isinstance(value,dict):
            error=delivery_error(value)
            if error:
                response.update(ok=False,error=error.json());exit_code=error_exit(error)
    except PeerError as error:
        response=dict(schema_version=1,ok=False,data=None,error=error.json(),request_id=request_id)
        exit_code=error_exit(error)
    except (ValueError,OSError) as error:
        response=dict(schema_version=1,ok=False,data=None,error={'code':'PROTOCOL_ERROR','message':str(error)[:1000],'may_have_received':False},request_id=request_id);exit_code=2
    finally:
        if journal:journal.close()
    raw=canonical(response)
    if len(raw)>CONTROL_RESPONSE:
        raw=canonical(dict(schema_version=1,ok=False,data=None,error={'code':'PAYLOAD_TOO_LARGE','message':'Result exceeds 2 MiB; request an artifact page'},request_id=request_id));exit_code=2
    print(raw.decode(),flush=True)
    raise SystemExit(exit_code)


if __name__=='__main__':main()
