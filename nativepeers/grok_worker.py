"""Grok's native headless stream, fixed tools, and bounded metadata process."""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
from urllib.parse import quote

from .errors import PeerError,require
from .provider_errors import classify,diagnostic


class HookEvidence:
    """Read only this launch's bounded native SessionStart append."""
    LIMIT=1048576

    def __init__(self,home,cwd,native_id):
        self.native_id=native_id;self.home=Path(home).resolve();self.cwd=Path(cwd).resolve()
        self.encoded=quote(str(self.cwd),safe='');self.path=self.native_path()
        self.offset=0;self.file_id=None;self.pending=b'';self.read_bytes=0;self.proof=None
        if self.path is None:
            require(not any((self.home/'sessions').glob('*/'+native_id+'/updates.jsonl')),
                    'OWNERSHIP_CONFLICT','Existing Grok updates has no unique validated native summary')
            if len(self.encoded.encode())<=255:self.path=self.home/'sessions'/self.encoded/native_id/'updates.jsonl'
        if self.path is None:return
        require(self.path.resolve()==self.path,'OWNERSHIP_CONFLICT','Grok native updates path contains a symlink')
        try:
            info=self.path.lstat()
            require(stat.S_ISREG(info.st_mode),'OWNERSHIP_CONFLICT','Grok native updates is not a regular file')
            self.offset=info.st_size;self.file_id=(info.st_dev,info.st_ino)
        except FileNotFoundError:pass

    def native_path(self):
        from .grok import summaries
        rows=summaries(native_id=self.native_id,home=self.home)
        if not rows:return None
        require(len(rows)==1 and Path(rows[0]['cwd']).resolve()==self.cwd,
                'OWNERSHIP_CONFLICT','Grok hook updates has no unique matching native UUID and workspace')
        path=Path(rows[0]['path']).parent/'updates.jsonl'
        require(path.resolve()==path and path.is_relative_to(self.home/'sessions') and path.parent.name==self.native_id,
                'OWNERSHIP_CONFLICT','Grok native updates is outside the selected native home')
        if path.parent.parent.name!=self.encoded:
            marker=path.parent.parent/'.cwd'
            try:
                info=marker.lstat()
                require(stat.S_ISREG(info.st_mode) and info.st_size<=16384,'OWNERSHIP_CONFLICT','Grok hashed session group has no regular bounded .cwd marker')
                cwd=marker.read_text().rstrip('\r\n')
                require(Path(cwd).is_absolute() and Path(cwd).resolve()==self.cwd,
                        'OWNERSHIP_CONFLICT','Grok hashed session group .cwd marker names another workspace')
            except (OSError,UnicodeError):raise PeerError('OWNERSHIP_CONFLICT','Grok hashed session group .cwd marker is missing or unreadable') from None
        return path

    def read(self):
        path=self.native_path()
        if path is None:return None
        require(self.path is None or self.path==path or self.file_id is None and self.offset==0,
                'OWNERSHIP_CONFLICT','Grok native session moved during this launch')
        self.path=path
        try:descriptor=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW)
        except FileNotFoundError:return None
        with os.fdopen(descriptor,'rb') as stream:
            info=os.fstat(stream.fileno());file_id=(info.st_dev,info.st_ino)
            require(stat.S_ISREG(info.st_mode) and (self.file_id is None or self.file_id==file_id)
                    and info.st_size>=self.offset,'OWNERSHIP_CONFLICT','Grok native updates was replaced or truncated during this launch')
            self.file_id=file_id
            if self.proof:return self.proof
            require(info.st_size-self.offset<=self.LIMIT-self.read_bytes,'SOURCE_OUTPUT_LIMIT','Fresh Grok hook updates exceed 1 MiB')
            stream.seek(self.offset);data=stream.read(self.LIMIT-self.read_bytes+1)
        require(self.read_bytes+len(data)<=self.LIMIT,'SOURCE_OUTPUT_LIMIT','Fresh Grok hook updates exceed 1 MiB')
        start=self.offset-len(self.pending);self.offset+=len(data);self.read_bytes+=len(data);self.pending+=data
        lines=self.pending.split(b'\n');self.pending=lines.pop()
        for line in lines:
            end=start+len(line)+1
            try:event=json.loads(line)
            except ValueError:raise PeerError('PROTOCOL_ERROR','Grok native updates contains malformed JSON') from None
            params=event.get('params',{}) if isinstance(event,dict) else {};update=params.get('update',{})
            if event.get('method')=='_x.ai/session/update' and update.get('sessionUpdate')=='hook_execution' and update.get('event_name')=='session_start':
                require(params.get('sessionId')==self.native_id,'OWNERSHIP_CONFLICT','Grok native hook execution names another session UUID')
                for run in update.get('runs',[]):
                    name=run.get('name') if isinstance(run,dict) else None
                    if isinstance(name,str) and re.fullmatch(r'(?:project|user|global)/nativepeers:session_start\[0\]\.hooks\[0\]',name):
                        require(run.get('status',{}).get('status')=='success','PERMISSION_CAPABILITY_UNKNOWN','The owned Grok SessionStart hook did not complete successfully')
                        self.proof=dict(native_id=self.native_id,handler=name,path=str(self.path),from_offset=start,to_offset=end,
                                        event_id=params.get('_meta',{}).get('eventId'),event=event)
                        return self.proof
            start=end
        return None


def allowed_tools(profile):
    require(profile in ('analysis','workspace'),'PERMISSION_CAPABILITY_UNKNOWN','Unknown Grok worker profile')
    return ('read_file','grep','list_dir')+(('run_terminal_command','write','search_replace') if profile=='workspace' else ())


def tool_ids(profile):
    """The native stream uses display names; CLI tool filters use internal IDs."""
    return tuple('run_terminal_cmd' if tool=='run_terminal_command' else tool for tool in allowed_tools(profile))


def arguments(spec,native_id=None):
    tools=tool_ids(spec['permission_profile'])
    # A requested kernel sandbox keeps tools in this process, never a leader.
    args=['--no-auto-update','--prompt-file','/dev/stdin','--output-format','streaming-json',
          '--model',spec['model'],'--sandbox','nativepeers-'+spec['permission_profile'],
          '--permission-mode','dontAsk','--tools',','.join(tools),'--no-subagents','--disable-web-search',
          '--disallowed-tools','Agent,search_tool,use_tool','--deny','MCPTool(*)',
          '--max-turns',str(spec.get('max_task_turns',8))]
    for tool in ('Read','Grep')+(('Bash','Edit','Write') if spec['permission_profile']=='workspace' else ()):
        args+=['--allow',tool]
    require(not spec.get('reasoning_effort'),'EFFORT_UNSUPPORTED','Grok models does not advertise per-model reasoning effort; omit it')
    if native_id:args+=['--resume',native_id]
    elif spec.get('native_session_id'):args+=['--session-id',spec['native_session_id']]
    return args


def parse_catalog(text):
    models={};default=None;inside=False
    for line in text.splitlines():
        if line.startswith('Default model: '):default=line.partition(': ')[2].strip()
        if line.strip()=='Available models:':inside=True;continue
        match=re.fullmatch(r'\s*[-*]\s+([A-Za-z0-9][A-Za-z0-9_.:/-]*)(?:\s+\(default\))?\s*',line) if inside else None
        if match:models[match[1]]=match[1]
    require(models and default in models,'CAPABILITY_MISMATCH','Grok returned no valid native model catalog/default')
    return {'models':models,'default':default}


async def catalog(executable,*,timeout=30,terminate_grace=1,home=None):
    from .launch import launch_environment,apply_home
    from .process import identity,descendants,owned_status,signal_captured
    environment=launch_environment('grok')
    if home is not None:environment=apply_home('grok',home,environment)
    process=subprocess.Popen([str(Path(executable).resolve(strict=True)),'--no-auto-update','models'],env=environment,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,close_fds=True)
    owner=None;captured={};eof=[False,False];buffers=[bytearray(),bytearray()];overflow=False
    for stream in (process.stdout,process.stderr):os.set_blocking(stream.fileno(),False)
    async def drain(stream,index):
        nonlocal overflow
        while True:
            try:chunk=os.read(stream.fileno(),16384)
            except BlockingIOError:await asyncio.sleep(.01);continue
            if not chunk:eof[index]=True;return
            available=1048576-sum(map(len,buffers));buffers[index].extend(chunk[:max(0,available)])
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
                require(not overflow,'SOURCE_OUTPUT_LIMIT','Grok model catalog exceeds 1 MiB')
                await asyncio.sleep(.02)
    except BaseException as error:failure=error
    finally:
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
    require(not uncertain,'CLEANUP_UNCERTAIN','Grok catalog process/output closure was not proved',details=cleanup)
    if failure:raise failure
    output,error=map(bytes,buffers)
    if process.returncode!=0:
        code={'AUTH_REQUIRED':'AUTH_REQUIRED','RATE_LIMITED':'SOURCE_RATE_LIMITED'}.get(classify((error or output).decode(errors='replace')),'SOURCE_UNAVAILABLE')
        raise PeerError(code,'Grok native login is unavailable for the selected home.' if code=='AUTH_REQUIRED' else 'Grok native model catalog could not be read: '+code)
    return parse_catalog(output.decode())


class Stream:
    """Native streaming-json framing; engine usage never substitutes selection."""
    def __init__(self,native_id,profile):
        self.native_id=native_id;self.allowed=set(allowed_tools(profile));self.text=[];self.size=0;self.terminal=None;self.tools=None
        self.advertised_tools=None;self.denied_advertised_tools=[]

    def observe(self,event):
        require(isinstance(event,dict),'PROTOCOL_ERROR','Grok stream event must be an object')
        require(self.terminal is None,'PROTOCOL_ERROR','Grok emitted data after its terminal event')
        native=event.get('sessionId')
        if native:require(native==self.native_id,'OWNERSHIP_CONFLICT','Grok emitted another native session ID')
        kind=event.get('type')
        if kind=='available_commands':
            tools=event.get('tools')
            require(isinstance(tools,list) and all(isinstance(t,str) for t in tools),'PROTOCOL_ERROR','Grok tool inventory is malformed')
            extra=set(tools)-self.allowed
            unsafe={tool for tool in extra if '__' not in tool}
            require(not unsafe,'PERMISSION_CAPABILITY_UNKNOWN','Grok worker advertised built-in tools outside its fixed profile',details=sorted(unsafe))
            # Native --tools filters built-ins. MCP discovery can still advertise
            # names even though --deny MCPTool(*) and the owned hook deny them.
            self.advertised_tools=tools;self.denied_advertised_tools=sorted(extra)
            self.tools=[tool for tool in tools if tool in self.allowed]
        elif kind=='tool_call':
            require(event.get('toolName') in self.allowed,'PERMISSION_CAPABILITY_UNKNOWN','Grok invoked a tool outside its fixed profile')
        elif kind=='text':
            value=event.get('data');require(isinstance(value,str),'PROTOCOL_ERROR','Grok text event is malformed')
            self.size+=len(value.encode());require(self.size<=1048576,'SOURCE_OUTPUT_LIMIT','Grok worker result exceeds 1 MiB');self.text.append(value)
        elif kind in ('end','error'):self.terminal=event
        return kind

    def result(self,returncode):
        require(self.terminal is not None,'WORKER_CRASHED','Grok exited without a terminal result')
        event=self.terminal;reason=event.get('stopReason')
        success=event.get('type')=='end' and reason=='end_turn' and returncode==0
        canceled=reason in ('cancelled','canceled')
        error=None
        if not success:
            code='USER_CANCELED' if canceled else 'BUDGET_EXHAUSTED' if reason in ('max_tokens','max_turn_requests','max_turns') else classify(event)
            error=diagnostic(event,submitted=True,code=code,provider='grok')
        return dict(status='completed' if success else 'canceled' if canceled else 'failed',text=''.join(self.text),
                    usage=event.get('usage'),error=error,model_usage=event.get('modelUsage'),native_request_id=event.get('requestId'),
                    native_internal_rounds=event.get('num_turns'))
