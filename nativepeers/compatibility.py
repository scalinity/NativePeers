"""Only previously verified native executable fingerprints admit new work."""
import hashlib
import json
from pathlib import Path

from .errors import require
from .files import advisory_lock,atomic_json

_cache={}


def fingerprint(path):
    path=Path(path).resolve(strict=True);info=path.stat();key=(str(path),info.st_size,info.st_mtime_ns)
    if key not in _cache:
        with path.open('rb') as stream:_cache[key]=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(path),'sha256':_cache[key],'size':info.st_size}


def verify(journal,provider,executable,*,worker_id=None):
    receipt=journal.root/'compat/runtime.json'
    require(receipt.exists(),'CAPABILITY_MISMATCH','Native compatibility has not been installed or verified for this state root')
    document=json.loads(receipt.read_text());expected=document['providers'].get(provider)
    require(expected,'CAPABILITY_MISMATCH','Provider has no installed compatibility receipt')
    actual=fingerprint(executable)
    pinned=None
    if worker_id:
        row=journal.one('SELECT provider,lease_json FROM workers WHERE worker_id=?',(worker_id,))
        require(row and row['provider']==provider,'CAPABILITY_MISMATCH','Runtime lease does not match this provider')
        pinned=json.loads(row['lease_json']).get('verified_runtime')
        require(pinned is None or all(pinned.get(k)==actual[k] for k in ('path','sha256','size')),
                'CAPABILITY_MISMATCH','The worker\'s verified executable no longer matches its pinned runtime')
    retained=document.get('retained',{}).get(provider,{}).get(actual['sha256'],{})
    old_allowed=bool(pinned and not retained.get('revoked') and worker_id in retained.get('workers',[])
                     and all(retained.get('fingerprint',{}).get(k)==actual[k] for k in ('path','sha256','size')))
    require(actual['sha256']==expected['sha256'] or old_allowed,'CAPABILITY_MISMATCH','Native executable changed; passive compatibility review is required before this operation')
    return actual


def update_verified(journal,replacements,*,initialize=False,expected=None):
    """Retire only explicit approvals, scoped to leases already verified with them."""
    receipt=journal.root/'compat/runtime.json'
    with advisory_lock(journal.root/'locks/runtime.lock'):
        document=json.loads(receipt.read_text()) if receipt.exists() else {'schema_version':1,'providers':{}}
        require(not initialize or expected is None,'PROTOCOL_ERROR','Initialization is separate from a guarded runtime transition')
        if expected is not None:
            require(set(expected)==set(replacements),'PROTOCOL_ERROR','Guard every selected runtime transition')
            for provider,actual in replacements.items():
                current=document['providers'].get(provider,{})
                require(current and all(current.get(k)==expected[provider].get(k) for k in ('path','sha256','size')),
                        'CAPABILITY_MISMATCH','The runtime approval changed before the guarded transition')
                retained=document.get('retained',{}).get(provider,{}).get(actual['sha256'],{})
                require(not retained.get('revoked'),'CAPABILITY_MISMATCH','The target runtime approval was explicitly revoked')
        changed=[]
        for provider,actual in replacements.items():
            old=document['providers'].get(provider)
            if initialize and old is not None:continue
            changed.append(provider)
            if old and old['sha256']!=actual['sha256']:
                workers=[]
                for row in journal.all('SELECT worker_id,lease_json FROM workers WHERE provider=?',(provider,)):
                    pinned=json.loads(row['lease_json']).get('verified_runtime',{})
                    if all(pinned.get(k)==old.get(k) for k in ('path','sha256','size')):workers.append(row['worker_id'])
                history=document.setdefault('retained',{}).setdefault(provider,{})
                prior=history.get(old['sha256'],{})
                history[old['sha256']]={'fingerprint':old,'workers':sorted(set(workers+prior.get('workers',[]))),
                                       'revoked':prior.get('revoked',False)}
            document['providers'][provider]=actual
        if changed:atomic_json(receipt,document)
        return changed


def revoke(journal,provider,digest):
    require(isinstance(digest,str) and len(digest)==64 and all(c in '0123456789abcdef' for c in digest),
            'PROTOCOL_ERROR','Supply an exact SHA-256 fingerprint')
    receipt=journal.root/'compat/runtime.json'
    with advisory_lock(journal.root/'locks/runtime.lock'):
        document=json.loads(receipt.read_text())
        retained=document.get('retained',{}).get(provider,{}).get(digest)
        require(retained,'PROTOCOL_ERROR','No retained runtime approval has this provider/fingerprint')
        retained['revoked']=True;atomic_json(receipt,document)
    return {'provider':provider,'sha256':digest,'revoked':True,'current_approval_unchanged':True}


def record_verified(journal,source,*,preserve_existing=False,selected=None):
    value=json.loads(Path(source).read_text())
    receipt=journal.root/'compat/runtime.json'
    current=json.loads(receipt.read_text()).get('providers',{}) if receipt.exists() else {}
    providers={}
    for provider in ('claude','codex'):
        if preserve_existing and provider in current:continue
        info=value['providers'][provider]
        require(info.get('executable') and info.get('executable_sha256'),'CAPABILITY_MISMATCH','This source does not contain a worker/inbox runtime approval')
        actual=fingerprint((selected or {}).get(provider,info['executable']))
        require(info.get('executable_sha256')==actual['sha256'],'CAPABILITY_MISMATCH','Current binary differs from the executable integration evidence')
        providers[provider]=dict(actual,version=info['version'])
    if providers:update_verified(journal,providers,initialize=preserve_existing)


def diagnose(journal, executables=None):
    """Passive comparison only: never certify an update or launch a provider."""
    receipt=journal.root/'compat/runtime.json'
    providers=json.loads(receipt.read_text()).get('providers',{}) if receipt.exists() else {}
    from .grok import executable as grok_executable
    executables=executables or {'claude':Path.home()/'.local/bin/claude',
        'codex':Path.home()/'.local/share/codex-mcp-manager/current/bin/codex',
        'antigravity':Path.home()/'.local/bin/agy','grok':grok_executable()}
    operations={'claude':['interactive inbox','structured worker turns'],
        'codex':['native queue','private worker app-server'],
        'antigravity':['native hook inbox','streaming worker turns'],
        'grok':['native hook inbox','streaming worker turns']}
    results=[]
    for provider,path in executables.items():
        expected=providers.get(provider);error=None
        try:actual=fingerprint(path)
        except OSError as exception:actual=None;error=type(exception).__name__
        matches=bool(expected and actual and expected['sha256']==actual['sha256'])
        results.append(dict(provider=provider,state='PASS' if matches else 'CAPABILITY_MISMATCH',
            expected=expected,current=actual,error=error,affected_operations=operations.get(provider,[]),
            verification_scope=(expected or {}).get('scope','Installed executable receipt; live acceptance remains scoped to its recorded source/runtime fingerprints'),
            next_action='No executable change detected' if matches else 'Inspect the executable change and rerun affected passive/native integration checks before recording a new receipt'))
    return results
