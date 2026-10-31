"""Session-scoped bridge for the Claude Code `np` mod: HTTP/1.1 over a private Unix socket.

One bridge serves one Claude Code session. It is spawned by the mod, prints a
single ready line, and exits when the Claude owner exits, when mod heartbeats
stop for 60 s, after `POST /v1/shutdown`, or after 10 idle minutes with no
leases. Standard input closing is not a liveness signal: the engine closes a
spawned child's stdin at once. Requests carry a per-launch bearer token that
only the mod holds; the token is never written to disk or logs.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .errors import PeerError, require
from .files import canonical
from .models import new_id

PROTOCOL = 1
MAX_BODY = 1048576
MAX_HEAD = 16384
MAX_WAIT_MS = 25000
HEARTBEAT_LOSS = 60
OWNER_CHECK = 2
IDLE = 600
LEASE_RENEW = 10
HEARTBEAT_FRESH = 30
READ_TIMEOUT = 10


def socket_directory(uid=None):
    """`/tmp/npeer-<uid>`, mode 0700, owned by the user, never a symlink."""
    root = Path('/tmp').resolve()/f'npeer-{os.getuid() if uid is None else uid}'
    try:root.mkdir(mode=0o700)
    except FileExistsError:pass
    info = os.lstat(root)
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'BRIDGE_UNSAFE', 'Bridge socket directory is not a real directory')
    require(info.st_uid == os.getuid(), 'BRIDGE_UNSAFE', 'Bridge socket directory belongs to another user')
    if stat.S_IMODE(info.st_mode) != 0o700:os.chmod(root, 0o700)
    return root


def socket_path(claude_session, root=None):
    return (root or socket_directory())/('b-'+hashlib.sha256(claude_session.encode()).hexdigest()[:12]+'.sock')


def claim_socket(path):
    """Remove a stale socket only after a connect fails and its owner checks pass."""
    try:info = os.lstat(path)
    except FileNotFoundError:return
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), 'BRIDGE_UNSAFE', 'Bridge socket path holds something else')
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1);probe.connect(str(path))
    except OSError:
        path.unlink(missing_ok=True);return
    finally:
        probe.close()
    raise PeerError('BRIDGE_RUNNING', 'A bridge already serves this Claude session')


def wait_ms(query, default=0):
    value = int((query.get('wait_ms') or [default])[0])
    require(0 <= value <= MAX_WAIT_MS, 'PROTOCOL_ERROR', f'wait_ms must be 0 to {MAX_WAIT_MS}')
    return value


ROUTES = [
    ('GET', r'/v1/hello', 'hello'),
    ('POST', r'/v1/heartbeat', 'heartbeat'),
    ('GET', r'/v1/catalog', 'catalog'),
    ('GET', r'/v1/lineage', 'lineage'),
    ('POST', r'/v1/spawn', 'spawn'),
    ('POST', r'/v1/rebind', 'rebind'),
    ('GET', r'/v1/bindings/(?P<agent>[A-Za-z0-9_-]{1,128})', 'binding'),
    ('GET', r'/v1/mirror/status', 'mirror_status'),
    ('POST', r'/v1/mirror/(?P<op>attach|surfacing|ack|fault|release|fallthrough)', 'mirror'),
    ('GET', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/activity', 'activity'),
    ('POST', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/input', 'input'),
    ('POST', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/interrupt', 'interrupt'),
    ('POST', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/cancel', 'cancel_worker'),
    ('GET', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/result', 'result'),
    ('GET', r'/v1/workers/(?P<worker>[A-Za-z0-9_-]{1,128})/resume-command', 'resume_command'),
    ('POST', r'/v1/groups/(?P<group>[A-Za-z0-9_-]{1,128})/cancel', 'cancel_group'),
    ('GET', r'/v1/artifacts/(?P<artifact>[A-Za-z0-9_:-]{1,160})', 'artifact'),
    ('POST', r'/v1/shutdown', 'shutdown'),
]
ROUTES = [(method, re.compile(pattern+r'\Z'), name) for method, pattern, name in ROUTES]


class Bridge:
    """The HTTP surface. A backend answers every route but heartbeat and shutdown."""

    def __init__(self, backend, path, *, token=None, clock=time.monotonic, heartbeat_loss=HEARTBEAT_LOSS,
                 owner_check=OWNER_CHECK, idle=IDLE, lease_renew=LEASE_RENEW, out=None):
        self.backend, self.path, self.clock = backend, Path(path), clock
        self.token = token or secrets.token_urlsafe(32)
        self.heartbeat_loss, self.owner_check, self.idle, self.lease_renew = heartbeat_loss, owner_check, idle, lease_renew
        self.out = out or sys.stdout
        self.last_heartbeat = self.last_request = clock()
        self.stopped = asyncio.Event()
        self.reason = None
        self.active = 0

    def stop(self, reason):
        if not self.stopped.is_set():
            self.reason = reason;self.stopped.set()

    async def run(self):
        claim_socket(self.path)
        server = await asyncio.start_unix_server(self.connection, path=str(self.path), limit=MAX_HEAD)
        os.chmod(self.path, 0o600)
        # Recorded only once this bridge holds the socket: one that lost the socket never touches the holder's record.
        record = getattr(self.backend, 'record_manifest', None)
        if record:record(self.path)
        ready = dict(ready=True, socket=str(self.path), token=self.token, protocol=PROTOCOL, **self.backend.hello())
        self.out.write(json.dumps(ready, separators=(',', ':'))+'\n');self.out.flush()
        watch = asyncio.create_task(self.watchdog())
        try:
            await self.stopped.wait()
        finally:
            watch.cancel()
            server.close()
            try:await asyncio.wait_for(server.wait_closed(), 2)
            except (TimeoutError, asyncio.TimeoutError):pass
            self.path.unlink(missing_ok=True)
            await self.backend.close(self.reason)
        return self.reason

    async def watchdog(self):
        owner_due = renew_due = 0.0
        while not self.stopped.is_set():
            now = self.clock()
            if now - self.last_heartbeat > self.heartbeat_loss:self.stop('heartbeat-lost');break
            if now >= owner_due:
                owner_due = now + self.owner_check
                if not await self.backend.owner_alive():self.stop('owner-exited');break
            if now >= renew_due:
                renew_due = now + self.lease_renew
                # Leases are renewed only while the mod's heartbeats arrive; otherwise they lapse by TTL.
                if now - self.last_heartbeat <= HEARTBEAT_FRESH:await self.backend.renew_leases()
            if not self.active and now - self.last_request > self.idle and not self.backend.has_leases():self.stop('idle');break
            await asyncio.sleep(0.5)

    async def connection(self, reader, writer):
        request_id = new_id();self.active += 1
        try:
            status, response = await self.respond(reader, request_id)
        except Exception as error:  # A defect in one request never takes the bridge down.
            status, response = 500, dict(schema_version=1, ok=False, data=None, request_id=request_id,
                                         error={'code': 'PROTOCOL_ERROR', 'message': type(error).__name__, 'may_have_received': False})
        finally:
            self.active -= 1;self.last_request = self.clock()
        body = canonical(response)
        try:
            writer.write(b'HTTP/1.1 %d %s\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n'
                         % (status, b'OK' if status == 200 else b'Error', len(body)) + body)
            await asyncio.wait_for(writer.drain(), READ_TIMEOUT)
        except (ConnectionError, TimeoutError, asyncio.TimeoutError):pass
        finally:
            writer.close()

    async def respond(self, reader, request_id):
        def envelope(value=None, error=None):
            return dict(schema_version=1, ok=error is None, data=value if error is None else None, error=error, request_id=request_id)
        try:
            head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), READ_TIMEOUT)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, asyncio.TimeoutError):
            return 400, envelope(error={'code': 'PROTOCOL_ERROR', 'message': 'Malformed or oversized request head', 'may_have_received': False})
        lines = head.decode('latin-1').split('\r\n')
        try:method, target, _ = lines[0].split(' ', 2)
        except ValueError:
            return 400, envelope(error={'code': 'PROTOCOL_ERROR', 'message': 'Malformed request line', 'may_have_received': False})
        headers = {}
        for line in lines[1:]:
            if ':' in line:
                key, value = line.split(':', 1);headers[key.strip().lower()] = value.strip()
        if not hmac.compare_digest(headers.get('authorization', '').encode(), ('Bearer '+self.token).encode()):
            return 401, envelope(error={'code': 'CALLER_UNBOUND', 'message': 'Bridge token required', 'may_have_received': False})
        # Any authenticated request shows the mod is alive, as a heartbeat does: a module instance that hot-reloaded
        # keeps its bridge while it finishes a held step's polls.
        self.last_heartbeat = self.clock()
        length = int(headers.get('content-length') or 0)
        if length > MAX_BODY:
            return 413, envelope(error={'code': 'PAYLOAD_TOO_LARGE', 'message': f'Request body exceeds {MAX_BODY} bytes', 'may_have_received': False})
        try:raw = await asyncio.wait_for(reader.readexactly(length), READ_TIMEOUT) if length else b''
        except (asyncio.IncompleteReadError, TimeoutError, asyncio.TimeoutError):
            return 400, envelope(error={'code': 'PROTOCOL_ERROR', 'message': 'Request body ended early', 'may_have_received': False})
        url = urlsplit(target);query = parse_qs(url.query)
        for verb, pattern, name in ROUTES:
            found = pattern.match(url.path)
            if found and verb == method:break
        else:
            return 404, envelope(error={'code': 'PROTOCOL_ERROR', 'message': 'Unknown bridge route', 'may_have_received': False})
        try:
            body = json.loads(raw) if raw else {}
            require(isinstance(body, dict), 'PROTOCOL_ERROR', 'Request body must be a JSON object')
            return 200, envelope(await self.route(name, found.groupdict(), query, body))
        except PeerError as error:
            return 200, envelope(error=error.json())
        except (ValueError, KeyError, TypeError) as error:
            return 200, envelope(error={'code': 'PROTOCOL_ERROR', 'message': str(error)[:300], 'may_have_received': False})

    async def route(self, name, path, query, body):
        backend = self.backend
        if name == 'heartbeat':
            self.last_heartbeat = self.clock();return {'at': time.time()}
        if name == 'shutdown':
            asyncio.get_running_loop().call_soon(self.stop, 'shutdown');return {'stopping': True}
        if name == 'hello':return dict(backend.hello(), protocol=PROTOCOL)
        if name == 'catalog':return await backend.catalog()
        if name == 'lineage':return await backend.lineage((query.get('cursor') or [None])[0], wait_ms(query))
        if name == 'spawn':return await backend.spawn(body)
        if name == 'rebind':return await backend.rebind(body)
        if name == 'binding':return await backend.binding(path['agent'], wait_ms(query))
        if name == 'mirror_status':return await backend.mirror_status()
        if name == 'mirror':return await backend.mirror(path['op'], body)
        if name == 'activity':
            maximum = int((query.get('max') or [512])[0])
            require(1 <= maximum <= 512, 'PROTOCOL_ERROR', 'max must be 1 to 512')
            return await backend.activity(path['worker'], int((query.get('after') or [0])[0]), wait_ms(query), maximum, (query.get('item') or [None])[0])
        if name == 'input':return await backend.input(path['worker'], body)
        if name == 'interrupt':return await backend.interrupt(path['worker'], body)
        if name == 'cancel_worker':return await backend.cancel_worker(path['worker'], body)
        if name == 'result':return await backend.result(path['worker'])
        if name == 'resume_command':return await backend.resume_command(path['worker'])
        if name == 'cancel_group':return await backend.cancel_group(path['group'], body)
        if name == 'artifact':
            return await backend.artifact(path['artifact'], int((query.get('offset') or [0])[0]), int((query.get('limit') or [65536])[0]))
        raise PeerError('PROTOCOL_ERROR', 'Unknown bridge route')
