"""Durable live-verification usage; a null ceiling means tracking without a cap."""
from __future__ import annotations

import json
from pathlib import Path
import time
import uuid

from .files import advisory_lock, atomic_json


class LiveBudget:
    def __init__(self, path: Path):
        self.path = path

    def change(self, operation):
        with advisory_lock(self.path.with_suffix(".lock")):
            data = json.loads(self.path.read_text())
            result = operation(data)
            data["updated_at_unix"] = time.time()
            atomic_json(self.path, data)
            return result

    def reserve(self, label: str, count: int = 1, tranche: str = "M1") -> str:
        if count < 1:
            raise ValueError("A reservation must contain at least one turn")
        def operation(data):
            elapsed = sum((i.get("end") or time.time()) - i["start"] for i in data["active_intervals"])
            time_limit=data['ceiling']['active_seconds'];turn_limit=data['ceiling']['submitted_turns']
            if time_limit is not None and elapsed >= time_limit:
                raise RuntimeError("BUDGET_EXHAUSTED: active live time")
            used = sum(r["count"] for r in data["reservations"] if r["state"] != "not_admitted")
            if turn_limit is not None and used + count > turn_limit:
                raise RuntimeError("BUDGET_EXHAUSTED: submitted turns")
            if turn_limit is not None and tranche not in ("M1", "first_cross_provider_delegations") and used + count > turn_limit-data.get('delegation_reserve',0):
                raise RuntimeError("BUDGET_EXHAUSTED: preserve delegation reserve")
            reservation = str(uuid.uuid4())
            data["reservations"].append(dict(id=reservation, label=label, count=count,
                                              tranche=tranche, state="reserved", at=time.time()))
            data["reserved_turns"] = sum(r["count"] for r in data["reservations"] if r["state"] == "reserved")
            return reservation
        return self.change(operation)

    def admit(self, reservation: str):
        def operation(data):
            record = next(r for r in data["reservations"] if r["id"] == reservation)
            if record["state"] != "reserved":
                raise RuntimeError("Reservation already used")
            admitted_at=time.time()
            record.update(state="admitted", admitted_at=admitted_at)
            if not data["outstanding_native_requests"]:
                data["active_intervals"].append({"start":admitted_at,"end":None,"reservation_ids":[]})
            interval=data['active_intervals'][-1]
            if 'reservation_ids' in interval:interval['reservation_ids'].append(reservation)
            data["outstanding_native_requests"].append(reservation)
            data["consumed_turns"] += record["count"]
            data["reserved_turns"] -= record["count"]
        self.change(operation)

    def settle(self, reservation: str, *, observed_turns: int | None = None, native_id=None, completed_at=None):
        def operation(data):
            record = next(r for r in data["reservations"] if r["id"] == reservation)
            if record["state"] not in ("admitted", "uncertain"):
                raise RuntimeError("Only admitted work can settle")
            record.update(state="settled", settled_at=time.time(), observed_turns=observed_turns,
                          native_session_id=native_id,observed_completed_at=completed_at)
            data["outstanding_native_requests"].remove(reservation)
            if not data["outstanding_native_requests"]:
                interval=data['active_intervals'][-1]
                if 'reservation_ids' not in interval:
                    # Older ledgers stamped the first admission just before the interval.
                    previous_end=data['active_intervals'][-2]['end'] if len(data['active_intervals'])>1 else float('-inf')
                    interval['reservation_ids']=[r['id'] for r in data['reservations'] if r.get('state')=='settled' and r.get('admitted_at',float('-inf'))>=previous_end]
                batch=set(interval['reservation_ids'])
                interval['end']=max(r['observed_completed_at'] if r.get('observed_completed_at') is not None else r['settled_at'] for r in data['reservations'] if r['id'] in batch)
            data["active_seconds"] = sum((i.get("end") or time.time())-i["start"]
                                          for i in data["active_intervals"])
        self.change(operation)

    def release_unsubmitted(self,reservation: str):
        """Return a reservation only when the caller proved no native submission."""
        def operation(data):
            record=next(r for r in data['reservations'] if r['id']==reservation)
            if record['state']=='not_admitted':return False
            if record['state']!='admitted':return False
            now=time.time();record.update(state='not_admitted',settled_at=now,observed_turns=0,observed_completed_at=now,reason='definite-pre-submission-retention')
            data['outstanding_native_requests'].remove(reservation)
            data['consumed_turns']-=record['count']
            if not data['outstanding_native_requests']:
                interval=data['active_intervals'][-1]
                batch=set(interval.get('reservation_ids',[]))
                interval['end']=max((r.get('observed_completed_at') or r.get('settled_at',now)) for r in data['reservations'] if not batch or r['id'] in batch)
            data['active_seconds']=sum((i.get('end') or now)-i['start'] for i in data['active_intervals'])
            return True
        return self.change(operation)
