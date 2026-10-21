"""Private-stdio Codex JSON-RPC with one continuously draining reader."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..launch import launch_environment


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

    async def close(self):
        if self.process is None:
            return
        if self.process.stdin:
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), 10)
        except asyncio.TimeoutError:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(self.reader, self.error_reader, return_exceptions=True)
