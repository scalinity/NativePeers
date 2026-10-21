"""One durable live-verification allowance across every test process."""
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
            if elapsed >= data["ceiling"]["active_seconds"]:
                raise RuntimeError("BUDGET_EXHAUSTED: active live time")
            used = sum(r["count"] for r in data["reservations"] if r["state"] != "not_admitted")
            if used + count > data["ceiling"]["submitted_turns"]:
                raise RuntimeError("BUDGET_EXHAUSTED: submitted turns")
            if tranche not in ("M1", "first_cross_provider_delegations") and used + count > 32:
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
            record.update(state="admitted", admitted_at=time.time())
            if not data["outstanding_native_requests"]:
                data["active_intervals"].append({"start": time.time(), "end": None})
            data["outstanding_native_requests"].append(reservation)
            data["consumed_turns"] += record["count"]
            data["reserved_turns"] -= record["count"]
        self.change(operation)

    def settle(self, reservation: str, *, observed_turns: int | None = None, native_id=None):
        def operation(data):
            record = next(r for r in data["reservations"] if r["id"] == reservation)
            if record["state"] not in ("admitted", "uncertain"):
                raise RuntimeError("Only admitted work can settle")
            record.update(state="settled", settled_at=time.time(), observed_turns=observed_turns,
                          native_session_id=native_id)
            data["outstanding_native_requests"].remove(reservation)
            if not data["outstanding_native_requests"]:
                data["active_intervals"][-1]["end"] = time.time()
            data["active_seconds"] = sum((i.get("end") or time.time())-i["start"]
                                          for i in data["active_intervals"])
        self.change(operation)
