"""Collect exact owned native evidence without generating model turns."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json
from nativepeers.journal import Journal


def timestamp(value):return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def native_events(provider,native,cwd):
    if provider=='claude':
        slug=str(cwd).replace('/','-').replace('.','-')
        path=Path.home()/'.claude/projects'/slug/(native+'.jsonl')
    else:
        matches=list((Path.home()/'.codex/sessions/2026/10/21').glob('*'+native+'*'))
        if not matches:return None,[]
        path=matches[0]
    if not path.exists():return None,[]
    events=[]
    for line in path.open():
        event=json.loads(line)
        if provider=='claude':
            if event.get('sessionId') not in (None,native):raise ValueError('History identity mismatch')
            message=event.get('message',{});content=message.get('content',[])
            if event.get('type')=='attachment' and event.get('attachment',{}).get('type')=='queued_command':
                events.append({'kind':'user','at':timestamp(event['timestamp']),'id':event.get('uuid'),'text':event['attachment'].get('prompt',''),'native_ingress':'busy-queued-command-attachment'})
            if event.get('type')=='user':events.append({'kind':'user','at':timestamp(event['timestamp']),'id':event.get('uuid'),'text':content if isinstance(content,str) else json.dumps(content)})
            if event.get('type')=='assistant':
                for item in content:
                    if item.get('type')=='tool_use':events.append({'kind':'tool','at':timestamp(event['timestamp']),'id':item.get('id'),'text':json.dumps(item.get('input',{}))})
                    if item.get('type')=='text':events.append({'kind':'assistant','at':timestamp(event['timestamp']),'id':event.get('uuid'),'text':item['text'],'terminal':message.get('stop_reason')=='end_turn'})
        else:
            value=event.get('payload',{})
            if event['type']=='session_meta' and value.get('id')!=native:raise ValueError('Native thread mismatch')
            if event['type']=='response_item' and value.get('type') in ('custom_tool_call','function_call'):
                events.append({'kind':'tool','at':timestamp(event['timestamp']),'id':value.get('call_id'),'text':value.get('input',value.get('arguments',''))})
            if event['type']=='response_item' and value.get('role')=='user':
                events.append({'kind':'user','at':timestamp(event['timestamp']),'id':value.get('id'),'text':json.dumps(value.get('content',[]))})
            if event['type']=='event_msg' and value.get('type')=='task_complete':
                events.append({'kind':'assistant','at':timestamp(event['timestamp']),'id':value.get('turn_id'),'text':value.get('last_agent_message',''),'terminal':True})
    return path,events


def collect():
    journal=Journal(ROOT/'evidence/private/live-state');budget=LiveBudget(ROOT/'evidence/live-budget.json')
    ledger=json.loads(budget.path.read_text());checks=[]
    for group in journal.all('SELECT * FROM groups'):
        parent=journal.one('SELECT * FROM peer_instances WHERE peer_key=? ORDER BY last_seen DESC LIMIT 1',(group['parent_key'],));descriptor=json.loads(parent['descriptor_json'])
        policy=json.loads(group['policy_json']);spec=policy['original_request']
        if spec.get('client_request_id') not in ('NP-LIVE-07-batch-1','NP-LIVE-08-batch-1'):continue
        case='LIVE-07' if spec['workers'][0]['provider']=='codex' else 'LIVE-08'
        path,events=native_events(parent['provider'],parent['native_id'],descriptor['cwd'])
        result_rows=journal.all('SELECT t.*,w.native_id,w.provider,w.model_observed,w.effort_requested,w.role_title FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?',(group['group_id'],))
        task_starts=[json.loads(e['payload_json']) for e in journal.all("SELECT payload_json FROM events WHERE kind='native-task-started' AND subject_id IN (SELECT t.task_id FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?)",(group['group_id'],))]
        worker_results=[];native_terminals=[]
        for row in result_rows:
            if not row['result_artifact_id']:continue
            artifact=journal.one('SELECT * FROM artifacts WHERE artifact_id=?',(row['result_artifact_id'],));result=json.loads((journal.root/'artifacts'/artifact['relative_path']).read_text())
            worker_results.append(result)
            _,worker_events=native_events(row['provider'],row['native_id'],descriptor['cwd'])
            finals=[e for e in worker_events if e['kind']=='assistant' and e.get('terminal')]
            if finals:native_terminals.append(finals[-1]['at'])
        nonces=[re.search(r'nonce ([0-9a-f]+)',w['task']).group(1) for w in spec['workers']]
        parent_replies=[e for e in events if e['kind']=='assistant' and all(n in e['text'] for n in nonces) and all(r['native_id'] in e['text'] for r in result_rows)]
        inbound=[e for e in events if e['kind']=='user' and group['group_id'] in e['text'] and 'NativePeers group' in e['text']]
        overlap=len(task_starts)==2 and len(native_terminals)==2 and max(x['at'] for x in task_starts)<min(native_terminals)
        assertions={'actual_parent_tool_spawn':any(e['kind']=='tool' and 'nativepeers spawn' in e['text'] for e in events),
                    'two_distinct_native_workers':len({r['native_id'] for r in result_rows})==2,
                    'two_terminal_successes':len(worker_results)==2 and all(r['status']=='completed' for r in worker_results),
                    'overlapping_native_task_intervals':overlap,
                    'original_parent_ingestion':bool(inbound),'original_parent_attributed_response':bool(parent_replies),
                    'assigned_nonces_in_worker_results':all(any(n in r['summary'] for r in worker_results) for n in nonces),
                    'exact_required_model_effort':all(r['model_observed']=='gpt-6.1-sol' and r['effort_requested']=='xhigh' for r in result_rows) if case=='LIVE-07' else all(r['model_observed']=='claude-sonnet-5-5' for r in result_rows)}
        dependency={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}
        check={'test_id':case,'requirement_ids':['SPAWN-01' if case=='LIVE-07' else 'SPAWN-02'],'status':'PASS' if all(assertions.values()) else 'FAIL','evidence_class':'live',
            'assertions':assertions,'group_id':group['group_id'],'parent_native_id':parent['native_id'],'worker_native_ids':[r['native_id'] for r in result_rows],
            'parent_native_source':str(path),'native_task_starts':task_starts,'native_terminals':native_terminals,
            'parent_ingress_event_ids':[e['id'] for e in inbound],'parent_response_event_ids':[e['id'] for e in parent_replies],
            'provider_versions':{'claude':'2.1.306','codex':'0.163.0'},'source_dependencies':dependency,
            'limitations':['Client-side native task overlap; no claim about vendor GPU scheduling.','Reasoning effort was validated in native submitted configuration, not independently echoed by the inference backend.']}
        checks.append(check)
        if parent_replies:
            for event in journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='parent-completion-submitted'",(group['group_id'],)):
                payload=json.loads(event['payload_json']);reservation=payload.get('live_reservation')
                if reservation and next(r['state'] for r in ledger['reservations'] if r['id']==reservation)=='admitted':budget.settle(reservation,observed_turns=1,native_id=parent['native_id'],completed_at=max(e['at'] for e in parent_replies))
            for launch in (ROOT/'evidence/private').glob('NP-LIVE-*/parent-launch.json'):
                info=json.loads(launch.read_text())
                marker='acceptance '+launch.parent.name+'.'
                if info['provider']!=parent['provider'] or not any(marker in e['text'] for e in events if e['kind']=='user'):continue
                reservation=info['initial_reservation'];state=next(r['state'] for r in ledger['reservations'] if r['id']==reservation)
                waiting=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'WAITING' in e['text']]
                if state=='admitted' and waiting:budget.settle(reservation,observed_turns=1,native_id=parent['native_id'],completed_at=waiting[0]['at'])
    # Retain the failed unbound-parent attempt as consumed usage; it created no worker group.
    for launch in (ROOT/'evidence/private').glob('NP-LIVE-*/parent-launch.json'):
        info=json.loads(launch.read_text());reservation=info['initial_reservation']
        current=next(r['state'] for r in json.loads(budget.path.read_text())['reservations'] if r['id']==reservation)
        if current!='admitted' or info['provider']!='codex':continue
        identities=set()
        for line in (Path.home()/'.codex/session_index.jsonl').open():
            value=json.loads(line)
            if value.get('thread_name')==launch.parent.name:identities.add(value['id'])
        if len(identities)!=1:continue
        native=identities.pop();_,events=native_events('codex',native,ROOT/'evidence/scratch/live-workspace')
        finals=[e for e in events if e['kind']=='assistant' and e.get('terminal')]
        if finals:budget.settle(reservation,observed_turns=1,native_id=native,completed_at=finals[-1]['at'])
    acceptance=json.loads((ROOT/'evidence/acceptance.json').read_text());updates={c['test_id']:c for c in checks}
    acceptance['checks']=[updates.get(c['test_id'],c) for c in acceptance['checks']]
    atomic_json(ROOT/'evidence/acceptance.json',acceptance);atomic_json(ROOT/'evidence/early-delegation.json',{'checks':checks})
    if len(checks)==2 and all(c['status']=='PASS' for c in checks):budget.change(lambda d:d.update(delegation_reserve=0))
    print(json.dumps([{'test_id':c['test_id'],'status':c['status'],'assertions':c['assertions']} for c in checks]))
    journal.close()


if __name__=='__main__':collect()
