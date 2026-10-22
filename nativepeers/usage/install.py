"""Compare-and-apply status-line composition with recoverable owned definitions."""
import json
import hashlib
from pathlib import Path
import shlex

from .collectors import verify_usage as verify
from ..errors import require
from ..files import advisory_lock,atomic_json
from ..install import compare_write,file_hash
from ..launch import provider_home
from ..profiles import ROOT
from .collectors import executable
from .statusline import signature,entry_key


def install(journal,*,providers=('claude','antigravity'),remove=False,dry_run=False,homes=None):
    manifest_path=journal.root/'usage-install.json'
    with advisory_lock(journal.root/'locks/install.lock'):
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'schema_version':1,'providers':{}}
        operations=[];preserved=[]
        targets=[(p,Path(e['home'])) for e in manifest['providers'].values() for p in [e['provider']] if p in providers] if remove else [(p,(homes or {}).get(p,provider_home(p))) for p in providers]
        for provider,home in targets:
            path=Path(home)/'settings.json';key=entry_key(provider,home)
            expected=file_hash(path)
            raw=path.read_bytes() if path.exists() else b'{}';data=json.loads(raw)
            require(expected is None or hashlib.sha256(raw).hexdigest()==expected,'OWNERSHIP_CONFLICT','Status-line settings changed before planning')
            current=data.get('statusLine');owned=manifest['providers'].get(key)
            if owned and owned.get('removing') and current==owned['previous'] and ('statusLine' in data)==owned.get('previous_present',owned['previous'] is not None):
                if not dry_run:
                    manifest['providers'].pop(key,None);atomic_json(manifest_path,manifest)
                continue
            # A pending write is already durable before compare_write. Reconcile
            # against either side instead of guessing after an interrupted install.
            if owned and owned.get('pending') and current==owned['installed']:
                owned.pop('pending',None)
                if not dry_run:atomic_json(manifest_path,manifest)
            if remove:
                if not owned:continue
                if current!=owned['installed']:
                    preserved.append(dict(provider=provider,state='MODIFIED',path=str(path)));continue
                if not owned.get('previous_present',owned['previous'] is not None):data.pop('statusLine',None)
                else:data['statusLine']=owned['previous']
                after=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode()
                operations.append(dict(provider=provider,path=str(path),operation='restore-owned-statusline'))
                if not dry_run:
                    owned['removing']=True;atomic_json(manifest_path,manifest)
                    compare_write(path,after,expected);manifest['providers'].pop(key,None);atomic_json(manifest_path,manifest)
                continue
            if current is not None and (not isinstance(current,dict) or current.get('type')!='command' or current.get('enabled') is False):
                preserved.append(dict(provider=provider,state='DISABLED_OR_UNSUPPORTED',path=str(path)));continue
            if owned:
                require(current in (owned['installed'],owned.get('previous')),'OWNERSHIP_CONFLICT','Owned status-line definition was edited; preserve it before changing collection')
                previous=owned['previous']
            else:previous=current
            before_runtime=signature(executable(provider))
            runtime=verify(journal,provider,Path(before_runtime['path']))
            require(signature(executable(provider))==before_runtime and runtime.get('path',before_runtime['path'])==before_runtime['path'],
                'CAPABILITY_MISMATCH','Native executable changed while installing usage collection')
            # Absolute module entrypoint works regardless of native cwd/PYTHONPATH.
            command=shlex.join([str(ROOT/'bin/npeer-usage-statusline'),'--provider',provider,'--home',str(home),'--state',str(journal.root)])
            installed=dict(previous or {},type='command',command=command)
            if provider=='antigravity' and previous is None:installed['stack_with_default']=True
            entry=dict(provider=provider,previous=previous,previous_present=owned.get('previous_present',previous is not None) if owned else 'statusLine' in data,
                installed=installed,runtime_sha256=runtime['sha256'],runtime_stat=before_runtime,home=str(home))
            if current==installed and owned and all(owned.get(k)==v for k,v in entry.items()):continue
            operations.append(dict(provider=provider,path=str(path),operation='compose-native-quota-capture'))
            if not dry_run:
                manifest['providers'][key]=dict(entry,pending=True);atomic_json(manifest_path,manifest)
                data['statusLine']=installed
                compare_write(path,(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode(),expected)
                manifest['providers'][key]=entry;atomic_json(manifest_path,manifest)
        return dict(operations=operations,preserved=preserved,changed=bool(operations),inference_submitted=False)


def readiness(journal):
    """Passive owned-definition checks; never run or repair a native process."""
    path=journal.root/'usage-install.json'
    manifest=json.loads(path.read_text()) if path.exists() else {'providers':{}}
    result=[]
    for provider in ('claude','antigravity'):
        home=provider_home(provider);entry=manifest['providers'].get(entry_key(provider,home));settings=home/'settings.json'
        try:
            data=json.loads(settings.read_text()) if settings.exists() else {}
            current=data.get('statusLine')
            state='MISSING' if not entry else 'PRESENT' if current==entry['installed'] else 'MODIFIED'
            if isinstance(current,dict) and current.get('enabled') is False:state='DISABLED'
            if entry and signature(entry['runtime_stat']['path'])!=entry['runtime_stat']:state='CAPABILITY_MISMATCH'
        except (OSError,ValueError,KeyError,TypeError):state='UNKNOWN'
        result.append(dict(provider=provider,home=str(home),state=state,source='native-statusline',
            limitation='Presence is configuration evidence; actual quota fields depend on normal native emission.'))
    return result
