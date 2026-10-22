"""Bounded native-history observations advance receipts without replaying input."""
import json
from pathlib import Path

from .adapters.codex_app_server import AppServer
from .errors import require
from .files import canonical


def texts(value):
    if isinstance(value,str):yield value
    elif isinstance(value,list):
        for item in value:
            if isinstance(item,dict) and item.get('type') in ('text','input_text'):yield item.get('text','')


def frames(text):
    decoder=json.JSONDecoder();offset=0
    for _ in range(128):
        position=text.find('{',offset)
        if position<0:return
        try:value,end=decoder.raw_decode(text[position:])
        except ValueError:offset=position+1;continue
        offset=position+end
        if isinstance(value,dict) and value.get('schema_version')==1 and value.get('message_id'):yield value


async def observe(journal,registry,router,message_id):
    record=journal.one('SELECT * FROM messages WHERE message_id=?',(message_id,))
    require(record,'PROTOCOL_ERROR','Unknown message')
    peer=registry.get(record['to_key'],live=False);native=peer['peer_key']['native_session_id'];home=Path(peer['home'])
    provider=peer['peer_key']['provider']
    def unconfirmed():
        outcome=router.outcome(message_id)
        if provider=='antigravity':outcome['reconciliation']={'state':'UNCONFIRMED','reason':'No exact persisted Antigravity USER_INPUT envelope was observed; ephemeral hook output alone does not prove ingestion.'}
        return outcome
    path=Path(peer['native_transcript']) if peer.get('native_transcript') else None
    if path is None and peer['peer_key']['provider']=='codex':
        client=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex',environment={'CODEX_HOME':str(home)})
        try:
            await client.start();thread=(await client.call('thread/read',{'threadId':native,'includeTurns':False}))['thread']
            require(thread['id']==native,'OWNERSHIP_UNKNOWN','Native read returned another thread')
            path=Path(thread['path']) if thread.get('path') else None
        finally:await client.close()
    if path is None and peer['peer_key']['provider']=='claude':
        path=home/'projects'/str(peer['cwd']).replace('/','-').replace('.','-')/(native+'.jsonl')
    if path is None or not path.exists():return unconfirmed()
    require(path.resolve().is_relative_to(home) and not path.is_symlink(),'OWNERSHIP_UNKNOWN','Native history path is outside its provider home')
    if provider=='antigravity':require(native in path.resolve().parts,'OWNERSHIP_UNKNOWN','Antigravity history path differs from its native UUID')
    with path.open('rb') as stream:
        stream.seek(0,2);size=stream.tell();start=max(0,size-1024*1024);stream.seek(start)
        if start:stream.readline()
        lines=stream.read(1024*1024).splitlines()
    wanted=json.loads(record['payload_json'])
    for line in lines:
        try:event=json.loads(line)
        except ValueError:continue
        if peer['peer_key']['provider']=='claude':
            if event.get('sessionId')!=native:continue
            if event.get('type')=='user':content=event.get('message',{}).get('content')
            elif event.get('type')=='attachment' and event.get('attachment',{}).get('type')=='queued_command':content=event['attachment'].get('prompt','')
            else:continue
            event_id=event.get('uuid')
        elif provider=='codex':
            value=event.get('payload',{})
            if event.get('type')!='response_item' or value.get('role')!='user':continue
            content=value.get('content');event_id=value.get('id')
        elif provider=='antigravity':
            if event.get('type')!='USER_INPUT' or any(event.get(key,native)!=native for key in ('conversation_id','conversationId','sessionId')):continue
            content=event.get('content');event_id=event.get('step_index')
        else:continue
        if any(canonical(frame)==canonical(wanted) for text in texts(content) for frame in frames(text)):
            outcome=router.outcome(message_id)
            router.record(message_id,outcome['attempt']['attempt_id'],dict(stage='INGESTED',evidence_kind='matching-native-user-event',native_turn_id=event_id,
                native_expiry_enforced=False,source_path=str(path)),peer)
            return router.outcome(message_id)
    return unconfirmed()
