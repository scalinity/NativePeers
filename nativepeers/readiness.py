"""Passive installation checks. No provider startup, writes, repair or inference."""
import json
import os
from pathlib import Path
import tomllib

from . import install as installation
from .antigravity_install import desired, profile_readiness, hook_readiness, mcp_readiness
from .launch import provider_home


def _check(kind,path,state,**fields):
    return dict(kind=kind,path=str(path),state=state,**fields)


def _file(path,expected,kind):
    try:
        state='MISSING' if not path.exists() else 'PRESENT' if path.read_bytes()==expected else 'MODIFIED'
    except OSError:state='UNKNOWN'
    return _check(kind,path,state)


def _hooks(installer,provider,path):
    try:
        data=json.loads(path.read_text()) if path.exists() else {}
        hooks=data.get('hooks',{});checks=[]
        for event,expected in installer.desired_hooks(provider).items():
            groups=hooks.get(event,[])
            candidates=[(group,handler) for group in groups for handler in group.get('hooks',[])
                        if installer.owned(handler,provider) or 'npeer-hook' in str(handler.get('command',''))]
            exact=(len(candidates)==1 and candidates[0][0].get('matcher')==expected.get('matcher')
                   and candidates[0][1]==expected['hooks'][0])
            state='PRESENT' if exact else 'MODIFIED' if candidates else 'MISSING'
            if data.get('disableAllHooks') is True and provider=='claude':state='DISABLED'
            checks.append(_check('hook',path,state,event=event))
        return checks
    except (OSError,ValueError,TypeError,AttributeError):
        return [_check('hooks',path,'UNKNOWN',reason='Hook configuration could not be read')]


def readiness(journal):
    """Return JSON-safe selected homes and owned-file checks, without side effects.

    PRESENT means the selected file matches the installer definition. Native
    effective precedence and tool availability remain unknown without a native
    runtime inventory. The journal is used only for its root path.
    """
    installer=installation.Installer(journal,native=False)
    homes={provider:provider_home(provider) for provider in ('claude','codex','antigravity')}
    checks={provider:[] for provider in homes}
    for provider in ('claude','codex'):
        path=homes[provider]/('settings.json' if provider=='claude' else 'hooks.json')
        checks[provider].extend(_hooks(installer,provider,path))
        skill=homes[provider]/'skills/nativepeers/SKILL.md' if provider=='claude' else installer.skills[provider]
        source=installation.ROOT/'integrations'/provider/'skills/nativepeers/SKILL.md'
        try:item=_file(skill,source.read_bytes(),'skill')
        except OSError:item=_check('skill',skill,'UNKNOWN',reason='Installer skill source is unavailable')
        checks[provider].append(item)
        hook=installation.ROOT/'bin/npeer-hook'
        checks[provider].append(_check('hook_entrypoint',hook,
            'MISSING' if not hook.is_file() else 'PRESENT' if os.access(hook,os.X_OK) else 'DISABLED'))
    config=homes['codex']/'config.toml'
    try:
        manifest=json.loads(installer.manifest.read_text()) if installer.manifest.exists() else {}
        trusted=manifest.get('trusted_hooks',{})
        states=tomllib.loads(config.read_text()).get('hooks',{}).get('state',{}) if config.exists() else {}
        state=('MISSING' if len(trusted)!=len(installer.desired_hooks('codex')) else
               'DISABLED' if any(states.get(key,{}).get('enabled') is False for key in trusted) else
               'PRESENT' if all(states.get(key,{}).get('trusted_hash')==value for key,value in trusted.items()) else 'MODIFIED')
        checks['codex'].append(_check('hook_trust',config,state))
    except (OSError,ValueError,TypeError,AttributeError):
        checks['codex'].append(_check('hook_trust',config,'UNKNOWN'))
    try:definitions=desired(installer.home)
    except OSError:
        definitions={}
        checks['antigravity'].append(_check('configuration',installer.home/'.gemini/config','UNKNOWN',
                                           reason='Installer definitions are unavailable'))
    for path,(kind,key,expected) in definitions.items():
        if kind=='file' and path.name=='SKILL.md':checks['antigravity'].append(_file(path,expected.encode(),'skill'))
    if definitions:
        spec={'cwd':str(Path.cwd())}
        checks['antigravity'].extend(profile_readiness(spec,home=installer.home)+hook_readiness(spec,home=installer.home)+mcp_readiness(spec,home=installer.home))
    hook=installation.ROOT/'bin/npeer-antigravity-hook'
    checks['antigravity'].append(_check('hook_entrypoint',hook,
        'MISSING' if not hook.is_file() else 'PRESENT' if os.access(hook,os.X_OK) else 'DISABLED'))
    command=installer.command
    try:
        command_state=('PRESENT' if command.is_symlink() and command.resolve()==(installation.ROOT/'bin/npeer').resolve()
                       and command.is_file() else 'MODIFIED' if command.exists() or command.is_symlink() else 'MISSING')
        if command_state=='PRESENT' and not os.access(command,os.X_OK):command_state='DISABLED'
    except OSError:command_state='UNKNOWN'
    return dict(passive=True,selected_homes={provider:str(home) for provider,home in homes.items()},
                native_effective_precedence='unknown',
                entrypoint=_check('entrypoint',command,command_state),
                providers={provider:dict(checks=items,native_effective_precedence='unknown',
                    configuration_home=str(installer.home/'.gemini/config' if provider=='antigravity' else homes[provider]))
                    for provider,items in checks.items()})
