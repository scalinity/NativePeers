"""Offline checks only: no native TUI, model server, journal or production write."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


PROBE_PATH = Path(__file__).resolve().parents[1] / "probes/legacy_owner_guard.py"
SPEC = importlib.util.spec_from_file_location("legacy_owner_guard_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


@pytest.fixture
def selection(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    binary = tmp_path / "immutable-old-codex"
    binary.write_bytes(b"offline executable fixture; never run")
    actual = {"path": str(binary), "size": binary.stat().st_size,
              "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
    receipt = tmp_path / ".local/state/nativepeers/compat/runtime.json"
    receipt.parent.mkdir(parents=True)
    document = {"providers": {"codex": dict(actual, version="fixture")}}
    receipt.write_text(json.dumps(document))
    base = SimpleNamespace(select_codex=Mock(return_value=(binary, {"route": "offline fixture"})),
                           digest=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest())
    return base, binary, receipt, document, actual


def test_actual_production_approval_is_read_without_modification(selection):
    base, binary, receipt, document, actual = selection
    before = receipt.read_bytes()
    result = probe.production_selection(base)
    assert result["executable"] == actual
    assert result["approval"] == document["providers"]["codex"]
    assert result["approval_sha256"] == hashlib.sha256(before).hexdigest()
    assert result["revoked"] is False
    assert receipt.read_bytes() == before
    assert not (binary.parent / ".local/state/nativepeers/journal.sqlite3").exists()


@pytest.mark.parametrize("field,value", [("sha256", "f" * 64), ("path", "/unrelated/executable"), ("size", 0)])
def test_approval_must_match_the_actual_selected_fingerprint(selection, field, value):
    base, _, receipt, document, _ = selection
    document["providers"]["codex"][field] = value
    receipt.write_text(json.dumps(document))
    before = receipt.read_bytes()
    with pytest.raises(probe.Blocked, match="exact nonrevoked"):
        probe.production_selection(base)
    assert receipt.read_bytes() == before


def test_revoked_selected_hash_never_qualifies(selection):
    base, _, receipt, document, actual = selection
    document["retained"] = {"codex": {actual["sha256"]: {"revoked": True}}}
    receipt.write_text(json.dumps(document))
    with pytest.raises(probe.Blocked, match="nonrevoked"):
        probe.production_selection(base)


@pytest.mark.parametrize("contents", ["{", "{}", '{"providers":{"codex":null}}', '{"providers":{"codex":{}}}'])
def test_missing_or_malformed_approval_never_qualifies(selection, contents):
    base, _, receipt, _, _ = selection
    receipt.write_text(contents)
    with pytest.raises(probe.Blocked):
        probe.production_selection(base)
    assert receipt.read_text() == contents


def test_selection_race_never_qualifies(selection):
    base, binary, _, _, _ = selection
    base.select_codex.side_effect = [(binary, {}), (binary.parent / "different-selected-executable", {})]
    with pytest.raises(probe.Blocked, match="changed while"):
        probe.production_selection(base)


def test_receipt_race_never_qualifies(selection):
    base, binary, receipt, document, _ = selection
    def selecting(_):
        if base.select_codex.call_count == 2:
            receipt.write_text(json.dumps(dict(document, unrelated_transition=True)))
        return binary, {}
    base.select_codex.side_effect = selecting
    with pytest.raises(probe.Blocked, match="changed while"):
        probe.production_selection(base)


def test_target_needs_an_actual_selected_change_and_matching_approval(selection):
    base, binary, receipt, document, actual = selection
    initial = probe.production_selection(base)
    with pytest.raises(probe.Blocked, match="parent to change"):
        probe.target_selection(base, "f" * 64, initial)
    new_binary = binary.with_name("immutable-new-codex")
    new_binary.write_bytes(b"separately selected executable fixture; never run")
    base.select_codex.return_value = (new_binary, {})
    with pytest.raises(probe.Blocked, match="nonrevoked"):
        probe.target_selection(base, "f" * 64, initial)
    target = dict(path=str(new_binary), size=new_binary.stat().st_size, sha256=base.digest(new_binary))
    document["providers"]["codex"] = target
    receipt.write_text(json.dumps(document))
    with pytest.raises(probe.Blocked, match="requested target"):
        probe.target_selection(base, actual["sha256"], initial)
    assert probe.target_selection(base, target["sha256"], initial)["executable"] == target


@pytest.fixture
def original():
    owner = {"pid": 100, "start": "offline-birth"}
    incarnation = "offline-incarnation"
    outcome = {"message_id": "offline-message", "state": "ACCEPTED_QUEUED",
               "attempt": {"attempt_id": "offline-attempt", "state": "ACCEPTED_QUEUED", "external_submission_id": "native-original"},
               "receipts": [{"stage": "ACCEPTED_QUEUED", "source_native_id": "offline-native-uuid", "details_json": "unaltered native receipt"}]}
    event = {"reason": "expired", "evidence": "exact-native-frame", "owner": [owner["pid"], owner["start"]],
             "observed_incarnation": incarnation, "target_incarnation": incarnation,
             "native_id": "offline-native-uuid",
             "attempt_id": "offline-attempt", "attempt_state": "ACCEPTED_QUEUED"}
    current = dict(deepcopy(outcome), suppression=event)
    events = [{"payload_json": json.dumps(event)}]
    return {"outcome": outcome}, current, events, owner, incarnation


def test_expired_original_keeps_accepted_receipts_and_separate_suppression(original):
    old, current, events, owner, incarnation = original
    before = deepcopy((old, current, events))
    probe.verify_original(old, current, events, owner, incarnation, complete=True)
    assert (old, current, events) == before
    probe.verify_original(old, old["outcome"], [], owner, incarnation, complete=False)


@pytest.mark.parametrize("field,value", [("reason", "quiet-informational"), ("evidence", "fixture"),
                                        ("owner", [100, "reused-pid"]), ("observed_incarnation", "new-incarnation"),
                                        ("native_id", "other-native-uuid"),
                                        ("target_incarnation", "other-recipient"), ("attempt_id", "other-attempt"),
                                        ("attempt_state", "INGESTED")])
def test_suppression_must_name_the_original_expired_frame_and_registered_owner(original, field, value):
    old, current, events, owner, incarnation = original
    current["suppression"][field] = value
    events[0]["payload_json"] = json.dumps(current["suppression"])
    with pytest.raises(RuntimeError, match="original registered owner"):
        probe.verify_original(old, current, events, owner, incarnation, complete=True)


@pytest.mark.parametrize("change", ["rewrite-attempt", "rewrite-receipt", "false-ingestion", "missing-event", "duplicate-event"])
def test_rewritten_acceptance_or_missing_separate_suppression_refuses_a_pass(original, change):
    old, current, events, owner, incarnation = original
    if change == "rewrite-attempt":
        current["attempt"]["external_submission_id"] = "replacement-id"
    elif change == "rewrite-receipt":
        current["receipts"][0]["details_json"] = "changed"
    elif change == "false-ingestion":
        old["outcome"]["receipts"].append({"stage": "INGESTED"})
        current["receipts"].append({"stage": "INGESTED"})
    elif change == "missing-event":
        events.clear()
    else:
        events.append(deepcopy(events[0]))
    with pytest.raises(RuntimeError):
        probe.verify_original(old, current, events, owner, incarnation, complete=True)


def test_suppression_before_consumption_is_not_accepted_as_a_fallback_result(original):
    old, current, events, owner, incarnation = original
    with pytest.raises(RuntimeError, match="before receiver consumption"):
        probe.verify_original(old, current, events, owner, incarnation, complete=False)


def test_legacy_block_rows_are_preserved_and_never_claim_silent_tail_pass():
    frames = [{"at": "fixture Eastern time", "offset": 120, "chat_tail": ["original final"],
               "lines": ["Blocked by hook: NativePeers kept this late notice out of the conversation (expired).", "body-token"]}]
    before = deepcopy(frames)
    result = probe.legacy_ui(frames, "body-token")
    assert result["status"] == "NOT_QUALIFIED"
    assert result["block_rows_expected"] and result["block_rows_observed"]
    assert result["queued_body_visible"]
    assert result["observations"][0]["rows"] == [frames[0]["lines"][0]]
    assert frames == before


def test_model_leak_requires_decoded_request_evidence_and_rejects_original_body(tmp_path):
    model = SimpleNamespace(run=tmp_path, records=[{"number": 1}])
    with pytest.raises(RuntimeError, match="lacks a decoded body"):
        probe.no_model_body(model, "expired-body")
    body_path = tmp_path / "model-0001.json"
    body_path.write_text(json.dumps({"input": [{"role": "user", "content": "new-human-control"}]}))
    probe.no_model_body(model, "expired-body")
    body_path.write_text(json.dumps({"input": [{"role": "user", "content": "expired-body"}]}))
    with pytest.raises(RuntimeError, match="leaked into model input"):
        probe.no_model_body(model, "expired-body")
    body_path.write_text(json.dumps({"input": [], "instructions": "expired-body"}))
    with pytest.raises(RuntimeError, match="leaked into model input"):
        probe.no_model_body(model, "expired-body")


def test_canonical_helper_is_loaded_only_from_the_explicit_source_root(tmp_path):
    with pytest.raises(probe.Blocked, match="must contain"):
        probe.load_base(tmp_path)
    canonical = tmp_path / "tests/probes/quiet_close_tui.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("CANONICAL_FIXTURE = 'explicit root only'\n")
    assert probe.load_base(tmp_path).CANONICAL_FIXTURE == "explicit root only"


def test_help_runs_without_loading_helpers_or_starting_native_processes(monkeypatch):
    def forbidden(*_, **__):
        raise AssertionError("Help must not load the canonical helper or run a probe")
    monkeypatch.setattr(probe, "load_base", forbidden)
    monkeypatch.setattr(probe, "run_probe", forbidden)
    with pytest.raises(SystemExit) as stopped:
        probe.main(["--help"])
    assert stopped.value.code == 0


@pytest.mark.parametrize("option,value", [("--wait-seconds", "1201"), ("--wait-seconds", "nan"),
                                         ("--wait-seconds", "4"), ("--drain-seconds", "31"),
                                         ("--target-sha256", "bad-hash")])
def test_unbounded_or_invalid_cli_input_is_rejected_before_loading_source(monkeypatch, option, value):
    monkeypatch.setattr(probe, "load_base", Mock(side_effect=AssertionError("must validate before loading")))
    argv = ["--nativepeers-root", "/unused", "--run", "/unused/fresh", "--target-sha256", "a" * 64, option, value]
    with pytest.raises(SystemExit) as stopped:
        probe.main(argv)
    assert stopped.value.code == 2


@pytest.mark.parametrize("cleanup,blocked,expected", [
    ({"confirmed_dead": True, "output_eof": True}, False, 0),
    ({"confirmed_dead": True, "output_eof": False}, False, 1),
    ({"confirmed_dead": False, "output_eof": True, "unknown_liveness": ["uninspectable birth"]}, True, 1),
    ({"confirmed_dead": True, "output_eof": True, "terminal_errors": ["capture failed"]}, False, 1),
])
def test_missing_birth_or_eof_proof_never_passes_even_when_prerequisite_is_blocked(tmp_path, monkeypatch, cleanup, blocked, expected):
    def save(path, value):
        Path(path).write_text(json.dumps(value))
    base = SimpleNamespace(Blocked=type("CanonicalBlocked", (RuntimeError,), {}),
                           prepare_run=lambda *_: tmp_path, eastern=lambda: "offline Eastern time",
                           digest=lambda _: "offline-digest", save=save)
    resource = SimpleNamespace(stop=Mock(return_value=cleanup))
    def offline_probe(args, canonical, source, run, report, resources):
        resources.append(("tui", resource, "offline-owned-receiver"))
        if blocked:
            raise probe.Blocked("offline prerequisite failure")
    monkeypatch.setattr(probe, "load_base", lambda _: base)
    monkeypatch.setattr(probe, "run_probe", offline_probe)
    code = probe.main(["--nativepeers-root", str(tmp_path), "--run", str(tmp_path / "unused"), "--target-sha256", "a" * 64])
    assert code == expected
    report = json.loads((tmp_path / "qualification.json").read_text())
    assert report["status"] == ("PASS" if expected == 0 else "FAIL")
    assert report["cleanup"][0]["role"] == "offline-owned-receiver"
    resource.stop.assert_called_once()


def test_constructor_failure_record_prevents_a_blocked_or_successful_report(tmp_path, monkeypatch):
    def save(path, value):
        Path(path).write_text(json.dumps(value))
    base = SimpleNamespace(Blocked=type("CanonicalBlocked", (RuntimeError,), {}),
                           prepare_run=lambda *_: tmp_path, eastern=lambda: "offline Eastern time",
                           digest=lambda _: "offline-digest", save=save)
    def failed_constructor(args, canonical, source, run, report, resources):
        save(run / "receiver.constructor-failure.json", {"descendant_cleanup": "UNVERIFIED", "output_eof": False})
        raise probe.Blocked("constructor could not inspect its child")
    monkeypatch.setattr(probe, "load_base", lambda _: base)
    monkeypatch.setattr(probe, "run_probe", failed_constructor)
    assert probe.main(["--nativepeers-root", str(tmp_path), "--run", str(tmp_path / "unused"), "--target-sha256", "a" * 64]) == 1
    report = json.loads((tmp_path / "qualification.json").read_text())
    assert report["status"] == "FAIL"
    assert report["constructor_failures"][0]["descendant_cleanup"] == "UNVERIFIED"


def test_explicit_driver_can_be_outside_installed_root(tmp_path):
    driver=tmp_path / 'canonical.py';driver.write_text("EXPLICIT_DRIVER=True\n")
    assert probe.load_base(tmp_path / 'installed',driver).EXPLICIT_DRIVER


def test_target_source_map_rejects_drift_and_escape(tmp_path):
    product=tmp_path / 'nativepeers/hooks.py';product.parent.mkdir();product.write_bytes(b'qualified target')
    digest=hashlib.sha256(product.read_bytes()).hexdigest();record=tmp_path / 'target.json'
    record.write_text(json.dumps({'source':{'nativepeers/hooks.py':digest}}))
    mapping=probe.target_source(record,tmp_path)
    base=SimpleNamespace(digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest())
    assert probe.source_matches(base,tmp_path,mapping)
    product.write_bytes(b'unknown drift');assert not probe.source_matches(base,tmp_path,mapping)
    record.write_text(json.dumps({'source':{'nativepeers/hooks.py':digest,'../escape':digest}}))
    with pytest.raises(probe.Blocked,match='Invalid target'):probe.target_source(record,tmp_path)


@pytest.mark.parametrize('signum',[probe.signal.SIGTERM,probe.signal.SIGHUP])
def test_termination_runs_cleanup_and_repeated_signal_cannot_interrupt_it(tmp_path,monkeypatch,signum):
    handlers={};old={sig:probe.signal.getsignal(sig) for sig in (probe.signal.SIGTERM,probe.signal.SIGHUP,probe.signal.SIGINT)}
    monkeypatch.setattr(probe.signal,'signal',lambda sig,handler:handlers.__setitem__(sig,handler))
    base=SimpleNamespace(Blocked=type('CanonicalBlocked',(RuntimeError,),{}),prepare_run=lambda *_:tmp_path,
                         eastern=lambda:'offline Eastern time',digest=lambda _:'offline',
                         save=lambda p,x:Path(p).write_text(json.dumps(x)))
    def stop():
        handlers[probe.signal.SIGHUP](probe.signal.SIGHUP,None)
        return {'confirmed_dead':True,'output_eof':True}
    resource=SimpleNamespace(stop=Mock(side_effect=stop))
    def interrupted(args,canonical,source,run,report,resources):
        resources.append(('tui',resource,'frozen-owned-receiver'))
        handlers[signum](signum,None)
    monkeypatch.setattr(probe,'load_base',lambda _:base);monkeypatch.setattr(probe,'run_probe',interrupted)
    assert probe.main(['--nativepeers-root',str(tmp_path),'--run',str(tmp_path/'unused'),'--target-sha256','a'*64])==1
    resource.stop.assert_called_once();assert handlers==old
    report=json.loads((tmp_path/'qualification.json').read_text());assert report['status']=='FAIL';assert 'interrupted by signal' in report['error']
