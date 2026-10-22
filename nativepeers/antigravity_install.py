"""Owned Antigravity configuration blocks and native worker tool profiles."""
import json
from pathlib import Path
import time

from .compatibility import fingerprint, verify
from .errors import require
from .files import advisory_lock, atomic_json, private_directory
from .install import compare_write, file_hash
from .profiles import ROOT, PYTHON

READ_TOOLS=['view_file','list_dir','find_by_name','grep_search']
WRITE_TOOLS=['write_to_file','replace_file_content','multi_replace_file_content','run_command']


def allowed_tools(profile,name,args=None):
    from .mcp_server import TOOLS
    if name=='call_mcp_tool':
        return isinstance(args,dict) and args.get('ServerName')=='nativepeers' and args.get('ToolName') in TOOLS
    return name in READ_TOOLS+(WRITE_TOOLS if profile=='workspace' else []) or name in {prefix+tool for prefix in ('mcp_nativepeers_','mcp__nativepeers__') for tool in TOOLS}


def worker_profile(profile):
    tools=READ_TOOLS+(WRITE_TOOLS if profile=='workspace' else [])
    return ('---\nname: nativepeers-'+profile+'\ndescription: Scoped independent NativePeers '+profile+' worker.\n'
            'mainAgent: true\nsubagent: false\ncommandExecutionPolicy: '+('sandbox' if profile=='workspace' else 'off')+'\n'
            'tools:\n'+''.join('  - '+name+'\n' for name in tools)+'skills: []\nplugins: []\n---\n'
            'Complete the focused NativePeers task. Preserve its assigned role and return its actual findings.\n')


def verify_tool_gate(spec):
    checks=profile_readiness(spec)+hook_readiness(spec)+mcp_readiness(spec)
    require(all(check['state']=='PRESENT' for check in checks),'PERMISSION_CAPABILITY_UNKNOWN',
            'The Antigravity NativePeers profile, tool gate or MCP facade is missing, disabled, modified or unresolved',details=checks)


def verify_worker_ready(journal,spec,executable):
    """Read-only admission check; never bootstrap, repair or launch a provider."""
    verify(journal,'antigravity',executable)
    verify_tool_gate(spec)


def workspace_directories(spec):
    """Inspect the launch directory through the nearest Git root, inclusively."""
    workspace=Path(spec['cwd']).resolve();directories=[workspace]
    for parent in workspace.parents:
        if (directories[-1]/'.git').exists():break
        directories.append(parent)
    return directories


def profile_readiness(spec,*,home=None):
    """Compare same-name profiles without guessing which native source wins.

    Both documented layouts: https://antigravity.google/docs/subagents/
    """
    home=home or Path.home();checks=[]
    profiles=[spec['permission_profile']] if 'permission_profile' in spec else ['analysis','workspace']
    for profile in profiles:
        name='nativepeers-'+profile;expected=worker_profile(profile)
        path=home/'.gemini/config/agents'/(name+'.md')
        targets=[path,home/'.gemini/config/agents'/name/'agent.md']
        targets.extend(target for directory in workspace_directories(spec) for target in
                       (directory/'.agents/agents'/(name+'.md'),directory/'.agents/agents'/name/'agent.md'))
        for target in targets:
            if target!=path and not (target.exists() or target.is_symlink()):continue
            try:state='MISSING' if not target.exists() else 'PRESENT' if target.read_text()==expected else 'MODIFIED'
            except (OSError,UnicodeError):state='UNKNOWN'
            checks.append(dict(kind='profile',path=str(target),state=state,
                reason='Owned worker profile matches' if state=='PRESENT' else 'Owned worker profile is absent, differs or could not be read',
                native_effective_precedence='unknown'))
    return checks


def hook_readiness(spec,*,home=None):
    """Compare the owned hook block and the native override locations we check."""
    home=home or Path.home();path=home/'.gemini/config/hooks.json'
    expected=desired(home)[path][2];checks=[]
    for target in (path,*(directory/'.agents/hooks.json' for directory in workspace_directories(spec)),home/'.gemini/antigravity-cli/settings.json'):
        optional=target!=path
        if optional and not target.exists():continue
        try:
            data=json.loads(target.read_text()) if target.exists() else {}
            block=data.get('hooks',{}) if target.name=='settings.json' else data
            current=block.get('nativepeers')
            if optional and 'nativepeers' not in block:continue
            state=('MISSING' if 'nativepeers' not in block else 'PRESENT' if current==expected else
                   'DISABLED' if isinstance(current,dict) and current.get('enabled') is False else 'MODIFIED')
        except (OSError,ValueError,TypeError,AttributeError):state='UNKNOWN'
        checks.append(dict(kind='hooks',path=str(target),state=state,
            reason='Owned hook definition matches' if state=='PRESENT' else 'Owned hook definition is absent, disabled, differs or could not be read',
            native_effective_precedence='unknown'))
    return checks


def mcp_readiness(spec,*,home=None):
    """Read owned MCP definitions; this does not resolve native runtime precedence."""
    home=home or Path.home();path=home/'.gemini/config/mcp_config.json'
    expected=desired(home)[path][2];checks=[]
    # The installed CLI documents workspace customizations, but `agy mcp list`
    # lists only global definitions. A conflicting workspace entry cannot prove
    # the owned facade is available; do not guess which definition wins.
    for target in [path,*(directory/'.agents/mcp_config.json' for directory in workspace_directories(spec))]:
        optional=target!=path
        if optional and not target.exists():continue
        state='MISSING';reason='Owned nativepeers MCP definition is absent'
        try:
            data=json.loads(target.read_text()) if target.exists() else {}
            servers=data.get('mcpServers',{})
            if not isinstance(servers,dict):raise TypeError('MCP servers must be an object')
            current=servers.get('nativepeers')
            if optional and 'nativepeers' not in servers:continue
            if 'nativepeers' in servers and not isinstance(current,dict):
                state='UNKNOWN';reason='Owned MCP definition is not a server object'
            elif current is not None:
                current=current.copy()
                disabled=current.pop('disabled',False)
                state=('DISABLED' if disabled is True else 'UNKNOWN' if disabled is not False
                       else 'PRESENT' if current==expected else 'MODIFIED')
                reason=('Owned MCP definition matches' if state=='PRESENT' else
                        'Native MCP configuration disables nativepeers' if state=='DISABLED' else
                        'Owned MCP definition differs or restricts its tools')
        except (OSError,ValueError,TypeError,AttributeError):
            state='UNKNOWN';reason='MCP configuration could not be read as a server map'
        checks.append(dict(kind='mcp_facade',path=str(target),state=state,reason=reason,
                           native_effective_precedence='unknown'))
    return checks


def desired(home):
    hook=lambda kind:{'type':'command','command':str(ROOT/'bin/npeer-antigravity-hook')+' '+kind,'timeout':3}
    return {
        home/'.gemini/config/hooks.json':('json','nativepeers',{
            'SessionStart':[hook('SessionStart')], 'PreInvocation':[hook('PreInvocation')],
            'PreToolUse':[{'matcher':'.*','hooks':[hook('PreToolUse')]}]}),
        home/'.gemini/config/mcp_config.json':('mcp','nativepeers',{'command':str(PYTHON),'args':['-m','nativepeers.mcp_server','--native-caller'],
            'env':{'PYTHONPATH':str(ROOT)}}),
        home/'.gemini/config/agents/nativepeers-analysis.md':('file',None,worker_profile('analysis')),
        home/'.gemini/config/agents/nativepeers-workspace.md':('file',None,worker_profile('workspace')),
        home/'.gemini/config/skills/nativepeers/SKILL.md':('file',None,(ROOT/'integrations/claude/skills/nativepeers/SKILL.md').read_text()),
    }


def install(journal,*,home=None,dry_run=False,remove=False,verified_source=None):
    home=home or Path.home();manifest_path=journal.root/'antigravity-install.json'
    with advisory_lock(journal.root/'locks/install.lock'):
        actual=None
        if not remove:
            binary=home/'.local/bin/agy'
            require(binary.is_file(),'AUTH_REQUIRED','Install the official Antigravity CLI first')
            source=verified_source or ROOT/'evidence/capabilities.json'
            expected=json.loads(source.read_text())['providers'].get('antigravity')
            actual=fingerprint(binary)
            require(expected and expected.get('executable_sha256')==actual['sha256'],
                    'CAPABILITY_MISMATCH','Review the current Antigravity executable and record its scoped verification before installation')
            actual=dict(actual,version=expected['version'],scope='Scoped compatibility recorded in '+str(source))
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'owned':{}}
        recovered=bool(manifest.get('pending')) or manifest.get('state') in ('INSTALLING','UNINSTALLING')
        for pending in manifest.pop('pending',[]):
            path=Path(pending['path'])
            require(not path.is_symlink(),'OWNERSHIP_CONFLICT','Interrupted installation target is now a symlink')
            raw=path.read_bytes() if path.exists() else b''
            if pending['kind']=='file':current=raw.decode() if path.exists() else None
            else:
                data=json.loads(raw) if raw.strip() else {}
                current=(data.get('mcpServers',{}) if pending['kind']=='mcp' else data).get(pending['key'])
            if current==(None if pending['remove'] else pending['value']):
                if pending['remove']:manifest['owned'].pop(str(path),None)
                else:manifest['owned'][str(path)]=pending['value']
        operations=[]
        for path,(kind,key,value) in desired(home).items():
            if remove and str(path) not in manifest['owned']:continue
            expected=file_hash(path);previous=manifest['owned'].get(str(path))
            raw=path.read_bytes() if path.exists() else b''
            if kind=='file':
                current=raw.decode() if path.exists() else None
                require(current in (None,value,previous),'OWNERSHIP_CONFLICT','An installed Antigravity skill/profile has local edits; preserve it before updating')
                if remove:
                    if current is None:continue
                    after=None
                else:after=value.encode()
            else:
                data=json.loads(raw) if raw.strip() else {};block=data.setdefault('mcpServers',{}) if kind=='mcp' else data
                current=block.get(key)
                require(current in (None,value,previous),'OWNERSHIP_CONFLICT','An Antigravity NativePeers block has local edits')
                if remove:block.pop(key,None)
                else:block[key]=value
                after=(json.dumps(data,indent=2,ensure_ascii=False)+'\n').encode()
                if current==(None if remove else value):continue
            if after!=raw:operations.append((path,expected,after,kind,value,current))
        plan={'operations':[{'path':str(p),'operation':'remove-owned' if remove else 'install-owned'} for p,*_ in operations],
              'managed_binary_change':False,'global_permissions_changed':False}
        if dry_run:return plan
        if operations:
            backup=private_directory(ROOT/'backups'/('antigravity-install-'+str(time.time_ns())))
            atomic_json(backup/'owned-blocks.json',{'files':[{'path':str(p),'kind':kind,'before_sha256':expected,'before_owned':current} for p,expected,after,kind,value,current in operations]})
            manifest.update(state='UNINSTALLING' if remove else 'INSTALLING',backup=str(backup),pending=[
                {'path':str(p),'kind':kind,'key':'nativepeers','value':value,'remove':remove}
                for p,expected,after,kind,value,current in operations])
            atomic_json(manifest_path,manifest)
            for path,expected,after,kind,value,current in operations:
                if after is None:
                    require(file_hash(path)==expected and not path.is_symlink(),'OWNERSHIP_CONFLICT','Antigravity file changed during uninstall')
                    path.unlink()
                else:compare_write(path,after,expected)
                if remove:manifest['owned'].pop(str(path),None)
                else:manifest['owned'][str(path)]=value
                manifest['pending'].pop(0)
                atomic_json(manifest_path,manifest)
        if operations or recovered:
            manifest.pop('pending',None)
            manifest.update(state='UNINSTALLED' if remove else 'INSTALLED')
            atomic_json(manifest_path,manifest)
        compatibility_updated=False
        if not remove:
            receipt_path=journal.root/'compat/runtime.json'
            receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {'schema_version':1,'providers':{}}
            if actual is not None and receipt['providers'].get('antigravity')!=actual:
                receipt['providers']['antigravity']=actual;atomic_json(receipt_path,receipt);compatibility_updated=True
        return dict(plan,installed=not remove,changed=bool(operations) or recovered or compatibility_updated,
                    compatibility_updated=compatibility_updated,manifest=str(manifest_path))
