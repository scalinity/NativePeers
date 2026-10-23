"""Private-stdio Codex JSON-RPC with one continuously draining reader."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
from pathlib import Path

from ..launch import launch_environment
from ..errors import PeerError
from ..process import identity, descendants, owned_status, signal_captured


class ProviderError(RuntimeError):
    def __init__(self, method, error):
        super().__init__(f"{method}: {error}")
        self.method, self.error = method, error


class AppServer:
    def __init__(self, executable: Path, arguments=(), *, cwd: Path | None = None, environment=None, ownership_changed=None, server_arguments=()):
        self.executable = executable.resolve(strict=True)
        self.arguments, self.cwd = list(arguments), cwd
        self.server_arguments = list(server_arguments)
        self.environment = environment or {}
        self.pending = {}
        self.events = asyncio.Queue(maxsize=8192)
        self.sequence = 0
        self.process = None
        self.stderr_tail = bytearray()
        self.cleanup = None
        self.owner = None
        self.descendants = {}
        self.ownership_changed = ownership_changed
        self.stdout_eof = False
        self.stderr_eof = False
        self.monitor = None

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            str(self.executable), *self.arguments, "app-server", *self.server_arguments, "--listen", "stdio://",
            cwd=self.cwd, env={**launch_environment("codex"),**self.environment},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True,
            limit=8 * 1024 * 1024)
        self.owner = identity(self.process.pid).json()
        self._publish_ownership()
        self.monitor = asyncio.create_task(self._monitor())
        self.reader = asyncio.create_task(self._read())
        self.error_reader = asyncio.create_task(self._stderr())
        result = await self.call("initialize", {"clientInfo": {"name": "nativepeers", "version": "0.1"},
                                                "capabilities": {"experimentalApi": True}})
        await self.write({"method": "initialized", "params": {}})
        return result

    def _publish_ownership(self):
        if self.ownership_changed:
            self.ownership_changed(self)

    async def _capture(self):
        captured = await asyncio.to_thread(descendants, [self.owner, *self.descendants.values()])
        if captured:
            self.descendants.update({r['pid']: r for r in captured})
            self._publish_ownership()

    async def _monitor(self):
        while True:
            try:
                await self._capture()
            except (OSError, TimeoutError):
                pass
            await asyncio.sleep(.05)

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
        else:
            self.stdout_eof = True
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)

    async def _stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self.stderr_tail.extend(chunk)
            del self.stderr_tail[:-65536]
        self.stderr_eof = True

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
        self.cleanup = {'state': 'UNCERTAIN', 'output_eof': False,
                        'message': 'Owned provider cleanup has not completed'}
        try:
            if self.process.stdin:
                self.process.stdin.close()
            for timeout, sig in ((exit_timeout, None), (kill_timeout, signal.SIGTERM), (kill_timeout, signal.SIGKILL)):
                with contextlib.suppress(OSError, TimeoutError):
                    await self._capture()
                records = [*self.descendants.values(), self.owner]
                if sig:
                    for record in records:
                        if record and record['pid'] != os.getpid():
                            with contextlib.suppress(ProcessLookupError):
                                signal_captured(record, sig)
                deadline = asyncio.get_running_loop().time() + timeout
                while asyncio.get_running_loop().time() < deadline:
                    if self.process.returncode is not None and all(owned_status(r) == 'DEAD' for r in records if r):
                        break
                    await asyncio.sleep(.01)
                if self.process.returncode is not None and all(owned_status(r) == 'DEAD' for r in records if r):
                    break
            # A parser/queue failure ends _read without proving EOF. Drain the
            # remaining bytes separately, bounded, without interpreting them.
            async def remaining_stdout():
                while await self.process.stdout.read(65536):
                    pass
                self.stdout_eof = True
            if self.reader.done() and not self.stdout_eof:
                self.reader = asyncio.create_task(remaining_stdout())
            readers = [self.reader, self.error_reader]
            _, pending = await asyncio.wait(readers, timeout=reader_timeout)
            live = [dict(record=r, state=owned_status(r)) for r in [self.owner, *self.descendants.values()] if r and owned_status(r) != 'DEAD']
            eof = self.stdout_eof and self.stderr_eof
            uncertain = bool(live) or not eof or self.process.returncode is None
            self.cleanup = {'state': 'UNCERTAIN' if uncertain else 'CLOSED', 'output_eof': eof,
                'stdout_eof': self.stdout_eof, 'stderr_eof': self.stderr_eof,
                'provider_exited': self.process.returncode is not None, 'unresolved_processes': live,
                'captured_descendants': len(self.descendants), 'pending_output_readers': len(pending),
                'message': 'Owned process or output closure is unproved' if uncertain else 'Owned processes and output pipes closed'}
            for task in pending:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            if uncertain:
                raise PeerError('CLEANUP_UNCERTAIN', self.cleanup['message'], details=self.cleanup)
            return self.cleanup
        finally:
            if self.monitor:
                self.monitor.cancel()
                await asyncio.gather(self.monitor, return_exceptions=True)
            if self.process.returncode is not None:
                self.process._transport.close()
            self._publish_ownership()
