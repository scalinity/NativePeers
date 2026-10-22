"""Bounded native-history observations advance receipts without replaying input."""
import asyncio
import hashlib
import json
import os
import stat
from contextlib import suppress
from pathlib import Path

from .adapters.codex_app_server import AppServer, ProviderError
from .errors import require
from .files import canonical
from .pagination import encode,decode,SQLITE_MAX


MAX_READ_BYTES=4*1024*1024


def history_window(path,scope,cursor,max_bytes,provider):
    """Read one backward page, including bounded file guards in the 4 MiB cap.

    Prefix and snapshot-tail guards allow appends without following a new file.
    They do not certify arbitrary edits to unsampled bytes during a size increase.
    Only complete, newline-terminated JSONL records are eligible for evidence.
    """
    try:position,ceiling=decode(cursor,scope) if cursor is not None else (None,None)
    except RecursionError:require(False,'PROTOCOL_ERROR','Invalid receipt-scan cursor')
    if cursor is not None:
        fields={'end','device','inode','prefix','tail','observed_size','mtime_ns','ctime_ns'}
        require(isinstance(position,dict) and set(position)==fields
                and all(type(position[k]) is int and 0<=position[k]<=SQLITE_MAX
                        for k in fields-{'prefix','tail'})
                and 0<position['end']<=ceiling<=position['observed_size']
                and all(isinstance(position[k],str) and len(position[k])==64 for k in ('prefix','tail')),
                'PROTOCOL_ERROR','Invalid receipt-scan cursor')
    require(stat.S_ISREG(path.stat().st_mode),'OWNERSHIP_UNKNOWN','Native history is not a regular file')
    with path.open('rb',buffering=0) as stream:
        before=os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode),'OWNERSHIP_UNKNOWN','Native history is not a regular file')
        if position is None:ceiling=before.st_size
        else:
            require((before.st_dev,before.st_ino)==(position['device'],position['inode'])
                    and before.st_size>=position['observed_size']
                    and (before.st_size>position['observed_size']
                         or (before.st_mtime_ns,before.st_ctime_ns)==(position['mtime_ns'],position['ctime_ns'])),
                    'PROTOCOL_ERROR','Receipt-scan history was replaced, truncated or edited')
        prefix_size=min(262144 if provider=='codex' else 4096,ceiling)
        prefix=stream.read(prefix_size)
        tail_start=max(0,ceiling-4096)
        stream.seek(tail_start);tail=stream.read(ceiling-tail_start)
        guards={'prefix':hashlib.sha256(prefix).hexdigest(),'tail':hashlib.sha256(tail).hexdigest()}
        if position is not None:
            require(all(position[k]==guards[k] for k in guards),'PROTOCOL_ERROR','Receipt-scan history prefix or snapshot tail changed')
        end=position['end'] if position is not None else ceiling
        window_limit=min(max_bytes,MAX_READ_BYTES-prefix_size-(ceiling-tail_start)-1)
        start=max(0,end-window_limit)
        stream.seek(max(0,start-1));raw=stream.read(end-max(0,start-1));window_read=len(raw)
        boundary=start==0 or raw[:1]==b'\n'
        if start:raw=raw[1:]
        left=0 if boundary else raw.find(b'\n')+1
        if not boundary and left==0:left=len(raw)
        right=raw.rfind(b'\n')+1
        inspected=[[start+left,start+right]] if left<right else []
        # Overlap an incomplete leading line when it can fit in the next page.
        # A line exceeding the window is skipped with strict backward progress.
        older=start+left if inspected else start
        after=os.fstat(stream.fileno());current=path.stat()
        require((current.st_dev,current.st_ino)==(before.st_dev,before.st_ino)
                and after.st_size>=before.st_size
                and (after.st_size>before.st_size
                     or (after.st_mtime_ns,after.st_ctime_ns)==(before.st_mtime_ns,before.st_ctime_ns)),
                'PROTOCOL_ERROR','Native history changed during receipt observation')
        next_position=dict(end=older,device=before.st_dev,inode=before.st_ino,**guards,
                           observed_size=after.st_size,mtime_ns=after.st_mtime_ns,ctime_ns=after.st_ctime_ns)
        scan=dict(source_path=str(path),snapshot_size=ceiling,byte_range=[start,end],
                  inspected_ranges=inspected,validation_ranges=[[0,prefix_size],[tail_start,ceiling]],
                  bytes_read=len(prefix)+len(tail)+window_read,
                  window_limit=window_limit,next_cursor=encode(scope,next_position,ceiling) if older else None)
        return raw[left:right].split(b'\n') if inspected else [],prefix,scan


def texts(value):
    if isinstance(value,str):yield value
    elif isinstance(value,list):
        for item in value:
            if isinstance(item,dict) and item.get('type') in ('text','input_text') and isinstance(item.get('text'),str):yield item['text']


def frames(text):
    decoder=json.JSONDecoder();offset=0
    for _ in range(128):
        position=text.find('{',offset)
        if position<0:return
        try:value,end=decoder.raw_decode(text[position:])
        except (ValueError,RecursionError):offset=position+1;continue
        offset=position+end
        if isinstance(value,dict) and value.get('schema_version')==1 and value.get('message_id'):yield value


async def observe(journal,registry,router,message_id,*,cursor=None,max_bytes=1048576):
    """Authorize the exact message before every call, including continuations.

    max_bytes bounds the JSONL window (default 1 MiB); guard reads are additional,
    and all reads together are capped at 4 MiB. Cursors freeze the original EOF.
    """
    require(type(max_bytes) is int and 1<=max_bytes<=MAX_READ_BYTES,'PROTOCOL_ERROR','Receipt-scan max_bytes must be 1 to 4194304')
    record=journal.one('SELECT * FROM messages WHERE message_id=?',(message_id,))
    require(record,'PROTOCOL_ERROR','Unknown message')
    peer=registry.get(record['to_key'],live=False);native=peer['peer_key']['native_session_id'];home=Path(peer['home'])
    provider=peer['peer_key']['provider']
    def unconfirmed(error=None,scan=None,reason=None):
        outcome=router.outcome(message_id)
        details=dict(scan or {},state='UNCONFIRMED',reason=reason or 'No exact native user envelope was observed in this window.')
        if scan is None:details.update(byte_range=None,inspected_ranges=[],next_cursor=None)
        if error is not None:details.update(reason='Native history is unavailable',error=type(error).__name__)
        elif provider=='antigravity' and reason is None:details['reason']='No exact persisted Antigravity USER_INPUT envelope was observed; ephemeral hook output alone does not prove ingestion.'
        outcome['reconciliation']=details
        return outcome
    path=Path(peer['native_transcript']) if peer.get('native_transcript') else None
    if path is None and peer['peer_key']['provider']=='codex':
        client=None
        try:
            client=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex',environment={'CODEX_HOME':str(home)})
            async with asyncio.timeout(5):
                await client.start();thread=(await client.call('thread/read',{'threadId':native,'includeTurns':False}))['thread']
            require(thread['id']==native,'OWNERSHIP_UNKNOWN','Native read returned another thread')
            path=Path(thread['path']) if thread.get('path') else None
        except (OSError,ProviderError) as error:
            return unconfirmed(error)
        finally:
            if client is not None:
                # Cleanup availability errors must not mask identity failures or cancellation.
                with suppress(OSError,ProviderError):
                    async with asyncio.timeout(20):await client.close()
    if path is None and peer['peer_key']['provider']=='claude':
        path=home/'projects'/str(peer['cwd']).replace('/','-').replace('.','-')/(native+'.jsonl')
    if path is None or not path.exists():return unconfirmed()
    require(path.resolve().is_relative_to(home.resolve()) and not path.is_symlink(),'OWNERSHIP_UNKNOWN','Native history path is outside its provider home')
    path=path.resolve()
    if provider=='antigravity':require(native in path.resolve().parts,'OWNERSHIP_UNKNOWN','Antigravity history path differs from its native UUID')
    try:
        scope=['receipt-scan',message_id,hashlib.sha256(canonical([
            record['payload_json'],peer['handle'],provider,native,str(path)])).hexdigest()]
        lines,prefix,scan=history_window(path,scope,cursor,max_bytes,provider)
    except OSError as error:
        return unconfirmed(error)
    if provider=='codex':
        try:header=json.loads(prefix.split(b'\n',1)[0])
        except (ValueError,RecursionError):return unconfirmed(scan=scan,reason='No bounded Codex history identity header was observed.')
        require(isinstance(header,dict) and header.get('type')=='session_meta'
                and isinstance(header.get('payload'),dict) and header['payload'].get('id')==native,
                'OWNERSHIP_UNKNOWN','Native history header differs from its exact Codex thread')
    wanted=json.loads(record['payload_json'])
    inspected_start=scan['inspected_ranges'][0][0] if scan['inspected_ranges'] else 0
    inspected_end=inspected_start
    for line in lines:
        inspected_end+=len(line)+1
        try:event=json.loads(line)
        except (ValueError,RecursionError):continue
        if not isinstance(event,dict):continue
        if peer['peer_key']['provider']=='claude':
            if event.get('sessionId')!=native:continue
            if event.get('type')=='user' and isinstance(event.get('message'),dict):content=event['message'].get('content')
            elif event.get('type')=='attachment' and isinstance(event.get('attachment'),dict) and event['attachment'].get('type')=='queued_command':content=event['attachment'].get('prompt','')
            else:continue
            event_id=event.get('uuid')
        elif provider=='codex':
            value=event.get('payload',{})
            if event.get('type')!='response_item' or not isinstance(value,dict) or value.get('role')!='user':continue
            content=value.get('content');event_id=value.get('id')
        elif provider=='antigravity':
            if event.get('type')!='USER_INPUT' or any(event.get(key,native)!=native for key in ('conversation_id','conversationId','sessionId')):continue
            content=event.get('content');event_id=event.get('step_index')
        else:continue
        from .presentation import matches as readable_match
        try:matching=any(readable_match(text,wanted) or any(canonical(frame)==canonical(wanted) for frame in frames(text)) for text in texts(content))
        except (ValueError,RecursionError):continue
        if matching:
            outcome=router.outcome(message_id)
            router.record(message_id,outcome['attempt']['attempt_id'],dict(stage='INGESTED',evidence_kind='matching-native-user-event',native_turn_id=event_id,
                native_expiry_enforced=False,source_path=str(path)),peer)
            result=router.outcome(message_id)
            scan['inspected_ranges']=[[inspected_start,inspected_end]]
            result['reconciliation']=dict(scan,state='INGESTED')
            return result
    return unconfirmed(scan=scan)
