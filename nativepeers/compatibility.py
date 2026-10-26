"""Only previously verified native executable fingerprints admit new work."""
import hashlib
import json
from pathlib import Path

from .errors import PeerError,require
from .files import advisory_lock,atomic_json

_cache={}


def update_status(provider,expected,actual,document):
    """Add detail without changing the existing machine-facing refusal code."""
    state='UPDATE_UNTESTED' if expected else 'QUALIFICATION_REQUIRED'
    reason='The selected executable has not been checked; a different hash is not an observed incompatibility.'
    if actual is None:state='EXECUTABLE_NOT_FOUND';reason='The selected executable is unavailable.'
    elif document.get('retained',{}).get(provider,{}).get(actual['sha256'],{}).get('revoked'):
        state='REVOKED';reason='This exact runtime approval was revoked.'
    else:
        check=document.get('update_checks',{}).get(provider,{})
        if check.get('runtime')==actual:
            state=check['compatibility_state'];reason=check['reason']
    action={'EXECUTABLE_NOT_FOUND':'Restore the selected native executable, then rerun doctor.',
            'REVOKED':'Select a qualified non-revoked runtime; routine renewal cannot override revocation.',
            'INCOMPATIBLE':'Inspect the named missing contract and qualify/adapt the affected seam; see docs/RUNTIME_UPDATES.md.',
            'PROVENANCE_UNVERIFIED':'Inspect the vendor installation and its code signature.',
            'QUALIFICATION_REQUIRED':'Qualify the affected native seams; see docs/RUNTIME_UPDATES.md.'}
    return dict(compatibility_state=state,reason=reason,operations=['worker_turns','inbox'],
        next_action=action.get(state,f'npeer runtime-check --provider {provider} --apply --json'))


def fingerprint(path,*,fresh=False):
    path=Path(path).resolve(strict=True);info=path.stat();key=(str(path),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    if fresh or key not in _cache:
        with path.open('rb') as stream:_cache[key]=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(path),'sha256':_cache[key],'size':info.st_size}


def verify(journal,provider,executable,*,worker_id=None):
    receipt=journal.root/'compat/runtime.json'
    require(receipt.exists(),'CAPABILITY_MISMATCH','Native compatibility has not been installed or verified for this state root')
    try:
        document=json.loads(receipt.read_text());expected=document['providers'].get(provider)
        if not isinstance(document.get('retained',{}),dict):raise ValueError('Invalid retained approvals')
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise PeerError('CAPABILITY_MISMATCH','Runtime approval receipt is unreadable') from None
    require(isinstance(expected,dict) and isinstance(expected.get('sha256'),str),
            'CAPABILITY_MISMATCH','Provider has no installed compatibility receipt')
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
    require(not retained.get('revoked') and (actual['sha256']==expected['sha256'] or old_allowed),
            'CAPABILITY_MISMATCH','Native executable is not approved for worker/inbox operations',
            details=update_status(provider,expected,actual,document))
    return actual


def update_verified(journal,replacements,*,initialize=False,expected=None,selected=None):
    """Retire only explicit approvals, scoped to leases already verified with them."""
    receipt=journal.root/'compat/runtime.json'
    with advisory_lock(journal.root/'locks/runtime.lock'):
        document=json.loads(receipt.read_text()) if receipt.exists() else {'schema_version':1,'providers':{}}
        require(not initialize or expected is None,'PROTOCOL_ERROR','Initialization is separate from a guarded runtime transition')
        if expected is not None:
            require(set(expected)==set(replacements),'PROTOCOL_ERROR','Guard every selected runtime transition')
            for provider,actual in replacements.items():
                current=document['providers'].get(provider,{})
                require(current and current==expected[provider],
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
        if selected is not None:
            require(set(selected)==set(replacements),'PROTOCOL_ERROR','Recheck every selected runtime')
            for provider,path in selected.items():
                require(fingerprint(path,fresh=True)=={k:replacements[provider][k] for k in ('path','sha256','size')},
                        'CAPABILITY_MISMATCH','Selected runtime changed before approval',details={'compatibility_state':'UPDATE_RACED'})
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
    receipt_error=None
    try:
        document=json.loads(receipt.read_text()) if receipt.exists() else {}
        providers=document.get('providers',{})
        if not isinstance(providers,dict) or any(not isinstance(value,dict) for value in providers.values()):
            raise ValueError('Invalid runtime provider entries')
    except (OSError,ValueError,TypeError,AttributeError) as error:
        document={};providers={};receipt_error='Runtime receipt unreadable: '+type(error).__name__
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
        expected=providers.get(provider);error=receipt_error
        try:actual=fingerprint(path)
        except OSError as exception:actual=None;error=type(exception).__name__
        detail=update_status(provider,expected,actual,document)
        matches=bool(expected and actual and expected['sha256']==actual['sha256'] and detail['compatibility_state']!='REVOKED')
        results.append(dict(provider=provider,state='PASS' if matches else 'CAPABILITY_MISMATCH',
            expected=expected,current=actual,error=error,affected_operations=operations.get(provider,[]),
            verification_scope=(expected or {}).get('scope','Installed executable receipt; live acceptance remains scoped to its recorded source/runtime fingerprints'),
            **dict(detail,compatibility_state='APPROVED' if matches else detail['compatibility_state'],
                   reason='The exact selected runtime is approved.' if matches else detail['reason'],
                   next_action='No executable change detected' if matches else detail['next_action'])))
    return results
