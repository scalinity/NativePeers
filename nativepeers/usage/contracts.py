"""Usage-only transport receipts; never approvals for workers or native inboxes."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from ..compatibility import fingerprint
from ..errors import PeerError,require
from ..files import advisory_lock,atomic_json
from .native_command import BROWSER_PROFILE,guarded_argv

# The installed arm64 build was inspected through its Go function table and
# native instructions: print usage forces quota refresh, clears the cached
# pointer, then Get waits through forceRefresh. A version string alone cannot
# carry that freshness proof to a different executable.
AGY_FRESH_FETCH={'b89677b741d8d9d38cf0a6911ab468fac629158194039904cbfd645fdb897554':
    'native-print-usage-force-refresh-v1'}


def managed_claude_startup():
    root=Path('/Library/Application Support/ClaudeCode')
    paths=[root/'managed-settings.json',*sorted((root/'managed-settings.d').glob('*.json'))]
    hashes={}
    for path in paths:
        if not path.exists():continue
        raw=path.read_bytes();data=json.loads(raw)
        require(isinstance(data,dict) and not any(data.get(k) for k in ('hooks','statusLine','enabledPlugins','mcpServers')),
            'SOURCE_NOT_QUALIFIED','Managed Claude startup customizations require a separate no-inference inspection')
        hashes[path.name]=hashlib.sha256(raw).hexdigest()
    return hashes

def inspect(provider,binary,*,authorize_pty=False):
    runtime=fingerprint(binary)
    version=subprocess.run([str(binary),'--version'],capture_output=True,text=True,timeout=3,check=True).stdout.strip()
    match=re.search(r'(\d+)\.(\d+)\.(\d+)',version)
    require(match,'CAPABILITY_MISMATCH','Native usage source has no recognizable version')
    numbers=tuple(map(int,match.groups()))
    guard=subprocess.run(guarded_argv('/usr/bin/open',['/nonexistent-npeer-browser-fixture']),capture_output=True,text=True,timeout=3)
    require(guard.returncode!=0 and 'Operation not permitted' in guard.stderr,'CAPABILITY_MISMATCH','Browser-launch guard did not deny the known launcher')
    result=dict(runtime=runtime,version=version,browser_guard_sha256=hashlib.sha256(BROWSER_PROFILE.encode()).hexdigest(),
        inspected_at=time.time(),proof='installed-native-usage-dispatch',provider=provider)
    if provider=='antigravity':
        require(numbers>=(1,1,11),'CAPABILITY_MISMATCH','Antigravity read commands require native 1.1.11 or newer')
        result.update(route='native-print-usage',qualified=True,
            dispatch_basis='Google CLI 1.1.11 local read-only print-command contract; exact installed executable and browser guard',
            fresh_fetch_contract=AGY_FRESH_FETCH.get(runtime['sha256']),
            freshness_basis='inspected-native-force-refresh' if runtime['sha256'] in AGY_FRESH_FETCH else 'UNVERIFIED')
    elif provider=='claude':
        # Inspect only public command code embedded in the installed executable.
        source=Path(binary).read_bytes()
        registry=re.search(rb'type:"local",name:"usage"[^;]{0,800}supportsNonInteractive:!0',source)
        dispatcher=b'case"local":{' in source and b'shouldQuery:!1' in source
        cached_projection=b'o?.status==="seeded"' in source and b'rate_limits:m===null?null' in source
        require(registry and dispatcher and cached_projection,'CAPABILITY_MISMATCH','Installed Claude usage dispatcher/cache projection differs from inspected contract')
        result.update(route='native-pty-usage' if authorize_pty else 'native-positional-usage',qualified=authorize_pty,
            pty_authorized=authorize_pty,direct_reason='Direct native usage can project seeded cached limits without the cache-status marker.',
            dispatch_basis='Installed local usage registry, non-interactive filter, local dispatcher and seeded-cache projection inspected',
            freshness_basis='native-panel-success-and-no-cache-marker' if authorize_pty else 'UNVERIFIED')
        result['managed_startup']=managed_claude_startup()
    else:raise PeerError('PROTOCOL_ERROR','This usage transport inspector is for Claude/Antigravity')
    require(fingerprint(binary)==runtime,'CAPABILITY_MISMATCH','Native executable changed during inspection')
    return result


def record(journal,receipt):
    path=journal.root/'compat/usage-sources.json'
    with advisory_lock(journal.root/'locks/usage-contract.lock'):
        data=json.loads(path.read_text()) if path.exists() else {}
        data[receipt['provider']]=receipt;atomic_json(path,data)


def verified(journal,provider,binary):
    path=journal.root/'compat/usage-sources.json'
    receipt=json.loads(path.read_text()).get(provider) if path.exists() else None
    require(receipt and receipt.get('qualified'),'SOURCE_NOT_QUALIFIED','Native '+provider+' quota transport is not qualified; inspect the usage-only contract')
    require(receipt['runtime']==fingerprint(binary) and receipt['browser_guard_sha256']==hashlib.sha256(BROWSER_PROFILE.encode()).hexdigest(),
        'CAPABILITY_MISMATCH','Native usage executable or browser-launch guard changed')
    if provider=='claude':
        require(receipt.get('pty_authorized') and receipt['route']=='native-pty-usage','SOURCE_NOT_QUALIFIED','Claude PTY usage collection is not authorized')
        require(receipt.get('managed_startup')==managed_claude_startup(),'SOURCE_NOT_QUALIFIED','Managed Claude startup configuration changed since inspection')
    return receipt
