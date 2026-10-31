"""Only previously verified native executable fingerprints admit new work."""
import hashlib
import json
from pathlib import Path

from .errors import PeerError,require
from .files import advisory_lock,atomic_json

_cache={}
# A supported patch update renews on first use (runtime_updates.renew_selected); tests switch it off.
AUTO_RENEW=True


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
        next_action=('Wait for the CodexForge activation owner to qualify the runtime, then retry the unchanged request ID; inspect npeer doctor --json.'
                     if provider=='codex' and state in ('UPDATE_UNTESTED','QUALIFICATION_REQUIRED','UPDATE_RACED')
                     else action.get(state,'Inspect npeer doctor --json and ask the runtime owner to resolve the named condition.' if provider=='codex'
                                     else f'npeer runtime-check --provider {provider} --apply --json')),
        actual_runtime=actual, approved_runtime=expected)


def fingerprint(path,*,fresh=False):
    path=Path(path).resolve(strict=True);info=path.stat();key=(str(path),info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    if fresh or key not in _cache:
        with path.open('rb') as stream:_cache[key]=hashlib.file_digest(stream,'sha256').hexdigest()
    return {'path':str(path),'sha256':_cache[key],'size':info.st_size}


def verify(journal,provider,executable,*,worker_id=None,peer=None,renew=True):
    receipt=journal.root/'compat/runtime.json'
    require(receipt.exists(),'CAPABILITY_MISMATCH','Native compatibility has not been installed or verified for this state root')
    try:
        document=json.loads(receipt.read_text());expected=document['providers'].get(provider)
        if not isinstance(document.get('retained',{}),dict):raise ValueError('Invalid retained approvals')
    except (OSError,ValueError,TypeError,KeyError,AttributeError):
        raise PeerError('CAPABILITY_MISMATCH','Runtime approval receipt is unreadable') from None
    require(isinstance(expected,dict) and isinstance(expected.get('sha256'),str),
            'CAPABILITY_MISMATCH','Provider has no installed compatibility receipt')
    actual=fingerprint(executable,fresh=peer is not None)
    peer_grant=None
    if peer is not None:
        from .registry import Registry
        require(provider=='codex' and worker_id is None and peer['transport']['kind']=='codex-native-queue',
                'CAPABILITY_MISMATCH','Recipient runtime approval is only for interactive Codex inbox delivery',
                details={'compatibility_state':'RECIPIENT_MISMATCH','next_action':'Resolve the live interactive recipient again.'})
        current=Registry(journal).verify(peer['handle'],peer['incarnation_id'])
        require(current['owner']==peer['owner'] and actual['path']==peer['owner']['executable'],
                'CAPABILITY_MISMATCH','Recipient executable no longer matches its verified owner',
                details={'compatibility_state':'RECIPIENT_MISMATCH','next_action':'Resolve the live recipient again; do not reuse an altered owner.'})
        peer_grant={k:peer[k] for k in ('handle','incarnation_id','owner')}
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
    peer_allowed=bool(peer_grant and peer_grant in retained.get('peers',[])
                      and all(retained.get('fingerprint',{}).get(k)==actual[k] for k in ('path','sha256','size')))
    current_allowed=actual['sha256']==expected['sha256']
    if peer_grant:current_allowed=all(expected.get(k)==actual[k] for k in ('path','sha256','size'))
    approved=not retained.get('revoked') and (current_allowed or old_allowed or peer_allowed)
    if not approved and renew and AUTO_RENEW and pinned is None and peer is None and not retained.get('revoked'):
        from .runtime_updates import renew_selected
        if renew_selected(journal,provider,actual):return verify(journal,provider,executable,renew=False)
        try:document=json.loads(receipt.read_text())
        except (OSError,ValueError):pass
    require(approved,'CAPABILITY_MISMATCH','Native executable is not approved for worker/inbox operations',
            details=update_status(provider,expected,actual,document))
    return actual


def codex_inbox_runtime(journal,peer):
    """Verify the live recipient's executable; never fall back to mutable current."""
    try:
        return verify(journal,'codex',Path(peer['owner']['executable']),peer=peer,renew=False)
    except PeerError as error:
        if error.code=='CAPABILITY_MISMATCH':
            details=dict(error.details or {})
            actual=details.pop('actual_runtime',None)
            details['recipient_runtime']=actual
            try:details['selected_runtime']=fingerprint(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex')
            except OSError:details['selected_runtime']=None
            state=details.get('compatibility_state','CHECK_UNAVAILABLE')
            if state=='UPDATE_UNTESTED' and actual:
                expected=details.get('approved_runtime') or {}
                document=json.loads((journal.root/'compat/runtime.json').read_text())
                retained=document.get('retained',{}).get('codex',{})
                known=[expected]+[v.get('fingerprint',{}) for v in retained.values()]
                if any(v.get('path')==actual['path'] and v.get('sha256')!=actual['sha256'] for v in known):
                    state='EXECUTABLE_CHANGED'
                    details['next_action']='Ask the runtime owner to restore the exact qualified executable; no helper was started.'
                elif actual['sha256'] in retained:
                    state='RECIPIENT_NOT_RETAINED'
                    details['next_action']='Ask the runtime owner to retain this exact qualified live recipient or resume on the approved runtime.'
            details['compatibility_state']=state
            if state in ('UPDATE_UNTESTED','QUALIFICATION_REQUIRED','UPDATE_RACED'):
                details['next_action']='Wait for the exact recipient runtime to be qualified, then retry the unchanged request ID; inspect npeer doctor --json.'
            elif 'next_action' not in details:
                details['next_action']='Inspect npeer doctor --json and ask the runtime owner to resolve the recipient runtime condition.'
            error.details=details
        raise
    except OSError as error:
        missing=isinstance(error,FileNotFoundError)
        raise PeerError('CAPABILITY_MISMATCH','Recipient executable is unavailable; no queue helper was launched',
                        details={'compatibility_state':'EXECUTABLE_NOT_FOUND' if missing else 'CHECK_UNAVAILABLE',
                                 'next_action':'Ask the runtime owner to restore the qualified recipient executable.' if missing
                                               else 'Inspect access to the recipient executable with npeer doctor --json.',
                                 'recipient_executable':peer['owner']['executable']}) from error


def _codex_peer_grants(journal,runtime):
    from .registry import Registry
    peers=[]
    for row in journal.all("SELECT descriptor_json FROM peer_instances WHERE provider='codex' AND status='live'"):
        peer=json.loads(row['descriptor_json'])
        if peer['transport'].get('kind')!='codex-native-queue' or peer['owner'].get('executable')!=runtime.get('path'):continue
        try:Registry(journal).verify(peer['handle'],peer['incarnation_id'])
        except PeerError:continue
        peers.append({k:peer[k] for k in ('handle','incarnation_id','owner')})
    if peers:
        try:actual=fingerprint(Path(runtime['path']),fresh=True)
        except OSError:return []
        if any(actual[k]!=runtime.get(k) for k in ('path','sha256','size')):return []
    return peers


def retain_codex_recipients(journal,*,apply=False,expected=None):
    """Explicit install migration for live recipients of existing retained approvals."""
    receipt=journal.root/'compat/runtime.json'
    with advisory_lock(journal.root/'locks/runtime.lock'):
        document=json.loads(receipt.read_text())
        current=document['providers']['codex'];retained=document.get('retained',{}).get('codex',{})
        baseline={'approval':current,'retained':retained}
        require(not apply or expected is not None,'PROTOCOL_ERROR','Apply the inspected recipient-retention plan with its expected state')
        require(expected is None or expected==baseline,'CAPABILITY_MISMATCH','Codex approval or retained state changed before recipient retention')
        selected=fingerprint(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex',fresh=True)
        require(all(current.get(k)==selected[k] for k in ('path','sha256','size')),
                'CAPABILITY_MISMATCH','Selected runtime changed before recipient retention')
        additions=[]
        for digest,entry in retained.items():
            runtime=entry.get('fingerprint',{})
            if entry.get('revoked') or runtime.get('sha256')!=digest:continue
            grants=[p for p in _codex_peer_grants(journal,runtime) if p not in entry.get('peers',[])]
            if grants:additions.append({'sha256':digest,'peers':grants})
        # Keep the dry-run guard independent of the mutations returned below.
        baseline=json.loads(json.dumps(baseline))
        if apply and additions:
            for item in additions:
                retained[item['sha256']].setdefault('peers',[]).extend(item['peers'])
            atomic_json(receipt,document)
        return {'applied':apply,'expected':baseline,'additions':additions,'selected_runtime':selected}


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
            if old and any(old.get(k)!=actual.get(k) for k in ('path','sha256','size')):
                workers=[]
                for row in journal.all('SELECT worker_id,lease_json FROM workers WHERE provider=?',(provider,)):
                    pinned=json.loads(row['lease_json']).get('verified_runtime',{})
                    if all(pinned.get(k)==old.get(k) for k in ('path','sha256','size')):workers.append(row['worker_id'])
                history=document.setdefault('retained',{}).setdefault(provider,{})
                prior=history.get(old['sha256'],{})
                peers=list(prior.get('peers',[]))
                if provider=='codex':
                    for grant in _codex_peer_grants(journal,old):
                        if grant not in peers:peers.append(grant)
                history[old['sha256']]={'fingerprint':old,'workers':sorted(set(workers+prior.get('workers',[]))),
                                       'peers':peers,'revoked':prior.get('revoked',False)}
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
        'codex':['new recipient queue helpers','private worker app-server'],
        'antigravity':['native hook inbox','streaming worker turns'],
        'grok':['native hook inbox','streaming worker turns']}
    results=[]
    for provider,path in executables.items():
        expected=providers.get(provider);error=receipt_error
        try:actual=fingerprint(path)
        except OSError as exception:actual=None;error=type(exception).__name__
        detail=update_status(provider,expected,actual,document)
        if actual is None and error!='FileNotFoundError':
            detail.update(compatibility_state='CHECK_UNAVAILABLE',reason='The selected executable could not be inspected.',
                          next_action='Inspect access to the selected executable, then rerun doctor.')
        matches=bool(expected and actual and expected['sha256']==actual['sha256'] and detail['compatibility_state']!='REVOKED')
        bounded=bool(matches and expected.get('update_basis'))
        results.append(dict(provider=provider,state='SUPPORTED_UPDATE' if bounded else 'PASS' if matches else 'CAPABILITY_MISMATCH',
            expected=expected,current=actual,error=error,affected_operations=operations.get(provider,[]),
            verification_scope=(expected or {}).get('scope','Installed executable receipt; live acceptance remains scoped to its recorded source/runtime fingerprints'),
            native_acceptance=(expected or {}).get('native_acceptance','UNKNOWN') if matches else 'NOT_RUN',
            **dict(detail,compatibility_state='SUPPORTED_UPDATE' if bounded else 'APPROVED' if matches else detail['compatibility_state'],
                   reason='Selected runtime admitted by bounded checks; native behavior NOT_RUN.' if bounded else 'The exact selected runtime is approved.' if matches else detail['reason'],
                   next_action='No executable change detected' if matches else detail['next_action'])))
    return results
