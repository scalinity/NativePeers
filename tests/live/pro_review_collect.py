#!/usr/bin/env python3
"""Observe exact owned parents and settle the retained Pro-review ledger."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from nativepeers.budget import LiveBudget
from nativepeers.core import Core
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from tests.live.pro_review import RUN,STATE,LEDGER,source,stamp
from tests.live.review_collect import native_events
from tests.live.collect_antigravity import antigravity_events


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
            ledger=json.loads(LEDGER.read_text())
            for reservation_id in [launch['reservation'],*launch.get('continuation_reservations',[])]:
                reservation=next(r for r in ledger['reservations'] if r['id']==reservation_id)
                completed=[e for e in finals if e['at']>=reservation.get('admitted_at',float('inf'))]
                if completed and reservation['state'] in ('admitted','uncertain'):
                    budget.settle(reservation_id,observed_turns=1,native_id=case['parent_native_id'],completed_at=completed[-1]['at'])
            checks.append(dict(provider=provider,status='PASS' if all(assertions.values()) else 'PENDING' if case.get('status') is None or not finals else 'FAIL',
                assertions=assertions,parent_native_id=case['parent_native_id'],worker_native_ids=ids,native_history=str(history),
                parent_terminal_event_ids=[e['id'] for e in finals],source=case['source'],groups=case.get('groups',[])))
        ledger=json.loads(LEDGER.read_text());report=dict(at=stamp(),checks=checks,budget={k:ledger[k] for k in ('consumed_turns','reserved_turns','active_seconds','outstanding_native_requests')})
        atomic_json(RUN/'report.json',report)
        print(json.dumps(dict(checks=[dict(provider=c['provider'],status=c['status'],not_yet_true=[k for k,v in c['assertions'].items() if not v]) for c in checks],budget=report['budget'])))
    finally:journal.close()


if __name__=='__main__':collect()
