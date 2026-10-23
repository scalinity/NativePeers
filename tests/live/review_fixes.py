#!/usr/bin/env python3
"""Explicit, bounded review-fix retests in disposable native conversations."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from nativepeers.adapters.codex_app_server import AppServer
from nativepeers.artifacts import Artifacts
from nativepeers.budget import LiveBudget
from nativepeers.caller import bind_caller
from nativepeers.compatibility import fingerprint
from nativepeers.core import Core
from nativepeers.files import atomic_json,private_directory
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id
from nativepeers.profiles import config_arguments
from nativepeers.process import identity
from nativepeers.worktrees import git

STATE=ROOT/'evidence/private/review-live-state'
RUN=ROOT/'evidence/private/review-retest'
FIXTURE=ROOT/'evidence/scratch/review-workspace'
LEDGER=ROOT/'evidence/live-budget.json'


def stamp():return datetime.now(ZoneInfo('America/New_York')).isoformat()


async def prepare():
    import subprocess,tomllib
    private_directory(RUN);FIXTURE.mkdir(parents=True,exist_ok=True)
    (FIXTURE/'fixture.txt').write_text('alpha=7\nbeta=11\ngamma=13\n')
    if not (FIXTURE/'.git').exists():
        git(FIXTURE,'init','-q');git(FIXTURE,'add','fixture.txt');git(FIXTURE,'commit','-qm','Create native review fixture')
    journal=Journal(STATE)
    try:
        providers={}
        for name,path in [('claude',Path.home()/'.local/bin/claude'),('codex',Path.home()/'.local/share/codex-mcp-manager/current/bin/codex')]:
            providers[name]=dict(fingerprint(path),version=subprocess.check_output([str(path),'--version'],text=True).strip())
        atomic_json(STATE/'compat/runtime.json',{'schema_version':1,'providers':providers,'scope':'Provisional disposable review test only; live acceptance pending','at':stamp()})
        atomic_json(STATE/'test-budget.json',{'path':str(LEDGER),'tranche':'review_fixes'})
        atomic_json(STATE/'policy.json',{'task_wall_seconds':150,'group_wall_seconds':420,'followup_grace_seconds':30})
        config=tomllib.loads((Path.home()/'.codex/config.toml').read_text())
        overrides={'mcp_server_policy':{k:{'enabled':False} for k in config.get('mcp_servers',{})},
                   'plugins':{k:{'enabled':False} for k in config.get('plugins',{})},
                   'features.multi_agent':False,'features.multi_agent_v2':False,'features.js_repl':False,'web_search':'disabled'}
        overrides['mcp_server_policy'].update({'codex_apps':{'enabled':False},'openai-api-key-local-confirmation':{'enabled':False}})
        client=AppServer(Path(providers['codex']['path']))
        try:
            await client.start();listing=await client.call('hooks/list',{'cwd':str(FIXTURE)})
            hooks=[h for row in listing['data'] for h in row['hooks']]
            own=[h for h in hooks if str(ROOT/'bin/npeer-hook') in h.get('command','')]
            if len(own)!=2:raise RuntimeError('Expected both installed NativePeers hooks')
            overrides['hooks.state']={h['key']:({'enabled':True,'trusted_hash':h['currentHash']} if h in own else {'enabled':False}) for h in hooks}
            models=(await client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            model=next(m for m in models if m['model']=='gpt-6.1-sol')
            if 'xhigh' not in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']]:raise RuntimeError('Required xhigh effort unavailable')
        finally:await client.close()
        overrides['projects']={str(FIXTURE):{'trust_level':'trusted'}}
        atomic_json(RUN/'codex-overrides.json',overrides)
        settings=json.loads((Path.home()/'.claude/settings.json').read_text())
        atomic_json(RUN/'claude-settings.json',{'enabledPlugins':{k:False for k in settings.get('enabledPlugins',{})},
            'permissions':{'allow':[f'Bash({ROOT}/.venv/bin/python {ROOT}/tests/live/review_fixes.py:*)']}})
        atomic_json(RUN/'passive.json',{'at':stamp(),'providers':providers,'claude_model':settings.get('model','sonnet'),
            'codex_model':'gpt-6.1-sol','codex_effort':'xhigh','installed_native_hooks':len(own),
            'source_dependencies':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}})
        for parent,provider in [('claude','codex'),('codex','claude')]:
            workers=[]
            for role in ['A','B']:
                nonce=f'RV-{parent}-{role}'
                reads='once' if role=='A' else 'four separate times in successive tool calls; do not combine those calls'
                task=f'Read fixture.txt {reads} using native read tools (or the native read-only shell if needed). Report your assigned nonce {nonce}, your native worker title, sum=31 and maximum gamma=13. End your task. Never start other workers or vendor-native subagents. Later focused followups are independent tasks; return their requested findings without sending peer replies.'
                worker={'provider':provider,'title':f'RV {parent} child {role}','task':task,'permission_profile':'analysis','workspace':{'mode':'inherit-read-only'}}
                if provider=='codex':worker.update(model='gpt-6.1-sol',reasoning_effort='xhigh')
                workers.append(worker)
            atomic_json(RUN/(parent+'-spawn.json'),{'schema_version':1,'client_request_id':'RV-'+parent+'-batch-1','completion_mode':'push','workers':workers,
                'limits':{'max_concurrent_workers':2,'max_depth':1,'wall_time_seconds':150,'max_task_turns_per_worker':8,'followup_grace_seconds':30}})
        print(json.dumps({'prepared':True,'state_root':str(STATE),'providers':providers,'model_inputs_submitted':0}))
    finally:journal.close()


def launch(provider,workspace=None):
    passive=json.loads((RUN/'passive.json').read_text());binary=Path(passive['providers'][provider]['path'])
    if fingerprint(binary)['sha256']!=passive['providers'][provider]['sha256']:raise RuntimeError('Executable changed after passive preparation')
    command=f'{ROOT}/.venv/bin/python {ROOT}/tests/live/review_fixes.py parent-case {provider}'
    prompt=f'''Authorized disposable NativePeers review-fix acceptance RV-{provider}. Run this exact command ONCE through your native shell tool:
{command}
The helper binds to your actual native identity, spawns exactly two independent native workers, tests bounded task waits and owned task cancellation, and reads actual worker results. The helper's local status loops do not invoke models. Do not duplicate its actions, answer delegated tasks yourself, start vendor-native agents, change configuration or do unrelated work. Allow this fixture command up to 240 seconds to return; do not kill it merely for exceeding an initial tool wait. If your tool returns a running session, collect that same tool session's final output. After it returns, report RV-INITIAL-{provider}, the helper's assertions, the two distinct worker native UUIDs, each initial worker nonce and finding, and its retained followup result. Then end your turn. When the separate NativePeers aggregate completion arrives in THIS conversation, report RV-COMPLETION-{provider}, both initial worker UUIDs, nonces and findings from that actual completion. This is a bounded harmless fixture test; send no peer reply to controller notices. Only run the one command above.'''
    native=new_id() if provider=='claude' else None
    if provider=='claude':
        args=[str(binary),'--name','RV Claude Parent','--session-id',native,'--model',passive['claude_model'],'--permission-mode','dontAsk',
              '--settings',str(RUN/'claude-settings.json'),'--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
    else:
        overrides=json.loads((RUN/'codex-overrides.json').read_text())
        args=[str(binary),'--nativepeers-worker','--no-daemon','--name','RV Codex Parent','--dangerously-bypass-approvals-and-sandbox',*config_arguments(overrides),prompt]
    budget=LiveBudget(LEDGER);reservation=budget.reserve('RV '+provider+' parent initial input',tranche='review_fixes')
    expected_owner=dict(identity(os.getpid()).json(),executable=str(binary))
    workspace=workspace or FIXTURE
    if workspace not in (FIXTURE,ROOT/'evidence/scratch/live-workspace'):raise RuntimeError('Workspace is outside the owned disposable fixtures')
    atomic_json(RUN/(provider+'-launch.json'),{'provider':provider,'native_id':native,'initial_reservation':reservation,'executable':str(binary),'owner_expected_after_exec':expected_owner,'argv':args,'cwd':str(workspace),'at':stamp()})
    environment=launch_environment(provider);environment.update(NPEER_HOME=str(STATE),PYTHONPATH=str(ROOT),TERM='xterm-256color')
    os.chdir(workspace);budget.admit(reservation);os.execve(binary,args,environment)


async def until(predicate,timeout=150):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        await asyncio.sleep(.05)
    raise TimeoutError('Bounded review fixture observation timed out')


async def parent_case(provider):
    journal=Journal(STATE);core=Core(journal);caller=bind_caller(journal,core.registry,allow_external=False)
    parent=core.registry.verify(caller.key,caller.incarnation)
    if parent['peer_key']['provider']!=provider:raise RuntimeError('Native parent provider differs')
    record={'provider':provider,'at':stamp(),'parent_native_id':parent['peer_key']['native_session_id'],'caller_incarnation':caller.incarnation,'assertions':{}}
    def save():atomic_json(RUN/(provider+'-case.json'),record)
    def row(task):return journal.one('SELECT * FROM tasks WHERE task_id=?',(task,))
    def started(task):return journal.one("SELECT at FROM events WHERE subject_id=? AND kind='native-task-started'",(task,))
    def result(task):return core.delegation.result(caller,task)['results'][0]
    group_id=None;waiter=None
    try:
        spec=json.loads((RUN/(provider+'-spawn.json')).read_text());status=await core.tool(caller,'worker_spawn',{'spec':spec});group_id=status['group_id']
        record['group_id']=group_id;record['spawned_by_actual_parent']=True;save()
        workers=journal.all('SELECT * FROM workers WHERE group_id=? ORDER BY role_title',(group_id,));a,b=workers
        ta=journal.one('SELECT task_id FROM tasks WHERE worker_id=?',(a['worker_id'],))['task_id'];tb=journal.one('SELECT task_id FROM tasks WHERE worker_id=?',(b['worker_id'],))['task_id']
        record.update(initial_task_ids=[ta,tb],worker_ids=[a['worker_id'],b['worker_id']]);save()
        waiter=asyncio.create_task(core.tool(caller,'task_wait',{'handle':ta,'timeout_ms':30000,'on_timeout':'push'}))
        await asyncio.sleep(.2)
        route=journal.one('SELECT state FROM completion_routes WHERE group_id=? AND completion_generation=1',(group_id,))
        record['route_during_task_wait']=route['state'];record['assertions']['task_wait_does_not_lease_batch']=route['state']!='WAIT_LEASED'
        await until(lambda:started(tb),90)
        b=journal.one('SELECT * FROM workers WHERE worker_id=?',(b['worker_id'],));native_before=b['native_id'];record['followup_native_id']=native_before
        async def followup(label,text):
            receipt=await core.router.send(caller,b['peer_key'],text,client_request_id='RV-'+provider+'-'+label)
            task=journal.one('SELECT task_id FROM messages WHERE message_id=?',(receipt['message_id'],))['task_id']
            if not task:raise RuntimeError('Owned followup was not admitted')
            return task
        active=await followup('active','RV-ACTIVE-'+provider+'. Read fixture.txt in four separate successive tool calls, then report this nonce and gamma=13. A scoped cancellation may interrupt this task. Do not reply or start any other work.')
        canceled=await followup('queued','RV-NEVER-'+provider+'. This queued fixture task will be canceled before native input; do not start other work.')
        record.update(active_cancel_task=active,queued_cancel_task=canceled);save()
        before=row(tb)['state'];cancel_receipt=await core.delegation.cancel(caller,canceled,'review queued task cancellation')
        record['assertions']['queued_cancel_preserves_running_initial']=before=='RUNNING' and row(tb)['state']=='RUNNING' and row(canceled)['state']=='CANCELED'
        record['queued_cancel_receipt']=cancel_receipt
        first=await waiter
        record['first_wait_ready']=first.get('ready',False);record['first_wait_disposition']=first.get('notification_disposition')
        record['assertions']['partial_wait_keeps_batch_notification']=first.get('notification_disposition')=='batch-notification-unchanged'
        record['assertions']['task_wait_returned_requested_result']=bool(first.get('ready') and [r['task_id'] for r in first['result']['results']]==[ta])
        save()
        await until(lambda:started(active),150)
        kept=await followup('kept','RV-KEPT-'+provider+'. Read fixture.txt once, then report this exact nonce, your title, sum=31 and gamma=13. Do not send replies, start workers or perform other work.')
        record['kept_task_id']=kept;save()
        record['active_cancel_receipt']=await core.delegation.cancel(caller,active,'review running task cancellation with a queued successor')
        for _ in range(3):
            waited=await core.tool(caller,'task_wait',{'handle':kept,'timeout_ms':30000,'on_timeout':'retain'})
            if waited.get('ready'):break
        else:raise TimeoutError('Kept followup did not complete within its bounded waits')
        initial=[result(t) for t in [ta,tb]];later=result(kept);current=journal.one('SELECT * FROM workers WHERE worker_id=?',(b['worker_id'],))
        record.update(initial_results=initial,kept_result=later,worker_native_ids=[r['worker']['native_session_id'] for r in initial],states={t:row(t)['state'] for t in [ta,tb,active,canceled,kept]})
        assertions=record['assertions']
        assertions['both_initial_tasks_completed']=all(r['status']=='completed' for r in initial)
        assertions['distinct_native_workers']=len(set(record['worker_native_ids']))==2
        assertions['queued_input_never_started']=started(canceled) is None and row(canceled)['state']=='CANCELED'
        assertions['native_started_task_canceled']=started(active) is not None and row(active)['state']=='CANCELED'
        assertions['accepted_successor_preserved']=later['status']=='completed' and 'RV-KEPT-'+provider in later['summary']
        assertions['same_owned_native_history']=current['native_id']==native_before==later['worker']['native_session_id']
        record['status']='PASS' if all(assertions.values()) else 'FAIL';save()
        print(json.dumps(record,ensure_ascii=False),flush=True)
    except BaseException as error:
        record.update(status='FAIL',error={'type':type(error).__name__,'message':str(error)[:1000]});save()
        if waiter and not waiter.done():waiter.cancel()
        if group_id:
            try:await core.delegation.cancel(caller,group_id,'bounded review fixture failure cleanup')
            except Exception:pass
        print(json.dumps(record,ensure_ascii=False),flush=True)
        raise
    finally:journal.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','launch','parent-case']);parser.add_argument('provider',nargs='?',choices=['claude','codex']);parser.add_argument('--workspace',type=Path);args=parser.parse_args()
    if args.action=='prepare':asyncio.run(prepare())
    elif args.action=='launch':launch(args.provider,args.workspace)
    else:asyncio.run(parent_case(args.provider))


if __name__=='__main__':main()
