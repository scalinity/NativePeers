"""Finite native read commands, private cwd, no stdin, bounded owned teardown."""
import asyncio
import contextlib
import errno
import os
from pathlib import Path
import signal
import sys
import tempfile
import subprocess
import codecs
import re

from ..errors import PeerError,require
from ..launch import launch_environment,home_environment
from ..process import identity,matches,signal_owned,owned_status,signal_captured

BROWSER_PROFILE='''(version 1)(allow default)
(deny appleevent-send)
(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript")
 (regex #".*[.]app/Contents/MacOS/.*"))
(deny mach-lookup (global-name "com.apple.coreservices.launchservicesd")
 (global-name "com.apple.lsd.mapdb") (global-name "com.apple.security.agent"))'''


def guarded_argv(binary,arguments):
    require(sys.platform=='darwin' and Path('/usr/bin/sandbox-exec').is_file(),
        'CAPABILITY_MISMATCH','Native usage browser-launch guard is unavailable on this host')
    return ['/usr/bin/sandbox-exec','-p',BROWSER_PROFILE,str(binary),*arguments]


async def run_native(provider,binary,arguments,home,*,timeout=15,max_bytes=1048576,terminal=False,capture_directory=None,cwd_parent=None):
    """Only adapters supply arguments. This is not an exposed arbitrary command tool."""
    try:
        from ..launch import apply_home
        from .store import _home_layout
        environment=apply_home(provider,home,launch_environment(provider),explicit=_home_layout.get() if provider=='claude' else None)
    except RuntimeError:raise PeerError('CAPABILITY_MISMATCH','The native nested-launch contract does not permit this usage collector') from None
    if provider=='claude':environment.update(DISABLE_AUTOUPDATER='1',CLAUDE_CODE_SKIP_PROMPT_HISTORY='1')
    with tempfile.TemporaryDirectory(prefix='npeer-usage-',dir=cwd_parent) as name:
        directory=Path(name);directory.chmod(0o700)
        for command in ('open','xdg-open'):
            path=directory/command;path.write_text('#!/bin/sh\nexit 1\n');path.chmod(0o700)
        environment.update(PATH=str(directory)+':'+environment.get('PATH','/usr/bin:/bin'),BROWSER=str(directory/'open'))
        argv=guarded_argv(binary,arguments)
        master=slave=None;screen=None;commands=0;ready=asyncio.Event();last_output=0.0
        if terminal:
            import fcntl,struct,termios
            from .claude_native import Screen
            require(provider=='claude','PROTOCOL_ERROR','Only the gated Claude usage transport owns a terminal')
            master,slave=os.openpty();os.set_blocking(master,False)
            fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',200,160,0,0));screen=Screen()
            environment['TERM']='xterm-256color'
        # Local creation is synchronous: cancellation cannot strand a child
        # between async process creation and ownership registration.
        try:
            process=subprocess.Popen(argv,env=environment,cwd=directory,
                stdin=slave if terminal else subprocess.DEVNULL,stdout=slave if terminal else subprocess.PIPE,stderr=slave if terminal else subprocess.PIPE,
                start_new_session=True,close_fds=True)
        except BaseException:
            for descriptor in (master,slave):
                if descriptor is not None:os.close(descriptor)
            raise
        if slave is not None:os.close(slave)
        if not terminal:
            for stream in (process.stdout,process.stderr):os.set_blocking(stream.fileno(),False)
        owner=None;owned={};total=0;output=[bytearray(),bytearray()];eof=[False,False]
        inspection_failure=None
        try:owner=identity(process.pid).json()
        except OSError as error:inspection_failure=error

        async def drain(stream,index,capture=True):
            nonlocal total
            while True:
                try:chunk=os.read(stream.fileno(),16384)
                except BlockingIOError:await asyncio.sleep(.02);continue
                if not chunk:eof[index]=True;return
                if not capture:continue
                total+=len(chunk)
                require(total<=max_bytes,'SOURCE_OUTPUT_LIMIT','Native usage output exceeds 1 MiB')
                output[index].extend(chunk)

        async def drain_terminal(capture=True):
            nonlocal total,commands,last_output
            from .claude_native import parse
            decoder=codecs.getincrementaldecoder('utf-8')('replace')
            while True:
                try:chunk=os.read(master,16384)
                except BlockingIOError:await asyncio.sleep(.02);continue
                except OSError as error:
                    if error.errno==errno.EIO:eof[:]=[True,True];return
                    raise
                if not chunk:eof[:]=[True,True];return
                if not capture:continue
                total+=len(chunk);require(total<=max_bytes,'SOURCE_OUTPUT_LIMIT','Native usage terminal exceeds 1 MiB')
                screen.feed(decoder.decode(chunk));last_output=asyncio.get_running_loop().time();text=screen.text()
                # No trust/login/permission screen is acknowledged by this reader.
                if any(word in text.lower() for word in ('do you trust','quick safety check','choose the text style','please sign in','select login','allow this','permission required')):
                    raise PeerError('SOURCE_ACCESS_DENIED','Native usage collector encountered a prompt requiring human action')
                normal_prompt=re.search(r'(?m)^\s*[❯>]\s*$',text) or ('Claude Code v' in text and re.search(r'(?m)^\s*❯\s*Try [^\n]+$',text))
                if commands==0 and normal_prompt:
                    os.write(master,b'/usage\r');commands=1
                    # Only the collector's in-memory screen is cleared. Native
                    # history/UI is untouched; old startup text cannot certify
                    # or reject the response to the command just submitted.
                    screen.cells=[[' ']*screen.columns for _ in range(screen.rows)]
                    continue
                if commands:
                    try:
                        parse(text,fresh_transport=True)
                        if 'loading usage' not in text.lower() and 'refreshing' not in text.lower():ready.set()
                    except PeerError as error:
                        if error.code!='SOURCE_SCHEMA_MISMATCH':raise

        async def sample():
            nonlocal owner
            if process.poll() is None:
                try:
                    current=identity(process.pid).json()
                    if owner and all(current[k]==owner[k] for k in ('pid','start','uid')) or not owner and current['ppid']==os.getpid():owner=current
                except ProcessLookupError:pass
            # Capture actual descendants while ancestry is still established.
            try:raw=subprocess.run(['/bin/ps' if sys.platform=='darwin' else '/usr/bin/ps','-axo','pid=,ppid='],capture_output=True,timeout=.4).stdout
            except subprocess.TimeoutExpired:return
            parents={int(p):int(pp) for p,pp in (line.split() for line in raw.decode().splitlines())}
            roots=({process.pid} if owner and owned_status(owner)=='LIVE' and process.poll() is None else set())|{p for p,r in owned.items() if owned_status(r)=='LIVE'}
            pending=list(parents)
            for _ in range(8):
                selected=[p for p in pending if parents[p] in roots]
                if not selected:break
                for pid in selected:
                    pending.remove(pid)
                    try:record=identity(pid).json()
                    except ProcessLookupError:continue
                    if record['uid']==os.getuid() and record['ppid'] in roots:owned[pid]=record;roots.add(pid)

        readers=[asyncio.create_task(drain_terminal())] if terminal else [asyncio.create_task(drain(process.stdout,0)),asyncio.create_task(drain(process.stderr,1))]
        failure=None
        try:
            if inspection_failure:raise PeerError('OWNERSHIP_UNKNOWN','Created usage process identity inspection failed',details={'pid':process.pid,'controller_created':True})
            async with asyncio.timeout(max(.1,timeout-2)):
                while process.poll() is None:
                    if ready.is_set() and asyncio.get_running_loop().time()-last_output>=.25:break
                    await sample()
                    for reader in readers:
                        if reader.done():reader.result()
                    await asyncio.sleep(.05)
                if not terminal:await asyncio.gather(*readers)
                for reader in readers:
                    if reader.done():reader.result()
        except BaseException as error:failure=error
        finally:
            # Never signal a dead/reused leader's group. Captured descendants
            # retain their own PID/start/executable identities after reparenting.
            for sig,grace in ((signal.SIGTERM,.5),(signal.SIGKILL,.5)):
                if process.poll() is None:
                    with contextlib.suppress(OSError):
                        current=identity(process.pid).json()
                        if owner and all(current[k]==owner[k] for k in ('pid','start','uid')) or not owner and current['ppid']==os.getpid():owner=current
                if owner and process.poll() is None:
                    with contextlib.suppress(ProcessLookupError):signal_captured(owner,sig)
                for record in reversed(list(owned.values())):
                    with contextlib.suppress(ProcessLookupError):signal_captured(record,sig)
                deadline=asyncio.get_running_loop().time()+grace
                while process.poll() is None and asyncio.get_running_loop().time()<deadline:await asyncio.sleep(.02)
            # Failed parsing/cap checks do not establish pipe EOF. Finish a
            # bounded discard drain after stopping the captured processes.
            for index,reader in enumerate(readers):
                if reader.done() and not eof[index]:
                    readers[index]=asyncio.create_task(drain_terminal(False) if terminal else drain((process.stdout,process.stderr)[index],index,False))
            _,pending=await asyncio.wait(readers,timeout=.3)
            for reader in pending:reader.cancel()
            await asyncio.gather(*readers,return_exceptions=True)
            for stream in (process.stdout,process.stderr):
                if stream:stream.close()
            uncertain=owner is None or process.poll() is None or not all(eof) or bool(pending) or any(owned_status(r)!='DEAD' for r in owned.values())
            if master is not None:os.close(master)
            if capture_directory is not None:
                from ..files import private_directory
                capture=private_directory(capture_directory)
                for filename,data in [('native-screen.txt',screen.text().encode() if screen else bytes(output[0])),('native-stderr.txt',bytes(output[1]))]:
                    path=capture/filename;path.write_bytes(data);path.chmod(0o600)
        if isinstance(failure,asyncio.CancelledError):raise failure
        require(not uncertain,'CLEANUP_UNCERTAIN','Native usage command did not prove owned process/output cleanup',
            details={'pid':process.pid,'owner':owner,'captured_descendants':list(owned.values()),'output_eof':eof,'provider_exited':process.poll() is not None})
        if isinstance(failure,TimeoutError):raise PeerError('SOURCE_TIMEOUT','Native usage read exceeded its bounded deadline',
            details={'submitted_commands':commands,'cleanup':'CLOSED'})
        if failure:raise failure
        return dict(stdout=screen.text().encode() if screen else bytes(output[0]),stderr=bytes(output[1]),exit_code=process.returncode,submitted_commands=commands,
            cleanup={'state':'CLOSED','provider_exited':True,'captured_descendants':len(owned),'output_eof':True})
