"""Supported Antigravity CLI catalog, native metadata and short lifecycle hooks."""
import argparse
import asyncio
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from .errors import PeerError,require
from .files import canonical
from .journal import Journal
from .process import ancestry
from .registry import Registry


def native_home():
    return Path.home()/'.gemini/antigravity-cli'


def summaries(*, native_id=None, title=None, home=None):
    path=(home or native_home())/'conversation_summaries.db'
    if not path.exists():return []
    require(native_id is not None or title is not None,'PROTOCOL_ERROR','An exact native ID or title is required')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=1) as db:
        db.row_factory=sqlite3.Row
        field,value=('conversation_id',native_id) if native_id is not None else ('title',title)
        return [dict(r) for r in db.execute('SELECT conversation_id,title FROM conversation_summaries WHERE '+field+'=?',(value,))]


def resolve_name(name):
    rows=summaries(title=name)
    require(rows,'TARGET_OFFLINE','No Antigravity conversation has that exact native title')
    require(len(rows)==1,'AMBIGUOUS_TARGET','Several Antigravity histories have that title; use a native UUID',details=rows)
    return rows[0]['conversation_id']


async def catalog(executable,*,timeout=30,terminate_grace=1):
    from .launch import launch_environment
    import signal,subprocess
    from .process import identity,descendants,owned_status,signal_captured
    # Register the synchronous creation handle before any fallible inspection.
    process=subprocess.Popen([str(executable),'models'],env=launch_environment('antigravity'),
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    owner=None;captured={};eof=[False,False];buffers=[bytearray(),bytearray()];overflow=False
    for stream in (process.stdout,process.stderr):os.set_blocking(stream.fileno(),False)
    async def drain(stream,index):
        nonlocal overflow
        while True:
            try:chunk=os.read(stream.fileno(),16384)
            except BlockingIOError:await asyncio.sleep(.01);continue
            if not chunk:eof[index]=True;return
            available=1048576-sum(map(len,buffers))
            buffers[index].extend(chunk[:max(0,available)])
            if len(chunk)>available:overflow=True
    readers=[asyncio.create_task(drain(stream,i)) for i,stream in enumerate((process.stdout,process.stderr))]
    failure=None
    try:
        async with asyncio.timeout(timeout):
            while process.poll() is None or not all(eof):
                if owner is None and process.poll() is None:
                    try:
                        current=identity(process.pid).json()
                        if current['ppid']==os.getpid():owner=current
                    except (ProcessLookupError,FileNotFoundError,PermissionError):pass
                for record in descendants([owner,*captured.values()]):captured[record['pid']]=record
                for reader in readers:
                    if reader.done():reader.result()
                require(not overflow,'SOURCE_OUTPUT_LIMIT','Antigravity model catalog exceeds 1 MiB')
                await asyncio.sleep(.02)
    except BaseException as error:failure=error
    finally:
        # EOF and leader exit are independent; escaped captured children remain
        # owned by birth, never by an exited leader's process group.
        for sig,grace in ((signal.SIGTERM,terminate_grace),(signal.SIGKILL,terminate_grace)):
            records=[r for r in [owner,*captured.values()] if r]
            for record in reversed(records):signal_captured(record,sig)
            deadline=asyncio.get_running_loop().time()+grace
            while (process.poll() is None or any(owned_status(r)!='DEAD' for r in records)) and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.01)
        _,pending=await asyncio.wait(readers,timeout=terminate_grace)
        for reader in pending:reader.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
        cleanup=dict(state='CLOSED',controller_pid=process.pid,owner=owner,captured_descendants=list(captured.values()),
            provider_exited=process.poll() is not None,stdout_eof=eof[0],stderr_eof=eof[1])
        uncertain=owner is None or not cleanup['provider_exited'] or not all(eof) or any(owned_status(r)!='DEAD' for r in captured.values())
        if uncertain:cleanup['state']='UNCERTAIN'
        for stream in (process.stdout,process.stderr):stream.close()
    if isinstance(failure,asyncio.CancelledError):raise failure
    require(not uncertain,'CLEANUP_UNCERTAIN','Antigravity catalog process/output closure was not proved',details=cleanup)
    if failure:raise failure
    output,error=map(bytes,buffers)
    if process.returncode!=0:
        from .provider_errors import classify
        text=(error or output).decode(errors='replace')[-4000:]
        try:terminal=json.loads(text)
        except ValueError:terminal=text
        classification=classify(terminal)
        code={'AUTH_REQUIRED':'AUTH_REQUIRED','RATE_LIMITED':'SOURCE_RATE_LIMITED'}.get(classification,'SOURCE_UNAVAILABLE')
        message='Antigravity native login is unavailable for the selected home; check login in agy.' if code=='AUTH_REQUIRED' else 'Antigravity native model catalog could not be read: '+code
        raise PeerError(code,message)
    rows={line.split('\t',1)[0]:line.split('\t',1)[1] for line in output.decode().splitlines() if '\t' in line}
    require(rows,'CAPABILITY_MISMATCH','Antigravity returned no valid native model catalog entries')
    return rows


def arguments(spec,native_id=None):
    args=['--input-format','stream-json','--output-format','stream-json','--model',spec['model'],
          '--agent','nativepeers-'+spec['permission_profile'],'--sandbox',
          '--print-timeout',str(spec.get('wall_time_seconds',1800))+'s']
    if native_id:args+=['--conversation',native_id]
    if spec.get('reasoning_effort'):args+=['--effort',spec['reasoning_effort']]
    return args


async def queue_native_hook(peer,message):
    return {'stage':'SUBMITTED_UNCONFIRMED','evidence_kind':'local-journal-pending-antigravity-hook',
            'native_wakeup':False,'message_id':message['message_id'],
            'limitation':'Pending the exact owner next native invocation; no idle wakeup or model ingestion is claimed.'}


def observe(journal,event,kind):
    native=event['conversationId'];binary=(Path.home()/'.local/bin/agy').resolve()
    ancestors=[p.json() for p in ancestry()[1:]]
    owners=[p for p in ancestors if p['executable']==str(binary)]
    require(owners,'CALLER_UNBOUND','Antigravity hook has no exact native CLI owner in its ancestry')
    owner=owners[0]
    bootstrap=os.getenv('NPEER_BOOTSTRAP')
    if bootstrap:
        from .caller import read_binding, verify_owned_binding
        path=Path(bootstrap)
        lease=read_binding(journal,path,allow_reserved=True)
        row=verify_owned_binding(journal,lease,ancestors,provider='antigravity',native_id=native)
        worker_id=row['worker_id']
        journal.event(worker_id,'native-hook-observed',{'native_id':native,'model':event.get('modelName'),'event':kind,'process':owner})
        if kind=='PreToolUse':
            from .antigravity_install import allowed_tools
            row=journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(worker_id,))
            require(row,'CALLER_UNBOUND','Worker lease no longer exists')
            spec=json.loads(row['lease_json'])['spec']
            name=event['toolCall']['name'];allowed=allowed_tools(spec['permission_profile'],name,event['toolCall'].get('args'))
            journal.event(worker_id,'antigravity-tool-gate',{'native_id':native,'tool':name,'allowed':allowed})
            if not allowed:
                return {'decision':'deny','reason':'Tool is outside this NativePeers worker profile.'}
            return {'decision':'allow'}
        return {}
    from .compatibility import verify
    verify(journal,'antigravity',owner['executable'])
    home=native_home();registry=Registry(journal)
    transcript=Path(event['transcriptPath']).expanduser().resolve()
    require(transcript.is_relative_to(home.resolve()) and native in transcript.parts,
            'OWNERSHIP_CONFLICT','Native transcript path differs from hook UUID/home')
    workspaces=event.get('workspacePaths')
    require(isinstance(workspaces,list) and workspaces and isinstance(workspaces[0],str) and Path(workspaces[0]).is_absolute(),
            'PROTOCOL_ERROR','Native hook has no absolute workspace path')
    existing=registry.for_owner('antigravity',owner,native)
    if existing:peer=existing[0]
    else:
        from .policy import load
        policy=load(journal);names=summaries(native_id=native,home=home)
        peer=registry.register('antigravity',home,native,names[0]['title'] if names else None,owner,workspaces[0],
            {'kind':'antigravity-native-hook'},policy={'can_spawn':True,'profiles':['analysis','workspace'],'max_depth':policy['max_depth'],
                'max_concurrent_workers':policy['workers_per_group']},capabilities={'idle_wakeup':False,'bounded_wait':True})
        peer['native_transcript']=str(transcript)
        journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
    if kind!='PreInvocation':return {'decision':'allow'} if kind=='PreToolUse' else {}
    from .presentation import render
    messages=[]
    with journal.transaction():
        pending=journal.all("SELECT m.message_id,m.payload_json,a.attempt_id FROM messages m JOIN attempts a USING(message_id) WHERE m.to_key=? AND a.target_incarnation=? AND m.expires_at>? AND a.state='SUBMITTED_UNCONFIRMED' AND NOT EXISTS (SELECT 1 FROM events e WHERE e.subject_id=m.message_id AND e.kind IN ('antigravity-hook-output-unconfirmed','notification-suppressed')) ORDER BY m.created_at LIMIT 10",
                            (peer['handle'],peer['incarnation_id'],time.time()))
        from .notification_policy import suppress
        for row in pending:
            # Quiet, closed-exchange and closed-phase frames stay retained and are never marked as output.
            try:retained=suppress(journal,row['message_id'],json.loads(row['payload_json']),dict(evidence='pending-hook-frame',transport='antigravity-native-hook',attempt_id=row['attempt_id'],native_id=native))
            except (ValueError,TypeError,AttributeError,KeyError):retained=None  # Malformed legacy frames keep their existing refusal path.
            if retained:continue
            value=render(json.loads(row['payload_json']))
            content='\n\n'.join([*messages,value])
            if len(canonical({'injectSteps':[{'ephemeralMessage':content}]}))>65536:break
            messages.append(value)
            journal.event(row['message_id'],'antigravity-hook-output-unconfirmed',{'attempt_id':row['attempt_id'],'native_id':native,'invocation':event.get('invocationNum')})
    if not messages:return {}
    # Peer provenance stays explicit; this is not an injected human instruction or a processing receipt.
    return {'injectSteps':[{'ephemeralMessage':'\n\n'.join(messages)}]}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('operation',choices=['hook','resolve-name']);parser.add_argument('value');args=parser.parse_args()
    if args.operation=='resolve-name':
        try:print(resolve_name(args.value))
        except Exception as error:print(str(error),file=sys.stderr);raise SystemExit(2)
        return
    journal=Journal();event=None
    try:
        raw=sys.stdin.buffer.read(65537);require(len(raw)<=65536,'PAYLOAD_TOO_LARGE','Hook input too large')
        event=json.loads(raw);print(json.dumps(observe(journal,event,args.value)))
    except Exception as error:
        print('NativePeers Antigravity hook unavailable: '+getattr(error,'code',type(error).__name__)+': '+str(error),file=sys.stderr)
        journal.event('antigravity-hook','hook-binding-error',{'error':str(error),'native_id':event.get('conversationId') if isinstance(event,dict) else None,
            'ancestry':[p.json() for p in ancestry()],'event':args.value,'verified_caller':False})
        if args.value=='PreToolUse':
            print(json.dumps({'decision':'deny','reason':'NativePeers could not verify the owned worker tool grant.'} if os.getenv('NPEER_BOOTSTRAP') else {'decision':'allow'}))
        else:print('{}')
    finally:journal.close()


if __name__=='__main__':main()
