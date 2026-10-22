#!/usr/bin/env python3
"""Explicitly budgeted native parent/worker paths; never run by offline pytest."""
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

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.caller import bind_caller
from nativepeers.core import Core
from nativepeers.files import atomic_json,private_directory
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.profiles import config_arguments
from nativepeers.adapters.codex_app_server import AppServer

RUN=ROOT/'evidence/private/model-antigravity/live'
STATE=RUN/'state'
FIXTURE=ROOT/'evidence/scratch/antigravity-workspace'
LEDGER=ROOT/'evidence/live-budget.json'


def stamp():return datetime.now(ZoneInfo('America/New_York')).isoformat()


def prepare():
    private_directory(RUN);FIXTURE.mkdir(parents=True,exist_ok=True)
    (FIXTURE/'fixture.txt').write_text('alpha=7\nbeta=11\ngamma=13\n')
    journal=Journal(STATE)
    production=Path.home()/'.local/state/nativepeers/compat/runtime.json'
    atomic_json(STATE/'compat/runtime.json',json.loads(production.read_text()))
    atomic_json(STATE/'test-budget.json',{'path':str(LEDGER),'tranche':'antigravity'})
    atomic_json(STATE/'policy.json',{'task_wall_seconds':150,'group_wall_seconds':240,'followup_grace_seconds':1})
    from nativepeers.policy import load
    load(journal)
    # Reuse unchanged installed native hook trust, refreshed for this fixture only.
    overrides=json.loads((ROOT/'evidence/private/review-retest/codex-overrides.json').read_text())
    overrides['projects']={str(FIXTURE):{'trust_level':'trusted'}}
    atomic_json(RUN/'codex-overrides.json',overrides)
    claude=json.loads((Path.home()/'.claude/settings.json').read_text())
    atomic_json(RUN/'claude-settings.json',{'enabledPlugins':{name:False for name in claude.get('enabledPlugins',{})},
        'permissions':{'allow':[f'Bash({ROOT}/.venv/bin/python {ROOT}/tests/live/antigravity_paths.py:*)']}})
    atomic_json(RUN/'dependencies.json',{'at':stamp(),'source':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'nativepeers').rglob('*.py')}})
    journal.close();print('Prepared disposable native state; no model inputs submitted')


async def parent_case(provider):
    journal=Journal(STATE);core=Core(journal);caller=bind_caller(journal,core.registry,allow_external=False)
    parent=core.registry.verify(caller.key,caller.incarnation)
    assert parent['peer_key']['provider']==provider
    providers=['antigravity','antigravity'] if provider=='claude' else ['antigravity','claude'] if provider=='codex' else ['codex','antigravity']
    workers=[]
    for role,child in zip(['A','B'],providers):
        worker={'provider':child,'title':'NP '+provider+' final '+role,
                'task':'Read '+str(FIXTURE/'fixture.txt')+' using a native file tool. Compute the sum and maximum from the actual file. Report NP-'+provider+'-'+role+'-CHILD, your assigned NativePeers title and the finding. Do not start other workers, vendor-native subagents, send peer replies or do unrelated work.',
                'model':{'claude':'claude-sonnet-5-5','codex':'gpt-6.1-sol','antigravity':'gemini-4-argon-high'}[child],
                'permission_profile':'analysis','workspace':{'mode':'inherit-read-only'}}
        if child=='codex':worker['reasoning_effort']='xhigh'
        if child=='antigravity' and provider!='antigravity':
            canary=FIXTURE/('DENIED-final-'+provider+'-'+role+'.txt')
            assert not canary.exists()
            worker['task']+=' As an authorized negative test of a temporary fixture tool declaration, issue exactly one native write_to_file request to create '+str(canary)+' with harmless text "permission canary". The NativePeers analysis gate must deny it. Do not retry through another tool. Report the actual read result and the native write denial.'
        workers.append(worker)
    spec={'schema_version':1,'client_request_id':'NP-final-'+provider+'-1','workers':workers,'completion_mode':'push' if provider=='claude' else 'wait',
          'limits':{'max_concurrent_workers':2,'max_depth':1,'wall_time_seconds':150,'max_task_turns_per_worker':4,'followup_grace_seconds':1}}
    path=RUN/(provider+'-case.json')
    if path.exists():
        raw=path.read_bytes();archive=path.with_name(path.stem+'.'+hashlib.sha256(raw).hexdigest()[:12]+'.json');archive.write_bytes(raw)
    record={'provider':provider,'parent_native_id':parent['peer_key']['native_session_id'],'at':stamp(),
            'source_dependencies':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'nativepeers').rglob('*.py')}}
    try:
        if provider=='antigravity':
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters,stdio_client
            params=StdioServerParameters(command=str(ROOT/'.venv/bin/python'),args=['-m','nativepeers.mcp_server','--native-caller'],cwd=ROOT,
                env={'NPEER_HOME':str(STATE),'PYTHONPATH':str(ROOT)})
            async with stdio_client(params) as (reader,writer):
                async with ClientSession(reader,writer) as session:
                    await session.initialize()
                    bound=json.loads((await session.call_tool('task_status',{})).content[0].text)
                    assert bound['ok'] and bound['data']['native_session_id']==record['parent_native_id']
                    output=json.loads((await session.call_tool('worker_spawn',{'spec':spec})).content[0].text)
                    assert output['ok'],output
                    status=output['data'];record['actual_native_context_mcp_binding']=bound['data']
        else:status=await core.tool(caller,'worker_spawn',{'spec':spec})
        record['group_id']=status['group_id'];atomic_json(path,record)
        task_ids=[t['task_id'] for t in journal.all('SELECT task_id FROM tasks JOIN workers USING(worker_id) WHERE group_id=? ORDER BY tasks.rowid',(status['group_id'],))]
        handles=task_ids if provider=='claude' else [status['group_id']]
        for handle in handles:
            for _ in range(6):
                value=await core.tool(caller,'task_wait',{'handle':handle,'timeout_ms':30000,'on_timeout':'retain'})
                if value.get('ready'):break
            else:raise TimeoutError('Bounded worker wait expired')
        results=core.delegation.result(caller,status['group_id'])['results']
        record['results']=results
        if len(results)!=2 or any(r['status']!='completed' for r in results):
            raise RuntimeError('Native workers did not complete: '+json.dumps([{'worker':r['worker'],'error':r['error']} for r in results]))
        assertions={'two_completed_results':len(results)==2 and all(r['status']=='completed' for r in results),
                    'distinct_child_native_ids':len({r['worker']['native_session_id'] for r in results})==2 and all(r['worker']['native_session_id']!=record['parent_native_id'] for r in results)}
        gates=[]
        for worker,result,role in zip(workers,results,['A','B']):
            assertions[role+'_exact_model']=result['model_requested']==worker['model'] and result['model_observed']==worker['model']
            assertions[role+'_nonce']='NP-'+provider+'-'+role+'-CHILD' in result['summary']
            assertions[role+'_finding']='31' in result['summary'] and '13' in result['summary']
            if worker['provider']=='antigravity':
                row=journal.one('SELECT worker_id FROM workers WHERE group_id=? AND native_id=?',(status['group_id'],result['worker']['native_session_id']))
                tool_events=[json.loads(r['payload_json']) for r in journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='antigravity-tool-gate'",(row['worker_id'],))]
                gates+=tool_events
                native_log=Path.home()/'.gemini/antigravity-cli/brain'/result['worker']['native_session_id']/'.system_generated/logs/transcript.jsonl'
                native_events=[json.loads(line) for line in native_log.read_text().splitlines()]
                assertions[role+'_actual_native_read']=any(e.get('type')=='GENERIC' and e.get('status')=='DONE' and '1: alpha=7' in e.get('content','') and '3: gamma=13' in e.get('content','') for e in native_events)
                assertions[role+'_native_read_allowed']=any(g['tool']=='view_file' and g['allowed'] for g in tool_events)
                if provider!='antigravity':
                    assertions[role+'_native_write_denied']=any(g['tool']=='write_to_file' and not g['allowed'] for g in tool_events)
                    assertions[role+'_fixture_unmodified']=not (FIXTURE/('DENIED-final-'+provider+'-'+role+'.txt')).exists()
        starts=[json.loads(journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-task-started'",(task,))['payload_json'])['at'] for task in task_ids]
        assertions['native_task_intervals_overlap']=max(starts)<min(r['completed_at'] for r in results)
        record.update(results=results,assertions=assertions,native_tool_gate_events=gates,initial_task_ids=task_ids,native_task_started_at=starts,
                      status='PASS' if all(assertions.values()) else 'FAIL')
        if provider=='claude' and all(assertions.values()):
            from nativepeers.worktrees import snapshot
            import ast
            baseline=snapshot(parent['cwd']);editors=[]
            for child in ('claude','codex','antigravity'):
                filename='nativepeers_'+child+'_fixture.py'
                task='Implement '+filename+' in your current isolated worktree. Define exactly one function scale(values, factor) that returns a new list containing each input multiplied by factor. Use a list comprehension, no imports or module-level side effects. Run a harmless native Python check for scale([2,3],4)==[8,12] and scale([],7)==[]. Report NP-IMPLEMENT-'+child+', the filename and the actual test outcome. Do not modify other files, commit, merge, start agents or do unrelated work.'
                editor={'provider':child,'title':'NP Implementation '+child,'task':task,
                        'model':{'claude':'claude-sonnet-5-5','codex':'gpt-6.1-sol','antigravity':'gemini-4-argon-high'}[child],
                        'permission_profile':'workspace','workspace':{'mode':'isolated-worktree'}}
                if child=='codex':editor['reasoning_effort']='xhigh'
                editors.append(editor)
            edit_spec={'schema_version':1,'client_request_id':'NP-implementation-three-1','workers':editors,'completion_mode':'wait',
                       'limits':{'max_concurrent_workers':3,'max_depth':1,'wall_time_seconds':150,'max_task_turns_per_worker':4,'followup_grace_seconds':1}}
            edited=await core.tool(caller,'worker_spawn',{'spec':edit_spec});record['implementation_group_id']=edited['group_id'];atomic_json(path,record)
            for _ in range(6):
                output=await core.tool(caller,'task_wait',{'handle':edited['group_id'],'timeout_ms':30000,'on_timeout':'retain'})
                if output.get('ready'):break
            else:raise TimeoutError('Implementation worker wait expired')
            implemented=output['result']['results'];record['implementation_results']=implemented
            assertions['three_implementation_results']=len(implemented)==3 and all(r['status']=='completed' for r in implemented)
            for result in implemented:
                child=result['worker']['provider'];work=Path(result['changes']['worktree']);filename='nativepeers_'+child+'_fixture.py'
                assertions[child+'_worktree_inside_project']=work.parent==(Path(baseline['root'])/'.worktrees')
                assertions[child+'_patch_includes_new_file']=bool(result['changes']['patch_artifact']) and filename in result['changes'].get('untracked_files',[])
                assertions[child+'_explicit_model']=result['model_observed']=={'claude':'claude-sonnet-5-5','codex':'gpt-6.1-sol','antigravity':'gemini-4-argon-high'}[child]
                if (work/filename).exists():
                    module=ast.parse((work/filename).read_text());allowed=(ast.Module,ast.FunctionDef,ast.arguments,ast.arg,ast.Return,ast.ListComp,ast.comprehension,ast.BinOp,ast.Mult,ast.Name,ast.Load,ast.Store,ast.Expr,ast.Constant)
                    assert all(isinstance(node,allowed) for node in ast.walk(module))
                    scope={'__builtins__':{}};exec(compile(module,str(work/filename),'exec'),scope)
                    assertions[child+'_actual_code_passes']=scope['scale']([2,3],4)==[8,12] and scope['scale']([],7)==[]
                else:assertions[child+'_actual_code_passes']=False
            starts=[json.loads(journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-task-started'",(t['task_id'],))['payload_json'])['at'] for t in journal.all('SELECT task_id FROM tasks JOIN workers USING(worker_id) WHERE group_id=?',(edited['group_id'],))] if assertions['three_implementation_results'] else []
            assertions['implementation_workers_overlap']=bool(starts) and max(starts)<min(r['completed_at'] for r in implemented)
            assertions['original_checkout_preserved']=snapshot(parent['cwd'])==baseline
            record['assertions']=assertions;record['status']='PASS' if all(assertions.values()) else 'FAIL'

        atomic_json(path,record);print(json.dumps(record),flush=True)
    except BaseException as error:
        record.update(status='FAIL',error={'type':type(error).__name__,'message':str(error)[:1500]});atomic_json(path,record)
        if record.get('group_id'):
            try:await core.delegation.cancel(caller,record['group_id'],'bounded native test failure cleanup')
            except Exception:pass
        raise
    finally:journal.close()


def launch(provider):
    from nativepeers.policy import load
    journal=Journal(STATE)
    try:
        load(journal)
        from nativepeers.compatibility import verify
        for name,path in [('claude','.local/bin/claude'),('codex','.local/share/codex-mcp-manager/current/bin/codex'),('antigravity','.local/bin/agy')]:
            verify(journal,name,Path.home()/path)
    finally:journal.close()
    remaining=json.loads(LEDGER.read_text())
    needed=4 if provider=='claude' else 3
    ceiling=remaining['ceiling']['submitted_turns']
    if ceiling is not None and ceiling-remaining['consumed_turns']-remaining['reserved_turns']<needed:
        raise RuntimeError('Test slots must remain for the parent, worker and any completion wakeup')
    command=f'{ROOT}/.venv/bin/python {ROOT}/tests/live/antigravity_paths.py parent-case {provider}'
    prompt=f'''Authorized disposable NativePeers integration NP-{provider}. Use your native shell tool to run this exact command ONCE:
{command}
It binds to your actual native identity, launches two independent native workers concurrently, and waits locally for their attributed results. Do not answer the assigned child task yourself, retry the command, invoke vendor-native subagents, change configuration or do unrelated work. Give the command up to 210 seconds; collect the same running tool process if needed. After it completes, report NP-{provider}-PARENT-RESULT, both actual child native UUIDs, requested/observed models, assigned child nonces and findings. If the helper also runs implementation workers, report their three native UUIDs, filenames and actual test outcomes, then end your turn. If it fails, report the actual error without substitutions or retries.'''
    if provider=='claude':prompt+=' A separately attributed NativePeers controller completion may arrive after your initial result. When it arrives, report NP-claude-PUSH-RESULT with both actual child UUIDs, nonces and findings, then end. Do not reply to the controller as a peer or run another command.'
    native=new_id() if provider=='claude' else None
    if provider=='claude':
        binary=(Path.home()/'.local/bin/claude').resolve()
        args=[str(binary),'--session-id',native,'--name','NP Argon Claude Parent','--permission-mode','dontAsk',
              '--settings',str(RUN/'claude-settings.json'),'--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--tools','Bash,Read','--',prompt]
    elif provider=='codex':
        binary=(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex').resolve()
        async def refresh_hooks():
            client=AppServer(binary)
            try:
                await client.start();value=await client.call('hooks/list',{'cwd':str(ROOT/'evidence/scratch/live-workspace')})
                hooks=[h for row in value['data'] for h in row['hooks']]
                return {h['key']:({'enabled':True,'trusted_hash':h['currentHash']} if h.get('command','').startswith(str(ROOT/'bin/npeer-hook')) else {'enabled':False}) for h in hooks}
            finally:await client.close()
        overrides=json.loads((RUN/'codex-overrides.json').read_text());overrides['hooks.state']=asyncio.run(refresh_hooks())
        overrides.update({'memories.generate_memories':False,'memories.use_memories':False})
        args=[str(binary),'--name','NP Argon Codex Parent','--dangerously-bypass-approvals-and-sandbox',
              *config_arguments(overrides),prompt]
    else:
        binary=(Path.home()/'.local/bin/agy').resolve()
        args=[str(binary),'--model','gemini-4-argon-high','--dangerously-skip-permissions','--prompt-interactive',prompt]
    budget=LiveBudget(LEDGER);reservation=budget.reserve('NP '+provider+' native parent input',tranche='antigravity')
    launch_path=RUN/(provider+'-launch.json')
    if launch_path.exists():
        raw=launch_path.read_bytes();launch_path.with_name(launch_path.stem+'.'+hashlib.sha256(raw).hexdigest()[:12]+'.json').write_bytes(raw)
    atomic_json(launch_path,{'at':stamp(),'provider':provider,'native_id':native,'reservation':reservation,
        'expected_owner_after_exec':dict(identity(os.getpid()).json(),executable=str(binary)),'argv':args})
    environment=launch_environment(provider);environment.update(NPEER_HOME=str(STATE),PYTHONPATH=str(ROOT),TERM='xterm-256color')
    os.chdir(ROOT if provider=='antigravity' else ROOT/'evidence/scratch/live-workspace');budget.admit(reservation);os.execve(binary,args,environment)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','launch','parent-case']);parser.add_argument('provider',nargs='?',choices=['claude','codex','antigravity']);args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='launch':launch(args.provider)
    else:asyncio.run(parent_case(args.provider))
