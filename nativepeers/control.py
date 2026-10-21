"""Bounded private local group requests. No shell or provider-general RPC."""
import asyncio
import json
import os
from pathlib import Path
import stat

from .errors import require, PeerError
from .files import canonical, private_directory
from .models import CONTROL_RESPONSE, PEER_FRAME


def runtime_root():
    return private_directory(Path('/tmp')/('npeer-'+str(os.getuid())))


async def group_call(journal,group_id,operation,payload,timeout=5):
    path=journal.root/'groups'/group_id/'owner.json'
    require(path.exists() and not path.is_symlink(),'SUPERVISOR_LOST','Group owner manifest is unavailable')
    manifest=json.loads(path.read_text())
    from .process import matches
    require(matches(manifest['owner']),'SUPERVISOR_LOST','Group supervisor is no longer alive')
    endpoint=Path(manifest['endpoint']);info=endpoint.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,
            'OWNERSHIP_UNKNOWN','Unsafe group endpoint')
    wire=canonical({'operation':operation,'payload':payload,'token':manifest['token']})+b'\n'
    require(len(wire)<=PEER_FRAME,'PAYLOAD_TOO_LARGE','Group request exceeds 64 KiB')
    reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(endpoint),limit=CONTROL_RESPONSE+1),timeout)
    try:
        writer.write(wire);await writer.drain()
        raw=await asyncio.wait_for(reader.readline(),timeout)
        require(len(raw)<=CONTROL_RESPONSE,'PROTOCOL_ERROR','Group response exceeds 2 MiB')
        value=json.loads(raw)
        if not value.get('ok'):
            error=value['error'];raise PeerError(error['code'],error['message'],may_have_received=error.get('may_have_received',False))
        return value['data']
    finally:
        writer.close();await writer.wait_closed()
