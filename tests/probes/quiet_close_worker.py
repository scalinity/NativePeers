#!/usr/bin/env python3
"""Post-activation worker push acceptance; --help/import never launches a candidate.

Reuse quiet_close_tui.py's native PTY/model/capture harness. Three fresh standalone
parents each spawn one real gpt-6.1-sol/xhigh analysis worker. OPEN must receive
the genuine controller push; QUIET retains it before admission; QUEUED accepts
the original completion before close and consumes it silently after the final.
Only each parent's normal launch has a fixture prompt. A still-running native
shell helper reads the result later without injecting a prompt or caller binding.
All provider traffic is configured for localhost; external traffic is not measured.
Requires pyte and the existing NativePeers Python dependencies. No installation,
runtime qualification, approval renewal, synthetic routes or frame replay occurs.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import quiet_close_tui as base

MODEL, EFFORT = "gpt-6.1-sol", "xhigh"
PHASES = ("open", "quiet", "queued")


def approved_record(path, actual):
    if path.is_symlink():
        raise base.Blocked("Installed approval must be an actual file")
    try:
        document = json.loads(path.read_text())
        record = document["providers"]["codex"]
        exact = all(record.get(key) == actual[key] for key in ("path", "sha256", "size"))
        revoked = document.get("retained", {}).get("codex", {}).get(actual["sha256"], {}).get("revoked", False)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise base.Blocked("Installed Codex runtime approval is unavailable") from error
    if not exact or revoked:
        raise base.Blocked("Selected package lacks exact installed, non-revoked approval; no launch permitted")
    return record


def spawn_spec(phase, token):
    if phase not in PHASES:
        raise ValueError("Unknown worker acceptance phase")
    return {"schema_version": 1, "client_request_id": str(uuid.uuid4()), "completion_mode": "push",
            "workers": [{"provider": "codex", "title": f"Quiet push {phase} {token}",
                         "task": f"Return QCW_TASK_{phase}_{token} as directed by the localhost fixture.",
                         "model": MODEL, "reasoning_effort": EFFORT, "permission_profile": "analysis",
                         "workspace": {"mode": "inherit-read-only"}}],
            "limits": {"max_concurrent_workers": 1, "max_depth": 1, "wall_time_seconds": 180,
                       "max_task_turns_per_worker": 1}}


def wait_file(path, seconds=180):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(str(path))
        time.sleep(.03)


def completion(journal, group, target):
    return journal.one("SELECT m.*,c.state route_state FROM messages m JOIN send_requests s USING(message_id) "
                       "JOIN completion_routes c ON c.delivery_id=s.client_request_id "
                       "WHERE c.group_id=? AND c.parent_key=? AND m.to_key=?", (group, target, target))


def require_accepted(outcome, envelope, queue, group, target, native_id):
    stages = {receipt["stage"] for receipt in outcome["receipts"]}
    queue_id = outcome["attempt"].get("external_submission_id")
    if (envelope["kind"] != "completion" or envelope["from"].get("kind") != "controller"
            or envelope["from"].get("group_id") != group or envelope["to"]["peer_key"] != target
            or outcome["message_id"] != envelope["message_id"] or outcome["target"] != target
            or outcome["state"] != "ACCEPTED_QUEUED" or outcome["attempt"]["state"] != "ACCEPTED_QUEUED"
            or stages != {"ACCEPTED_QUEUED"} or outcome.get("suppression") or not queue_id
            or base.queue_ids(queue) != {queue_id}
            or not any(receipt["evidence_kind"] == "native-queue-admission" and receipt["source_native_id"] == native_id
                       for receipt in outcome["receipts"])):
        raise RuntimeError("No exact genuine accepted controller completion/native queue ID before close")
    return queue_id


def observe_accepted(config, spawned, who):
    from nativepeers.journal import Journal
    run, group = Path(config["run"]), spawned["group_id"]
    deadline = time.monotonic() + 90
    with closing(Journal(run / "peer-state", read_only=True)) as journal:
        while time.monotonic() < deadline:
            row = completion(journal, group, who["key"])
            if row and row["route_state"] == "PUSH_ACCEPTED":
                break
            time.sleep(.03)
        else:
            raise TimeoutError("Real controller did not reach native queue admission during the held parent tool")
    outcome = base.product_cli(config, "status", row["message_id"])
    env = base.private_env(run / "codex-home", run / "peer-state", run, Path(config["source"]))
    rpc = base.Metadata(Path(config["binary"]), run / "workspace", env, run, "helper-accepted-queue")
    try:
        rpc.start()
        queue = rpc.call("thread/queue/list", {"threadId": who["native_session_id"], "limit": 100})
        queue_id = require_accepted(outcome, json.loads(row["payload_json"]), queue, group, who["key"], who["native_session_id"])
        proof = {"at": base.eastern(), "queue_id": queue_id, "outcome": outcome,
                 "envelope": json.loads(row["payload_json"]), "queue": queue}
    finally:
        cleanup = rpc.stop()
        base.save(run / "helper-accepted-queue-cleanup.json", cleanup)
        if not cleanup["confirmed_dead"] or not cleanup["output_eof"]:
            raise RuntimeError("Accepted-queue metadata helper cleanup is unproved")
    base.save(run / "accepted-before-close.json", proof)


def require_silent_frame(frame, final, worker_text):
    base.require_quiet_frame(frame, final)
    text = "\n".join(frame.get("chat_tail", []) + frame["lines"])
    if worker_text in (text.split(final, 1)[-1] if final in text else text):
        raise RuntimeError("Suppressed worker completion body rendered after the final")


def require_consumed(before, after, event, identity, completed_at):
    suppression = json.loads(event["payload_json"]) if event else {}
    if (after["state"] != "ACCEPTED_QUEUED" or after["attempt"] != before["attempt"]
            or after["receipts"] != before["receipts"] or after.get("suppression") != suppression
            or suppression.get("reason") != "completion-phase-closed"
            or suppression.get("attempt_state") != "ACCEPTED_QUEUED"
            or suppression.get("attempt_id") != before["attempt"]["attempt_id"]
            or suppression.get("native_id") != identity["native_session_id"]
            or suppression.get("observed_incarnation") != identity["incarnation_id"]
            or suppression.get("owner") != [identity["owner"]["pid"], identity["owner"]["start"]]
            or not event or event["at"] < completed_at):
        raise RuntimeError("Original accepted completion was not suppressed separately after the completed final")


def helper(config_path, phase, done):
    config = json.loads(Path(config_path).read_text())
    run = Path(config["run"])
    sys.path.insert(0, config["source"])
    try:
        if Path(os.environ["NPEER_HOME"]).resolve() != run / "peer-state" or Path(os.environ["CODEX_HOME"]).resolve() != run / "codex-home":
            raise RuntimeError("Native helper refused nonfixture homes")
        who = base.product_cli(config, "whoami")
        base.save(run / "identity.json", who)
        base.product_cli(config, "notifications", "open")
        spawned = base.product_cli(config, "spawn", "--spec", str(run / "spawn.json"))
        base.save(run / "spawned.json", spawned)
        if phase == "queued":
            observe_accepted(config, spawned, who)
        if phase in ("quiet", "queued"):
            first = base.product_cli(config, "notifications", "close")
            if first["state"] != "QUIET" or first != base.product_cli(config, "notifications", "close"):
                raise RuntimeError("Repeated native close was not identical QUIET")
            base.save(run / "closed.json", first)
        base.save(done, {"ok": True, "result": spawned})
        print("QCW_SPAWN_READY", flush=True)
        wait_file(run / "collect")
        collected = {"result": base.product_cli(config, "result", spawned["group_id"], "--include-artifacts"),
                     "retained": base.product_cli(config, "inbox", "--retained", "--limit", "100"),
                     "policy": base.product_cli(config, "notifications", "status")}
        base.save(run / "collected.json", collected)
        wait_file(run / "exit")  # Avoid a background-tool completion during the quiet observation.
    except BaseException as error:
        base.save(run / "helper-error.json", {"error": repr(error)})
        base.save(done, {"ok": False, "error": repr(error)})
        raise


class Model(base.Model):
    def answer(self, payload):
        latest = base.last_user_text(payload)
        phase, token = self.phase, self.token
        if f"QCW_TASK_{phase}_{token}" in latest and "NativePeers assigned worker title:" in latest:
            if payload.get("model") != MODEL or payload.get("reasoning", {}).get("effort") != EFFORT:
                raise RuntimeError("Worker wire request did not use the exact model/effort")
            (self.run / "worker-entered").touch()
            wait_file(self.run / "release")
            return {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": f"QCW_WORKER_RESULT_{phase}_{token}"}]}
        self.parent_requests += 1
        if f"QCW_WORKER_RESULT_{phase}_{token}" in latest and "NativePeers · Worker results" in latest:
            return {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": f"QCW_PUSH_RECEIVED_{phase}_{token}"}]}
        return super().answer(payload)


class Detached:
    """Track genuine private controller manifests, including detached native families."""
    def __init__(self, state):
        self.state, self.families, self.records = state, {}, {}

    def capture(self):
        from nativepeers.process import owned_status
        for path in [*self.state.glob("groups/*/owner.json"), *self.state.glob("workers/*.json")]:
            value = json.loads(path.read_text())
            for owner in [value.get("owner"), value.get("native_process"), *value.get("native_descendants", [])]:
                if not owner:
                    continue
                key = (owner["pid"], owner["start"])
                self.records[key] = owner
                if key not in self.families and owned_status(owner) == "LIVE":
                    process = type("Observed", (), {"pid": owner["pid"], "poll": lambda _, r=owner: 0 if owned_status(r) == "DEAD" else None})()
                    family = base.Family(process)
                    if any(family.birth[field] != owner[field] for field in ("pid", "uid", "start")):
                        raise RuntimeError("Detached manifest birth no longer matches")
                    self.families[key] = family
        for family in self.families.values():
            family.capture()

    def stop(self):
        from nativepeers.process import owned_status
        self.capture()
        stopped = [family.stop() for family in reversed(list(self.families.values()))]
        statuses = [{"owner": owner, "state": owned_status(owner)} for owner in self.records.values()]
        manifests = [json.loads(path.read_text()) for path in self.state.glob("workers/*.json")]
        eof = bool(manifests) and all((m.get("cleanup") or {}).get("output_eof") is True for m in manifests)
        groups = [json.loads(path.read_text()) for path in self.state.glob("groups/*/owner.json")]
        controllers_closed = bool(groups) and all(g.get("released") is True and g.get("cleanup_unresolved") == [] for g in groups)
        return {"confirmed_dead": all(row["state"] == "DEAD" for row in statuses)
                and all(row["confirmed_dead"] for row in stopped), "output_eof": eof and controllers_closed,
                "members": statuses, "families": stopped, "worker_pipe_records": [m.get("cleanup") for m in manifests],
                "controller_pipe_records": [{key: g.get(key) for key in ("released", "cleanup_unresolved")} for g in groups]}


def prepare(source, run, binary, approval, model, resources, report):
    from nativepeers.compatibility import update_verified
    from nativepeers.install import Installer
    from nativepeers.journal import Journal
    home, state, cwd = run / "codex-home", run / "peer-state", run / "workspace"
    for path in (home, cwd, run / "tmp"):
        path.mkdir(mode=0o700)
    env = base.private_env(home, state, run, source)
    with closing(Journal(state)) as journal:
        update_verified(journal, {"codex": approval}, initialize=True, selected={"codex": binary})
        installer = Installer(journal, native=False)
        groups, command = installer.desired_hooks("codex"), installer.command_for("codex")
    if "QueuedInput" not in groups or str(source) not in (source / "bin/npeer-hook").read_text():
        raise base.Blocked("Selected package's actual hook entrypoint/QueuedInput is unavailable")
    base.save(state / "policy.json", {"followup_grace_seconds": 1})
    base.save(home / "hooks.json", {"hooks": {name: [group] for name, group in groups.items()}})
    shell_env = {name: env[name] for name in ("CODEX_HOME", "NPEER_HOME", "PYTHONPATH", "TMPDIR")}
    (home / "config.toml").write_text(f'''model = "{MODEL}"
model_reasoning_effort = "{EFFORT}"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "danger-full-access"
check_for_update_on_startup = false
web_search = "disabled"
[features]
daemon_auto_start = false
[model_providers.fixture]
name = "Local worker push fixture"
base_url = "http://127.0.0.1:{model.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
[projects.{json.dumps(str(cwd))}]
trust_level = "trusted"
[shell_environment_policy]
inherit = "all"
set = {{ {", ".join(json.dumps(k) + " = " + json.dumps(v) for k, v in shell_env.items())} }}
''')
    rpc = base.Metadata(binary, cwd, env, run, "trust")
    resources.append(rpc)
    rpc.start()
    hooks = base.inventory_hooks(groups, rpc.call("hooks/list", {"cwds": [str(cwd)]}), home / "hooks.json", command)
    layer = next(layer for layer in rpc.call("config/read", {"includeLayers": True, "cwd": str(cwd)})["layers"]
                 if layer["name"].get("type") == "user" and not layer["name"].get("profile"))
    if Path(layer["name"]["file"]).resolve() != home / "config.toml":
        raise RuntimeError("Native trust write escaped the private home")
    rpc.call("config/batchWrite", {"filePath": str(home / "config.toml"), "expectedVersion": layer["version"],
             "reloadUserConfig": True, "edits": [{"keyPath": "hooks.state", "mergeStrategy": "replace",
             "value": {h["key"]: {"enabled": True, "trusted_hash": h["currentHash"]} for h in hooks}}]})
    trusted = base.inventory_hooks(groups, rpc.call("hooks/list", {"cwds": [str(cwd)]}), home / "hooks.json", command)
    if not all(h["enabled"] and h["trustStatus"] == "trusted" for h in trusted):
        raise base.Blocked("Native desired hooks are not trusted")
    report["hooks"] = trusted
    return cwd, env, state, rpc


def phase_run(source, run, binary, approval, phase, report, resources):
    import pyte
    from nativepeers.journal import Journal
    token = uuid.uuid4().hex[:12]
    model = Model(run)
    model.phase, model.token, model.parent_requests = phase, token, 0
    resources.append(model)
    model.thread = threading.Thread(target=model.serve_forever, daemon=True)
    model.thread.start()
    cwd, env, state, rpc = prepare(source, run, binary, approval, model, resources, report)
    config = {"run": str(run), "source": str(source), "binary": str(binary)}
    base.save(run / "config.json", config)
    base.save(run / "spawn.json", spawn_spec(phase, token))
    marker, done, final = "QCW_BOOT_" + token, run / "ready.json", "QCW_PARENT_FINAL_" + token
    model.plan(marker, shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(run / "config.json"), phase, str(done)]), done, final)
    tui = base.Tui([str(binary), "--no-daemon", marker], cwd, env, run, "parent", pyte)
    tracker = Detached(state)
    resources.extend([tui, tracker])
    def until(predicate, seconds=90):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            tracker.capture()
            tui.pump()
            if model.errors or (run / "helper-error.json").exists() or tui.process.poll() is not None:
                raise RuntimeError("Owned fixture failed; inspect raw evidence")
            if predicate():
                return
            time.sleep(.03)
        raise TimeoutError("Worker push acceptance phase")
    until(lambda: (run / "spawned.json").exists() and (run / "worker-entered").exists())
    identity = json.loads((run / "identity.json").read_text())
    if identity["owner"]["pid"] != tui.process.pid or identity["owner"]["start"] != tui.birth["start"]:
        raise RuntimeError("Parent shell lacks genuine standalone PID/birth identity")
    if phase == "queued":
        # The helper is still waiting for real queue acceptance, so this worker
        # completes while the parent's original native tool/turn remains active.
        (run / "release").touch()
    until(lambda: done.exists() and final in tui.text())
    until(lambda: base.native_tail(run / "codex-home", identity["native_session_id"], final) is not None)
    before = model.parent_requests
    worker_text, answer = f"QCW_WORKER_RESULT_{phase}_{token}", f"QCW_PUSH_RECEIVED_{phase}_{token}"
    if phase in ("quiet", "queued"):
        def audit(frame):
            require_silent_frame(frame, final, worker_text)
        boundary = False
        for frame in tui.frames:
            boundary = boundary or final in "\n".join(frame.get("chat_tail", []) + frame["lines"])
            if boundary:
                audit(frame)
        tui.audit = audit
        audit({"lines": tui.screen.display[:]})
    if phase != "queued":
        (run / "release").touch()
    group = json.loads(done.read_text())["result"]["group_id"]
    def delivery():
        with closing(Journal(state, read_only=True)) as journal:
            return completion(journal, group, identity["key"])
    until(lambda: (delivery() or {}).get("route_state") == ("RETAINED" if phase == "quiet" else "PUSH_ACCEPTED")
          and (phase != "open" or answer in tui.text()))
    if phase == "queued":
        def suppressed():
            with closing(Journal(state, read_only=True)) as journal:
                return journal.one("SELECT * FROM events WHERE subject_id=? AND kind='notification-suppressed'",
                                   (delivery()["message_id"],))
        until(lambda: suppressed() is not None)
    with closing(Journal(state, read_only=True)) as journal:
        envelope = json.loads(delivery()["payload_json"])
        if envelope["from"].get("kind") != "controller" or envelope["from"].get("group_id") != group:
            raise RuntimeError("Completion did not originate from its real group controller")
        receipts = journal.all("SELECT * FROM receipts WHERE message_id=? ORDER BY receipt_id", (envelope["message_id"],))
        stages = {receipt["stage"] for receipt in receipts}
        if ("RETAINED" if phase == "quiet" else "ACCEPTED_QUEUED") not in stages or (
                phase == "quiet" and stages & {"INGESTED", "MODEL_REPORTED_PROCESSED", "ACCEPTED_QUEUED"}):
            raise RuntimeError("Controller completion lacks the expected genuine transport receipt")
        workers = journal.all("SELECT * FROM workers WHERE group_id=?", (group,))
    (run / "collect").touch()
    until(lambda: (run / "collected.json").exists())
    collected = json.loads((run / "collected.json").read_text())
    result = collected["result"]["results"]
    if len(result) != 1 or len(workers) != 1 or result[0]["status"] != "completed" or result[0]["summary"] != worker_text or result[0]["model_requested"] != MODEL or result[0]["model_observed"] != MODEL or result[0]["reasoning_effort_requested"] != EFFORT or workers[0]["native_id"] != result[0]["worker"]["native_session_id"]:
        raise RuntimeError("Real retained worker result/model differed from the exact request")
    uuid.UUID(workers[0]["native_id"])
    if not collected["result"].get("artifact_pages"):
        raise RuntimeError("Real worker result artifact was not readable")
    end = time.monotonic() + 5
    until(lambda: time.monotonic() >= end)
    until(lambda: all((json.loads(path.read_text()).get("cleanup") or {}).get("output_eof") is True
                     for path in state.glob("workers/*.json"))
          and json.loads((state / "groups" / group / "owner.json").read_text()).get("released") is True)
    with closing(Journal(state, read_only=True)) as journal:
        if receipts != journal.all("SELECT * FROM receipts WHERE message_id=? ORDER BY receipt_id", (envelope["message_id"],)):
            raise RuntimeError("Result/inbox reads rewrote completion receipts")
    if phase in ("quiet", "queued"):
        if model.parent_requests != before or collected["policy"]["state"] != "QUIET" or envelope not in collected["retained"]:
            raise RuntimeError("QUIET completion was not retained silently")
        base.require_native_silence(run / "codex-home", identity["native_session_id"], final, [worker_text])
        for record in model.records:
            if worker_text in base.input_text(json.loads((run / f"model-{record['number']:04}.json").read_text())):
                raise RuntimeError("Silent completion appeared in a model request, including before the count baseline")
        if phase == "queued":
            from nativepeers.registry import Registry
            from nativepeers.routing import Router
            proof = json.loads((run / "accepted-before-close.json").read_text())
            if proof["envelope"] != envelope:
                raise RuntimeError("Queued completion identity/body changed after close")
            tail = base.native_tail(run / "codex-home", identity["native_session_id"], final)
            stamp = json.loads(Path(tail["path"]).read_text().splitlines()[tail["completed_record"]])["timestamp"]
            completed_at = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
            with closing(Journal(state, read_only=True)) as journal:
                outcome = Router(journal, Registry(journal)).outcome(envelope["message_id"])
            require_consumed(proof["outcome"], outcome, suppressed(), identity, completed_at)
            queue = rpc.call("thread/queue/list", {"threadId": identity["native_session_id"], "limit": 100})
            if base.queue_ids(queue):
                raise RuntimeError("The original accepted native completion did not drain")
            report.update(accepted_before_close=proof, suppression=suppressed(), queue_after=queue)
    elif model.parent_requests <= before or collected["policy"]["state"] != "OPEN":
        raise RuntimeError("OPEN controller push did not produce a parent model request")
    report.update(identity=identity, group_id=group, worker_id=workers[0]["worker_id"], task_id=result[0]["task_id"], receipts=receipts,
                  parent_requests_after_final=model.parent_requests - before, result=collected)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_helper":
        helper(*argv[1:])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex")
    parser.add_argument("--nativepeers-root", required=True)
    parser.add_argument("--run", required=True)
    args = parser.parse_args(argv)
    source = Path(args.nativepeers_root).resolve(strict=True)
    sys.path.insert(0, str(source))
    run, resources = base.prepare_run(args.run, source), []
    report = {"status": "RUNNING", "command": [sys.executable, str(Path(__file__).resolve()), *argv],
              "cwd": str(Path.cwd()), "started_at": base.eastern(), "probe_sha256": base.digest(__file__),
              "reused_probe_sha256": base.digest(base.__file__), "phases": {}}
    try:
        from nativepeers.compatibility import fingerprint
        if not callable(getattr(base, "require_quiet_frame", None)):
            raise base.Blocked("Worker acceptance requires the canonical TUI base with require_quiet_frame")
        binary, selection = base.select_codex(args.codex)
        installed = Path.home() / ".local/state/nativepeers/compat/runtime.json"
        actual = fingerprint(binary, fresh=True)
        approval = approved_record(installed, actual)
        report.update(executable=actual, selection=selection, installed_approval_sha256=base.digest(installed))
        report["source"] = {str(path.relative_to(source)): base.digest(path) for path in (source / "nativepeers").rglob("*.py")}
        for phase in PHASES:
            directory = run / phase
            directory.mkdir()
            report["phases"][phase] = {}
            phase_run(source, directory, binary, approval, phase, report["phases"][phase], resources)
        if base.digest(installed) != report["installed_approval_sha256"] or fingerprint(binary, fresh=True) != actual:
            raise RuntimeError("Installed approval or selected package changed during acceptance")
        if any(base.digest(source / name) != expected for name, expected in report["source"].items()):
            raise RuntimeError("Selected product source changed during acceptance")
        if len({report["phases"][phase]["worker_id"] for phase in PHASES}) != len(PHASES):
            raise RuntimeError("Each phase must use a distinct real worker")
        report["status"] = "PASS"
    except BaseException as error:
        report.update(status="BLOCKED" if isinstance(error, (base.Blocked, ImportError)) else "FAIL", error=repr(error))
    finally:
        for phase in PHASES:
            if (run / phase).exists():
                (run / phase / "release").touch()
        for resource in reversed(resources):
            try:
                if isinstance(resource, Model):
                    report.setdefault("model_records", []).append({"phase": resource.phase, "records": resource.records, "errors": resource.errors})
                    resource.shutdown()
                    resource.server_close()
                    resource.thread.join(3)
                    if resource.errors or resource.thread.is_alive() or resource.socket.fileno() != -1:
                        raise RuntimeError("Local Responses fixture errors or unproved cleanup")
                else:
                    cleanup = resource.stop()
                    report.setdefault("cleanup", []).append(cleanup)
                    if not cleanup["confirmed_dead"] or not cleanup["output_eof"] or cleanup.get("terminal_errors"):
                        raise RuntimeError("Owned fixture death/output EOF is unproved")
            except BaseException as error:
                report.setdefault("cleanup_errors", []).append(repr(error))
                report["status"] = "FAIL"
        report["finished_at"] = base.eastern()
        base.save(run / "qualification.json", report)
    print(json.dumps({"status": report["status"], "report": str(run / "qualification.json")}))
    return {"PASS": 0, "BLOCKED": 2}.get(report["status"], 1)


if __name__ == "__main__":
    raise SystemExit(main())
