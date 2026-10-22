#!/usr/bin/env python3
"""Read exact native evidence and settle only this bounded verification batch."""
import json,sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from tests.live.pro_followup import RUN,base
from tests.live.pro_review_collect import json_objects
from tests.live.review_collect import native_events
from tests.live.collect_antigravity import antigravity_events
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.registry import Registry


def history(peer):
    if peer['peer_key']['provider']=='antigravity' and not peer.get('native_transcript'):
        from uuid import UUID
        peer=dict(peer,native_transcript=str(Path(peer['home'])/'brain'/str(UUID(peer['peer_key']['native_session_id']))/'.system_generated/logs/transcript.jsonl'))
    return antigravity_events(peer) if peer['peer_key']['provider']=='antigravity' else native_events(peer)


def success(value,tool,native):
    for obj in json_objects(value):
        data=obj.get('data')
        if obj.get('ok') is not True or not isinstance(data,dict):continue
        if tool=='task_status' and data.get('state')=='BOUND' and data.get('native_session_id')==native:return True
        if tool=='peer_inbox' and isinstance(data.get('messages'),list) and 'next_cursor' in data:return True
    return False


def tools(peer,result):
    path,_=history(peer);provider=peer['peer_key']['provider'];pending={};found={}
    if provider=='antigravity' and path.with_name('transcript_full.jsonl').exists():path=path.with_name('transcript_full.jsonl')
    for line in path.read_text().splitlines():
        event=json.loads(line);stamp=event.get('created_at') if provider=='antigravity' else event.get('timestamp',event.get('ts'))
        if not stamp:continue
        at=datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
        if not result['started_at']<=at<=result['completed_at']+1:continue
        if provider=='codex':
            payload=event.get('payload',{});item=payload.get('item',{});tool=item.get('tool')
            if event.get('type')=='event_msg' and payload.get('type')=='item_completed' and payload.get('thread_id')==peer['peer_key']['native_session_id'] and item.get('type')=='McpToolCall' and item.get('server')=='nativepeers' and item.get('status')=='completed' and tool in ('task_status','peer_inbox'):
                args=item.get('arguments',{})
                if tool=='peer_inbox' and not (args.get('unread') is True and args.get('paged') is True):continue
                if success(item.get('result'),tool,peer['peer_key']['native_session_id']):found[tool]=item['id']
        else:
            index=event.get('step_index')
            if event.get('type')=='PLANNER_RESPONSE':
                for call in event.get('tool_calls',[]):
                    function=call.get('function',call);name=function.get('name','');args=function.get('args',function.get('arguments',{}))
                    if isinstance(args,str):args=json.loads(args)
                    tool=None;values={}
                    if name=='call_mcp_tool' and isinstance(args,dict) and args.get('ServerName')=='nativepeers':
                        tool=args.get('ToolName');values=args.get('Arguments',{})
                    elif isinstance(name,str) and 'nativepeers' in name:
                        tool=next((t for t in ('task_status','peer_inbox') if name.endswith('_'+t)),None);values=args
                    if tool is not None:
                        if isinstance(values,str):values=json.loads(values)
                        if tool in ('task_status','peer_inbox') and (tool!='peer_inbox' or values.get('unread') is True and values.get('paged') is True):pending[index+1]=tool
            if index in pending and event.get('type') in ('GENERIC','MCP_TOOL') and event.get('status')=='DONE':
                tool=pending[index]
                if success(event.get('content'),tool,peer['peer_key']['native_session_id']):found[tool]=index
    return {'history':str(path),'successful_calls':found}


def collect():
    journal=Journal(base.STATE);registry=Registry(journal);budget=LiveBudget(base.LEDGER);checks=[]
    try:
        for provider in ('codex','antigravity'):
            path=RUN/(provider+'-case.json')
            if not path.exists():continue
            case=json.loads(path.read_text());launch=json.loads((RUN/(provider+'-launch.json')).read_text());peer=registry.get(case['caller'],live=False)
            parent_history,events=history(peer);finals=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'NP-PRO2-PARENT' in e['text']]
            assertions=dict(case['assertions']);ids=[r['worker']['native_session_id'] for r in case['results']]
            assertions.update(current_source=case['source']==base.source(),case_passed=case['status']=='PASS',
                exact_parent=peer['owner']['pid']==launch['owner_expected_after_exec']['pid'] and peer['peer_key']['native_session_id']==case['parent_native_id'],
                actual_parent_tool=any(e['kind']=='tool' and 'pro_followup.py parent '+provider in e['text'] for e in events),
                original_parent_report=bool(finals and all(native in finals[-1]['text'] for native in ids)))
            evidence={}
            for result in case['results']:
                native=result['worker']['native_session_id']
                evidence[native]=tools(registry.get(result['worker']['peer_key'],live=False),result)
                assertions[native+'_native_mcp']=all(t in evidence[native]['successful_calls'] for t in ('task_status','peer_inbox'))
            reservation=next(r for r in json.loads(base.LEDGER.read_text())['reservations'] if r['id']==launch['reservation'])
            if finals and reservation['state'] in ('admitted','uncertain'):
                budget.settle(reservation['id'],observed_turns=1,native_id=case['parent_native_id'],completed_at=finals[-1]['at'])
            checks.append(dict(provider=provider,status='PASS' if all(assertions.values()) else 'PENDING' if not finals else 'FAIL',assertions=assertions,
                parent_native_id=case['parent_native_id'],worker_native_ids=ids,parent_final_events=[e['id'] for e in finals],native_tool_evidence=evidence,source=case['source']))
        ledger=json.loads(base.LEDGER.read_text());report={'checks':checks,'budget':{k:ledger[k] for k in ('consumed_turns','reserved_turns','active_seconds','outstanding_native_requests')}}
        atomic_json(RUN/'report.json',report);print(json.dumps({'checks':[{'provider':c['provider'],'status':c['status'],'remaining':[k for k,v in c['assertions'].items() if not v]} for c in checks],'budget':report['budget']}))
    finally:journal.close()


if __name__=='__main__':collect()
