#!/usr/bin/env python3
r"""Opt-in issue-7 NO MODEL LEAK qualification for an already registered old TUI.

Run BEFORE the parent normally activates the new, separately approved package::

  .venv/bin/python tests/probes/legacy_owner_guard.py \
    --nativepeers-root /absolute/NativePeers \
    --run /absolute/NativePeers/evidence/scratch/legacy-owner-NEW \
    --target-sha256 NEW_PACKAGE_SHA256

The product hook must target the source root. --base-probe can select the
canonical driver from a separate issue lane. --target-source-record freezes an
explicit product source map (the JSON source field, or a path-to-SHA256 object). This probe reuses its native TUI, localhost model,
metadata-only client, whitelist environment and birth/EOF cleanup primitives.
No native process starts on import or --help. No production configuration,
approval, selection, journal, queue or original session is written by the probe.

Both standalone TUIs use the initially selected, actually approved executable,
fresh credential-free CODEX_HOMEs and one private NPEER_HOME. Only SessionStart,
SessionEnd and UserPromptSubmit are installed/trusted in those private homes.
The receiver completes a real bootstrap turn and is frozen while idle and OPEN
(QUIET would prevent progress queue admission). The sender's native shell binds
normally and sends exactly one five-second progress frame, then is stopped.

ready-for-selection.json announces the frozen birth and original queued receipt.
The receiver resumes only after expiry AND actual managed selection plus actual
nonrevoked production approval match --target-sha256. The frozen interval is
bounded by --wait-seconds (at most 1,200); expiry of that budget kills only the
freshly birth-verified owned receiver, never resumes an unqualified executable.

PASS qualifies zero extra receiver model requests, no queued body in model input,
an expired exact-owner suppression, unchanged accepted receipts, readable retained
body, empty durable queue and a new human control. Old UserPromptSubmit block rows
are expected, captured and reported; legacy silent UI-tail acceptance is NEVER
claimed. The fresh-package 44-frame silent-tail probe is a separate qualification.
External network traffic is not instrumented. Exit: 0 PASS, 1 FAIL, 2 BLOCKED.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sys
import threading
import time
import traceback
import uuid


class Blocked(RuntimeError):
    pass


def load_base(source, explicit=None):
    """Resolve the canonical helper exclusively from the explicit source root."""
    path = Path(explicit).resolve(strict=True) if explicit else source / "tests/probes/quiet_close_tui.py"
    if not path.is_file():
        raise Blocked("--nativepeers-root must contain tests/probes/quiet_close_tui.py")
    spec = importlib.util.spec_from_file_location("_legacy_owner_quiet_close_tui", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def target_source(record, source):
    document = json.loads(Path(record).read_text())
    mapping = document.get("source", document)
    if not isinstance(mapping, dict) or "nativepeers/hooks.py" not in mapping:
        raise Blocked("Target source record requires an explicit product source map")
    for name, digest in mapping.items():
        path = Path(name)
        if (path.is_absolute() or ".." in path.parts or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise Blocked("Invalid target source entry")
    return mapping


def source_matches(base, source, mapping):
    return all(not (source / name).is_symlink() and (source / name).is_file()
               and base.digest(source / name) == digest for name, digest in mapping.items())


def production_selection(base):
    """Read the real receipt directly; never initialize a production Journal or renew."""
    binary, route = base.select_codex(None)
    receipt = Path.home() / ".local/state/nativepeers/compat/runtime.json"
    try:
        raw = receipt.read_bytes()
        document = json.loads(raw)
        approved = document["providers"]["codex"]
        actual = {"path": str(binary), "sha256": base.digest(binary), "size": binary.stat().st_size}
        revoked = document.get("retained", {}).get("codex", {}).get(actual["sha256"], {}).get("revoked", False)
        if revoked or any(approved.get(k) != v for k, v in actual.items()):
            raise Blocked("Managed current lacks an exact nonrevoked production runtime approval")
        # Refuse a selection/receipt race instead of certifying the combination of two snapshots.
        again, _ = base.select_codex(None)
        if again != binary or base.digest(binary) != actual["sha256"] or receipt.read_bytes() != raw:
            raise Blocked("Managed selection or production approval changed while being inspected")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise Blocked("Production runtime approval could not be read: " + repr(error)) from error
    return {"executable": actual, "route": route, "approval_path": str(receipt),
            "approval_sha256": hashlib.sha256(raw).hexdigest(), "approval": approved, "revoked": False}


def target_selection(base, target, initial):
    binary, _ = base.select_codex(None)
    if str(binary) == initial["executable"]["path"]:
        # Immutable package selection has not moved; avoid repeatedly hashing the old package for 20 minutes.
        raise Blocked("Still waiting for the parent to change managed selection")
    selected = production_selection(base)
    if selected["executable"]["sha256"] == initial["executable"]["sha256"]:
        raise Blocked("Still waiting for the parent to change managed selection")
    if selected["executable"]["sha256"] != target:
        raise Blocked("Managed current is not the requested target SHA-256")
    return selected


def check(report, phase, condition, details=None):
    report.setdefault("assertions", []).append({"phase": phase, "passed": bool(condition), "details": details})
    if not condition:
        raise RuntimeError("Qualification failed: " + phase)


def verify_original(original, current, events, owner, incarnation, *, complete):
    """Transport acceptance and consumption suppression must remain separate evidence."""
    if (current["state"] != "ACCEPTED_QUEUED" or current["attempt"] != original["outcome"]["attempt"]
            or current["receipts"] != original["outcome"]["receipts"]):
        raise RuntimeError("Original accepted attempt/receipts changed")
    if {r["stage"] for r in current["receipts"]} & {"INGESTED", "MODEL_REPORTED_PROCESSED"}:
        raise RuntimeError("Suppressed original acquired a false ingestion receipt")
    if not complete:
        if events or current.get("suppression"):
            raise RuntimeError("Frozen original was suppressed before receiver consumption")
        return
    if len(events) != 1:
        raise RuntimeError("Expected exactly one separate suppression event")
    event = json.loads(events[0]["payload_json"])
    expected = {"reason": "expired", "evidence": "exact-native-frame", "owner": [owner["pid"], owner["start"]],
                "observed_incarnation": incarnation, "target_incarnation": incarnation,
                "native_id": original["outcome"]["receipts"][0]["source_native_id"],
                "attempt_id": original["outcome"]["attempt"]["attempt_id"], "attempt_state": "ACCEPTED_QUEUED"}
    if any(event.get(k) != v for k, v in expected.items()) or current.get("suppression") != event:
        raise RuntimeError("Suppression did not identify the expired frame and original registered owner")


def no_model_body(model, body):
    for record in model.records:
        path = model.run / f"model-{record['number']:04}.json"
        if not path.is_file():
            raise RuntimeError("Counted model request lacks a decoded body")
        if body in json.dumps(json.loads(path.read_text()), ensure_ascii=False):
            raise RuntimeError("Original queued body leaked into model input")


def legacy_ui(frames, body):
    """Observe real frames without imposing the new package's silent-tail contract."""
    rows = []
    body_visible = False
    for frame in frames:
        lines = frame.get("chat_tail", []) + frame["lines"]
        body_visible |= body in "\n".join(lines)
        hits = [line for line in lines if re.search(r"blocked by hook|NativePeers kept this late notice", line, re.I)]
        if hits:
            rows.append({"at": frame["at"], "offset": frame["offset"], "rows": hits})
    return {"status": "NOT_QUALIFIED", "scope": "Legacy silent UI-tail acceptance is not qualified",
            "block_rows_expected": True, "block_rows_observed": bool(rows), "observations": rows,
            "queued_body_visible": body_visible, "capture": "Unmodified ANSI and faithful pyte frames/history"}


async def helper_action(base, config, action, role):
    """Invoked only by a planned model-issued native shell inside an owned TUI."""
    from nativepeers.caller import bind_caller
    from nativepeers.journal import Journal
    from nativepeers.registry import Registry
    from nativepeers.routing import Router
    from nativepeers.presentation import render

    if (action, role) not in {("bootstrap", "receiver"), ("send", "sender"), ("control", "receiver")}:
        raise ValueError("Unsupported legacy-owner native helper action")
    run = Path(config["run"])
    if (Path(os.environ["NPEER_HOME"]).resolve() != run / "peer-state"
            or Path(os.environ["CODEX_HOME"]).resolve() != run / (role + "-home")):
        raise RuntimeError("Native helper refused a nonfixture home")
    journal = Journal()
    try:
        registry = Registry(journal)
        caller = bind_caller(journal, registry)
        peer = registry.verify(caller.key, caller.incarnation)
        if peer["owner_kind"] != "interactive-standalone":
            raise RuntimeError("Helper requires real SessionStart standalone registration")
        who = base.product_cli(config, "whoami")
        base.save(run / f"{role}-{action}-identity.json", who)
        if action == "bootstrap":
            policy = base.product_cli(config, "notifications", "open")
            if policy["state"] != "OPEN":
                raise RuntimeError("Receiver must be OPEN for genuine progress queue admission")
            return {"whoami": who, "policy": policy}
        original_path = run / "original-accepted.json"
        if action == "send":
            if production_selection(base)["executable"] != config["initial"]["executable"]:
                raise RuntimeError("Parent changed selection before the original frame was queued")
            receiver = json.loads((run / "receiver-bootstrap-identity.json").read_text())
            # The source and native shell must prove both endpoints; no Caller/identity is fabricated.
            recipient = registry.verify(receiver["key"], receiver["incarnation_id"])
            if recipient["owner"] != receiver["owner"]:
                raise RuntimeError("Receiver owner changed before the native send")
            request_id = "legacy-owner-" + config["token"]
            if original_path.exists() or journal.one(
                    "SELECT 1 FROM send_requests WHERE caller_key=? AND client_request_id=?", (caller.key, request_id)):
                raise RuntimeError("One-shot send refused to replay an existing request")
            outcome = await Router(journal, registry).send(
                caller, receiver["key"], config["body"], kind="notify", expires_seconds=5,
                client_request_id=request_id)
            envelope = json.loads(journal.one("SELECT payload_json FROM messages WHERE message_id=?",
                                               (outcome["message_id"],))["payload_json"])
            original = {"request_id": request_id, "outcome": outcome, "envelope": envelope, "frame": render(envelope)}
            base.save(original_path, original)  # Retain even an uncertain result; never retry it.
            if (outcome["state"] != "ACCEPTED_QUEUED" or not outcome["attempt"].get("external_submission_id")
                    or len(outcome["receipts"]) != 1 or outcome["receipts"][0]["stage"] != "ACCEPTED_QUEUED"):
                raise RuntimeError("The original progress frame lacks exact native queue acceptance")
            return {"message_id": outcome["message_id"], "state": outcome["state"], "whoami": who}
        original = json.loads(original_path.read_text())
        before = journal.all("SELECT * FROM receipts ORDER BY receipt_id")
        retained = base.product_cli(config, "inbox", "--retained", "--limit", "100")
        base.save(run / "control-retained.json", retained)
        if retained != [original["envelope"]] or before != journal.all("SELECT * FROM receipts ORDER BY receipt_id"):
            raise RuntimeError("Retained native read changed body/receipts or did not expose the original")
        # Do not print the retained body into the model's shell result.
        return {"whoami": who, "retained_body_readable": True, "receipts_unchanged": True}
    finally:
        journal.close()


def helper_main(argv):
    config_path, action, role, done = argv
    config = json.loads(Path(config_path).read_text())
    source = Path(config["source"])
    sys.path.insert(0, str(source))
    base = load_base(source, config.get("base_probe"))
    try:
        result = asyncio.run(helper_action(base, config, action, role))
    except BaseException as error:
        base.save(done, {"ok": False, "error": repr(error), "traceback": traceback.format_exc()})
        raise
    base.save(done, {"ok": True, "result": result})
    print("LEGACY_OWNER_HELPER_DONE " + action, flush=True)


def run_probe(args, base, source, run, report, resources):
    initial = production_selection(base)
    report["initial_selection"] = initial
    binary = Path(initial["executable"]["path"])
    if initial["executable"]["sha256"] == args.target_sha256:
        raise Blocked("--target-sha256 must differ from the initially approved managed executable")
    sys.path.insert(0, str(source))
    from nativepeers.install import Installer
    from nativepeers.journal import Journal
    from nativepeers.process import owned_status, signal_captured
    from nativepeers.registry import Registry
    from nativepeers.routing import Router
    try:
        import pyte
    except ImportError as error:
        raise Blocked("pyte is required for faithful terminal capture") from error
    hook = source / "bin/npeer-hook"
    if "export PYTHONPATH=" + str(source) + "${PYTHONPATH" not in hook.read_text():
        raise Blocked("The product npeer-hook must target --nativepeers-root; no hook is rewritten")
    sources = [*sorted((source / "nativepeers").rglob("*.py")), hook]
    driver = Path(args.base_probe).resolve(strict=True) if args.base_probe else source / "tests/probes/quiet_close_tui.py"
    planned = target_source(args.target_source_record, source)
    report["planned_source"] = {"record": str(Path(args.target_source_record).resolve()), "sha256": base.digest(args.target_source_record), "files": planned}
    report["base_probe"] = {"path": str(driver), "sha256": base.digest(driver)}
    hashes = {str(p.relative_to(source)): base.digest(p) for p in sources}
    report["source"] = {"root": str(source), "sha256": hashes,
                        "python": {"path": sys.executable, "sha256": base.digest(sys.executable)}}
    token = uuid.uuid4().hex[:12]
    config = {"run": str(run), "source": str(source), "base_probe": str(driver), "initial": initial, "token": token,
              "body": "LEGACY_OWNER_EXPIRED_PROGRESS_" + token}
    config_path = run / "helper-config.json"
    base.save(config_path, config)
    (run / "tmp").mkdir()
    cwd = run / "workspace"
    cwd.mkdir()
    state = run / "peer-state"
    models, environments, tuis = {}, {}, []

    def stop_owned(kind, resource, role):
        try:
            cleanup = resource.stop()
            resources.remove((kind, resource, role))
            report.setdefault("cleanup", []).append({"role": role, **cleanup})
            if not cleanup["confirmed_dead"] or not cleanup.get("output_eof") or cleanup.get("terminal_errors"):
                raise RuntimeError("Owned process death/output EOF was not proved: " + role)
        except BaseException as error:
            report.setdefault("cleanup_errors", []).append({"role": role, "error": repr(error)})
            raise

    for role in ("receiver", "sender"):
        model_run = run / (role + "-model")
        model_run.mkdir()
        model = base.Model(model_run)
        model.thread = threading.Thread(target=model.serve_forever, daemon=True)
        try:
            model.thread.start()
        except BaseException:
            model.server_close()
            raise
        resources.append(("model", model, role))
        models[role] = model
        home = run / (role + "-home")
        home.mkdir(mode=0o700)
        env = base.private_env(home, state, run, source)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        environments[role] = env
        report.setdefault("environments", {})[role] = env
        shell_env = {k: env[k] for k in ("CODEX_HOME", "NPEER_HOME", "PYTHONPATH", "TMPDIR")}
        (home / "config.toml").write_text(f'''model = "fixture"
model_provider = "fixture"
model_context_window = 100000
approval_policy = "never"
sandbox_mode = "danger-full-access"
check_for_update_on_startup = false
web_search = "disabled"
[features]
daemon_auto_start = false
[model_providers.fixture]
name = "Credential-free localhost legacy-owner fixture"
base_url = "http://127.0.0.1:{model.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
[projects.{json.dumps(str(cwd))}]
trust_level = "trusted"
[shell_environment_policy]
inherit = "all"
set = {{ {", ".join(json.dumps(k) + " = " + json.dumps(v) for k, v in shell_env.items())} }}
''')
        journal = Journal(state)
        try:
            installer = Installer(journal, home=run, native=False)
            desired = installer.desired_hooks("codex")
            groups = {name: desired[name] for name in ("SessionStart", "SessionEnd", "UserPromptSubmit")}
            expected = installer.command_for("codex")
        finally:
            journal.close()
        (home / "hooks.json").write_text(json.dumps({"hooks": {k: [v] for k, v in groups.items()}}))
        rpc = base.Metadata(binary, cwd, env, run, role + "-trust")
        resources.append(("metadata", rpc, role + "-trust"))
        rpc.start()
        hooks = base.inventory_hooks(groups, rpc.call("hooks/list", {"cwds": [str(cwd)]}), home / "hooks.json", expected)
        read = rpc.call("config/read", {"includeLayers": True, "cwd": str(cwd)})
        layer = next(l for l in read["layers"] if l["name"].get("type") == "user" and not l["name"].get("profile"))
        if Path(layer["name"]["file"]).resolve() != home / "config.toml":
            raise RuntimeError("Refused config write outside private CODEX_HOME")
        rpc.call("config/batchWrite", {"filePath": str(home / "config.toml"), "expectedVersion": layer["version"],
                                      "reloadUserConfig": True, "edits": [{"keyPath": "hooks.state", "value": {
                                          h["key"]: {"enabled": True, "trusted_hash": h["currentHash"]} for h in hooks},
                                          "mergeStrategy": "replace"}]})
        trusted = base.inventory_hooks(groups, rpc.call("hooks/list", {"cwds": [str(cwd)]}), home / "hooks.json", expected)
        check(report, role + " private legacy hooks trusted", all(h["enabled"] and h["trustStatus"] == "trusted" for h in trusted), trusted)
        stop_owned("metadata", rpc, role + "-trust")
    # Metadata is read-only after this trust step; no thread is started/resumed through RPC.
    from subprocess import run as subprocess_run
    version = subprocess_run([str(binary), "--version"], cwd=cwd, env=environments["receiver"],
                             capture_output=True, text=True, timeout=20)
    check(report, "initial approved executable version", version.returncode == 0,
          {"stdout": version.stdout, "stderr": version.stderr})

    def pump_until(predicate, description, seconds=base.WAIT):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for tui in tuis:
                tui.pump()
                if tui.process.poll() is not None:
                    raise RuntimeError("Owned TUI exited during " + description)
            if any(m.errors for m in models.values()):
                raise RuntimeError("Local model fixture failed: " + repr({r: m.errors for r, m in models.items()}))
            if predicate():
                return
            time.sleep(.03)
        raise TimeoutError(description)

    def plan(role, action):
        marker = f"LEGACY_OWNER_COMMAND_{token}_{role}_{action}"
        done = run / (action + "-done.json")
        final = f"LEGACY_OWNER_FINAL_{token}_{role}_{action}"
        command = shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(config_path), action, role, str(done)])
        models[role].plan(marker, command, done, final)
        report.setdefault("commands", []).append({"role": role, "action": action, "input": marker,
                                                 "native_shell": command, "done": str(done), "final": final})
        return marker, done, final

    def completed(tui, done, final):
        if done.exists() and not json.loads(done.read_text()).get("ok"):
            raise RuntimeError("Native shell helper failed: " + done.read_text())
        return (done.exists() and final in tui.text() and not base.ACTIVE.search("\n".join(tui.screen.display[-5:]))
                and base.native_tail(run / (tui.role + "-home"), identities[tui.role]["native_session_id"], final) is not None)

    identities = {}
    marker, done, bootstrap_final = plan("receiver", "bootstrap")
    receiver = base.Tui([str(binary), "--no-daemon", "--name", "Issue 7 private legacy receiver " + token, marker],
                        cwd, environments["receiver"], run, "receiver", pyte)
    resources.append(("tui", receiver, "receiver"))
    tuis.append(receiver)
    identity_path = run / "receiver-bootstrap-identity.json"
    pump_until(identity_path.exists, "receiver real native shell whoami")
    identities["receiver"] = json.loads(identity_path.read_text())
    check(report, "receiver SessionStart identity matches exact owned birth", identities["receiver"]["owner"] == receiver.birth,
          identities["receiver"])
    pump_until(lambda: completed(receiver, done, bootstrap_final), "receiver completed bootstrap and idle final")
    queue_reader = base.Metadata(binary, cwd, environments["receiver"], run, "receiver-queue-reader")
    resources.append(("metadata", queue_reader, "receiver-queue-reader"))
    queue_reader.start()

    def read_queue(label):
        value = queue_reader.call("thread/queue/list", {"threadId": identities["receiver"]["native_session_id"], "limit": 100})
        base.save(run / (label + "-queue.json"), value)
        return base.queue_ids(value)

    def inspect_original(label, complete):
        journal = Journal(state, read_only=True)
        try:
            outcome = Router(journal, Registry(journal)).outcome(original["outcome"]["message_id"])
            events = journal.all("SELECT * FROM events WHERE subject_id=? AND kind='notification-suppressed' ORDER BY sequence",
                                 (original["outcome"]["message_id"],))
            stored = journal.one("SELECT payload_json FROM messages WHERE message_id=?", (outcome["message_id"],))
            base.save(run / (label + "-original.json"), {"outcome": outcome, "events": events, "stored": stored})
            check(report, label + " original body unchanged", json.loads(stored["payload_json"]) == original["envelope"])
            verify_original(original, outcome, events, receiver.birth, identities["receiver"]["incarnation_id"], complete=complete)
            check(report, label + " original transport/suppression evidence", True, {"outcome": outcome, "events": events})
        finally:
            journal.close()

    check(report, "receiver starts with empty durable queue", not read_queue("initial"))
    check(report, "initial selection still approved before freeze", production_selection(base)["executable"] == initial["executable"])
    history_path = Path(base.native_tail(run / "receiver-home", identities["receiver"]["native_session_id"], bootstrap_final)["path"])
    history_snapshot = run / "receiver-before-freeze.jsonl"
    base.shutil.copyfile(history_path, history_snapshot)
    receiver.freeze()
    frozen_at, frozen_start = base.eastern(), time.monotonic()
    baseline = models["receiver"].count()
    # A real-time alarm bounds every intervening synchronous RPC/subprocess wait too.
    def expire_frozen_budget(*_):
        sent = signal_captured(receiver.birth, signal.SIGKILL)
        receiver.signals.append({"owner": receiver.birth, "signal": "SIGKILL", "sent": sent, "cause": "frozen budget expired"})
        raise TimeoutError("Frozen receiver exceeded the bounded selection wait")

    previous_alarm = signal.signal(signal.SIGALRM, expire_frozen_budget)
    signal.setitimer(signal.ITIMER_REAL, args.wait_seconds)
    try:
        marker, done, sender_final = plan("sender", "send")
        sender = base.Tui([str(binary), "--no-daemon", "--name", "Issue 7 private legacy sender " + token, marker],
                          cwd, environments["sender"], run, "sender", pyte)
        resources.append(("tui", sender, "sender"))
        tuis.append(sender)
        sender_identity_path = run / "sender-send-identity.json"
        pump_until(sender_identity_path.exists, "sender real native shell whoami")
        identities["sender"] = json.loads(sender_identity_path.read_text())
        check(report, "sender SessionStart identity matches exact owned birth", identities["sender"]["owner"] == sender.birth,
              identities["sender"])
        pump_until(lambda: completed(sender, done, sender_final), "one native progress send and sender final")
        original = json.loads((run / "original-accepted.json").read_text())
        check(report, "one original frame is ACCEPTED_QUEUED", original["outcome"]["state"] == "ACCEPTED_QUEUED", original)
        expected_queue = {original["outcome"]["attempt"]["external_submission_id"]}
        check(report, "frozen queue contains exactly the original submission", read_queue("accepted-frozen") == expected_queue)
        inspect_original("accepted-frozen", False)
        stop_owned("tui", sender, "sender")
        tuis.remove(sender)
        check(report, "sender stopped before selection wait", owned_status(sender.birth) == "DEAD")
        report["identities"] = identities
        ready = {"at": base.eastern(), "frozen_at": frozen_at, "receiver_birth": receiver.birth,
                 "initial_selection": initial, "target_sha256": args.target_sha256,
                 "original": original, "maximum_frozen_seconds": args.wait_seconds,
                 "elapsed_frozen_seconds": time.monotonic() - frozen_start}
        base.save(run / "ready-for-selection.json", ready)
        base.save(run / "qualification.json", report)
        print(json.dumps({"status": "WAITING_FOR_PARENT_SELECTION", "ready": str(run / "ready-for-selection.json")}), flush=True)
        selected = None
        def target_ready():
            nonlocal selected
            try:
                selected = target_selection(base, args.target_sha256, initial)
            except (Blocked, base.Blocked, OSError) as error:
                report["selection_wait_last_blocked"] = repr(error)
                return False
            return time.time() > original["envelope"]["expires_at"] + .25
        # Bound the polling interval without spawning or messaging any production session.
        deadline = frozen_start + args.wait_seconds
        while not target_ready():
            receiver.pump()
            if receiver.process.poll() is not None or owned_status(receiver.birth) != "LIVE":
                raise RuntimeError("Frozen receiver birth is no longer authoritatively live")
            if time.monotonic() >= deadline:
                expire_frozen_budget()
            time.sleep(.25)
        report["target_selection"] = selected
        check(report, "planned installed source present before legacy consumption", source_matches(base, source, planned)
              and all(base.digest(source / p) == h for p, h in hashes.items() if p not in planned)
              and base.digest(driver) == report["base_probe"]["sha256"]
              and base.digest(__file__) == report["probe_sha256"])
        check(report, "old owned executable unchanged", base.digest(binary) == initial["executable"]["sha256"])
        check(report, "no receiver model work while frozen", models["receiver"].count() == baseline)
        check(report, "original remains durable before resume", read_queue("before-resume") == expected_queue)
        inspect_original("before-resume", False)
        frozen_frames = len(receiver.frames)
        # Hash/approval are re-read after the final metadata check, immediately before SIGCONT.
        report["resume_selection"] = target_selection(base, args.target_sha256, initial)
        check(report, "resume only the exact live receiver birth", owned_status(receiver.birth) == "LIVE"
              and signal_captured(receiver.birth, signal.SIGCONT))
        receiver.signals.append({"owner": receiver.birth, "signal": "SIGCONT", "sent": True})
        report["frozen_interval"] = {"started_at": frozen_at, "resumed_at": base.eastern(),
                                      "seconds": time.monotonic() - frozen_start, "limit": args.wait_seconds}
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_alarm)

    monitor = Journal(state, read_only=True)
    try:
        pump_until(lambda: bool(monitor.one("SELECT 1 FROM events WHERE subject_id=? AND kind='notification-suppressed'",
                                           (original["outcome"]["message_id"],))), "old registered owner suppresses original expired frame")
    finally:
        monitor.close()
    end = time.monotonic() + args.drain_seconds
    pump_until(lambda: time.monotonic() >= end, "bounded legacy drain dwell", args.drain_seconds + 5)
    check(report, "NO MODEL LEAK: zero extra receiver requests after selection change", models["receiver"].count() == baseline,
          {"before": baseline, "after": models["receiver"].count()})
    no_model_body(models["receiver"], config["body"])
    check(report, "original queue body absent from actual receiver model inputs", True)
    inspect_original("after-consumption", True)
    check(report, "durable original queue drained by receiver", not read_queue("after-consumption"))
    journal = Journal(state, read_only=True)
    try:
        rows = journal.all("SELECT * FROM peer_instances WHERE peer_key=?", (identities["receiver"]["key"],))
        check(report, "guard kept the original registered incarnation", len(rows) == 1
              and rows[0]["incarnation_id"] == identities["receiver"]["incarnation_id"] and rows[0]["status"] == "live", rows)
        check(report, "exactly one original message and native admission", journal.one("SELECT COUNT(*) n FROM messages")["n"] == 1
              and journal.one("SELECT COUNT(*) n FROM attempts")["n"] == 1)
    finally:
        journal.close()
    report["legacy_ui_tail"] = legacy_ui(receiver.frames[frozen_frames:], config["body"])
    base.save(run / "legacy-ui.json", report["legacy_ui_tail"])
    base.save(run / "before-human-native-tail.json", base.native_tail(
        run / "receiver-home", identities["receiver"]["native_session_id"], bootstrap_final))

    # A real new human turn, issued once through this owned PTY, proves the old owner remains usable.
    marker, done, control_final = plan("receiver", "control")
    control_before = models["receiver"].count()
    check(report, "zero extra receiver requests through all pre-control inspections", control_before == baseline,
          {"bootstrap": baseline, "before_human": control_before})
    receiver.send(marker)
    pump_until(lambda: completed(receiver, done, control_final), "new human control request, retained read and expected answer")
    control = json.loads(done.read_text())["result"]
    control_records = models["receiver"].records[control_before:]
    check(report, "new human control produced expected model requests and answer", len(control_records) >= 2
          and all(marker in base.last_user_text(json.loads((models["receiver"].run / f"model-{r['number']:04}.json").read_text()))
                  for r in control_records)
          and control["retained_body_readable"] and control["receipts_unchanged"]
          and control["whoami"]["owner"] == receiver.birth
          and control["whoami"]["incarnation_id"] == identities["receiver"]["incarnation_id"], control)
    no_model_body(models["receiver"], config["body"])
    inspect_original("after-human-control", True)
    check(report, "queue remains empty after human control", not read_queue("after-human-control"))
    check(report, "no private runtime approval was created or copied", not (state / "compat/runtime.json").exists())
    report["final_selection"] = target_selection(base, args.target_sha256, initial)
    check(report, "planned sources and old executable remain unchanged", source_matches(base, source, planned)
          and all(base.digest(source / p) == h for p, h in hashes.items() if p not in planned)
          and base.digest(driver) == report["base_probe"]["sha256"]
          and base.digest(binary) == initial["executable"]["sha256"] and base.digest(__file__) == report["probe_sha256"])
    check(report, "native history preserves the original completed bootstrap", history_path.read_bytes().startswith(history_snapshot.read_bytes()),
          {"snapshot": str(history_snapshot), "sha256": base.digest(history_snapshot)})
    report["native_history"] = [{"path": str(p), "sha256": base.digest(p),
                                 "queued_body_present": config["body"] in p.read_text()}
                                for role in ("receiver", "sender")
                                for p in sorted((run / (role + "-home") / "sessions").rglob("*.jsonl"))]
    check(report, "both owned native histories retained", len(report["native_history"]) >= 2)
    report["model_counts"] = {"receiver_bootstrap": baseline, "receiver_before_human": control_before,
                              "receiver_after_human": models["receiver"].count(), "sender": models["sender"].count()}
    report["no_model_leak"] = "PASS"


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--nativepeers-root", required=True, help="Explicit source root containing the canonical TUI probe")
    result.add_argument("--base-probe", help="Explicit canonical TUI driver; defaults to source root tests/probes/quiet_close_tui.py")
    result.add_argument("--target-source-record", help="Required for live run: JSON source map expected after normal activation")
    result.add_argument("--run", required=True, help="Fresh absolute git-ignored directory inside the source root")
    result.add_argument("--target-sha256", required=True, help="Exact NEW executable SHA-256; parent activates and approves normally")
    result.add_argument("--wait-seconds", type=float, default=1200, help="Maximum frozen interval, 5 through 1200 seconds (default: 1200)")
    result.add_argument("--drain-seconds", type=float, default=8, help="Observation after suppression, 5 through 30 seconds (default: 8)")
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_helper":
        helper_main(argv[1:])
        return 0
    cli = parser()
    args = cli.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.target_sha256):
        cli.error("--target-sha256 must be exactly 64 lowercase hexadecimal characters")
    if not 5 <= args.wait_seconds <= 1200 or not 5 <= args.drain_seconds <= 30:
        cli.error("--wait-seconds must be 5..1200 and --drain-seconds must be 5..30")
    try:
        source = Path(args.nativepeers_root).expanduser().resolve(strict=True)
        base = load_base(source, args.base_probe) if args.base_probe else load_base(source)
    except (Blocked, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}))
        return 2
    try:
        run = base.prepare_run(args.run, source)
    except (base.Blocked, OSError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}))
        return 2
    report = {"status": "RUNNING", "started_at": base.eastern(), "run": str(run), "cwd": str(Path.cwd()),
              "command": [sys.executable, str(Path(__file__).resolve()), *argv], "probe_sha256": base.digest(__file__),
              "scope": "NO MODEL LEAK for original registered interactive owner across managed selection change",
              "legacy_ui_tail": {"status": "NOT_QUALIFIED", "block_rows_expected": True},
              "limitations": ["Legacy UserPromptSubmit may render block rows; silent UI-tail acceptance is not qualified.",
                              "The fresh-package 44-frame silent-tail and worker/controller lifecycle checks are separate.",
                              "External network traffic is not instrumented; only configured localhost model requests are counted."]}
    resources = []
    code = 1
    try:
        run_probe(args, base, source, run, report, resources)
        report["status"], code = "PASS", 0
    except BaseException as error:
        blocked = isinstance(error, (Blocked, base.Blocked))
        report.update(status="BLOCKED" if blocked else "FAIL", error=repr(error), traceback=traceback.format_exc())
        code = 2 if blocked else 1
    finally:
        for kind, resource, role in reversed(resources):
            try:
                if kind == "model":
                    report.setdefault("models", {})[role] = {"records": resource.records, "errors": resource.errors,
                                                             "url": f"http://127.0.0.1:{resource.server_port}/v1"}
                    resource.shutdown()
                    resource.server_close()
                    resource.thread.join(3)
                    if resource.thread.is_alive() or resource.socket.fileno() != -1:
                        raise RuntimeError("Localhost model server cleanup unconfirmed")
                else:
                    cleanup = resource.stop()
                    report.setdefault("cleanup", []).append({"role": role, **cleanup})
                    if not cleanup["confirmed_dead"] or not cleanup.get("output_eof") or cleanup.get("terminal_errors"):
                        raise RuntimeError("Owned process death/output EOF unconfirmed")
            except BaseException as error:
                report.setdefault("cleanup_errors", []).append({"role": role, "error": repr(error)})
        constructors = sorted(run.glob("*.constructor-failure.json"))
        if constructors:
            report["constructor_failures"] = [json.loads(p.read_text()) for p in constructors]
        if report.get("cleanup_errors") or constructors:
            report["status"], code = "FAIL", 1
        report["finished_at"] = base.eastern()
        base.save(run / "qualification.json", report)
    print(json.dumps({"status": report["status"], "report": str(run / "qualification.json"), "error": report.get("error")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
