#!/opt/homebrew/bin/python3
"""Launch one sacrificial native TUI, reserving its initial and return turns."""
import json
import os
from pathlib import Path
import sys
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json, private_directory
from nativepeers.launch import launch_environment


provider, label, *mode = sys.argv[1:]
run = private_directory(ROOT/'evidence/private'/label)
fixture = ROOT/'evidence/scratch/gate-workspace'
budget = LiveBudget(ROOT/'evidence/live-budget.json')
initial = budget.reserve(f'{label} initial native prompt')
completion = budget.reserve(f'{label} helper native completion')
atomic_json(run/'return-reservation.json', {'id': completion})
atomic_json(run/'initial-reservation.json', {'id': initial})
command = f'/opt/homebrew/bin/python3 {ROOT}/tests/probes/seams.py context --provider {provider} --run {run}'
prompt = (f'NativePeers disposable integration probe. Run exactly this harmless command once using your native shell tool:\n{command}\n'
          f'It writes only scoped probe evidence and launches a finite non-model helper. After it returns, say READY {label} and stop. '
          f'When a later NativePeers completion arrives, reply exactly RETURN {label}. Do not invoke Agent/subagents, spawn model workers, '
          'change configuration, or do unrelated work. No need to inspect the repository.')
environment = launch_environment(provider)
environment.update(NPEER_PROBE_RUN=str(run), TERM='xterm-256color')
if provider == 'claude':
    native_id = str(uuid.uuid4())
    settings = {'hooks': {'SessionStart': [{'hooks': [{'type':'command','command':f'/opt/homebrew/bin/python3 {ROOT}/tests/probes/seams.py hook --provider claude --run {run}','timeout':5}]}]},
                'permissions': {'allow': [f'Bash(/opt/homebrew/bin/python3 {ROOT}/tests/probes/seams.py:*)']}}
    atomic_json(run/'settings.json', settings)
    binary = (Path.home()/'.local/bin/claude').resolve()
    args = [str(binary),'--name',label,'--session-id',native_id,'--model','sonnet','--permission-mode','dontAsk',
            '--setting-sources','','--settings',str(run/'settings.json'),'--strict-mcp-config',
            '--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
else:
    binary = (Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve()
    listing = json.loads((ROOT/'evidence/passive/hook-list.json').read_text())
    args = [str(binary),'--name',label,'--dangerously-bypass-approvals-and-sandbox',
            '-c',f'projects={{ {json.dumps(str(fixture))} = {{trust_level="trusted"}} }}']
    states = []
    for hook in listing['data'][0]['hooks']:
        key = json.dumps(hook['key'])
        if 'NativePeers' in hook['sourcePath']:
            states.append(f'{key} = {{trusted_hash={json.dumps(hook["currentHash"])}}}')
        else:
            states.append(f'{key} = {{enabled=false}}')
    args += ['-c','hooks.state={'+','.join(states)+'}']
    config=tomllib.loads((Path.home()/'.codex/config.toml').read_text())
    policies = [f'{json.dumps(name)} = {{enabled=false}}' for name in config.get('mcp_servers',{})]
    policies.append('codex_apps = {enabled=false}')
    args += ['-c','mcp_server_policy={'+','.join(policies)+'}']
    plugins = [f'{json.dumps(name)} = {{enabled=false}}' for name in config.get('plugins',{})]
    args += ['-c','plugins={'+','.join(plugins)+'}']
    if 'standalone' in mode:args += ['--no-daemon']
    args += [prompt]
atomic_json(run/'launch.json',dict(provider=provider,executable=str(binary),argv=args,
                                  provider_home=str(Path.home()/('.codex' if provider=='codex' else '.claude')),
                                  environment_names=sorted(environment),initial_reservation=initial,return_reservation=completion))
os.chdir(fixture)
budget.admit(initial)
os.execve(binary,args,environment)
