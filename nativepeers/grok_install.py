"""Owned Grok-native hooks, guarded runtime initialization and passive readiness."""
import json
from pathlib import Path
import time
import tomlkit

from .compatibility import fingerprint,verify
from .errors import require
from .files import advisory_lock,atomic_json,private_directory
from .grok import executable,native_home
from .install import compare_write,file_hash
from .profiles import ROOT


def desired(home=None):
    home=Path(home) if home is not None else native_home()
    return {home/'hooks/nativepeers.json':{'hooks':{kind:[{'hooks':[{'type':'command',
        'command':str(ROOT/'bin/npeer-grok-hook'),'args':[kind],'timeout':5}]}]
        for kind in ('SessionStart','SessionEnd','UserPromptSubmit','PreToolUse')}}}


def desired_profiles(state_root):
    state=Path(state_root).resolve()
    return {'nativepeers-'+profile:dict(extends=base,deny=[str(state/'grok-kernel-denied')])
            for profile,base in (('analysis','read-only'),('workspace','workspace'))}


def profile_readiness(journal,spec=None,*,home=None):
    home=Path(home) if home is not None else Path((spec or {}).get('provider_home') or native_home())
    expected=desired_profiles(journal.root);paths=[home/'sandbox.toml'];checks=[]
    if spec and spec.get('cwd'):
        project=Path(spec['cwd']).resolve()/'.grok/sandbox.toml'
        if project.exists() or project.is_symlink():paths.append(project)
    for path in paths:
        try:
            data=tomlkit.parse(path.read_text()) if path.exists() else {}
            profiles=data.get('profiles',{})
            current={name:profiles[name] for name in expected if name in profiles}
            state='MODIFIED' if path.is_symlink() else 'MISSING' if not current else 'PRESENT' if current==expected else 'MODIFIED'
        except (OSError,ValueError,TypeError):state='UNKNOWN'
        checks.append(dict(kind='sandbox_profiles',path=str(path),state=state,required=True,
                           native_effective_precedence='unknown',reason='Only named NativePeers profiles are compared'))
    if checks[0]['state']=='MISSING' and any(check['state']=='PRESENT' for check in checks[1:]):checks[0]['required']=False
    return checks


def hook_readiness(spec=None,*,home=None):
    home=Path(home) if home is not None else Path((spec or {}).get('provider_home') or native_home())
    checks=[]
    definitions=desired(home);global_path=next(iter(definitions));expected=definitions[global_path]
    paths=[global_path]
    if spec and spec.get('cwd'):
        project=Path(spec['cwd']).resolve()/'.grok/hooks/nativepeers.json'
        if project.exists() or project.is_symlink():paths.append(project)
    for path in paths:
        try:
            data=json.loads(path.read_text()) if path.exists() else None
            state='MODIFIED' if path.is_symlink() else 'MISSING' if data is None else 'PRESENT' if data==expected else 'MODIFIED'
        except (OSError,ValueError,TypeError):state='UNKNOWN'
        checks.append(dict(kind='hooks',path=str(path),state=state,
            reason='Owned Grok-native hook definition matches' if state=='PRESENT' else 'Owned Grok-native hooks are absent, modified or unreadable',
            native_effective_precedence='unknown',folder_trust='Native folder trust is required; static presence does not prove trust',required=True))
    # A disposable trusted fixture may carry the identical owned hook without a
    # global installation. Conflicting global/project entries still refuse.
    if checks[0]['state']=='MISSING' and any(check['state']=='PRESENT' for check in checks[1:]):checks[0]['required']=False
    path=ROOT/'bin/npeer-grok-hook'
    import os
    checks.append(dict(kind='hook_entrypoint',path=str(path),state='MISSING' if not path.is_file() else 'PRESENT' if os.access(path,os.X_OK) else 'DISABLED'))
    return checks


def verify_worker_ready(journal,spec,executable,*,worker_id=None):
    """Check static admission; a fresh owned hook separately proves kernel enforcement."""
    verify(journal,'grok',executable,worker_id=worker_id)
    require(spec.get('permission_profile') in ('analysis','workspace'),'PERMISSION_CAPABILITY_UNKNOWN','Grok supports only analysis and workspace worker profiles')
    checks=hook_readiness(spec)+profile_readiness(journal,spec)
    require(all(check['state']=='PRESENT' for check in checks if check.get('required',True)),'PERMISSION_CAPABILITY_UNKNOWN',
            'Owned Grok-native hooks, sandbox profiles or entrypoint are absent, modified or unresolved',details=checks)
    from .grok_worker import arguments,allowed_tools
    args=arguments(spec)
    sandbox='nativepeers-'+spec['permission_profile']
    def option(name):return args[args.index(name)+1] if name in args and args.index(name)+1<len(args) else None
    require(option('--sandbox')==sandbox and option('--tools')==','.join(allowed_tools(spec['permission_profile']))
            and '--no-auto-update' in args,'PERMISSION_CAPABILITY_UNKNOWN','Grok launch must retain its native sandbox and fixed tool inventory')


def owned_value(path,raw):
    if not raw.strip():return None
    if path.name!='sandbox.toml':return json.loads(raw)
    profiles=tomlkit.parse(raw.decode()).get('profiles',{})
    current={name:profiles[name].unwrap() if hasattr(profiles[name],'unwrap') else profiles[name]
             for name in ('nativepeers-analysis','nativepeers-workspace') if name in profiles}
    return current or None


def install(journal,*,home=None,dry_run=False,remove=False,verified_source=None):
    home=Path(home) if home is not None else native_home();manifest_path=journal.root/'grok-install.json'
    require(not home.is_symlink() and not (home/'hooks').is_symlink(),'OWNERSHIP_CONFLICT','Grok home and hook directory must not be symlinks')
    with advisory_lock(journal.root/'locks/install.lock'):
        actual=None
        receipt_path=journal.root/'compat/runtime.json'
        receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {'schema_version':1,'providers':{}}
        if not remove and 'grok' not in receipt['providers']:
            source=Path(verified_source) if verified_source is not None else ROOT/'evidence/capabilities.json'
            expected=json.loads(source.read_text()).get('providers',{}).get('grok')
            actual=fingerprint(executable())
            require(expected and expected.get('executable_sha256')==actual['sha256'] and expected.get('version')
                    and expected.get('scope') not in ('usage-only','quota-only'),
                    'CAPABILITY_MISMATCH','Initial Grok worker/inbox approval requires exact runtime integration evidence')
            actual=dict(actual,version=expected['version'],scope='Worker/inbox compatibility recorded in '+str(source))
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'owned':{}}
        recovered=bool(manifest.get('pending')) or manifest.get('state') in ('INSTALLING','UNINSTALLING')
        for pending in manifest.pop('pending',[]):
            path=Path(pending['path'])
            require(not path.is_symlink(),'OWNERSHIP_CONFLICT','Interrupted Grok target is now a symlink')
            current=owned_value(path,path.read_bytes()) if path.exists() else None
            if current==(None if pending['remove'] else pending['value']):
                if pending['remove']:
                    manifest['owned'].pop(str(path),None);manifest.setdefault('owned_text',{}).pop(str(path),None)
                else:
                    manifest['owned'][str(path)]=pending['value']
                    if pending.get('owned_text') is not None:manifest.setdefault('owned_text',{})[str(path)]=pending['owned_text']
        targets={Path(path):value for path,value in manifest['owned'].items()} if remove else {**desired(home),home/'sandbox.toml':desired_profiles(journal.root)}
        operations=[]
        for path,value in targets.items():
            require(not path.is_symlink(),'OWNERSHIP_CONFLICT','Grok installation target is a symlink')
            expected=file_hash(path);raw=path.read_bytes() if path.exists() else b''
            current=owned_value(path,raw);previous=manifest['owned'].get(str(path))
            require(current in (None,value,previous),'OWNERSHIP_CONFLICT','Owned Grok hooks or sandbox profiles have local edits; preserve and reconcile them before updating')
            if current==(None if remove else value):continue
            owned_text=None
            if path.name=='sandbox.toml':
                # tomlkit validates semantics and renders only the owned tables.
                # Removing a whole TOML table through its AST also removes later
                # comments attached to it, so retain an exact owned byte span.
                text=raw.decode();prior_text=manifest.get('owned_text',{}).get(str(path))
                if current is not None:
                    require(prior_text is not None and text.count(prior_text)==1,'OWNERSHIP_CONFLICT','Owned Grok sandbox profile formatting changed; preserve it before updating')
                    text=text.replace(prior_text,'',1)
                if not remove:
                    owned_text=('\n' if text and not text.endswith('\n\n') else '')+tomlkit.dumps({'profiles':value})
                    text+=owned_text
                after=text.encode()
            else:after=None if remove else (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
            operations.append((path,expected,after,value,current,owned_text))
        plan=dict(operations=[dict(path=str(path),operation='remove-owned' if remove else 'install-owned') for path,*_ in operations],managed_binary_change=False,global_permissions_changed=False)
        if dry_run:return plan
        if operations:
            backup=private_directory(ROOT/'backups'/('grok-install-'+str(time.time_ns())))
            atomic_json(backup/'owned-blocks.json',dict(files=[dict(path=str(path),before_sha256=expected,before_owned=current) for path,expected,after,value,current,owned_text in operations]))
            manifest.update(state='UNINSTALLING' if remove else 'INSTALLING',backup=str(backup),pending=[dict(path=str(path),value=value,remove=remove,owned_text=owned_text) for path,expected,after,value,current,owned_text in operations])
            atomic_json(manifest_path,manifest)
            for path,expected,after,value,current,owned_text in operations:
                if after is None:
                    require(file_hash(path)==expected and not path.is_symlink(),'OWNERSHIP_CONFLICT','Grok hooks changed during uninstall')
                    path.unlink()
                else:compare_write(path,after,expected)
                if remove:
                    manifest['owned'].pop(str(path),None);manifest.setdefault('owned_text',{}).pop(str(path),None)
                else:
                    manifest['owned'][str(path)]=value
                    if owned_text is not None:manifest.setdefault('owned_text',{})[str(path)]=owned_text
                manifest['pending'].pop(0);atomic_json(manifest_path,manifest)
        if operations or recovered:
            manifest.pop('pending',None);manifest.update(state='UNINSTALLED' if remove else 'INSTALLED');atomic_json(manifest_path,manifest)
        compatibility_updated=False
        if actual is not None:
            from .compatibility import update_verified
            require(fingerprint(executable())=={k:actual[k] for k in ('path','sha256','size')},'CAPABILITY_MISMATCH','Grok executable changed during installation')
            compatibility_updated=bool(update_verified(journal,{'grok':actual},initialize=True))
        return dict(plan,installed=not remove,changed=bool(operations) or recovered or compatibility_updated,
                    compatibility_updated=compatibility_updated,manifest=str(manifest_path))
