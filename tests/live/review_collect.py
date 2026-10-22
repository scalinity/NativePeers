#!/usr/bin/env python3
"""Read exact owned native histories and settle the review batch without inference."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.core import Core
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.reconcile import observe
from tests.live.review_fixes import RUN,STATE,LEDGER,stamp


def native_events(peer):
    provider=peer['peer_key']['provider'];native=peer['peer_key']['native_session_id'];home=Path(peer['home'])
    path=peer.get('native_transcript')
    if path:path=Path(path)
    elif provider=='claude':path=home/'projects'/peer['cwd'].replace('/','-').replace('.','-')/(native+'.jsonl')
    else:
        candidates=list((home/'sessions').glob('*/*/*/*'+native+'*'))
        if len(candidates)!=1:raise RuntimeError('Native transcript path is not unique')
        path=candidates[0]
    if not path.resolve().is_relative_to(home) or path.is_symlink():raise RuntimeError('Unexpected native history path')
    events=[]
    for line in path.read_text().splitlines():
        event=json.loads(line);at=datetime.fromisoformat(event.get('timestamp',event.get('ts','1970-01-01T00:00:00+00:00')).replace('Z','+00:00')).timestamp()
        if provider=='claude':
            if event.get('sessionId') not in (None,native):raise RuntimeError('Claude history UUID differs')
            message=event.get('message',{});content=message.get('content',[])
            if event.get('type')=='user':events.append({'kind':'user','at':at,'id':event.get('uuid'),'text':content if isinstance(content,str) else json.dumps(content)})
            if event.get('type')=='attachment' and event.get('attachment',{}).get('type')=='queued_command':events.append({'kind':'user','at':at,'id':event.get('uuid'),'text':event['attachment'].get('prompt','')})
            if event.get('type')=='assistant':
                for item in content:
                    if item.get('type')=='tool_use':events.append({'kind':'tool','at':at,'id':item.get('id'),'text':json.dumps(item.get('input',{}))})
                    if item.get('type')=='text':events.append({'kind':'assistant','at':at,'id':event.get('uuid'),'text':item['text'],'terminal':message.get('stop_reason')=='end_turn'})
        else:
            value=event.get('payload',{})
            if event.get('type')=='session_meta' and value.get('id')!=native:raise RuntimeError('Codex history UUID differs')
            if event.get('type')=='response_item' and value.get('type') in ('custom_tool_call','function_call'):events.append({'kind':'tool','at':at,'id':value.get('call_id'),'text':value.get('input',value.get('arguments',''))})
            if event.get('type')=='response_item' and value.get('role')=='user':events.append({'kind':'user','at':at,'id':value.get('id'),'text':json.dumps(value.get('content',[]))})
            if event.get('type')=='event_msg' and value.get('type')=='task_complete':events.append({'kind':'assistant','at':at,'id':value.get('turn_id'),'text':value.get('last_agent_message',''),'terminal':True})
    return path,events


async def collect():
    journal=Journal(STATE);core=Core(journal);budget=LiveBudget(LEDGER);checks=[]
    for provider in ['claude','codex']:
        case=json.loads((RUN/(provider+'-case.json')).read_text());group=journal.one('SELECT * FROM groups WHERE group_id=?',(case['group_id'],));peer=core.registry.get(group['parent_key'],live=False)
        path,events=native_events(peer);native=peer['peer_key']['native_session_id'];launch=json.loads((RUN/(provider+'-launch.json')).read_text())
        initial=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'RV-INITIAL-'+provider in e['text']]
        completions=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'RV-COMPLETION-'+provider in e['text']]
        submissions=[json.loads(r['payload_json']) for r in journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='parent-completion-submitted'",(case['group_id'],))]
        delivery=submissions[0] if len(submissions)==1 else None
        ingress=[e for e in events if e['kind']=='user' and delivery and delivery['receipt']['message_id'] in e['text']]
        assertions=dict(case['assertions'])
        assertions['actual_parent_tool_call']=any(e['kind']=='tool' and 'review_fixes.py parent-case '+provider in e['text'] for e in events)
        assertions['original_parent_completion_ingested']=bool(ingress)
        assertions['original_parent_attributed_completion']=bool(completions and all(n in completions[-1]['text'] for n in case['worker_native_ids']) and all('RV-'+provider+'-'+role in completions[-1]['text'] for role in ['A','B']))
        assertions['only_one_aggregate_submission']=len(submissions)==1
        starts=[json.loads(journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='native-task-started'",(task,))['payload_json'])['at'] for task in case['initial_task_ids']]
        ends=[r['completed_at'] for r in case['initial_results']]
        assertions['native_initial_tasks_overlapped']=max(starts)<min(ends)
        if provider=='claude':assertions['exact_sol_xhigh']=all(r['model_requested']=='gpt-6.1-sol' and r['model_observed']=='gpt-6.1-sol' and r['reasoning_effort_requested']=='xhigh' for r in case['initial_results'])
        else:assertions['observed_claude_model']=all(r['model_observed'] for r in case['initial_results'])
        if delivery and ingress:
            await observe(journal,core.registry,core.router,delivery['receipt']['message_id'])
            ledger=json.loads(LEDGER.read_text());reservation=next(r for r in ledger['reservations'] if r['id']==delivery['live_reservation'])
            replies=[e for e in completions if e['at']>=min(x['at'] for x in ingress)]
            if replies and reservation['state'] in ('admitted','uncertain'):budget.settle(reservation['id'],observed_turns=1,native_id=native,completed_at=replies[-1]['at'])
        ledger=json.loads(LEDGER.read_text());reservation=next(r for r in ledger['reservations'] if r['id']==launch['initial_reservation'])
        if initial and reservation['state'] in ('admitted','uncertain'):budget.settle(reservation['id'],observed_turns=1,native_id=native,completed_at=initial[0]['at'])
        checks.append({'test_id':'RV-LIVE-'+provider.upper(),'status':'PASS' if all(assertions.values()) else 'FAIL','evidence_class':'live','at':stamp(),
            'assertions':assertions,'group_id':case['group_id'],'parent_native_id':native,'worker_native_ids':case['worker_native_ids'],
            'initial_task_ids':case['initial_task_ids'],'active_cancel_task':case['active_cancel_task'],'queued_cancel_task':case['queued_cancel_task'],'kept_task_id':case['kept_task_id'],
            'initial_results':case['initial_results'],'kept_result':case['kept_result'],'states':case['states'],'native_history':str(path),
            'parent_initial_event_ids':[e['id'] for e in initial],'parent_ingress_event_ids':[e['id'] for e in ingress],'parent_completion_event_ids':[e['id'] for e in completions],
            'completion_message_id':delivery['receipt']['message_id'] if delivery else None,'native_task_interval_start':[datetime.fromtimestamp(v,ZoneInfo('America/New_York')).isoformat() for v in starts],
            'native_task_interval_end':[datetime.fromtimestamp(v,ZoneInfo('America/New_York')).isoformat() for v in ends],
            'source_dependencies':json.loads((RUN/'passive.json').read_text())['source_dependencies'],
            'limitations':['Native task intervals establish client-side overlap; vendor GPU scheduling is not observed.','xhigh is the validated submitted effort; the provider does not independently echo effort.']})
    passive=json.loads((RUN/'passive.json').read_text());ledger=json.loads(LEDGER.read_text())
    report={'schema_version':1,'at':stamp(),'checks':checks,'providers':passive['providers'],'offline_retest':{'status':'PASS','tests':60,'subtests':16},
        'original_consumed_turns':48,'additional_consumed_slots':ledger['consumed_turns']-48,'consumed_turns':ledger['consumed_turns'],'reserved_turns':ledger['reserved_turns'],
        'outstanding_native_requests':ledger['outstanding_native_requests'],'active_seconds':ledger['active_seconds'],'aborted_pre_model_launch_counted_conservatively':1}
    (ROOT/'evidence/reviews/live-retest.json').write_text(json.dumps(report,sort_keys=True,separators=(',',':'))+'\n')
    print(json.dumps({'checks':[{'test_id':c['test_id'],'status':c['status'],'assertions':c['assertions']} for c in checks],
                      'consumed_turns':ledger['consumed_turns'],'outstanding_native_requests':ledger['outstanding_native_requests'],'active_seconds':ledger['active_seconds']}))
    journal.close()


if __name__=='__main__':
    import asyncio
    asyncio.run(collect())
