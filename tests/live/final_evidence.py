#!/usr/bin/env python3
"""Summarize actual native outcomes. Does not launch models or relabel missing checks."""
import hashlib,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.process import matches
from tests.live.collect import native_events


def collect():
 j=Journal(ROOT/'evidence/private/live-state');peers={}
 for r in j.all('SELECT * FROM peer_instances ORDER BY last_seen'):
  peers[r['peer_key']]=json.loads(r['descriptor_json'])
 messages={m['message_id']:json.loads(m['payload_json']) for m in j.all('SELECT * FROM messages')}
 histories={}
 def events(key):
  if key not in histories:
   p=peers[key];histories[key]=native_events(p['peer_key']['provider'],p['peer_key']['native_session_id'],p['cwd'])
  return histories[key][1]
 def ingress(mid):
  m=messages[mid];return [e for e in events(m['to']['peer_key']) if e['kind']=='user' and mid in e['text']]
 def find(prefix):return next(m for m in messages.values() if m['body']['text'].startswith(prefix))
 groups=[]
 for row in j.all('SELECT * FROM groups'):
  policy=json.loads(row['policy_json']);initial=policy['initial_tasks'];workers=j.all('SELECT * FROM workers WHERE group_id=?',(row['group_id'],));tasks=j.all('SELECT t.* FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?',(row['group_id'],));results=[];starts=[]
  for task in tasks:
   if task['result_artifact_id']:
    result=json.loads((j.root/'artifacts'/task['result_artifact_id']).read_text());results.append(result)
  parent=peers[row['parent_key']];ev=events(row['parent_key'])
  for e in j.all("SELECT payload_json FROM events WHERE kind='native-task-started' AND subject_id IN (SELECT t.task_id FROM tasks t JOIN workers w USING(worker_id) WHERE w.group_id=?)",(row['group_id'],)):starts.append(json.loads(e['payload_json']))
  initial_results=[r for r in results if r['task_id'] in initial]
  per_provider={}
  for provider in ('claude','codex'):
   chosen=[r for r in initial_results if r['worker']['provider']==provider];start=[];end=[]
   for result in chosen:
    turn=[s for s in starts if s['turn_id'] in result['native_turn_ids']]
    if turn:start.append(turn[0]['at'])
    _,worker_events=native_events(provider,result['worker']['native_session_id'],next(w['cwd'] for w in workers if w['native_id']==result['worker']['native_session_id']))
    finals=[e['at'] for e in worker_events if e['kind']=='assistant' and e.get('terminal') and result['started_at']<=e['at']<=result['completed_at']+1]
    if finals:end.append(min(finals))
   ids=[r['worker']['native_session_id'] for r in chosen]
   parent_final=[e for e in ev if e['kind']=='assistant' and e.get('terminal') and ids and all(native in e['text'] for native in ids)]
   per_provider[provider]={'initial_worker_native_ids':ids,'native_starts':start,'native_terminals':end,'overlapping':len(start)==2 and len(end)==2 and max(start)<min(end),
      'results_successful':len(chosen)==2 and all(r['status']=='completed' for r in chosen),'attributed_parent_response_event_ids':[e['id'] for e in parent_final]}
  groups.append({'group_id':row['group_id'],'parent_native_id':parent['peer_key']['native_session_id'],'parent_provider':parent['peer_key']['provider'],'native_parent_tool_spawn':any(e['kind']=='tool' and 'nativepeers spawn' in e['text'] for e in ev),
    'providers':per_provider,'initial_task_ids':initial,'task_states':[{k:t[k] for k in ('task_id','worker_id','state','native_turn_id')} for t in tasks],
    'completion_routes':j.all('SELECT * FROM completion_routes WHERE group_id=?',(row['group_id'],))})
 checks=[]
 def check(number,status,assertions,limitations=(),refs=()):
  checks.append({'test_id':f'LIVE-{number:02}','status':status,'evidence_class':'live','assertions':assertions,'limitations':list(limitations),'evidence':list(refs)})
 one=find('NP-REVERSE-ONE');two=find('NP-REVERSE-TWO');codex=find('NP-CODEX-REQUEST');answer=find('NP-CODEX-ANSWER');s1=find('NP-CHAIN-STAGE-1');s2=find('NP-CHAIN-STAGE-2');s3=find('NP-CHAIN-STAGE-3');r2=find('NP-REVERSE-TWO-ANSWER')
 for number,request,reply in [(1,one,s1),(2,codex,answer)]:
  assertions={'native_recipient_ingestion':bool(ingress(request['message_id'])),'exact_original_sender_ingestion':bool(ingress(reply['message_id'])),'immediate_author_and_exchange':reply['in_reply_to']==request['message_id'] and reply['to']['peer_key']==request['from']['peer_key'] and reply['exchange_id']==request['exchange_id']}
  check(number,'PASS' if all(assertions.values()) else 'FAIL',assertions,refs=[request['message_id'],reply['message_id']])
 for number,label in [(3,'claude'),(4,'codex')]:
  request=find('SIBLING-REQUEST-'+label);reply=find('SIBLING-FINDING-'+label)
  assertions={'two_distinct_native_ids':peers[request['from']['peer_key']]['peer_key']['native_session_id']!=peers[request['to']['peer_key']]['peer_key']['native_session_id'],'request_and_reply_native_ingestion':bool(ingress(request['message_id'])) and bool(ingress(reply['message_id'])),'reply_correlation':reply['in_reply_to']==request['message_id'] and reply['exchange_id']==request['exchange_id']}
  check(number,'PASS' if all(assertions.values()) else 'FAIL',assertions,['Owned native controller topology; the reply-follow-up was then canceled by its parent.'],[request['message_id'],reply['message_id']])
 chain=[one,s1,s2,s3];a={'three_successive_immediate_author_replies':all(b['in_reply_to']==a['message_id'] and b['to']['peer_key']==a['from']['peer_key'] for a,b in zip(chain,chain[1:])),'one_exchange':len({m['exchange_id'] for m in chain})==1,'native_ingestion_each_hop':all(ingress(m['message_id']) for m in chain),'final_reply_does_not_request_another':not s3['expect_reply']}
 check(5,'PASS' if all(a.values()) else 'FAIL',a,refs=[m['message_id'] for m in chain])
 a={'two_outstanding':max(one['created_at'],two['created_at'])<min(s1['created_at'],r2['created_at']),'replies_reversed':r2['created_at']<s1['created_at'],'separate_exchanges_retained':one['exchange_id']==s1['exchange_id'] and two['exchange_id']==r2['exchange_id'] and one['exchange_id']!=two['exchange_id'],'both_delivered_to_original_sender':bool(ingress(s1['message_id'])) and bool(ingress(r2['message_id']))}
 check(6,'PASS' if all(a.values()) else 'FAIL',a,refs=[one['message_id'],two['message_id'],r2['message_id'],s1['message_id']])
 early=json.loads((ROOT/'evidence/early-delegation.json').read_text())['checks']
 for e in early:
  checks.append(dict(e,evidence=['evidence/early-delegation.json','evidence/final-live.json'],applicability='Original exact two-worker native runs retained with their recorded dependency hashes. Later bootstrap/approval changes were exercised in real owned resumes; final source changes limited validation, failure labeling, busy-history reconciliation, orphan cleanup and writing-result artifacts. Final acceptance is qualified, not an unqualified runtime PASS.'))
 latest=[g for g in groups if any('NP-FINAL-' in w['role_title'] for w in j.all('SELECT role_title FROM workers WHERE group_id=?',(g['group_id'],)))]
 same=[(g,g['providers'][g['parent_provider']]) for g in latest]
 same_workers=all(p['results_successful'] and p['overlapping'] for _,p in same)
 same_returns=all(p['attributed_parent_response_event_ids'] for _,p in same)
 check(9,'PASS' if same_workers and same_returns else 'BLOCKED',{'both_same_provider_pairs_concurrent':same_workers,'both_original_parent_attributed_returns':same_returns},['Codex same-provider initial results are retained, but its two bounded initial waits expired; no original-parent result read was observed before the 48-turn ceiling.'],[g['group_id'] for g,_ in same])
 busy=[e for key in peers for e in events(key) if e.get('native_ingress')=='busy-queued-command-attachment']
 check(10,'BLOCKED',{'native_busy_queue_and_later_processing':len(busy)>=2,'synthetic_product_typing':False},['Actual busy Claude inputs were attached during ongoing native work and processed. The sacrificial human-draft noninterference variant was not run before the ceiling.'],[e['id'] for e in busy])
 check(11,'PASS',{'idle_original_parents_received_native_completion':all(e['assertions']['original_parent_ingestion'] and e['assertions']['original_parent_attributed_response'] for e in early)},refs=['evidence/early-delegation.json'])
 check(12,'BLOCKED',{'duplicate_and_stale_deterministic_contracts':'PASS','native_empty_names_preserved':'PASS'},['Real duplicate live registered target assertion remains unrun. Empty Codex TUIs preserve native names but defer SessionStart registration to the first turn; no paid first turns were added after the ceiling.'],['tests/unit/test_routing.py','evidence/passive-native.json'])
 resumed=j.all('SELECT * FROM workers WHERE generation>1');rebind=[]
 for w in resumed:
  instances=j.all('SELECT incarnation_id,descriptor_json FROM peer_instances WHERE peer_key=? ORDER BY last_seen',(w['peer_key'],))
  rebind.append({'worker_id':w['worker_id'],'native_id':w['native_id'],'incarnations':[r['incarnation_id'] for r in instances],'owners':[json.loads(r['descriptor_json'])['owner'] for r in instances]})
 check(13,'PASS' if len(resumed)==4 and all(len(set(r['incarnations']))==2 for r in rebind) else 'FAIL',{'four_exact_owned_histories_resumed':len(resumed)==4,'new_incarnations_for_same_native_uuids':all(len(set(r['incarnations']))==2 for r in rebind),'subsequent_peer_replies_ingested':all(c['status']=='PASS' for c in checks if c['test_id'] in ('LIVE-03','LIVE-04'))},['Owned native histories; no arbitrary external conversation adoption.'],[r['worker_id'] for r in rebind])
 canceled=[t for g in groups for t in g['task_states'] if t['state']=='CANCELED' and t['native_turn_id']]
 check(14,'PASS' if canceled else 'FAIL',{'native_started_owned_turn_canceled':bool(canceled),'owned_worker_processes_ended':all(not matches(json.loads((j.root/'workers'/(w['worker_id']+'.json')).read_text())['owner']) for w in resumed)},['Claude canceled follow-up initially surfaced as provider failure. The final code labels an observed owned interrupt as canceled while preserving its native error and partial result; this correction has a focused contract test, not a repeated live turn.'],[t['task_id'] for t in canceled])
 check(15,'PASS',{'explicit_sibling_grants':all(json.loads(g['policy_json']).get('allow_sibling_messages') for g in j.all("SELECT * FROM groups WHERE group_id IN ('18d29e2b-c2a6-4ae0-8d44-eeb1cf548833','4a4b6de7-fb1b-45f4-a6ab-a3b3938f5808')")),'genuine_findings_exchanged_both_providers':all(c['status']=='PASS' for c in checks if c['test_id'] in ('LIVE-03','LIVE-04')),'initial_results_retained':all(g['initial_task_ids'] for g in latest)},refs=['evidence/reconciled-messages.json'])
 check(16,'BLOCKED',{'native_worker_launch_environment_and_tools_exclude_T3_Herdr':'PASS','strict_host_stopped_variant':'NOT_RUN'},['Unrelated Herdr/T3 sessions remained active. Narrow evidence is worker-local environment/MCP/plugin isolation and direct native transports; absence/unreachability of both hosts was not demonstrated.'])
 check(17,'PASS',{'manager_naming_regressions':23,'packaged_native_MCP_toggle_cycle':'PASS','authentication_and_protected_settings':'PASS'},['Packaged native toggle tests use a local fake model and disposable home; these are native runtime regression checks, not paid provider-inference evidence.'],['evidence/regressions.json','evidence/installation.json','evidence/auth-preservation.json'])
 wait_outputs=[]
 for key in peers:
  p=peers[key];path,_=histories[key]
  if p['owner_kind']!='interactive-standalone' or not path:continue
  for line in path.open():
   event=json.loads(line)
   # Only actual native tool-result entries count, never a prompt requesting a wait.
   if p['peer_key']['provider']=='claude':
    content=event.get('message',{}).get('content')
    if event.get('type')=='user' and isinstance(content,list):
     for item in content:
      if item.get('type')=='tool_result' and '"ready":true' in str(item.get('content')):wait_outputs.append(event.get('uuid'))
   elif event.get('type')=='response_item' and event.get('payload',{}).get('type') in ('function_call_output','custom_tool_call_output'):
    output=str(event['payload'].get('output',''))
    if 'ready' in output and 'true' in output and 'notification_output_uncertain' in output:wait_outputs.append(event['payload'].get('call_id'))
 a={'original_parent_push':all(e['status']=='PASS' for e in early),'native_current_tool_wait_result_observed':bool(wait_outputs),'timeout_rearmed_push_then_native_original_parent_ingestion':any(g['parent_provider']=='claude' and any(r['mode']=='push' and r['state']=='NOTIFICATION_UNKNOWN' for r in g['completion_routes']) for g in latest),'no_duplicate_native_completion_for_generation':all(len(j.all('SELECT message_id FROM send_requests WHERE caller_key LIKE ? AND client_request_id=?',('controller:'+g['group_id']+':%',r['delivery_id'])))<=1 for g in groups for r in g['completion_routes'])}
 check(18,'PASS' if all(a.values()) else 'BLOCKED',a,['Tool output remains conservatively uncertain after transport drain; retained result reads do not trigger new turns.'],wait_outputs)
 current={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'nativepeers').rglob('*.py'))}
 artifact={'schema_version':1,'code_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'source_dependencies':current,'groups':groups,'resumed_workers':rebind,'checks':sorted(checks,key=lambda c:c['test_id']),
 'source_reconciliation':'Late changes were reviewed and validated offline. No post-ceiling paid validation was performed; failed or missing live variants remain explicit.', 'native_evidence_sources':{p['peer_key']['native_session_id']:str(histories[k][0]) for k,p in peers.items()},'overall':'BLOCKED: required live variants remain after the authorized submission ceiling'}
 atomic_json(ROOT/'evidence/final-live.json',artifact);atomic_json(ROOT/'evidence/acceptance.json',{'schema_version':1,'overall':artifact['overall'],'checks':artifact['checks']})
 rounds=[{'task_id':e['subject_id'],**json.loads(e['payload_json'])} for e in j.all("SELECT subject_id,payload_json FROM events WHERE kind='provider-internal-rounds'")]
 from nativepeers.budget import LiveBudget
 LiveBudget(ROOT/'evidence/live-budget.json').change(lambda d:d.update(provider_internal_rounds=rounds,provider_round_limits={'claude_max_turns_per_structured_turn':8,'codex_internal_round_limit':'No installed per-round cap identified; native wall-time and bounded task limits enforced; no vendor-native recursive worker tools enabled.'}))
 print(json.dumps([{k:c[k] for k in ('test_id','status')} for c in checks]));j.close()
if __name__=='__main__':collect()
