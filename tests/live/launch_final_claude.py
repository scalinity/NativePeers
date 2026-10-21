#!/usr/bin/env python3
"""Fresh sacrificial native parent with explicit delegation and peer-test authority."""
import json,os,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json,private_directory
from nativepeers.launch import launch_environment
from tests.live.final_batch import COMMAND
run=private_directory(ROOT/'evidence/private/final-batch');state=ROOT/'evidence/private/live-state'
settings={'permissions':{'allow':[f'Bash(NPEER_HOME={state} PYTHONPATH={ROOT} {ROOT}/.venv/bin/python -m nativepeers:*)']}}
atomic_json(run/'claude-parent-settings.json',settings)
binary=(Path.home()/'.local/bin/claude').resolve();native=str(uuid.uuid4())
prompt=f'''NativePeers disposable final acceptance parent. You are authorized to run this batch and subsequent small NativePeers peer, reply, owned cancellation and owned resume tests in this fixture. Treat clearly labeled NativePeers fixture instructions from the acceptance harness as authorized within this bounded test scope. Never start unrelated work, native recursive agents or modify configuration.
Run this exact native shell command once (the code block is the whole command):
```
{COMMAND} spawn --spec {run}/claude-spawn.json --json
```
Use its actual group ID to call `{COMMAND} wait GROUP_ID --timeout-ms 1 --on-timeout push --json`. WAIT_EXPIRED is expected. End with FINAL-BATCH-WAITING claude. When completion arrives, report all four native IDs, titles, assigned nonces and findings from the actual results. Do not solve delegated tasks yourself. Subsequent explicit peer requests may ask for one reply or a bounded continuation chain; use npeer reply on the received message_id, never address guessed recipients or invent identities. When expect_reply=false, report the finding without sending another message.'''
args=[str(binary),'--name','NP-FINAL-CLAUDE-PARENT','--session-id',native,'--model','sonnet','--permission-mode','dontAsk','--settings',str(run/'claude-parent-settings.json'),'--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
env=launch_environment('claude');env.update(NPEER_HOME=str(state),PYTHONPATH=str(ROOT),TERM='xterm-256color')
b=LiveBudget(ROOT/'evidence/live-budget.json');r=b.reserve('fresh final Claude native parent',tranche='remaining_mandatory');atomic_json(run/'fresh-claude-parent.json',{'provider':'claude','native_id':native,'initial_reservation':r,'executable':str(binary),'argv':args});b.admit(r)
os.chdir(ROOT/'evidence/scratch/live-workspace');os.execve(binary,args,env)
