"""Merge-only namespaced installation with owned-block rollback snapshots."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import tomllib

from .adapters.codex_app_server import AppServer
from .compatibility import record_verified
from .errors import require
from .files import advisory_lock,atomic_json,private_directory
from .profiles import ROOT


def file_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def compare_write(path,raw,expected):
    require(not path.is_symlink(),'OWNERSHIP_CONFLICT','Configuration path is a symlink')
    require(file_hash(path)==expected,'OWNERSHIP_CONFLICT','Configuration changed during installation; no stale write applied')
    path.parent.mkdir(parents=True,exist_ok=True)
    mode=path.stat().st_mode&0o777 if path.exists() else 0o600
    fd,name=tempfile.mkstemp(prefix='.nativepeers-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode)
        require(file_hash(path)==expected,'OWNERSHIP_CONFLICT','Concurrent configuration write detected')
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


class Installer:
    def __init__(self,journal,*,home=None,native=True):
        self.journal=journal;self.home=home or Path.home();self.native=native
        self.manifest=journal.root/'install-manifest.json'
        self.targets={'claude':self.home/'.claude/settings.json','codex':self.home/'.codex/hooks.json'}
        self.config=self.home/'.codex/config.toml';self.command=self.home/'.local/bin/npeer'
        self.skills={'claude':self.home/'.claude/skills/nativepeers/SKILL.md','codex':self.home/'.agents/skills/nativepeers/SKILL.md'}

    def command_for(self,provider):return str(ROOT/'bin/npeer-hook')+' --provider '+provider

    def owned(self,handler,provider):return handler.get('type')=='command' and handler.get('command')==self.command_for(provider)

    def merged(self,path,provider,remove=False):
        original=json.loads(path.read_text()) if path.exists() else {}
        result=json.loads(json.dumps(original));hooks=result.setdefault('hooks',{})
        for event in ['SessionStart','SessionEnd']:
            groups=hooks.setdefault(event,[])
            if remove:
                cleaned=[]
                for group in groups:
                    handlers=group.get('hooks',[]);remaining=[h for h in handlers if not self.owned(h,provider)]
                    if remaining:cleaned.append(dict(group,hooks=remaining))
                    elif not handlers:cleaned.append(group)
                if cleaned:hooks[event]=cleaned
                else:hooks.pop(event,None)
            elif not any(self.owned(h,provider) for g in groups for h in g.get('hooks',[])):
                groups.append({'hooks':[{'type':'command','command':self.command_for(provider),'timeout':3}]})
        if not hooks:result.pop('hooks',None)
        return original,result

    def plan(self,remove=False):
        operations=[]
        for provider,path in self.targets.items():
            expected=file_hash(path)
            before,after=self.merged(path,provider,remove)
            if before!=after:operations.append({'path':str(path),'operation':'remove-owned-hooks' if remove else 'append-owned-hooks','before_sha256':expected})
        for provider,path in self.skills.items():
            source=ROOT/'integrations'/provider/'skills/nativepeers/SKILL.md'
            if remove:
                if path.exists():operations.append({'path':str(path),'operation':'remove-owned-skill'})
            elif not path.exists() or path.read_bytes()!=source.read_bytes():operations.append({'path':str(path),'operation':'install-owned-skill'})
        collision=self.command.exists() or self.command.is_symlink()
        ours=self.command.is_symlink() and self.command.resolve()==(ROOT/'bin/npeer').resolve()
        if remove and ours:operations.append({'path':str(self.command),'operation':'remove-owned-entrypoint'})
        if not remove and not collision:operations.append({'path':str(self.command),'operation':'install-entrypoint'})
        if self.native:
            manifest=json.loads(self.manifest.read_text()) if self.manifest.exists() else {}
            states=tomllib.loads(self.config.read_text()).get('hooks',{}).get('state',{}) if self.config.exists() else {}
            trust=manifest.get('trusted_hooks',{})
            if (remove and trust) or (not remove and (len(trust)!=2 or any(states.get(k,{}).get('trusted_hash')!=v for k,v in trust.items()))):
                operations.append({'path':str(self.config),'operation':'remove-owned-hook-trust' if remove else 'trust-owned-hooks-with-native-version-check','before_sha256':file_hash(self.config)})
        return {'operations':operations,'command_collision':collision and not ours,'entrypoint':'python -m nativepeers' if collision and not ours else 'npeer','managed_binary_change':False}

    @contextlib.contextmanager
    def lock(self):
        with advisory_lock(self.journal.root/'locks/install.lock'):
            if not self.native:yield;return
            manager=Path.home()/'Documents/Tools/CodexMCP/manager.py'
            spec=importlib.util.spec_from_file_location('nativepeers_existing_manager',manager);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            with module.update_lock():yield

    async def trust(self,remove=False,manifest=None):
        if not self.native:return {}
        client=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex')
        try:
            await client.start();read=await client.call('config/read',{'includeLayers':True})
            layer=next(l for l in read['layers'] if l['name'].get('type')=='user' and Path(l['name']['file']).resolve()==self.config.resolve() and not l['name'].get('profile'))
            raw=tomllib.loads(self.config.read_text());state=raw.get('hooks',{}).get('state',{})
            updated=json.loads(json.dumps(state));owned={}
            if remove:
                for key,expected in manifest.get('trusted_hooks',{}).items():
                    if updated.get(key,{}).get('trusted_hash')==expected:
                        updated[key].pop('trusted_hash')
                        if not updated[key]:updated.pop(key)
            else:
                listing=await client.call('hooks/list',{'cwd':str(ROOT)})
                for hook in listing['data'][0]['hooks']:
                    if hook['sourcePath']==str(self.targets['codex']) and hook.get('command')==self.command_for('codex'):
                        owned[hook['key']]=hook['currentHash'];updated.setdefault(hook['key'],{})['trusted_hash']=hook['currentHash']
                require(len(owned)==2,'CAPABILITY_MISMATCH','Installed native hook inventory did not identify both namespaced lifecycle hooks')
            if updated!=state:
                await client.call('config/batchWrite',{'edits':[{'keyPath':'hooks.state','value':updated,'mergeStrategy':'replace'}],
                    'filePath':str(self.config),'expectedVersion':layer['version'],'reloadUserConfig':False})
            return owned
        finally:await client.close()

    async def install(self,dry_run=False):
        plan=self.plan()
        if dry_run:return plan
        with self.lock():
            plan=self.plan();prior=json.loads(self.manifest.read_text()) if self.manifest.exists() else {}
            current_state=tomllib.loads(self.config.read_text()).get('hooks',{}).get('state',{}) if self.config.exists() else {}
            trusted=all(current_state.get(k,{}).get('trusted_hash')==v for k,v in prior.get('trusted_hooks',{}).items())
            if prior and not plan['operations'] and trusted and prior.get('state')=='INSTALLED' and not prior.get('uninstalled'):
                return dict(plan,installed=True,changed=False,manifest=str(self.manifest))
            skill_inputs={}
            for provider,path in self.skills.items():
                source=ROOT/'integrations'/provider/'skills/nativepeers/SKILL.md'
                expected=file_hash(path);raw=source.read_bytes();desired=hashlib.sha256(raw).hexdigest()
                require(not path.is_symlink() and expected in (None,desired,prior.get('owned_files',{}).get(str(path))),
                        'OWNERSHIP_CONFLICT','Installed skill has local changes; preserve and reconcile them before updating')
                skill_inputs[provider]=(expected,raw,desired)
            backup=private_directory(ROOT/'backups'/('install-'+str(time.time_ns())))
            manifest=dict(schema_version=1,state='INSTALLING',project=str(ROOT),backup=str(backup),trusted_hooks=prior.get('trusted_hooks',{}),owned_files=prior.get('owned_files',{}),changed_keys=[])
            atomic_json(self.manifest,manifest)
            # Only changed hook blocks are recoverable snapshots. Credentials elsewhere in settings/config are never copied.
            for provider,path in self.targets.items():
                expected=file_hash(path)
                before,after=self.merged(path,provider)
                if before!=after:
                    atomic_json(backup/(provider+'-owned-blocks.json'),{'path':str(path),'before_sha256':expected,
                        'before_hooks':before.get('hooks',{}),'changed_events':['SessionStart','SessionEnd']})
                    compare_write(path,(json.dumps(after,indent=2,ensure_ascii=False)+'\n').encode(),expected)
                    manifest['changed_keys'].append(str(path)+':hooks.SessionStart/SessionEnd')
                    atomic_json(self.manifest,manifest)
            for provider,path in self.skills.items():
                expected,raw,desired=skill_inputs[provider]
                require(not path.is_symlink() and file_hash(path)==expected,'OWNERSHIP_CONFLICT','Installed skill changed during installation')
                if expected!=desired:compare_write(path,raw,expected)
                manifest['owned_files'][str(path)]=desired
                atomic_json(self.manifest,manifest)
            for entry in [ROOT/'bin/npeer',ROOT/'bin/npeer-hook']:entry.chmod(0o755)
            if not plan['command_collision'] and not self.command.exists() and not self.command.is_symlink():
                self.command.parent.mkdir(parents=True,exist_ok=True);self.command.symlink_to(ROOT/'bin/npeer')
            if self.native:
                atomic_json(backup/'codex-trust-blocks.json',{'path':str(self.config),'before_sha256':file_hash(self.config),
                    'before_state':tomllib.loads(self.config.read_text()).get('hooks',{}).get('state',{})})
                manifest['trusted_hooks']=await self.trust()
                record_verified(self.journal,ROOT/'evidence/capabilities.json')
            manifest['state']='INSTALLED'
            atomic_json(self.manifest,manifest)
        return dict(plan,installed=True,manifest=str(self.manifest),backup=str(backup))

    async def uninstall(self,dry_run=False):
        plan=self.plan(remove=True)
        if dry_run:return plan
        require(self.manifest.exists(),'OWNERSHIP_UNKNOWN','No owned installation manifest exists')
        manifest=json.loads(self.manifest.read_text());conflicts=[]
        with self.lock():
            for provider,path in self.targets.items():
                expected=file_hash(path)
                before,after=self.merged(path,provider,remove=True)
                if before!=after:compare_write(path,(json.dumps(after,indent=2,ensure_ascii=False)+'\n').encode(),expected)
            if self.native:await self.trust(remove=True,manifest=manifest)
            for provider,path in self.skills.items():
                if not path.exists():continue
                if file_hash(path)==manifest['owned_files'].get(str(path)):
                    path.unlink()
                    try:path.parent.rmdir()
                    except OSError:pass
                else:conflicts.append(str(path))
            if self.command.is_symlink() and self.command.resolve()==(ROOT/'bin/npeer').resolve():self.command.unlink()
            manifest['uninstalled']=True;manifest['conflicts']=conflicts;atomic_json(self.manifest,manifest)
        return dict(plan,uninstalled=not conflicts,preserved_modified_owned_files=conflicts,state_retained=str(self.journal.root))


async def install(journal,dry_run=False):return await Installer(journal).install(dry_run)
async def uninstall(journal,dry_run=False):return await Installer(journal).uninstall(dry_run)
