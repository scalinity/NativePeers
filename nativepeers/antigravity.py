"""Supported Antigravity CLI catalog, native metadata and short lifecycle hooks."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from .errors import require
from .files import canonical
from .journal import Journal
from .process import ancestry, matches
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


async def catalog(executable):
    from .launch import launch_environment
    process=await asyncio.create_subprocess_exec(str(executable),'models',env=launch_environment('antigravity'),
        stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try:
        output,error=await asyncio.wait_for(process.communicate(),30)
    except BaseException:
        if process.returncode is None:process.terminate();await process.wait()
        raise
    require(process.returncode==0,'AUTH_REQUIRED' if b'sign in' in (output+error).lower() else 'CAPABILITY_MISMATCH',
            'Antigravity could not list native models; sign in through agy before launching workers')
    return {line.split('\t',1)[0]:line.split('\t',1)[1] for line in output.decode().splitlines() if '\t' in line}


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
        from .caller import native_ancestor
        path=Path(bootstrap)
        require(path.parent.resolve()==(journal.root/'bindings').resolve() and not path.is_symlink(),
                'CALLER_UNBOUND','Invalid Antigravity bootstrap path')
        lease=json.loads(path.read_text())
        require(matches(lease['owner']) and native_ancestor(lease['owner'],ancestors) and lease['expires_at']>time.time(),
                'CALLER_UNBOUND','Owned controller is outside hook ancestry or expired')
        worker_id=lease.get('worker_id') or lease['grants']['worker_id']
        journal.event(worker_id,'native-hook-observed',{'native_id':native,'model':event.get('modelName'),'event':kind,'process':owner})
        if kind=='PreToolUse':
            from .antigravity_install import allowed_tools
            row=journal.one('SELECT lease_json FROM workers WHERE worker_id=?',(worker_id,))
            require(row,'CALLER_UNBOUND','Worker lease no longer exists')
            spec=json.loads(row['lease_json'])['spec']
            name=event['toolCall']['name'];allowed=allowed_tools(spec['permission_profile'],name)
            journal.event(worker_id,'antigravity-tool-gate',{'native_id':native,'tool':name,'allowed':allowed})
            if not allowed:
                return {'decision':'deny','reason':'Tool is outside this NativePeers worker profile.'}
            return {'decision':'allow'}
        return {}
    home=native_home();registry=Registry(journal)
    existing=[p for p in registry.list('antigravity') if p['owner']==owner and p['peer_key']['native_session_id']==native]
    if existing:peer=existing[0]
    else:
        from .policy import load
        policy=load(journal);names=summaries(native_id=native,home=home)
        peer=registry.register('antigravity',home,native,names[0]['title'] if names else None,owner,event['workspacePaths'][0],
            {'kind':'antigravity-native-hook'},policy={'can_spawn':True,'profiles':['analysis','workspace'],'max_depth':policy['max_depth'],
                'max_concurrent_workers':policy['workers_per_group']},capabilities={'idle_wakeup':False,'bounded_wait':True})
        transcript=Path(event['transcriptPath']).expanduser().resolve()
        require(transcript.is_relative_to(home.resolve()) and native in transcript.parts,'OWNERSHIP_CONFLICT','Native transcript path differs from hook UUID/home')
        peer['native_transcript']=str(transcript)
        journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
    if kind!='PreInvocation':return {'decision':'allow'} if kind=='PreToolUse' else {}
    messages=[];size=0
    with journal.transaction():
        pending=journal.all("SELECT m.message_id,m.payload_json,a.attempt_id FROM messages m JOIN attempts a USING(message_id) WHERE m.to_key=? AND a.target_incarnation=? AND m.expires_at>? AND a.state='SUBMITTED_UNCONFIRMED' AND NOT EXISTS (SELECT 1 FROM events e WHERE e.subject_id=m.message_id AND e.kind='antigravity-hook-output-unconfirmed') ORDER BY m.created_at LIMIT 10",
                            (peer['handle'],peer['incarnation_id'],time.time()))
        for row in pending:
            value=json.loads(row['payload_json']);size+=len(canonical(value))
            if size>65536:break
            messages.append(value)
            journal.event(row['message_id'],'antigravity-hook-output-unconfirmed',{'attempt_id':row['attempt_id'],'native_id':native,'invocation':event.get('invocationNum')})
    if not messages:return {}
    # Peer provenance stays explicit; this is not an injected human instruction or a processing receipt.
    return {'injectSteps':[{'ephemeralMessage':'NativePeers peer/controller messages; preserve sender provenance and native permissions. Reply only to peer messages using their message_id. Do not reply to controller notices.\n'+canonical(messages).decode()}]}


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
