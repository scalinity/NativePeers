"""Short lifecycle observations. An inherited worker hook never owns its route."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
import time

from .adapters.metadata import claude_name, codex_name
from .errors import require
from .files import atomic_json
from .journal import Journal
from .models import new_id
from .process import ancestry, matches
from .registry import Registry


def observe(provider, event, journal):
    binary=(Path.home()/('.local/bin/claude' if provider=='claude' else '.local/share/codex-mcp-manager/current/bin/codex')).resolve()
    owners=[p for p in ancestry()[1:] if p.executable==str(binary)]
    require(owners,"CALLER_UNBOUND","No exact native provider owner in hook ancestry")
    owner=owners[0].json()
    bootstrap=os.getenv('NPEER_BOOTSTRAP')
    if bootstrap:
        path=Path(bootstrap)
        require(path.parent.resolve()==(journal.root/'bindings').resolve() and not path.is_symlink(),
                'CALLER_UNBOUND','Invalid worker bootstrap path')
        lease=json.loads(path.read_text())
        require(matches(lease['owner']) and lease['expires_at']>time.time(), 'CALLER_UNBOUND','Worker controller lease expired')
        # Bounded observation only: no registration, waits, identity change, or turn-end unregistration.
        journal.event(lease['worker_id'],'native-hook-observed',dict(event=event['hook_event_name'],native_id=event['session_id'],process=owner))
        return {'controller_owned':True,'worker_id':lease['worker_id']}
    registry=Registry(journal)
    if event['hook_event_name']=='SessionEnd':
        journal.db.execute("UPDATE peer_instances SET status='offline' WHERE native_id=? AND pid=? AND process_start=? AND owner_kind='interactive-standalone'",(event['session_id'],owner['pid'],owner['start']))
        return {'ended':True}
    home=Path(os.getenv('CODEX_HOME' if provider=='codex' else 'CLAUDE_CONFIG_DIR',Path.home()/('.codex' if provider=='codex' else '.claude'))).resolve()
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
    parser=argparse.ArgumentParser();parser.add_argument('--provider',choices=['claude','codex'],required=True);args=parser.parse_args()
    journal=None
    try:
        raw=sys.stdin.buffer.read(65537)
        require(len(raw)<=65536,'PAYLOAD_TOO_LARGE','Hook input too large')
        event=json.loads(raw);journal=Journal();observe(args.provider,event,journal)
    except Exception as error:
        print('NativePeers registration unavailable: '+type(error).__name__,file=sys.stderr)
    finally:
        if journal:journal.close()


if __name__=='__main__':main()
