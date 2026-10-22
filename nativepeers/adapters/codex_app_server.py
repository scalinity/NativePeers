"""Private-stdio Codex JSON-RPC with one continuously draining reader."""
from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from ..launch import launch_environment
from ..errors import PeerError


class ProviderError(RuntimeError):
    def __init__(self, method, error):
        super().__init__(f"{method}: {error}")
        self.method, self.error = method, error


class AppServer:
    def __init__(self, executable: Path, arguments=(), *, cwd: Path | None = None, environment=None):
        self.executable = executable.resolve(strict=True)
        self.arguments, self.cwd = list(arguments), cwd
        self.environment = environment or {}
        self.pending = {}
        self.events = asyncio.Queue(maxsize=8192)
        self.sequence = 0
        self.process = None
        self.stderr_tail = bytearray()
        self.cleanup = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable), *self.arguments, "app-server", "--listen", "stdio://",
            cwd=self.cwd, env={**launch_environment("codex"),**self.environment},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True,
            limit=8 * 1024 * 1024)
        self.reader = asyncio.create_task(self._read())
        self.error_reader = asyncio.create_task(self._stderr())
        result = await self.call("initialize", {"clientInfo": {"name": "nativepeers", "version": "0.1"},
                                                "capabilities": {"experimentalApi": True}})
        await self.write({"method": "initialized", "params": {}})
        return result

    async def write(self, value):
        self.process.stdin.write(json.dumps(value, ensure_ascii=False).encode() + b"\n")
        await self.process.stdin.drain()

    async def call(self, method, params, timeout=30):
        self.sequence += 1
        number = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[number] = future
        try:
            await self.write({"id": number, "method": method, "params": params})
            response = await asyncio.wait_for(future, timeout)
            if "error" in response:
                raise ProviderError(method, response["error"])
            return response["result"]
        finally:
            self.pending.pop(number, None)

    async def _read(self):
        error = ConnectionError("Provider closed stdout")
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "id" in message and "method" not in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        future.set_result(message)
                elif "id" in message:
                    # No implicit permission grant, arbitrary tool execution or token refresh.
                    await self.write({"id": message["id"], "error": {
                        "code": -32601, "message": "NativePeers has no grant for this client request"}})
                    self.events.put_nowait(message)
                else:
                    self.events.put_nowait(message)
        except Exception as exception:
            error = exception
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)

    async def _stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self.stderr_tail.extend(chunk)
            del self.stderr_tail[:-65536]

    async def next_event(self):
        task=asyncio.create_task(self.events.get())
        try:
            done,_=await asyncio.wait([task,self.reader],return_when=asyncio.FIRST_COMPLETED)
            if task in done:return task.result()
            if not self.events.empty():return self.events.get_nowait()
            raise ConnectionError('Provider disconnected before its terminal event')
        finally:
            if not task.done():task.cancel()

    async def close(self, *, exit_timeout=10, kill_timeout=5, reader_timeout=1):
        if self.process is None:
            return
        if self.cleanup is not None:
            if self.cleanup['state']=='UNCERTAIN':raise PeerError('CLEANUP_UNCERTAIN',self.cleanup['message'],details=self.cleanup)
            return self.cleanup
        if self.process.stdin:
            self.process.stdin.close()
        # Process.wait() can itself await inherited pipes after the leader exits.
        # Observe the subprocess returncode separately from pipe EOF.
        for timeout,action in ((exit_timeout,None),(kill_timeout,'terminate'),(kill_timeout,'kill')):
            if self.process.returncode is not None:break
            if action:
                with contextlib.suppress(ProcessLookupError):getattr(self.process,action)()
            deadline=asyncio.get_running_loop().time()+timeout
            while self.process.returncode is None and asyncio.get_running_loop().time()<deadline:
                await asyncio.sleep(.01)
        readers=[task for task in (getattr(self,'reader',None),getattr(self,'error_reader',None)) if task]
        _,pending=await asyncio.wait(readers,timeout=reader_timeout) if readers else (set(),set())
        uncertain=bool(pending) or self.process.returncode is None
        for task in pending:task.cancel()
        await asyncio.gather(*readers,return_exceptions=True)
        if self.process.returncode is not None:
            # asyncio exposes no public close for subprocess output readers.
            # Close only our local pipe transport, never a dead leader's group.
            self.process._transport.close()
        self.cleanup={'state':'UNCERTAIN' if uncertain else 'CLOSED','provider_exited':self.process.returncode is not None,
            'pending_output_readers':len(pending),'message':'Provider exited without output-pipe EOF; descendant ownership is unresolved' if pending else 'Provider process did not confirm exit' if uncertain else 'Owned provider and output readers closed'}
        if uncertain:raise PeerError('CLEANUP_UNCERTAIN',self.cleanup['message'],details=self.cleanup)
        return self.cleanup
