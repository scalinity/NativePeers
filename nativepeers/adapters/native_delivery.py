"""The M1-proven queue and Claude socket codecs. Admission is not processing."""
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import stat
import signal

from ..errors import PeerError, require
from ..files import async_advisory_lock, canonical
from ..launch import launch_environment
from ..models import PEER_FRAME, new_id
from ..process import identity,signal_owned
from ..presentation import render

QUEUE_STOP_GRACE=2


async def stop_queue_writer(process,owner):
    if owner and process.returncode is None:signal_owned(owner,signal.SIGTERM,group=True)
    try:await asyncio.wait_for(process.communicate(),QUEUE_STOP_GRACE)
    except asyncio.TimeoutError:
        if owner and process.returncode is None:signal_owned(owner,signal.SIGKILL,group=True)
        try:await asyncio.wait_for(process.communicate(),QUEUE_STOP_GRACE)
        except asyncio.TimeoutError:
            raise PeerError('DELIVERY_UNKNOWN','Queue helper cleanup could not be confirmed; reconcile the owned attempt',may_have_received=True) from None


async def codex_queue(peer, message, *, journal):
    env = launch_environment("codex")
    env["CODEX_HOME"] = peer["home"]
    from ..compatibility import codex_inbox_runtime
    async with async_advisory_lock(journal.root/'locks/runtime.lock'):
        runtime=codex_inbox_runtime(journal,peer)
        process = await asyncio.create_subprocess_exec(runtime["path"], "queue", "--thread",
            peer["peer_key"]["native_session_id"], "--message", render(message),
            env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True)
    try:owner=identity(process.pid).json()
    except ProcessLookupError:owner=None
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 25)
    except asyncio.TimeoutError:
        await stop_queue_writer(process,owner)
        raise PeerError("DELIVERY_UNKNOWN", "Queue writer timed out; reconcile before another submission", may_have_received=True)
    except asyncio.CancelledError as error:
        try:await stop_queue_writer(process,owner)
        except PeerError as cleanup:error.add_note(str(cleanup))
        raise
    except Exception:
        await stop_queue_writer(process,owner)
        raise
    match = re.fullmatch(rb"Queued message ([0-9a-f-]+) for thread ([0-9a-f-]+)\.\s*", stdout)
    if process.returncode or not match or match[2].decode() != peer["peer_key"]["native_session_id"]:
        raise PeerError("DELIVERY_UNKNOWN", "Native queue outcome is unconfirmed", may_have_received=True,
                        details={"exit_code":process.returncode, "diagnostic":stderr.decode(errors="replace")[:1024]})
    return dict(stage="ACCEPTED_QUEUED", external_submission_id=match[1].decode(),
                native_expiry_enforced=False, evidence_kind="native-queue-admission", runtime=runtime)


def socket_endpoint(peer):
    value = peer["transport"]["endpoint"]
    path = Path(value[4:] if value.startswith("uds:") else value)
    info, parent = path.lstat(), path.parent.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "TARGET_GONE", "Unsafe or replaced native socket")
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid() and not parent.st_mode & 0o077,
            "TARGET_GONE", "Native socket directory is not private")
    expected = peer["transport"].get("endpoint_identity")
    require(expected is None or expected == [info.st_dev, info.st_ino], "TARGET_REBOUND", "Native socket inode changed")
    return path


def _claude_deliver(peer, message):
    path = socket_endpoint(peer)
    # A finite receipt endpoint is an external local endpoint, never a fake Claude session.
    receipt_path = path.parent/("np-"+new_id()[:16]+".sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(receipt_path)); os.chmod(receipt_path, 0o600)
        listener.listen(4); listener.settimeout(1.5)
        frame = dict(msgV=1, msg_id=message["message_id"], type="user", priority="next",
                     session_id=peer["peer_key"]["native_session_id"],
                     message=dict(role="user", content=render(message)),
                     **{"from":"uds:"+str(receipt_path)})
        wire = canonical(frame)+b"\n"
        require(len(wire) <= PEER_FRAME, "PAYLOAD_TOO_LARGE", "Native delivery wrapper exceeds 64 KiB")
        admitted = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3); client.connect(str(path))
                admitted = True
                client.sendall(wire); client.shutdown(socket.SHUT_WR)
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(1)
                raw = b""
                while b"\n" not in raw and len(raw) <= PEER_FRAME:
                    part = connection.recv(4096)
                    if not part: break
                    raw += part
                receipt = json.loads(raw.split(b"\n")[0])
                if receipt.get("action") == "peer_message_status" and receipt.get("orig_msg_id") == message["message_id"]:
                    stages = {"held":"HELD", "denied":"REJECTED", "expired":"REJECTED", "delivered":"INGESTED"}
                    if receipt.get("status") in stages:
                        return dict(stage=stages[receipt["status"]], evidence_kind="native-inbox-receipt",
                                    native_status=receipt["status"], native_expiry_enforced=False)
        except (socket.timeout, json.JSONDecodeError):
            pass
        except OSError as exception:
            raise PeerError("DELIVERY_UNKNOWN" if admitted else "TARGET_GONE", "Native inbox submission failed",
                            may_have_received=admitted) from exception
        return dict(stage="SUBMITTED_UNCONFIRMED", evidence_kind="socket-write", native_expiry_enforced=False)
    finally:
        listener.close(); receipt_path.unlink(missing_ok=True)


async def claude_inbox(peer, message):
    return await asyncio.to_thread(_claude_deliver, peer, message)
