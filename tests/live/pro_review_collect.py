#!/usr/bin/env python3
"""Observe exact owned parents and settle the retained Pro-review ledger."""
import asyncio
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.core import Core
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from tests.live.pro_review import RUN,STATE,LEDGER,artifact_nonce_matches,source,stamp
from tests.live.review_collect import native_events
from tests.live.collect_antigravity import antigravity_events


def json_objects(value,depth=0):
    if depth>8:return
    if isinstance(value,str):
        try:decoded=json.loads(value)
        except ValueError:
            # Native tool steps can wrap the MCP JSON in a short text label.
            decoder=json.JSONDecoder()
            for index,char in enumerate(value):
                if char!='{':continue
                try:decoded,_=decoder.raw_decode(value[index:])
                except ValueError:continue
                yield from json_objects(decoded,depth+1)
            return
        yield from json_objects(decoded,depth+1)
    elif isinstance(value,list):
        for item in value:yield from json_objects(item,depth+1)
    elif isinstance(value,dict):
        yield value
        for key in ('content','text','output','result','response','Ok','structuredContent'):
            if key in value:yield from json_objects(value[key],depth+1)


def artifact_result(value,context):
    for item in json_objects(value):
        if item.get('isError') or item.get('is_error') or item.get('ok') is False:return False
        page=item.get('data')
        if item.get('ok') is not True or not isinstance(page,dict):continue
        if page.get('artifact_id')!=context['artifact_id'] or page.get('sha256')!=context['sha256']:continue
        try:raw=base64.b64decode(page['data_base64'],validate=True);text=raw.decode()
        except (KeyError,TypeError,ValueError,UnicodeError):continue
        if (page.get('offset')==0 and len(raw)==context['byte_size']==page.get('bytes_read')
                and hashlib.sha256(raw).hexdigest()==context['sha256'] and artifact_nonce_matches(text,context)):
            return True
    return False


def artifact_call(value,context):
    if not isinstance(value,dict):return False
    function=value.get('function',value)
    if not isinstance(function,dict):return False
    name=function.get('name','')
    if name=='call_mcp_tool':
        args=function.get('args')
        if not isinstance(args,dict) or args.get('ServerName')!='nativepeers' or args.get('ToolName')!='artifact_read':return False
        function={'name':'mcp_nativepeers_artifact_read','arguments':args.get('Arguments')}
        name=function['name']
    if not isinstance(name,str) or 'nativepeers' not in name or not name.endswith('_artifact_read'):return False
    args=function.get('arguments',function.get('args',function.get('input',{})))
    if isinstance(args,str):
        try:args=json.loads(args)
        except ValueError:return False
    return isinstance(args,dict) and args.get('handle')==context['artifact_id']


def artifact_read_evidence(peer,context,result):
    provider=peer['peer_key']['provider']
    if provider=='antigravity' and not peer.get('native_transcript'):
        from uuid import UUID
        native=str(UUID(peer['peer_key']['native_session_id']))
        peer=dict(peer,native_transcript=str(Path(peer['home'])/'brain'/native/'.system_generated/logs/transcript.jsonl'))
    history,_=(antigravity_events(peer) if provider=='antigravity' else native_events(peer))
    full=history.with_name('transcript_full.jsonl')
    if provider=='antigravity' and full.exists():
        assert full.resolve().is_relative_to(Path(peer['home'])) and not full.is_symlink()
        history=full
    pending={};wrapped=set()
    for line in history.read_text().splitlines():
        event=json.loads(line);at=event.get('created_at') if provider=='antigravity' else event.get('timestamp',event.get('ts'))
        if not at:continue
        when=datetime.fromisoformat(at.replace('Z','+00:00')).timestamp()
        if not result['started_at']<=when<=result['completed_at']+1:continue
        call_id=None;output=None;event_id=None
        if provider=='antigravity':
            event_id=event.get('step_index')
            if event.get('type')=='PLANNER_RESPONSE':
                for call in event.get('tool_calls',[]):
                    if artifact_call(call,context):
                        key=call.get('id',event_id);pending[key]=event_id
                        if call.get('name')=='call_mcp_tool':wrapped.add(key)
            if event.get('type') in ('MCP_TOOL','GENERIC') and event.get('status')=='DONE' and pending:
                call_id=event.get('tool_call_id',next(reversed(pending)))
                if event.get('type')=='MCP_TOOL' or (call_id in wrapped and event_id==pending[call_id]+1):output=event.get('content')
        else:
            payload=event.get('payload',{});kind=payload.get('type');call_id=payload.get('call_id');event_id=call_id
            item=payload.get('item',{})
            if (event.get('type')=='event_msg' and kind=='item_completed' and item.get('type')=='McpToolCall'
                    and payload.get('thread_id')==peer['peer_key']['native_session_id'] and item.get('status')=='completed'):
                call=dict(name='mcp__'+item.get('server','')+'__'+item.get('tool',''),arguments=item.get('arguments',{}))
                if artifact_call(call,context) and artifact_result(item.get('result'),context):
                    return dict(native_history=str(history),native_session_id=peer['peer_key']['native_session_id'],call_event_id=item['id'],result_event_id=item['id'],artifact_id=context['artifact_id'],sha256=context['sha256'])
            if event.get('type')=='response_item':
                if kind in ('function_call','custom_tool_call') and call_id and artifact_call(payload,context):pending[call_id]=call_id
                if kind in ('function_call_output','custom_tool_call_output'):output=payload.get('output')
            elif event.get('type')=='event_msg':
                invocation=payload.get('invocation',{})
                call=dict(name='mcp__'+invocation.get('server','')+'__'+invocation.get('tool',''),arguments=invocation.get('arguments',{}))
                if kind=='mcp_tool_call_begin' and call_id and artifact_call(call,context):pending[call_id]=call_id
                if kind=='mcp_tool_call_end':output=payload.get('result')
        if call_id in pending and artifact_result(output,context):
            return dict(native_history=str(history),native_session_id=peer['peer_key']['native_session_id'],
                        call_event_id=pending[call_id],result_event_id=event_id,artifact_id=context['artifact_id'],sha256=context['sha256'])
    return None


def collect():
    journal=Journal(STATE);core=Core(journal);budget=LiveBudget(LEDGER);checks=[]
    try:
        for provider in ('codex','antigravity'):
            path=RUN/(provider+'-case.json')
            if not path.exists():continue
            case=json.loads(path.read_text());launch=json.loads((RUN/(provider+'-launch.json')).read_text())
            peer=core.registry.get(case['caller'],live=False)
            assert peer['owner']['pid']==launch['owner_expected_after_exec']['pid']
            assert peer['peer_key']['native_session_id']==case['parent_native_id']
            history,events=(antigravity_events(peer) if provider=='antigravity' else native_events(peer))
            finals=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'NP-PRO-'+provider+'-PARENT' in e['text']]
            ids=[r['worker']['native_session_id'] for r in case.get('results',[])]
            assertions=dict(case.get('assertions',{}))
            assertions.update(current_source=case['source']==source(),case_passed=case.get('status')=='PASS',
                actual_parent_tool_call=any(e['kind']=='tool' and 'pro_review.py parent '+provider in e['text'] for e in events),
                original_parent_observed_results=bool(finals and len(ids)==2 and all(native in finals[-1]['text'] for native in ids)))
            artifact_reads={};context=case.get('context_artifact')
            assertions['artifact_context_recorded']=bool(context)
            for result in case.get('results',[]):
                child=result['worker']['provider'];evidence=None
                if context:
                    try:evidence=artifact_read_evidence(core.registry.get(result['worker']['peer_key'],live=False),context,result)
                    except (OSError,json.JSONDecodeError,RuntimeError):pass
                    assertions[child+'_artifact_nonce']=artifact_nonce_matches(result['summary'],context)
                artifact_reads[child]=evidence
                assertions[child+'_native_artifact_read']=evidence is not None
            ledger=json.loads(LEDGER.read_text())
            for reservation_id in [launch['reservation'],*launch.get('continuation_reservations',[])]:
                reservation=next(r for r in ledger['reservations'] if r['id']==reservation_id)
                completed=[e for e in events if e['kind']=='assistant' and e.get('terminal') and ('NP-PRO-'+provider+'-PARENT' in e['text'] or 'NP-PRO-PUSH-DISPATCH' in e['text']) and e['at']>=reservation.get('admitted_at',float('inf'))]
                if completed and reservation['state'] in ('admitted','uncertain'):
                    budget.settle(reservation_id,observed_turns=1,native_id=case['parent_native_id'],completed_at=completed[-1]['at'])
            checks.append(dict(provider=provider,status='PASS' if all(assertions.values()) else 'PENDING' if case.get('status') is None or not finals else 'FAIL',
                assertions=assertions,parent_native_id=case['parent_native_id'],worker_native_ids=ids,native_history=str(history),
                parent_terminal_event_ids=[e['id'] for e in finals],source=case['source'],groups=case.get('groups',[]),artifact_reads=artifact_reads))
        pushed=None
        if (RUN/'push.json').exists():
            case=json.loads((RUN/'push.json').read_text());peer=core.registry.get(case['caller'],live=False)
            history,events=native_events(peer)
            records=journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='parent-completion-submitted'",(case['group_id'],))
            submissions=[json.loads(row['payload_json']) for row in records]
            submission=submissions[0] if len(submissions)==1 else None
            replies=[e for e in events if e['kind']=='assistant' and e.get('terminal') and 'NP-PRO-PUSH-RECEIVED' in e['text']]
            ingress=[e for e in events if e['kind']=='user' and submission and submission['receipt']['message_id'] in e['text']]
            result=core.delegation.result(__import__('nativepeers.caller',fromlist=['Caller']).Caller('peer',case['caller']),case['task_id'])['results']
            assertions=dict(one_submission=len(submissions)==1,original_parent_ingested=bool(ingress),
                original_parent_reported=bool(replies and case['worker_native_id'] in replies[-1]['text'] and 'NP-PRO-PUSH-CHILD' in replies[-1]['text'] and '42' in replies[-1]['text']),
                exact_native_history=bool(result and result[0]['worker']['native_session_id']==case['worker_native_id']),
                current_source=case['source']==source())
            if ingress and replies:
                from nativepeers.reconcile import observe
                receipt=asyncio.run(observe(journal,core.registry,core.router,submission['receipt']['message_id']))
                assertions['native_queue_ingestion']=receipt['state']=='INGESTED' and receipt['attempt']['transport']=='codex-native-queue'
                reservation=next(r for r in json.loads(LEDGER.read_text())['reservations'] if r['id']==submission['live_reservation'])
                if reservation['state'] in ('admitted','uncertain'):budget.settle(reservation['id'],observed_turns=1,native_id=case['parent_native_id'],completed_at=replies[-1]['at'])
            pushed=dict(status='PASS' if all(assertions.values()) else 'PENDING',assertions=assertions,
                parent_native_id=case['parent_native_id'],worker_native_id=case['worker_native_id'],
                completion_message_id=submission['receipt']['message_id'] if submission else None,
                native_reply_event_ids=[e['id'] for e in replies],source=case['source'])
        ledger=json.loads(LEDGER.read_text());report=dict(at=stamp(),checks=checks,push=pushed,budget={k:ledger[k] for k in ('consumed_turns','reserved_turns','active_seconds','outstanding_native_requests')})
        atomic_json(RUN/'report.json',report)
        print(json.dumps(dict(checks=[dict(provider=c['provider'],status=c['status'],not_yet_true=[k for k,v in c['assertions'].items() if not v]) for c in checks],push=pushed,budget=report['budget'])))
    finally:journal.close()


if __name__=='__main__':collect()
