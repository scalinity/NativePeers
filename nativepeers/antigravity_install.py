"""Owned Antigravity configuration blocks and native worker tool profiles."""
import json
from pathlib import Path
import time

from .compatibility import fingerprint
from .errors import require
from .files import advisory_lock, atomic_json, private_directory
from .install import compare_write, file_hash
from .profiles import ROOT, PYTHON

READ_TOOLS=['view_file','list_dir','find_by_name','grep_search']
WRITE_TOOLS=['write_to_file','replace_file_content','multi_replace_file_content','run_command']


def allowed_tools(profile,name):
    from .mcp_server import TOOLS
    return name in READ_TOOLS+(WRITE_TOOLS if profile=='workspace' else []) or name in {prefix+tool for prefix in ('mcp_nativepeers_','mcp__nativepeers__') for tool in TOOLS}


def worker_profile(profile):
    tools=READ_TOOLS+(WRITE_TOOLS if profile=='workspace' else [])
    return ('---\nname: nativepeers-'+profile+'\ndescription: Scoped independent NativePeers '+profile+' worker.\n'
            'mainAgent: true\nsubagent: false\ncommandExecutionPolicy: '+('sandbox' if profile=='workspace' else 'off')+'\n'
            'tools:\n'+''.join('  - '+name+'\n' for name in tools)+'skills: []\nplugins: []\n---\n'
            'Complete the focused NativePeers task. Preserve its assigned role and return its actual findings.\n')


def verify_tool_gate(spec):
    home=Path.home();path=home/'.gemini/config/hooks.json'
    expected=desired(home)[path][2]
    require(path.is_file() and json.loads(path.read_text()).get('nativepeers')==expected,
            'PERMISSION_CAPABILITY_UNKNOWN','The required Antigravity NativePeers tool gate is missing or modified')
    project=Path(spec['cwd'])/'.agents/hooks.json'
    if project.exists():
        override=json.loads(project.read_text()).get('nativepeers')
        require(override is None or override==expected,'PERMISSION_CAPABILITY_UNKNOWN','Workspace overrides the owned Antigravity tool gate')
    settings=home/'.gemini/antigravity-cli/settings.json'
    if settings.exists():
        override=json.loads(settings.read_text()).get('hooks',{}).get('nativepeers')
        require(override is None or override==expected,'PERMISSION_CAPABILITY_UNKNOWN','Native settings override the owned Antigravity tool gate')


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
            for path,expected,after,kind,value,current in operations:
                if after is None:
                    require(file_hash(path)==expected and not path.is_symlink(),'OWNERSHIP_CONFLICT','Antigravity file changed during uninstall')
                    path.unlink()
                else:compare_write(path,after,expected)
                if remove:manifest['owned'].pop(str(path),None)
                else:manifest['owned'][str(path)]=value
            manifest.update(state='UNINSTALLED' if remove else 'INSTALLED',backup=str(backup))
            atomic_json(manifest_path,manifest)
        compatibility_updated=False
        if not remove:
            receipt_path=journal.root/'compat/runtime.json'
            receipt=json.loads(receipt_path.read_text()) if receipt_path.exists() else {'schema_version':1,'providers':{}}
            if actual is not None and receipt['providers'].get('antigravity')!=actual:
                receipt['providers']['antigravity']=actual;atomic_json(receipt_path,receipt);compatibility_updated=True
        return dict(plan,installed=not remove,changed=bool(operations) or compatibility_updated,
                    compatibility_updated=compatibility_updated,manifest=str(manifest_path))
