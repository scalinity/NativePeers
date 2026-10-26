"""Short lifecycle observations. An inherited worker hook never owns its route."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time

from .adapters.metadata import claude_name, codex_name
from .caller import read_binding, verify_owned_binding
from .errors import require
from .files import atomic_json
from .journal import Journal
from .models import new_id
from .process import ancestry
from .registry import Registry


def observe(provider, event, journal):
    if provider=='grok':
        from .grok_hooks import observe as grok_observe
        return grok_observe(journal,event,event.get('hook_event_name'))
    # Grok deliberately supplies Claude aliases, including session_id. An outer
    # Claude process must never turn that native Grok UUID into a Claude peer.
    require(not ('sessionId' in event or 'hookEventName' in event),'CALLER_UNBOUND','A Grok-native hook cannot register as another provider')
    binary=(Path.home()/('.local/bin/claude' if provider=='claude' else '.local/share/codex-mcp-manager/current/bin/codex')).resolve()
    ancestors=[p.json() for p in ancestry()[1:]]
    owners=[p for p in ancestors if p['executable']==str(binary)]
    require(owners,"CALLER_UNBOUND","No exact native provider owner in hook ancestry")
    owner=owners[0]
    bootstrap=os.getenv('NPEER_BOOTSTRAP')
    if bootstrap:
        path=Path(bootstrap)
        lease=read_binding(journal,path,allow_reserved=True)
        row=verify_owned_binding(journal,lease,ancestors,
                                 provider=provider,native_id=event['session_id'])
        # Bounded observation only: no registration, waits, identity change, or turn-end unregistration.
        worker_id=row['worker_id']
        journal.event(worker_id,'native-hook-observed',dict(event=event['hook_event_name'],native_id=event['session_id'],process=owner))
        return {'controller_owned':True,'worker_id':worker_id}
    home=Path(os.getenv('CODEX_HOME' if provider=='codex' else 'CLAUDE_CONFIG_DIR',Path.home()/('.codex' if provider=='codex' else '.claude'))).resolve()
    if event['hook_event_name']=='UserPromptSubmit':
        # Consumption-time close-out guard: the exact registered owner is the authority. It never
        # registers, rebinds or creates an incarnation, and unknown input passes unchanged.
        from .models import home_id
        from .notification_policy import guard
        peer=journal.one("SELECT * FROM peer_instances WHERE provider=? AND home_id=? AND native_id=? AND pid=? AND process_start=? AND status='live' AND owner_kind='interactive-standalone'",
                         (provider,home_id(home),event['session_id'],owner['pid'],owner['start']))
        if not peer:return {'guarded':False,'reason':'no-exact-registered-peer'}
        result=guard(journal,peer,event.get('prompt'))
        if provider=='claude' and result.get('decision')=='block':
            # Claude-documented field: keeps the blocked body out of the visible block notice (not an erase guarantee).
            result['hookSpecificOutput']={'hookEventName':'UserPromptSubmit','suppressOriginalPrompt':True}
        return result
    if provider=='codex':
        from .process import terminal_owner
        require(terminal_owner(owner),'UNSUPPORTED_TRANSPORT','Unowned private app-server has no verified standalone TUI ingress')
    registry=Registry(journal)
    if provider=='claude' and event['hook_event_name']=='PreToolUse':
        row=journal.one("SELECT descriptor_json FROM peer_instances WHERE native_id=? AND pid=? AND process_start=? AND status='live' AND owner_kind='interactive-standalone'",
                        (event['session_id'],owner['pid'],owner['start']))
        if not row:return {'updated':False,'reason':'no-exact-registered-parent'}
        peer=json.loads(row['descriptor_json']);mode=event.get('permission_mode')
        profiles=['analysis']+(['workspace'] if mode in ('bypassPermissions','dontAsk','acceptEdits','default','auto') else [])
        peer['policy']=dict(peer['policy'],profiles=profiles,permission_mode_observed=mode)
        from .files import canonical
        journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
        return {'updated':True,'profiles':profiles}
    if event['hook_event_name']=='SessionEnd':
        journal.db.execute("UPDATE peer_instances SET status='offline' WHERE native_id=? AND pid=? AND process_start=? AND owner_kind='interactive-standalone'",(event['session_id'],owner['pid'],owner['start']))
        return {'ended':True}
    if provider=='codex':
        transcript=event.get('transcript_path')
        if not transcript:
            # Internal historyless threads can emit hooks under the same TUI PID.
            # They must not replace its registered conversation or become peers.
            journal.event(event['session_id'],'registration-ignored',{'reason':'no-native-history-identity','owner':owner})
            return {'registered':False,'reason':'no-native-history-identity'}
        path=Path(transcript).resolve()
        require(path.is_relative_to(home) and not Path(transcript).is_symlink(),'OWNERSHIP_CONFLICT','Native history is outside the provider home')
        with path.open('rb') as stream:header=json.loads(stream.readline(262145))
        require(header.get('type')=='session_meta' and header.get('payload',{}).get('id')==event['session_id'],
                'OWNERSHIP_CONFLICT','Native history header differs from the hook UUID')
    native_title=codex_name(home,event['session_id']) if provider=='codex' else claude_name(home,event['session_id'],owner['pid'])
    transport={'kind':'codex-native-queue'}
    if provider=='claude':
        endpoint=os.getenv('CLAUDE_CODE_MESSAGING_SOCKET')
        require(endpoint,'UNSUPPORTED_TRANSPORT','This native session has no inbox')
        path=Path(endpoint.removeprefix('uds:'));info=path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid(),'TARGET_GONE','Unexpected inbox endpoint')
        transport={'kind':'claude-inbox','endpoint':endpoint,'endpoint_identity':[info.st_dev,info.st_ino]}
    mode=event.get('permission_mode')
    # Analysis is the only inferred worker grant. Workspace is granted only when native mode is known.
    profiles=['analysis']
    if mode in ('bypassPermissions','dontAsk','acceptEdits','never','full-auto','default'):profiles.append('workspace')
    from .policy import load
    local=load(journal)
    policy={'profiles':profiles,'max_depth':local['max_depth'],'max_concurrent_workers':local['workers_per_group'],'permission_mode_observed':mode}
    peer=registry.register(provider,home,event['session_id'],native_title,owner,event['cwd'],transport,policy=policy)
    transcript=event.get('transcript_path')
    if transcript:
        path=Path(transcript).resolve()
        if path.is_relative_to(home):
            peer['native_transcript']=str(path)
            from .files import canonical
            journal.db.execute('UPDATE peer_instances SET descriptor_json=? WHERE incarnation_id=?',(canonical(peer).decode(),peer['incarnation_id']))
    binding=journal.root/'bindings'/new_id()
    atomic_json(binding,dict(state='BOUND',kind='peer',peer_key=peer['handle'],incarnation_id=peer['incarnation_id'],owner=owner,expires_at=time.time()+7*86400,grants=policy))
    if provider=='claude' and os.getenv('CLAUDE_ENV_FILE'):
        with open(os.environ['CLAUDE_ENV_FILE'],'a') as stream:
            stream.write("\nexport NPEER_HOME="+__import__('shlex').quote(str(journal.root))+"\nexport NPEER_BINDING="+__import__('shlex').quote(str(binding))+"\n")
    return {'registered':peer['handle'],'incarnation_id':peer['incarnation_id']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--provider',choices=['claude','codex','grok'],required=True);args=parser.parse_args()
    journal=None;event=None
    try:
        raw=sys.stdin.buffer.read(65537)
        require(len(raw)<=65536,'PAYLOAD_TOO_LARGE','Hook input too large')
        event=json.loads(raw);journal=Journal();result=observe(args.provider,event,journal)
        if args.provider=='grok':
            print(json.dumps(result))
            if result.get('decision')=='deny' and os.getenv('NPEER_BOOTSTRAP'):raise SystemExit(2)
        elif event.get('hook_event_name')=='UserPromptSubmit' and isinstance(result,dict) and result.get('decision')=='block':
            # Claude/Codex read UserPromptSubmit JSON from stdout; only a guard block is ever printed.
            print(json.dumps(result))
    except Exception as error:
        print('NativePeers registration unavailable: '+type(error).__name__,file=sys.stderr)
        if args.provider=='grok':
            if os.getenv('NPEER_BOOTSTRAP') and isinstance(event,dict) and event.get('hookEventName')=='pre_tool_use':
                print(json.dumps({'decision':'deny','reason':'NativePeers could not verify the owned worker tool grant.'}));raise SystemExit(2)
            print('{}')
    finally:
        if journal:journal.close()


if __name__=='__main__':main()
