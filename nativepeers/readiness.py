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
            checks.append(_check('hook',path,state,event=event,**({'guard':'passive-configuration-only','live_verified':False} if event=='UserPromptSubmit' else {})))
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
    homes={provider:provider_home(provider) for provider in ('claude','codex','antigravity','grok')}
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
    from .grok_install import hook_readiness as grok_hook_readiness,profile_readiness as grok_profile_readiness
    checks['grok'].extend(grok_hook_readiness(home=homes['grok'])+grok_profile_readiness(journal,home=homes['grok']))
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


def operation_readiness(journal):
    """One passive view; a quota receipt never approves worker or inbox execution."""
    from .compatibility import diagnose
    from .usage.collectors import executable
    from .usage.contracts import verified
    from .errors import PeerError
    from .launch import home_environment
    integrations=readiness(journal);runtime=diagnose(journal);results=[]
    for item in runtime:
        provider=item['provider'];home=provider_home(provider)
        config_override=home_environment(provider,home)
        quota={'state':'UNKNOWN','operation':'quota-read'}
        try:
            if provider=='grok':
                quota.update(reason='Grok exposes owned-session usage; no documented native account quota source is available')
            elif provider=='codex':
                from .compatibility import verify
                verified_runtime=verify(journal,provider,executable(provider))
                quota.update(state='APPROVED',runtime=verified_runtime,contract='native-account-quota-rpc')
            else:
                receipt=verified(journal,provider,executable(provider))
                quota.update(state='APPROVED',runtime=receipt['runtime'],contract=receipt['route'])
        except (PeerError,OSError,ValueError,KeyError) as error:
            quota.update(state=error.code if isinstance(error,PeerError) else 'UNKNOWN',reason=str(error)[:256])
            if isinstance(error,PeerError) and isinstance(error.details,dict):
                quota.update({key:error.details[key] for key in ('compatibility_state','reason','next_action','operation') if key in error.details})
        broad={'state':'APPROVED' if item['state']=='PASS' else item['state'],
               'runtime':item['current'],'approved_runtime':item['expected'],'scope':item['verification_scope']}
        broad.update({key:item[key] for key in ('compatibility_state','reason','next_action','native_acceptance') if key in item})
        checks=integrations['providers'].get(provider,{}).get('checks',[{'state':'UNKNOWN'}])
        results.append(dict(provider=provider,selected_home=str(home),
            configuration_layout='implicit ~/.claude.json' if provider=='claude' and not config_override else 'explicit native provider home',
            operations={'quota':quota,'worker_turns':dict(broad),'inbox':dict(broad),
                        'mcp':{'state':'UNKNOWN','reason':'A bound native caller and effective native tool inventory are required; static definitions alone are insufficient.'}},
            integration_states=sorted({c['state'] for c in checks}),native_acceptance='Only the source/runtime and operation named by each acceptance record are covered.'))
    return results
