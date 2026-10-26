"""Exact Grok hook identity and bounded native context delivery."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .errors import require
from .files import canonical
from .grok import executable,native_home,summaries
from .journal import Journal
from .process import ancestry
from .registry import Registry

EVENTS={'SessionStart':'session_start','SessionEnd':'session_end','UserPromptSubmit':'user_prompt_submit','PreToolUse':'pre_tool_use'}


def kernel_sandbox(spec):
    """Probe inherited Seatbelt permissions without creating or changing a file."""
    profile=spec['permission_profile'];cwd=Path(spec['cwd']).resolve()
    result=dict(profile=profile,verified=False,checked_pid=os.getpid(),checks=[])
    if sys.platform!='darwin':return dict(result,reason='Kernel permission probe is qualified only on macOS')
    if not spec.get('state_root'):return dict(result,reason='The exact NativePeers state root is required')
    try:
        import ctypes
        library=ctypes.CDLL('/usr/lib/libsandbox.dylib',use_errno=True)
        check=library.sandbox_check;check.restype=ctypes.c_int
        targets=[(cwd/'.npeer-kernel-probe',profile=='analysis'),
                 (Path(spec['state_root']).resolve()/'grok-kernel-denied/.npeer-kernel-probe',True)]
        if profile=='workspace':targets.append((cwd.parent/'.npeer-kernel-probe',True))
        for path,denied in targets:
            state=check(ctypes.c_int(os.getpid()),ctypes.c_char_p(b'file-write-data'),ctypes.c_int(1),ctypes.c_char_p(os.fsencode(path)))
            result['checks'].append(dict(path=str(path),operation='file-write-data',result=state,expected_denied=denied))
        result['verified']=profile in ('analysis','workspace') and all(c['result']==(1 if c['expected_denied'] else 0) for c in result['checks'])
    except (OSError,AttributeError,ValueError) as error:result['reason']=type(error).__name__
    return result


def observe(journal,event,kind):
    require(isinstance(event,dict) and kind in EVENTS and event.get('hookEventName')==EVENTS[kind]
            and event.get('hook_event_name',kind)==kind,'PROTOCOL_ERROR','Expected a Grok-native hook event')
    native=event.get('sessionId')
    try:require(str(uuid.UUID(native))==native,'PROTOCOL_ERROR','Invalid Grok native UUID')
    except (ValueError,TypeError,AttributeError):require(False,'PROTOCOL_ERROR','Invalid Grok native UUID')
    require(event.get('session_id',native)==native,'OWNERSHIP_CONFLICT','Grok hook native UUID aliases disagree')
    cwd=event.get('workspaceRoot') or event.get('cwd')
    require(isinstance(cwd,str) and Path(cwd).is_absolute(),'PROTOCOL_ERROR','Grok hook has no absolute workspace')
    ancestors=[p.json() for p in ancestry()[1:]]
    binary=executable()
    bootstrap=os.getenv('NPEER_BOOTSTRAP');lease=None;row=None
    if bootstrap:
        from .caller import read_binding,verify_owned_binding
        lease=read_binding(journal,Path(bootstrap),allow_reserved=True)
        row=verify_owned_binding(journal,lease,ancestors,provider='grok',native_id=native)
        full=json.loads(row['lease_json'])
        binary=Path(full['verified_runtime']['path'] if full.get('verified_runtime') else full['executable']).resolve()
    owners=[p for p in ancestors if p['executable']==str(binary)]
    require(owners,'CALLER_UNBOUND','Grok hook has no exact native CLI owner in its ancestry')
    owner=owners[0]
    from .caller import native_ancestor,nearest_native
    nearest=nearest_native(ancestors,binary,journal=journal)
    require(nearest is not None and native_ancestor(owner,[nearest]),'CALLER_UNBOUND','Grok hook belongs to an enclosing native session')
    if bootstrap:
        spec=dict(json.loads(row['lease_json'])['spec'],state_root=str(journal.root))
        from .compatibility import verify
        verify(journal,'grok',owner['executable'],worker_id=row['worker_id'])
        names=summaries(native_id=native,home=Path(spec.get('provider_home') or native_home()))
        journal.event(row['worker_id'],'native-hook-observed',dict(native_id=native,model=event.get('modelName') or event.get('model') or (names[0]['model'] if len(names)==1 else None),event=kind,process=owner))
        kernel=kernel_sandbox(spec)
        journal.event(row['worker_id'],'grok-kernel-check',dict(kernel,native_id=native,process=owner))
        if kind=='PreToolUse':
            from .grok_worker import allowed_tools
            name=event.get('toolName');allowed=kernel['verified'] and name in allowed_tools(spec['permission_profile']) and event.get('toolInputTruncated') is not True
            journal.event(row['worker_id'],'grok-tool-gate',dict(native_id=native,tool=name,allowed=allowed))
            return {'decision':'allow' if allowed else 'deny',**({} if allowed else {'reason':'Tool is outside this NativePeers worker profile.'})}
        return {}
    from .compatibility import verify
    verify(journal,'grok',owner['executable'])
    home=native_home();registry=Registry(journal)
    existing=registry.for_owner('grok',owner,native)
    if kind=='SessionEnd':
        if event.get('subagentType'):return {}
        journal.db.execute("UPDATE peer_instances SET status='offline' WHERE provider='grok' AND native_id=? AND pid=? AND process_start=? AND owner_kind='interactive-standalone'",(native,owner['pid'],owner['start']))
        return {}
    names=summaries(native_id=native,home=home)
    require(len(names)==1 and Path(names[0]['cwd']).resolve()==Path(cwd).resolve(),
            'OWNERSHIP_CONFLICT','Grok hook has no unique matching native summary in the selected home/workspace')
    if existing:
        peer=existing[0]
        mode=event.get('permissionMode')
        profiles=['analysis']+(['workspace'] if mode in ('default','acceptEdits','auto','dontAsk','bypassPermissions') else [])
        peer['policy']=dict(peer['policy'],profiles=profiles,permission_mode_observed=mode)
        journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
    else:
        require(kind=='SessionStart' and not event.get('subagentType'),'CALLER_UNBOUND','Grok-native session has no top-level startup registration')
        from .policy import load
        policy=load(journal);mode=event.get('permissionMode')
        profiles=['analysis']+(['workspace'] if mode in ('default','acceptEdits','auto','dontAsk','bypassPermissions') else [])
        peer=registry.register('grok',home,native,names[0]['title'],owner,cwd,{'kind':'grok-native-hook'},
            policy=dict(can_spawn=True,profiles=profiles,max_depth=policy['max_depth'],max_concurrent_workers=policy['workers_per_group'],permission_mode_observed=mode),
            capabilities=dict(idle_wakeup=False,bounded_wait=True))
        peer['native_transcript']=str(Path(names[0]['path']).parent/'chat_history.jsonl')
        journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
    if kind!='PreToolUse':return {}
    from .presentation import render
    messages=[]
    with journal.transaction():
        pending=journal.all("SELECT m.message_id,m.payload_json,a.attempt_id FROM messages m JOIN attempts a USING(message_id) WHERE m.to_key=? AND a.target_incarnation=? AND m.expires_at>? AND a.state='SUBMITTED_UNCONFIRMED' AND NOT EXISTS (SELECT 1 FROM events e WHERE e.subject_id=m.message_id AND e.kind='grok-hook-output-unconfirmed') ORDER BY m.created_at LIMIT 10",(peer['handle'],peer['incarnation_id'],time.time()))
        for row in pending:
            content='\n\n'.join([*messages,render(json.loads(row['payload_json']))])
            if len(canonical({'hookSpecificOutput':{'hookEventName':kind,'additionalContext':content}}))>65536:break
            messages.append(render(json.loads(row['payload_json'])))
            journal.event(row['message_id'],'grok-hook-output-unconfirmed',dict(attempt_id=row['attempt_id'],native_id=native,prompt_id=event.get('promptId')))
    # Grok 1.0.70 discards allowing UserPromptSubmit output. PreToolUse is its
    # documented model-facing context route; emitting it is not an ingestion receipt.
    return {'hookSpecificOutput':{'hookEventName':kind,'additionalContext':'\n\n'.join(messages)}} if messages else {}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('event',choices=EVENTS);args=parser.parse_args()
    journal=None;event=None
    try:
        raw=sys.stdin.buffer.read(262145);require(len(raw)<=262144,'PAYLOAD_TOO_LARGE','Grok hook input too large')
        event=json.loads(raw);journal=Journal();result=observe(journal,event,args.event);print(json.dumps(result))
        if result.get('decision')=='deny' and os.getenv('NPEER_BOOTSTRAP'):raise SystemExit(2)
    except Exception as error:
        print('NativePeers Grok hook unavailable: '+getattr(error,'code',type(error).__name__)+': '+str(error),file=sys.stderr)
        if journal:journal.event('grok-hook','hook-binding-error',dict(error=str(error),native_id=event.get('sessionId') if isinstance(event,dict) else None,event=args.event,verified_caller=False))
        if args.event=='PreToolUse' and os.getenv('NPEER_BOOTSTRAP'):
            print(json.dumps({'decision':'deny','reason':'NativePeers could not verify the owned worker tool grant.'}));raise SystemExit(2)
        print('{}')
    finally:
        if journal:journal.close()


if __name__=='__main__':main()
