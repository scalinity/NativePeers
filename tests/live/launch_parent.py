#!/usr/bin/env python3
"""New native parent fixture for an actual model-initiated delegation."""
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.files import private_directory,atomic_json
from nativepeers.launch import launch_environment
from nativepeers.profiles import config_arguments

provider,case=sys.argv[1:]
fixture=ROOT/'evidence/scratch/live-workspace';state=ROOT/'evidence/private/live-state'
run=private_directory(ROOT/'evidence/private'/case)
budget=LiveBudget(ROOT/'evidence/live-budget.json');reservation=budget.reserve(case+' native parent prompt',tranche='first_cross_provider_delegations')
environment=launch_environment(provider);environment.update(NPEER_HOME=str(state),PYTHONPATH=str(ROOT),TERM='xterm-256color')
command=f'NPEER_HOME={state} PYTHONPATH={ROOT} {ROOT}/.venv/bin/python -m nativepeers spawn --spec {run}/spawn.json --json'
prompt=(f'NativePeers disposable integration acceptance {case}. Use your native shell tool to run exactly this command once:\n{command}\n'
        f'It spawns two independent native workers. Do not answer their tasks yourself and do not use vendor-native Agent/subagents. '
        f'When the tool returns a group ID, say WAITING {case} and end your turn. '
        f'When the NativePeers controller completion arrives, report BOTH worker titles, their distinct native IDs, each assigned nonce and each worker finding. '
        'Use only the actual worker results. Do not spawn anything else, change configuration, or start unrelated work.')
if provider=='claude':
    import uuid
    binary=(Path.home()/'.local/bin/claude').resolve();settings={'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':f'{ROOT}/.venv/bin/python -m nativepeers.hooks --provider claude','timeout':3}]}]},
        'permissions':{'allow':[f'Bash({ROOT}/.venv/bin/python -m nativepeers:*)',f'Bash(NPEER_HOME={state} PYTHONPATH={ROOT} {ROOT}/.venv/bin/python -m nativepeers:*)']}}
    atomic_json(run/'settings.json',settings)
    args=[str(binary),'--name',case,'--session-id',str(uuid.uuid4()),'--model','sonnet','--permission-mode','dontAsk',
          '--setting-sources','','--settings',str(run/'settings.json'),'--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
else:
    binary=(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve()
    listing=json.loads((ROOT/'evidence/passive/live-parent-hooks.json').read_text())
    config=json.loads((ROOT/'evidence/passive/live-parent-overrides.json').read_text())
    states={h['key']:({'trusted_hash':h['currentHash']} if 'NativePeers' in h['sourcePath'] else {'enabled':False}) for h in listing['data'][0]['hooks']}
    config['hooks.state']=states
    config['projects']={str(fixture):{'trust_level':'trusted'}}
    args=[str(binary),'--nativepeers-worker','--no-daemon','--name',case,'--dangerously-bypass-approvals-and-sandbox',*config_arguments(config),prompt]
atomic_json(run/'parent-launch.json',{'provider':provider,'executable':str(binary),'argv':args,'initial_reservation':reservation})
os.chdir(fixture);budget.admit(reservation);os.execve(binary,args,environment)
