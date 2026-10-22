"""Only previously verified native executable fingerprints admit new work."""
import hashlib
import json
from pathlib import Path

from .errors import require
from .files import atomic_json

_cache={}


def fingerprint(path):
    path=Path(path).resolve(strict=True);info=path.stat();key=(str(path),info.st_size,info.st_mtime_ns)
    if key not in _cache:
        with path.open('rb') as stream:_cache[key]=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(path),'sha256':_cache[key],'size':info.st_size}


def verify(journal,provider,executable):
    receipt=journal.root/'compat/runtime.json'
    require(receipt.exists(),'CAPABILITY_MISMATCH','Native compatibility has not been installed or verified for this state root')
    expected=json.loads(receipt.read_text())['providers'].get(provider)
    require(expected,'CAPABILITY_MISMATCH','Provider has no installed compatibility receipt')
    actual=fingerprint(executable)
    require(actual['sha256']==expected['sha256'],'CAPABILITY_MISMATCH','Native executable changed; passive compatibility review is required before this operation')
    return actual


def record_verified(journal,source):
    value=json.loads(Path(source).read_text())
    receipt=journal.root/'compat/runtime.json'
    providers=json.loads(receipt.read_text())['providers'] if receipt.exists() else {}
    for provider in ('claude','codex'):
        info=value['providers'][provider];actual=fingerprint(info['executable'])
        require(info.get('executable_sha256')==actual['sha256'],'CAPABILITY_MISMATCH','Current binary differs from the executable integration evidence')
        providers[provider]=dict(actual,version=info['version'])
    atomic_json(journal.root/'compat/runtime.json',{'providers':providers,'schema_version':1})
