#!/usr/bin/env python3
"""Read exact owned test queues; remove only a known owned residual submission ID."""
import asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.adapters.codex_app_server import AppServer
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
async def main():
 j=Journal(ROOT/'evidence/private/live-state');ledger=json.loads((ROOT/'evidence/live-budget.json').read_text());sessions={r['native_session_id'] for r in ledger['reservations'] if r.get('native_session_id','').startswith('01')}
 known={r['external_submission_id'] for r in j.all('SELECT external_submission_id FROM attempts') if r['external_submission_id']}
 for p in (ROOT/'evidence/private/final-batch').glob('*.json'):
  v=json.loads(p.read_text());sid=v.get('receipt',{}).get('external_submission_id')
  if sid:known.add(sid)
 c=AppServer(Path.home()/'.local/share/codex-mcp-manager/current/bin/codex');report=[]
 try:
  await c.start()
  for native in sorted(sessions):
   rows=(await c.call('thread/queue/list',{'threadId':native,'limit':100}))
   remaining=[];removed=[]
   for item in rows['data']:
    if item['id'] in known:
     await c.call('thread/queue/delete',{'threadId':native,'queuedSubmissionId':item['id']});removed.append(item['id'])
    else:remaining.append(item['id'])
   report.append({'native_id':native,'remaining_submission_ids':remaining,'removed_owned_ids':removed,'next_cursor':rows.get('nextCursor')})
 finally:await c.close();j.close()
 atomic_json(ROOT/'evidence/queue-settlement.json',{'status':'PASS' if all(not r['remaining_submission_ids'] and not r['next_cursor'] for r in report) else 'BLOCKED','read_api':'thread/queue/list','owned_removal_api':'thread/queue/delete','threads':report,'new_model_inputs':0})
 print(json.dumps(report))
if __name__=='__main__':asyncio.run(main())
