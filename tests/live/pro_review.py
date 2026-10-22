#!/usr/bin/env python3
"""Explicit native reacceptance for the Pro review; never part of offline pytest."""
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
from nativepeers.adapters.codex_app_server import AppServer
from nativepeers.budget import LiveBudget
from nativepeers.caller import bind_caller
from nativepeers.compatibility import fingerprint
from nativepeers.core import Core
from nativepeers.files import atomic_json,private_directory
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id
from nativepeers.process import identity,matches
from nativepeers.profiles import config_arguments
from nativepeers.worktrees import git,snapshot

RUN=Path(os.environ.get('NPEER_PRO_REVIEW_RUN',ROOT/'evidence/private/pro-review-20261022/native')).resolve()
assert RUN.is_relative_to(ROOT/'evidence/private/pro-review-20261022'),'Live run must stay in its private project folder'
STATE=RUN/'state';FIXTURE=RUN/'fixture';LEDGER=ROOT/'evidence/live-budget.json'
MODELS={'codex':'gpt-6.1-sol','antigravity':'gemini-3.8-flash-medium'}


def stamp():return datetime.now(ZoneInfo('America/New_York')).isoformat()

def source():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}


async def prepare():
    private_directory(RUN);FIXTURE.mkdir()
    (FIXTURE/'base.py').write_text('VALUE = 1\n')
    git(FIXTURE,'init','-q');git(FIXTURE,'add','base.py');git(FIXTURE,'commit','-qm','Create reliability verification fixture')
    (FIXTURE/'base.py').write_text('VALUE = 2\n')
    (FIXTURE/'brief.txt').write_text('VALUE=7\n')
    journal=Journal(STATE)
    try:
        production=json.loads((Path.home()/'.local/state/nativepeers/compat/runtime.json').read_text())
        providers={}
        for provider,relative in [('codex','.local/share/codex-mcp-manager/current/bin/codex'),('antigravity','.local/bin/agy')]:
            info=fingerprint(Path.home()/relative)
            assert info['sha256']==production['providers'][provider]['sha256'],provider+' executable changed; renew affected passive gates first'
            providers[provider]=info
        atomic_json(STATE/'compat/runtime.json',production)
        atomic_json(STATE/'test-budget.json',{'path':str(LEDGER),'tranche':'pro_review'})
        atomic_json(STATE/'policy.json',{'task_wall_seconds':180,'group_wall_seconds':240,'followup_grace_seconds':3})
        import tomllib
        config=tomllib.loads((Path.home()/'.codex/config.toml').read_text())
        overrides={'mcp_server_policy':{name:{'enabled':False} for name in config.get('mcp_servers',{})},
            'plugins':{name:{'enabled':False} for name in config.get('plugins',{})},
            'features.multi_agent':False,'features.multi_agent_v2':False,'features.js_repl':False,'web_search':'disabled',
            'memories.generate_memories':False,'memories.use_memories':False,
            'model':'gpt-6.1-sol','model_reasoning_effort':'xhigh','projects':{str(FIXTURE):{'trust_level':'trusted'}}}
        overrides['mcp_server_policy'].update({'codex_apps':{'enabled':False},'openai-api-key-local-confirmation':{'enabled':False}})
        client=AppServer(Path(providers['codex']['path']))
        try:
            await client.start();catalog=(await client.call('model/list',{'includeHidden':True,'limit':100}))['data']
            model=next(m for m in catalog if m['model']==MODELS['codex']);assert 'xhigh' in [e['reasoningEffort'] for e in model['supportedReasoningEfforts']]
            listing=await client.call('hooks/list',{'cwd':str(FIXTURE)})
            hooks=[h for row in listing['data'] for h in row['hooks']]
            own=[h for h in hooks if h.get('command','').startswith(str(ROOT/'bin/npeer-hook'))];assert len(own)==2
            overrides['hooks.state']={h['key']:({'enabled':True,'trusted_hash':h['currentHash']} if h in own else {'enabled':False}) for h in hooks}
        finally:await client.close()
        from nativepeers.antigravity import catalog as agy_catalog
        catalog=await agy_catalog(Path(providers['antigravity']['path']));assert MODELS['antigravity'] in catalog
        atomic_json(RUN/'overrides.json',overrides)
        atomic_json(RUN/'prepared.json',{'at':stamp(),'providers':providers,'source':source(),'models':MODELS,'model_inputs':0})
        print('Prepared native fixture and verified catalogs; no model input submitted.')
    finally:journal.close()


async def parent(provider):
    journal=Journal(STATE);core=Core(journal)
    caller=bind_caller(journal,core.registry,allow_external=False);peer=core.registry.verify(caller.key,caller.incarnation)
    assert peer['peer_key']['provider']==provider
    before=snapshot(FIXTURE,untracked_allowlist=['brief.txt']);editing=provider=='codex'
    case_path=RUN/(provider+'-case.json')
    prior=json.loads(case_path.read_text()) if case_path.exists() else None
    if prior:
        assert prior['caller']==caller.key and prior['source']==source(),'Continuation must keep the original caller and product source'
    record=prior or {'at':stamp(),'provider':provider,'parent_native_id':peer['peer_key']['native_session_id'],'caller':caller.key,'source':source(),'groups':[]}
    if record.get('error'):record.setdefault('prior_harness_errors',[]).append(record.pop('error'))
    record.pop('status',None)
    atomic_json(case_path,record)
    try:
        from nativepeers.artifacts import Artifacts
        imported=None if record['groups'] else Artifacts(journal).add_file(FIXTURE/'brief.txt',caller)
        workers=[]
        for child in ('codex','antigravity'):
            task=('Read '+str(FIXTURE/'brief.txt')+' and report its exact contents and product VALUE * 6. '
                  'Do not start agents or send peer messages. ')
            if editing:
                task+='Create only answer_'+child+'.py defining answer() returning VALUE * 6 with VALUE taken from brief.txt. Run an actual Python check that answer()==42. Do not commit or modify base.py. '
            else:
                task+='Call the namespaced NativePeers task_status tool without a handle to report your verified native UUID. Use native read tools only. '
            task+='Report NP-PRO-'+provider+'-'+child+' with the result and actual checks.'
            worker={'provider':child,'title':'NP Pro '+provider+' '+child,'task':task,'model':MODELS[child],
                    'permission_profile':'workspace' if editing else 'analysis',
                    'workspace':{'mode':'isolated-worktree','untracked_allowlist':['brief.txt']} if editing else {'mode':'inherit-read-only'},
                    'context_refs':[imported['artifact_id']] if imported else []}
            if child=='codex':worker['reasoning_effort']='xhigh'
            workers.append(worker)
        spec={'schema_version':1,'client_request_id':'pro-'+provider+'-initial','workers':workers,'completion_mode':'wait',
              'limits':{'max_concurrent_workers':2,'max_depth':1,'wall_time_seconds':180,'max_task_turns_per_worker':6,'followup_grace_seconds':3}}
        if record['groups']:
            original=journal.one('SELECT policy_json FROM groups WHERE group_id=?',(record['groups'][0],))
            spec=json.loads(original['policy_json'])['original_request']
            lease=json.loads(journal.one('SELECT lease_json FROM workers WHERE group_id=? LIMIT 1',(record['groups'][0],))['lease_json'])
            before={k:v for k,v in lease['baseline'].items() if k!='input_tree'} if editing else before
        status=await core.delegation.spawn(caller,spec);group=status['group_id']
        if group not in record['groups']:record['groups'].append(group)
        atomic_json(case_path,record)
        # Local observation spends no additional model input. Exercise the public terminal fast path.
        async with asyncio.timeout(210):
            while any(t['state'] not in ('COMPLETED','FAILED','CANCELED','TIMED_OUT','PARTIAL','INTERRUPTED') for t in core.delegation.status(caller,group)['tasks']):await asyncio.sleep(.1)
        value=await core.tool(caller,'task_wait',{'handle':group,'timeout_ms':30000,'on_timeout':'retain'})
        assert value['ready'];results=value['result']['results'];record['results']=results
        assertions={'two_results':len(results)==2,'completed':all(r['status']=='completed' for r in results),
            'distinct_native_ids':len({r['worker']['native_session_id'] for r in results})==2,
            'original_parent_distinct':all(r['worker']['native_session_id']!=record['parent_native_id'] for r in results),
            'source_preserved':snapshot(FIXTURE,untracked_allowlist=['brief.txt'])==before,
            'terminal_wait_claimed':journal.one('SELECT state FROM completion_routes WHERE group_id=?',(group,))['state']=='NOTIFICATION_UNKNOWN',
            'no_automatic_push':not journal.one("SELECT sequence FROM events WHERE subject_id=? AND kind='parent-completion-submitted'",(group,))}
        starts=[]
        for result in results:
            child=result['worker']['provider'];assertions[child+'_model']=result['model_observed']==MODELS[child]
            assertions[child+'_finding']='42' in result['summary'] and 'NP-PRO-'+provider+'-'+child in result['summary']
            event=journal.one("SELECT at FROM events WHERE subject_id=? AND kind='native-task-started'",(result['task_id'],));starts.append(event['at'] if event else result['started_at'])
            if editing:
                changes=result['changes'];assertions[child+'_delta']=bool(changes.get('worker_delta_artifact'))
                code=Path(changes['worktree'])/('answer_'+child+'.py');namespace={'__file__':str(code),'__name__':'nativepeers_fixture'};exec(compile(code.read_text(),str(code),'exec'),namespace);assertions[child+'_code']=namespace['answer']()==42
                assertions[child+'_untracked_manifest']='brief.txt' in changes['baseline']['untracked_inputs']
        assertions['overlapping_native_tasks']=max(starts)<min(r['completed_at'] for r in results)
        record['assertions']=assertions
        # Exact released native history resume, without a second writer.
        if editing and all(assertions.values()):
            row=journal.one("SELECT * FROM workers WHERE group_id=? AND provider='antigravity'",(group,))
            async with asyncio.timeout(30):
                while journal.one('SELECT state FROM workers WHERE worker_id=?',(row['worker_id'],))['state']!='RELEASED_RESUMABLE':await asyncio.sleep(.1)
                while matches(json.loads((STATE/'workers'/(row['worker_id']+'.json')).read_text())['owner']):await asyncio.sleep(.1)
            from nativepeers.recovery import resume_worker
            resumed=record.get('resumed') or await resume_worker(journal,caller,row['worker_id'],'Read answer_antigravity.py. Return NP-PRO-RESUMED and 42; do not edit or launch other work.','wait')
            record['resumed']=resumed;atomic_json(RUN/(provider+'-case.json'),record)
            async with asyncio.timeout(210):
                while True:
                    waited=await core.delegation.wait(caller,resumed['task_id'],30000,'retain')
                    if waited['ready']:break
            followup=core.delegation.result(caller,resumed['task_id'])['results'][0];record['resume_result']=followup
            assertions['exact_resume']=followup['worker']['native_session_id']==row['native_id'] and followup['model_observed']==MODELS['antigravity'] and followup['status']=='completed' and 'NP-PRO-RESUMED' in followup['summary']
        record['status']='PASS' if all(assertions.values()) else 'FAIL'
        print(json.dumps({'status':record['status'],'parent_native_id':record['parent_native_id'],'assertions':assertions,
            'children':[{'native_id':r['worker']['native_session_id'],'model':r['model_observed'],'summary':r['summary']} for r in results]}),flush=True)
    except BaseException as error:
        record.update(status='FAIL',error=type(error).__name__+': '+str(error));raise
    finally:
        for group in record['groups']:
            try:await core.delegation.cancel(caller,group,'Pro-review fixture cleanup')
            except Exception as error:record.setdefault('cleanup_errors',[]).append(str(error))
        atomic_json(RUN/(provider+'-case.json'),record);journal.close()


async def push():
    journal=Journal(STATE);core=Core(journal)
    try:
        caller=bind_caller(journal,core.registry,allow_external=False)
        case=json.loads((RUN/'codex-case.json').read_text());assert caller.key==case['caller'] and case['status']=='PASS'
        path=RUN/'push.json';assert not path.exists(),'Push test is one-shot; inspect its existing operation instead of replaying'
        row=journal.one("SELECT * FROM workers WHERE group_id=? AND provider='antigravity'",(case['groups'][0],))
        from nativepeers.recovery import resume_worker
        resumed=await resume_worker(journal,caller,row['worker_id'],'Read answer_antigravity.py. Return NP-PRO-PUSH-CHILD and 42. Do not edit files, send peer messages or start agents.','push')
        atomic_json(path,{'at':stamp(),'parent_native_id':case['parent_native_id'],'caller':caller.key,'worker_native_id':row['native_id'],'source':source(),**resumed})
        print(json.dumps(dict(resumed,parent_native_id=case['parent_native_id'],worker_native_id=row['native_id'],state='DISPATCHED; wait for native controller completion')))
    finally:journal.close()


def launch(provider):
    prepared=json.loads((RUN/'prepared.json').read_text());binary=Path(prepared['providers'][provider]['path'])
    assert fingerprint(binary)['sha256']==prepared['providers'][provider]['sha256']
    assert source()==prepared['source'],'Source changed after preparation'
    command=f'NPEER_PRO_REVIEW_RUN={RUN} {ROOT}/.venv/bin/python {ROOT}/tests/live/pro_review.py parent {provider}'
    prompt=f'''Authorized bounded NativePeers reliability test. Run this exact command ONCE through your native shell tool:
{command}
Allow this same tool process up to 480 seconds and collect its output if it runs in the background. It launches two native fixture workers and, for Codex, resumes one released Gemini history. Do not start vendor-native agents, retry commands, answer child tasks yourself or change configuration. After completion, report NP-PRO-{provider}-PARENT with the actual parent UUID, two child UUIDs, observed models, result 42 and actual assertion verdict. End your turn.'''
    args=[str(binary),'--name','NP Pro Sol Parent','--dangerously-bypass-approvals-and-sandbox',*config_arguments(json.loads((RUN/'overrides.json').read_text())),prompt] if provider=='codex' else [str(binary),'--model',MODELS[provider],'--dangerously-skip-permissions','--prompt-interactive',prompt]
    budget=LiveBudget(LEDGER);reservation=budget.reserve('Pro review '+provider+' parent input',tranche='pro_review');budget.admit(reservation)
    atomic_json(RUN/(provider+'-launch.json'),{'at':stamp(),'reservation':reservation,'owner_expected_after_exec':dict(identity(os.getpid()).json(),executable=str(binary))})
    env=launch_environment(provider);env.update(NPEER_HOME=str(STATE),NPEER_PRO_REVIEW_RUN=str(RUN),PYTHONPATH=str(ROOT),TERM='xterm-256color')
    os.chdir(ROOT if provider=='antigravity' else FIXTURE);os.execve(binary,args,env)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','launch','parent','push']);parser.add_argument('provider',nargs='?',choices=['codex','antigravity']);args=parser.parse_args()
    if args.action=='prepare':asyncio.run(prepare())
    elif args.action=='parent':asyncio.run(parent(args.provider))
    elif args.action=='push':asyncio.run(push())
    else:launch(args.provider)
