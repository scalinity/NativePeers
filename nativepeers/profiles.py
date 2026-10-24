"""Worker-local provider tool inventories. No global permissions are edited."""
import json
from pathlib import Path
import tomllib

import tomlkit

from .errors import require
from .launch import provider_home

ROOT=Path(__file__).resolve().parent.parent
PYTHON=ROOT/'.venv/bin/python'


def facade_config(journal,binding):
    return dict(command=str(PYTHON),args=['-m','nativepeers.mcp_server','--binding',str(binding)],
                cwd=str(ROOT),env={'NPEER_HOME':str(journal.root),'PYTHONPATH':str(ROOT)},enabled=True)


def codex_config(journal,binding,profile,*,home=None):
    home=Path(home) if home is not None else provider_home('codex')
    path=home/'config.toml'
    config=tomllib.loads(path.read_text()) if path.exists() else {}
    policies={name:{'enabled':False} for name in config.get('mcp_servers',{})}
    policies.update({'codex_apps':{'enabled':False},'openai-api-key-local-confirmation':{'enabled':False}})
    return {'mcp_servers':{'nativepeers':dict(facade_config(journal,binding),default_tools_approval_mode='approve')},
            # The native shell's core environment omits custom variables. Supply
            # only this worker's binding explicitly; its read-only sandbox stays intact.
            'shell_environment_policy.set':dict(config.get('shell_environment_policy',{}).get('set',{}),
                NPEER_HOME=str(journal.root),NPEER_BOOTSTRAP=str(binding),NPEER_BINDING=''),
            'mcp_server_policy':policies,
            'plugins':{name:{'enabled':False} for name in config.get('plugins',{})},
            'features.multi_agent':False,'features.multi_agent_v2':False,
            'features.js_repl':False,'web_search':'disabled',
            'memories.generate_memories':False,'memories.use_memories':False}


def config_arguments(config):
    def value(item):
        if isinstance(item,dict):return '{'+','.join(json.dumps(k)+'='+value(v) for k,v in item.items())+'}'
        if isinstance(item,list):return '['+','.join(value(v) for v in item)+']'
        return tomlkit.item(item).as_string()
    args=[]
    for key,item in config.items():
        args += ['-c',key+'='+value(item)]
    return args


def claude_arguments(journal,binding,spec,native_id,*,resume=False,home=None):
    settings_path=(Path(home) if home is not None else provider_home('claude'))/'settings.json'
    settings=json.loads(settings_path.read_text()) if settings_path.exists() else {}
    profile=spec.get('permission_profile','analysis')
    require(profile in ('analysis','workspace'),'PERMISSION_CAPABILITY_UNKNOWN','Trusted profile requires an explicit operator grant')
    overrides={'enabledPlugins':{k:False for k in settings.get('enabledPlugins',{})}}
    args=['-p','--output-format','stream-json','--verbose','--model',spec['model'],
          '--permission-mode','dontAsk','--max-turns',str(spec.get('max_task_turns',8)),
          '--strict-mcp-config','--mcp-config',json.dumps({'mcpServers':{'nativepeers':{k:v for k,v in facade_config(journal,binding).items() if k!='enabled'}}})]
    if spec.get('reasoning_effort'):args+=['--effort',spec['reasoning_effort']]
    if profile=='analysis':
        args += ['--tools','Read,Glob,Grep','--allowedTools','Read,Glob,Grep,mcp__nativepeers__*']
    else:
        args += ['--restricted','--tools','Bash,Read,Glob,Grep,Edit,Write','--allowedTools','Bash,Read,Glob,Grep,Edit,Write,mcp__nativepeers__*']
        overrides['sandbox']={'enabled':True,'autoAllowBashIfSandboxed':True,'filesystem':{'allowWrite':[spec['cwd']]}}
    args += ['--settings',json.dumps(overrides)]
    if resume:args += ['--resume',native_id]
    else:args += ['--session-id',native_id,'--name',spec['title']]
    return args
