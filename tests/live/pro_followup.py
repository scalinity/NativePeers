#!/usr/bin/env python3
"""One explicit, bounded Sol/Flash native return check; never run by pytest."""
import argparse,asyncio,json,os,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
RUN=ROOT/'evidence/private/pro-followup-20261022/native'
from tests.live import pro_review as base
base.RUN=RUN;base.STATE=RUN/'state';base.FIXTURE=RUN/'fixture'
from nativepeers.budget import LiveBudget
from nativepeers.caller import bind_caller
from nativepeers.core import Core
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.launch import launch_environment
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.profiles import config_arguments


async def parent(provider):
    journal=Journal(base.STATE);core=Core(journal);groups=[]
    try:
        caller=bind_caller(journal,core.registry,allow_external=False)
        peer=core.registry.verify(caller.key,caller.incarnation)
        assert peer['peer_key']['provider']==provider
        children=['codex','antigravity'] if provider=='codex' else ['codex']
        workers=[]
        for child in children:
            spec={'provider':child,'title':'NP Pro2 '+provider+' '+child,'model':base.MODELS[child],
                  'task':'Use the NativePeers task_status tool with no handle to obtain your own UUID. Then call peer_inbox with unread=true, paged=true, limit=1. Return NP-PRO2-CHILD, your exact UUID and 42. Do not start agents, send messages or edit files.',
                  'permission_profile':'analysis','workspace':{'mode':'inherit-read-only'}}
            if child=='codex':spec['reasoning_effort']='xhigh'
            workers.append(spec)
        group=(await core.delegation.spawn(caller,{'client_request_id':'pro2-'+provider,'workers':workers,'completion_mode':'wait',
            'limits':{'max_concurrent_workers':len(workers),'max_depth':1,'wall_time_seconds':50,'max_task_turns_per_worker':4,'followup_grace_seconds':0}}))['group_id'];groups.append(group)
        async with asyncio.timeout(70):
            while True:
                output=await core.delegation.wait(caller,group,30000,'retain')
                if output['ready']:break
        results=output['result']['results']
        assertions={'all_completed':len(results)==len(workers) and all(r['status']=='completed' for r in results),
            'distinct_native_ids':len({r['worker']['native_session_id'] for r in results})==len(workers),
            'own_uuid_reported':all(r['worker']['native_session_id'] in r['summary'] and 'NP-PRO2-CHILD' in r['summary'] and '42' in r['summary'] for r in results),
            'original_parent':all(r['worker']['native_session_id']!=peer['peer_key']['native_session_id'] for r in results),
            'models':all(r['model_observed']==base.MODELS[r['worker']['provider']] for r in results),
            'wait_claim':journal.one('SELECT state FROM completion_routes WHERE group_id=?',(group,))['state']=='NOTIFICATION_UNKNOWN'}
        if len(results)>1:
            starts=[journal.one("SELECT at FROM events WHERE subject_id=? AND kind='native-task-started'",(r['task_id'],)) for r in results]
            assertions['overlap']=all(starts) and max(e['at'] for e in starts)<min(r['completed_at'] for r in results)
        record={'status':'PASS' if all(assertions.values()) else 'FAIL','parent_native_id':peer['peer_key']['native_session_id'],
                'caller':caller.key,'provider':provider,'source':base.source(),'assertions':assertions,'group_id':group,'results':results}
        atomic_json(RUN/(provider+'-case.json'),record)
        print(json.dumps({'status':record['status'],'parent_native_id':record['parent_native_id'],'assertions':assertions,'children':[{'native_id':r['worker']['native_session_id'],'model':r['model_observed'],'summary':r['summary']} for r in results]}),flush=True)
    finally:
        for group in groups:
            try:await core.delegation.cancel(caller,group,'Pro2 native test settled')
            except Exception:pass
        journal.close()


def launch(provider):
    prepared=json.loads((RUN/'prepared.json').read_text());assert prepared['source']==base.source()
    binary=Path(prepared['providers'][provider]['path'])
    command=f'{ROOT}/.venv/bin/python {ROOT}/tests/live/pro_followup.py parent {provider}'
    prompt=f'''Authorized bounded NativePeers verification. Run this exact command ONCE through your native shell tool:
{command}
Wait for that same tool process and collect its output. It runs {'two' if provider=='codex' else 'one'} small native fixture workers. Do not retry, spawn native subagents, edit configuration, send peer messages or solve worker tasks yourself. After the command returns, report NP-PRO2-PARENT, its actual parent UUID, child UUIDs, observed models and assertion verdict, then end your turn.'''
    args=[str(binary),'--name','NP Pro2 Sol Parent','--dangerously-bypass-approvals-and-sandbox',*config_arguments(json.loads((RUN/'overrides.json').read_text())),prompt] if provider=='codex' else [str(binary),'--model',base.MODELS[provider],'--dangerously-skip-permissions','--prompt-interactive',prompt]
    budget=LiveBudget(base.LEDGER);reservation=budget.reserve('Pro2 '+provider+' original parent',tranche='pro_followup');budget.admit(reservation)
    atomic_json(RUN/(provider+'-launch.json'),{'reservation':reservation,'owner_expected_after_exec':dict(identity(os.getpid()).json(),executable=str(binary))})
    environment=launch_environment(provider);environment.update(NPEER_HOME=str(base.STATE),PYTHONPATH=str(ROOT),TERM='xterm-256color')
    os.chdir(ROOT if provider=='antigravity' else base.FIXTURE);os.execve(binary,args,environment)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','launch','parent']);parser.add_argument('provider',nargs='?',choices=['codex','antigravity']);args=parser.parse_args()
    if args.action=='prepare':asyncio.run(base.prepare())
    elif args.action=='parent':asyncio.run(parent(args.provider))
    else:launch(args.provider)
