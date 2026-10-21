#!/usr/bin/env python3
"""Settle only exact owned inputs with native user and subsequent terminal evidence."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from tests.live.collect import native_events

def collect():
 j=Journal(ROOT/'evidence/private/live-state');b=LiveBudget(ROOT/'evidence/live-budget.json');ledger=json.loads(b.path.read_text());records=[]
 peers={}
 for r in j.all('SELECT descriptor_json FROM peer_instances'):
  p=json.loads(r['descriptor_json']);peers[p['peer_key']['native_session_id']]=p
 for p in (ROOT/'evidence/private/final-batch').glob('*.json'):
  value=json.loads(p.read_text())
  if value.get('reservation') and value.get('message_id'):records.append(dict(value,marker=value['message_id']))
 fresh=ROOT/'evidence/private/final-batch/fresh-claude-parent.json'
 if fresh.exists():
  v=json.loads(fresh.read_text());records.append({'reservation':v['initial_reservation'],'native_id':v['native_id'],'marker':'NativePeers disposable final acceptance parent.'})
 for event in j.all("SELECT subject_id,payload_json FROM events WHERE kind='live-peer-reservation'"):
  v=json.loads(event['payload_json']);records.append(dict(v,marker=event['subject_id']))
 for event in j.all("SELECT payload_json FROM events WHERE kind='parent-completion-submitted'"):
  v=json.loads(event['payload_json']);reservation=v.get('live_reservation')
  if reservation:
   message=j.one('SELECT to_key FROM messages WHERE message_id=?',(v['receipt']['message_id'],));peer=j.one('SELECT native_id FROM peer_instances WHERE peer_key=? ORDER BY last_seen DESC LIMIT 1',(message['to_key'],))
   records.append({'reservation':reservation,'native_id':peer['native_id'],'marker':v['receipt']['message_id']})
 evidence=[]
 for record in records:
  if record['native_id'] not in peers:continue
  p=peers[record['native_id']];source,events=native_events(p['peer_key']['provider'],record['native_id'],p['cwd'])
  incoming=[e for e in events if e['kind']=='user' and record['marker'] in e['text']]
  if not incoming:continue
  start=min(e['at'] for e in incoming);finals=[e for e in events if e['kind']=='assistant' and e.get('terminal') and e['at']>=start]
  # A following admitted user input does not make an earlier terminal ambiguous: only settle with a terminal after this input.
  if not finals:continue
  final=min(finals,key=lambda x:x['at']);reservation=next(r for r in ledger['reservations'] if r['id']==record['reservation'])
  if reservation['state'] in ('admitted','uncertain'):b.settle(reservation['id'],observed_turns=1,native_id=record['native_id'],completed_at=final['at'])
  evidence.append({'reservation':reservation['id'],'native_id':record['native_id'],'input_event_ids':[e['id'] for e in incoming],'response_event_id':final['id'],'completed_at':final['at'],'native_source':str(source),'marker':record['marker']})
 atomic_json(ROOT/'evidence/final-input-observations.json',{'observations':evidence})
 ledger=json.loads(b.path.read_text());print(json.dumps({k:ledger[k] for k in ['consumed_turns','reserved_turns','outstanding_native_requests','active_seconds']}))
 j.close()
if __name__=='__main__':collect()
