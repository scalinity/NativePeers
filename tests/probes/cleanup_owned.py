#!/usr/bin/env python3
"""End only settled disposable test owners whose recorded OS incarnation still matches."""
import json,signal,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.process import matches,signal_owned

ledger=json.loads((ROOT/'evidence/live-budget.json').read_text());assert not ledger['outstanding_native_requests'] and ledger['reserved_turns']==0
assert json.loads((ROOT/'evidence/queue-settlement.json').read_text())['status']=='PASS'
j=Journal(ROOT/'evidence/private/live-state');checkpoint=json.loads((ROOT/'evidence/checkpoint.json').read_text());owners=list(checkpoint['active_owned_processes'])
for r in j.all("SELECT descriptor_json FROM peer_instances WHERE owner_kind='interactive-standalone'"):
 p=json.loads(r['descriptor_json']);assert p['title'] in ('NP-LIVE-07','NP-LIVE-08B','NP-FINAL-CLAUDE-PARENT');owners.append(p['owner'])
unique={(o['pid'],o['start']):o for o in owners};report=[]
for owner in unique.values():
 alive=matches(owner);sent=signal_owned(owner,signal.SIGTERM,group=True) if alive else False
 report.append({'owner':owner,'matched_before_cleanup':alive,'term_sent':sent})
deadline=time.monotonic()+6
while time.monotonic()<deadline and any(matches(r['owner']) for r in report):time.sleep(.05)
for r in report:
 if matches(r['owner']):r['kill_sent']=signal_owned(r['owner'],signal.SIGKILL,group=True)
 r['alive_after']=matches(r['owner'])
workers=[]
for p in (j.root/'workers').glob('*.json'):
 v=json.loads(p.read_text());workers.append({'worker_id':v['worker_id'],'controller_alive':matches(v['owner']),'native_alive':bool(v.get('native_process') and matches(v['native_process']))})
result={'status':'PASS' if all(not r['alive_after'] for r in report) and all(not r['controller_alive'] and not r['native_alive'] for r in workers) else 'BLOCKED','interactive_owners':report,'workers':workers,'unrelated_processes_signaled':False,'histories_retained':True}
atomic_json(ROOT/'evidence/cleanup.json',result);print(json.dumps({'status':result['status'],'interactive_owners_ended':len(report),'worker_controllers_ended':len(workers)}));j.close()
