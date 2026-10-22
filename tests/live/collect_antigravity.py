#!/usr/bin/env python3
"""Read the current owned native batch, reconcile receipts and settle its ledger."""
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.core import Core
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.reconcile import observe
from tests.live.antigravity_paths import RUN,STATE,LEDGER,stamp
from tests.live.review_collect import native_events


def antigravity_events(peer):
    path=Path(peer['native_transcript']);events=[]
    assert path.resolve().is_relative_to(Path(peer['home'])) and not path.is_symlink()
    for line in path.read_text().splitlines():
        value=json.loads(line);at=datetime.fromisoformat(value['created_at'].replace('Z','+00:00')).timestamp()
        content=value.get('content','')
        if value.get('type')=='USER_INPUT':events.append({'kind':'user','at':at,'id':value['step_index'],'text':content})
        if value.get('type')=='PLANNER_RESPONSE':
            for tool in value.get('tool_calls',[]):events.append({'kind':'tool','at':at,'id':value['step_index'],'text':json.dumps(tool)})
            if content and not value.get('tool_calls'):events.append({'kind':'assistant','at':at,'id':value['step_index'],'text':content,'terminal':True})
    return path,events


async def collect():
    journal=Journal(STATE);core=Core(journal);budget=LiveBudget(LEDGER);checks=[]
    for provider in ('claude','codex','antigravity'):
        case=json.loads((RUN/(provider+'-case.json')).read_text());launch=json.loads((RUN/(provider+'-launch.json')).read_text())
        row=journal.one('SELECT descriptor_json FROM peer_instances WHERE native_id=? AND pid=?',
                        (case['parent_native_id'],launch['expected_owner_after_exec']['pid']))
        if not row:raise RuntimeError(provider+': current case does not match the owned launch')
        peer=json.loads(row['descriptor_json']);path,events=(antigravity_events(peer) if provider=='antigravity' else native_events(peer))
        finals=[e for e in events if e['kind']=='assistant' and e.get('terminal')]
        initial=[e for e in finals if 'NP-'+provider+'-PARENT-RESULT' in e['text']]
        assertions=dict(case.get('assertions',{}));assertions['actual_parent_tool_call']=any(e['kind']=='tool' and 'antigravity_paths.py parent-case '+provider in e['text'] for e in events)
        ids=[r['worker']['native_session_id'] for r in case.get('results',[])]
        assertions['original_parent_reported_children']=bool(initial and ids and all(n in initial[-1]['text'] for n in ids))
        assertions['case_completed']=case.get('status')=='PASS'
        if provider=='antigravity':assertions['mcp_bound_in_actual_native_context']=case.get('actual_native_context_mcp_binding',{}).get('native_session_id')==case['parent_native_id']
        push=None
        if provider=='claude':
            submissions=journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='parent-completion-submitted'",(case['group_id'],))
            push=json.loads(submissions[0]['payload_json']) if len(submissions)==1 else None
            ingress=[e for e in events if e['kind']=='user' and push and push['receipt']['message_id'] in e['text']]
            reports=[e for e in finals if 'NP-claude-PUSH-RESULT' in e['text']]
            assertions['one_aggregate_submission']=len(submissions)==1
            assertions['original_parent_push_ingested']=bool(ingress)
            assertions['original_parent_push_attributed']=bool(reports and all(n in reports[-1]['text'] for n in ids))
            implementation_ids=[r['worker']['native_session_id'] for r in case.get('implementation_results',[])]
            assertions['original_parent_reported_implementations']=bool(initial and len(implementation_ids)==3 and all(n in initial[-1]['text'] for n in implementation_ids))
            if push and ingress and reports:
                await observe(journal,core.registry,core.router,push['receipt']['message_id'])
                ledger=json.loads(LEDGER.read_text());reservation=next(r for r in ledger['reservations'] if r['id']==push['live_reservation'])
                if reservation['state'] in ('admitted','uncertain'):budget.settle(reservation['id'],observed_turns=1,native_id=case['parent_native_id'],completed_at=reports[-1]['at'])
        if initial:
            ledger=json.loads(LEDGER.read_text());reservation=next(r for r in ledger['reservations'] if r['id']==launch['reservation'])
            if reservation['state'] in ('admitted','uncertain'):budget.settle(reservation['id'],observed_turns=1,native_id=case['parent_native_id'],completed_at=initial[-1]['at'])
        checks.append({'provider':provider,'status':'PASS' if assertions and all(assertions.values()) else 'FAIL','assertions':assertions,
                       'parent_native_id':case['parent_native_id'],'group_id':case['group_id'],'worker_native_ids':ids,'results':case.get('results',[]),
                       'implementation_group_id':case.get('implementation_group_id'),'implementation_results':case.get('implementation_results',[]),
                       'native_tool_gate_events':case.get('native_tool_gate_events',[]),'native_history':str(path),
                       'source_dependencies':case.get('source_dependencies',{}),'initial_native_event_ids':[e['id'] for e in initial],
                       'completion_message_id':push['receipt']['message_id'] if push else None})
    ledger=json.loads(LEDGER.read_text());report={'at':stamp(),'checks':checks,'live_budget':{k:ledger[k] for k in ('consumed_turns','reserved_turns','active_seconds','outstanding_native_requests')}}
    atomic_json(RUN/'report.json',report);print(json.dumps({'checks':[{'provider':c['provider'],'status':c['status'],'failed':[k for k,v in c['assertions'].items() if not v]} for c in checks],'live_budget':report['live_budget']}))
    journal.close()


if __name__=='__main__':asyncio.run(collect())
