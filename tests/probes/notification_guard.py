#!/usr/bin/env python3
"""Disposable probe: does Codex run UserPromptSubmit for queued input at consumption time?

Topology: one private *loaded app-server* (stdio JSON-RPC, private CODEX_HOME and
workspace, localhost Responses fixture, zero external model requests). This is NOT an
interactive TUI and must never be cited as UI acceptance. On this loaded idle thread
thread/queue/add AUTO-DISPATCHES the item immediately (observed: queue/list is empty and a
follow-up thread/queue/start finds nothing), so the probe issues no explicit start. It marks
the event log before each add, waits for the exact UserPromptSubmit record of the frame (its
turn_id names the native turn), then for positive hook/completed and turn/completed events.

The candidate hook is a private command hook trusted only in the private home through
hooks/list + config/batchWrite. To adapt it to a real NativePeers hook, pass --hook-command
(the command must read the Codex UserPromptSubmit JSON on stdin and block the marker frames
printed in the evidence); the default hook is the recording guard written below.

Opt-in --nativepeers-root PATH mode: the private hook becomes a wrapper that imports the ACTUAL
nativepeers.notification_policy.guard from that tree, against a private NativePeers journal seeded
with clearly PROVISIONAL fixture peers (the recipient uses the real native thread UUID and the real
app-server owner PID/birth found through hook ancestry). Frames are produced by the real Router and
presentation.render and handed to the native queue by a fixture adapter; close/open use the real
notification_policy.apply. It is a loaded-private-app-server policy integration probe: the fixture
registration is operator-seeded, NOT interactive-standalone TUI identity qualification.

Usage: python tests/probes/notification_guard.py [--codex PATH] [--hook-command CMD] [--nativepeers-root PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WAIT = 30.0
_FIXTURES: list = []
HOOK_SCRIPT = '''#!/usr/bin/env python3
import json, os, sys, time
home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
payload = json.load(sys.stdin)
rules = json.load(open(os.path.join(home, "guard-rules.json")))
prompt = payload.get("prompt") or ""
action = "allow"
if any(m in prompt for m in rules["block"]):
    action = "block"
elif any(m in prompt for m in rules["stop"]):
    action = "stop"
with open(os.path.join(home, "hook-records.jsonl"), "a") as stream:
    stream.write(json.dumps({"ts": time.time(), "pid": os.getpid(), "action": action, "input": payload}) + "\\n")
if action == "block":
    print(json.dumps({"decision": "block", "reason": "notification guard probe block"}))
elif action == "stop":
    print(json.dumps({"continue": False, "stopReason": "notification guard probe stop"}))
'''

WRAPPER_SCRIPT = r'''#!/usr/bin/env python3
# Private UserPromptSubmit wrapper around the ACTUAL NativePeers notification_policy.guard.
import json, os, sys, time
from pathlib import Path
cfg = json.load(open(sys.argv[1]))
sys.path.insert(0, cfg["nativepeers_root"])
payload = json.load(sys.stdin)
from nativepeers.journal import Journal
from nativepeers.models import home_id
from nativepeers.notification_policy import guard
from nativepeers.process import identity


def record(action, **extra):
    with open(cfg["records"], "a") as stream:
        stream.write(json.dumps({"ts": time.time(), "pid": os.getpid(), "action": action, "input": payload, **extra}) + "\n")


chain, owner, pid, codex = [], None, os.getppid(), str(Path(cfg["codex"]).resolve())
while pid > 1 and len(chain) < 16:
    try:
        ident = identity(pid).json()
    except Exception as error:
        chain.append({"pid": pid, "error": type(error).__name__}); break
    chain.append({"pid": ident["pid"], "start": ident["start"], "executable": ident["executable"]})
    if owner is None and str(Path(ident["executable"]).resolve()) == codex:
        owner = ident
    pid = ident.get("ppid") or 0
prompt, barrier = payload.get("prompt"), Path(cfg["barrier"])
if (barrier / "arm").exists():
    # Bounded fixture barrier: lets the parent observe native queue acceptance before close/guard.
    (barrier / "entered.json").write_text(json.dumps({"prompt": prompt, "pid": os.getpid(), "ts": time.time()}))
    deadline = time.monotonic() + cfg["barrier_timeout"]
    while not (barrier / "release").exists():
        if time.monotonic() > deadline:
            record("barrier-timeout", chain=chain); print("probe barrier timeout", file=sys.stderr); sys.exit(1)
        time.sleep(0.05)
journal = Journal(Path(cfg["state"]))
try:
    peer = None
    if owner:
        peer = journal.one("SELECT * FROM peer_instances WHERE provider='codex' AND home_id=? AND native_id=? AND pid=? AND process_start=? AND status='live'",
                           (home_id(Path(cfg["home"])), payload.get("session_id"), owner["pid"], owner["start"]))
    if peer is None:
        record("no-exact-registered-peer", chain=chain, owner=owner); sys.exit(0)
    result = guard(journal, peer, prompt)
finally:
    journal.close()
record("block" if result.get("decision") == "block" else "allow", guard=result, chain=chain, owner=owner,
       peer={k: peer[k] for k in ("peer_key", "incarnation_id", "native_id", "pid", "process_start", "owner_kind")})
if result.get("decision") == "block":
    print(json.dumps(result))
'''


class Fixture(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.lock = threading.Lock()
        self.responses: list[str] = []  # decoded request bodies of POST .../responses
        self.undecoded = 0
        self.other: list[str] = []


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        with self.server.lock:
            self.server.other.append("GET " + self.path)
        self.send_error(404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.rstrip("/").endswith("/responses"):
            with self.server.lock:
                self.server.other.append("POST " + self.path)
            return self.send_error(404)
        encoding = self.headers.get("Content-Encoding")
        try:
            if encoding == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            elif encoding == "zstd":
                from compression import zstd
                raw = zstd.decompress(raw)
            text = raw.decode()
        except Exception as error:  # noqa: BLE001 - recorded, and fails the marker check conservatively
            text, undecoded = f"<undecodable {encoding}: {type(error).__name__}>", 1
        else:
            undecoded = 0
        with self.server.lock:
            self.server.undecoded += undecoded
            self.server.responses.append(text)
            number = len(self.server.responses)
        events = [
            {"type": "response.created", "response": {"id": f"fx-{number}"}},
            {"type": "response.output_item.done", "item": {
                "type": "message", "role": "assistant", "id": f"fx-msg-{number}",
                "content": [{"type": "output_text", "text": f"FIXTURE_ASSISTANT_REPLY_{number}"}]}},
            {"type": "response.completed", "response": {"id": f"fx-{number}", "usage": {
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}}]
        body = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Rpc:
    """Private stdio JSON-RPC; one reader thread keeps every message in arrival order."""

    def __init__(self, binary: Path, env: dict, cwd: Path, stderr_path: Path):
        self.log: list[dict] = []
        self.cond = threading.Condition()
        self.counter = 0
        self.stderr = stderr_path.open("wb")
        self.process = subprocess.Popen(
            [str(binary), "app-server", "--listen", "stdio://"], cwd=cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
        self.pid = self.process.pid
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self.cond:
                self.log.append(message)
                self.cond.notify_all()
            if "id" in message and "method" in message:  # server request: no approval handler by design
                self.send({"id": message["id"], "error": {"code": -32601, "message": "probe has no handler"}})
        with self.cond:
            self.log.append({"eof": True})
            self.cond.notify_all()

    def send(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()

    def wait_for(self, predicate, timeout=WAIT, what="event", start=0):
        deadline = time.monotonic() + timeout
        with self.cond:
            while True:
                for message in self.log[start:]:
                    if predicate(message):
                        return message
                    if message.get("eof"):
                        raise RuntimeError(f"app-server exited while waiting for {what}")
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"{what} not observed within {timeout}s")
                self.cond.wait(left)

    def call(self, method, params, timeout=WAIT):
        self.counter += 1
        request_id = self.counter
        self.send({"id": request_id, "method": method, "params": params})
        reply = self.wait_for(lambda m: m.get("id") == request_id and "method" not in m, timeout, method)
        if "error" in reply:
            raise RuntimeError(f"{method}: {json.dumps(reply['error'])}")
        return reply["result"]

    def notifications(self, method=None):
        with self.cond:
            return [m for m in self.log if "method" in m and "id" not in m and (method is None or m["method"] == method)]

    def mark(self):
        with self.cond:
            return len(self.log)

    def since(self, mark):
        with self.cond:
            return [m for m in self.log[mark:] if "method" in m and "id" not in m]


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def default_codex() -> Path:
    # Same resolution as the CodexForge launcher: customized current release, else official.
    custom = Path.home() / ".local/share/codex-mcp-manager/current/bin/codex"
    official = Path.home() / ".codex/packages/standalone/current/bin/codex"
    return custom if custom.exists() else official


def texts(items):
    return [item["text"] for item in items if isinstance(item, dict) and item.get("type") == "text"]


class Checks:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, name, ok, detail=""):
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self):
        return bool(self.rows) and all(row["ok"] for row in self.rows)


def run(args, evidence: dict, checks: Checks, root: Path, rpc_holder: list):
    home, work = root / "codex-home", root / "workspace"
    (home / "hooks").mkdir(parents=True)
    work.mkdir()
    private_home = root / "user-home"
    private_home.mkdir()
    binary = Path(args.codex).resolve(strict=True)
    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=20,
                             env={"PATH": os.environ["PATH"], "HOME": str(private_home), "CODEX_HOME": str(home)})
    evidence["executable"] = {"path": str(binary), "sha256": sha256(binary), "version": version.stdout.strip(),
                              "selected_from": args.codex}
    run_id = uuid.uuid4().hex[:12]
    block_frame, stop_frame = f"NPG-BLOCK-{run_id}", f"NPG-STOP-{run_id}"
    control_frame, allowed_frame = f"NPG-CONTROL-{run_id}", f"NPG-ALLOWED-{run_id}"
    evidence["frames"] = {"block": block_frame, "stop": stop_frame, "control": control_frame, "allowed": allowed_frame}
    (home / "guard-rules.json").write_text(json.dumps({"block": [block_frame], "stop": [stop_frame]}))
    records_path = home / "hook-records.jsonl"
    state = root / "nativepeers-state"
    if args.nativepeers_root:
        nproot = Path(args.nativepeers_root).resolve(strict=True)
        for needed in ("nativepeers/notification_policy.py", "nativepeers/presentation.py", "nativepeers/routing.py"):
            if not (nproot / needed).is_file():
                raise RuntimeError(f"--nativepeers-root lacks {needed}")
        # The probe process imports the selected tree (Router/render/apply) after thread start.
        if str(nproot) not in sys.path:
            sys.path.insert(0, str(nproot))
        state.mkdir(mode=0o700)
        state.chmod(0o700)
        (state / "PROVISIONAL-FIXTURE.txt").write_text("Operator-seeded probe fixture; not interactive-standalone identity qualification.\n")
        (root / "barrier").mkdir()
        wrapper = home / "hooks" / "guard_wrapper.py"
        wrapper.write_text(WRAPPER_SCRIPT)
        wrapper_cfg = home / "hooks" / "guard_wrapper.json"
        wrapper_cfg.write_text(json.dumps({"nativepeers_root": str(nproot), "state": str(state), "home": str(home), "codex": str(binary),
                                           "records": str(records_path), "barrier": str(root / "barrier"), "barrier_timeout": 60}))
        hook_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(wrapper))} {shlex.quote(str(wrapper_cfg))}"
        evidence["hook"] = {"kind": "actual notification_policy.guard wrapper", "command": hook_command}
        evidence["nativepeers_root"] = str(nproot)
        evidence["nativepeers_sources"] = {name: sha256(nproot / name) for name in
                                           ("nativepeers/notification_policy.py", "nativepeers/presentation.py", "nativepeers/routing.py", "nativepeers/hooks.py")
                                           if (nproot / name).is_file()}
    elif args.hook_command:
        hook_command = args.hook_command
        evidence["hook"] = {"kind": "external --hook-command", "command": hook_command}
    else:
        script = home / "hooks" / "guard.py"
        script.write_text(HOOK_SCRIPT)
        hook_command = f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
        evidence["hook"] = {"kind": "private recording guard", "command": hook_command}

    fixture = Fixture()
    _FIXTURES.append(fixture)
    threading.Thread(target=fixture.serve_forever, daemon=True).start()
    evidence["fixture_url"] = f"http://127.0.0.1:{fixture.server_address[1]}"
    (home / "config.toml").write_text(f'''model = "fixture"
model_provider = "fixture"
model_context_window = 100000
approval_policy = "never"
sandbox_mode = "read-only"
[features]
daemon_auto_start = false
[model_providers.fixture]
name = "Local fixture"
base_url = "{evidence["fixture_url"]}/v1"
wire_api = "responses"
requires_openai_auth = false
[projects.{json.dumps(str(work))}]
trust_level = "trusted"
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = {json.dumps(hook_command)}
timeout = 90
''')
    env = {"PATH": os.environ["PATH"], "HOME": str(private_home), "CODEX_HOME": str(home),
           "TMPDIR": str(root), "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
    rpc = Rpc(binary, env, work, root / "app-server.stderr")
    rpc_holder.append(rpc)
    evidence["app_server_pid"] = rpc.pid
    evidence["topology"] = ("private loaded stdio app-server (NOT an interactive TUI; not UI acceptance); "
                            "thread/queue/add auto-dispatched natively on the owned loaded thread (no explicit queue/start); "
                            "TUI queue behavior not exercised")
    rpc.call("initialize", {"clientInfo": {"name": "notification-guard-probe", "version": "1"},
                            "capabilities": {"experimentalApi": True}})
    rpc.send({"method": "initialized", "params": {}})

    # Trust the candidate hook only in the private home, through native APIs.
    listed = rpc.call("hooks/list", {"cwds": [str(work)]})["data"]
    hooks = [h for entry in listed for h in entry["hooks"] if h["eventName"] in ("userPromptSubmit", "UserPromptSubmit")]
    checks.add("hooks/list shows exactly one candidate UserPromptSubmit command hook", len(hooks) == 1
               and hooks[0].get("handlerType") == "command", json.dumps([{k: h.get(k) for k in ("key", "eventName", "trustStatus", "enabled")} for h in hooks]))
    hook = hooks[0]
    evidence["hook"].update({"key": hook["key"], "hash": hook["currentHash"], "trust_before": hook["trustStatus"]})
    checks.add("hook is untrusted before explicit private trust", hook["trustStatus"] == "untrusted", hook["trustStatus"])
    snapshot = rpc.call("config/read", {"includeLayers": True, "cwd": str(work)})
    layer = next(l for l in snapshot["layers"] if l["name"]["type"] == "user" and l["name"].get("profile") is None)
    checks.add("config layer being written is the private CODEX_HOME", Path(layer["name"]["file"]).resolve() == (home / "config.toml").resolve(), layer["name"]["file"])
    rpc.call("config/batchWrite", {
        "filePath": layer["name"]["file"], "expectedVersion": layer["version"], "reloadUserConfig": True,
        "edits": [{"keyPath": ".".join(json.dumps(p) for p in ("hooks", "state", hook["key"])),
                   "value": {"enabled": True, "trusted_hash": hook["currentHash"]}, "mergeStrategy": "replace"}]})
    after = [h for entry in rpc.call("hooks/list", {"cwds": [str(work)]})["data"] for h in entry["hooks"] if h["key"] == hook["key"]]
    checks.add("hook trusted and enabled via native API", len(after) == 1 and after[0]["trustStatus"] == "trusted" and after[0]["enabled"],
               json.dumps(after[0] if after else None)[:300])

    thread = rpc.call("thread/start", {"cwd": str(work), "ephemeral": False})["thread"]
    thread_id = thread["id"]
    evidence["thread_id"] = thread_id
    checks.add("native thread id is a UUID and thread is non-ephemeral", str(uuid.UUID(thread_id)) == thread_id and not thread["ephemeral"], thread_id)

    def requests():
        with fixture.lock:
            return len(fixture.responses)

    def records():
        return [json.loads(l) for l in records_path.read_text().splitlines()] if records_path.exists() else []

    def text_input(text):
        return [{"type": "text", "text": text, "text_elements": []}]

    def turn_id_of(message):
        params = message.get("params", {})
        return (params.get("turn") or {}).get("id") or params.get("turnId")

    def wait_terminal(turn_id, mark):
        done = rpc.wait_for(lambda m: m.get("method") == "turn/completed" and turn_id_of(m) == turn_id, what=f"turn/completed {turn_id}")
        return done, rpc.since(mark)

    def assistant_items(events):
        found = []
        for event in events:
            item = event.get("params", {}).get("item") or {}
            if event["method"] == "item/completed" and item.get("type") == "agentMessage":
                found.append(item.get("text"))
            if event["method"] == "item/agentMessage/delta":
                found.append(event["params"].get("delta"))
        return found

    def hook_events(events):
        return [{"method": e["method"], "run": (e["params"].get("run") or {})} for e in events if e["method"].startswith("hook/")]

    phases = evidence.setdefault("phases", {})

    # 1. Ordinary control input through turn/start: hook invoked, allowed, one model request.
    mark, req0, rec0 = rpc.mark(), requests(), len(records())
    turn = rpc.call("turn/start", {"threadId": thread_id, "input": text_input(control_frame)})["turn"]
    done, events = wait_terminal(turn["id"], mark)
    new_records = records()[rec0:]
    phases["control"] = {"turn_id": turn["id"], "status": done["params"]["turn"].get("status"), "model_requests": requests() - req0,
                         "hook": [r["action"] for r in new_records], "assistant": assistant_items(events), "hook_events": hook_events(events)}
    # Generic mode stays strict: the hook must have allowed it. In actual-policy mode this bootstrap control runs BEFORE the
    # fixture recipient is registered (its UUID/owner exist only after thread/start), so the wrapper correctly reports
    # no-exact-registered-peer and passes the prompt unchanged; the registered-peer controls come later in integration().
    expected_action = "no-exact-registered-peer" if args.nativepeers_root else "allow"
    checks.add(f"control: hook ran once with the exact prompt, action={expected_action}" + (" (pre-registration bootstrap)" if args.nativepeers_root else ""),
               len(new_records) == 1 and new_records[0]["action"] == expected_action and new_records[0]["input"].get("prompt") == control_frame, json.dumps(new_records)[:300])
    checks.add("control: exactly one model request and assistant output", requests() - req0 == 1 and len(phases["control"]["assistant"]) >= 1,
               json.dumps(phases["control"])[:300])
    hook_session = new_records[0]["input"].get("session_id") if new_records else None
    evidence["hook_session_id"] = hook_session
    checks.add("hook input names this native thread", hook_session == thread_id, f"{hook_session} vs {thread_id}")

    def wait_record(frame, rec_before):
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            hits = [r for r in records()[rec_before:] if r["input"].get("prompt") == frame]
            if hits:
                return hits
            time.sleep(0.05)
        raise TimeoutError(f"no UserPromptSubmit record for {frame} within {WAIT}s")

    def queue_and_consume(label, frame, expect_action):
        client_id = str(uuid.uuid4())
        req_before, rec_before = requests(), len(records())
        mark = rpc.mark()
        added = rpc.call("thread/queue/add", {"threadId": thread_id, "input": text_input(frame), "clientUserMessageId": client_id})["queuedSubmission"]
        hits = wait_record(frame, rec_before)  # native auto-dispatch: hook runs at model-consumption time
        turn_id = hits[0]["input"].get("turn_id")
        checks.add(f"{label}: queue/add auto-dispatched and the hook saw the exact frame once, naming a native turn",
                   len(hits) == 1 and bool(turn_id) and hits[0]["action"] == expect_action, json.dumps([{k: h["input"].get(k) for k in ("prompt", "turn_id", "session_id")} | {"action": h["action"]} for h in hits])[:300])
        hook_done = rpc.wait_for(lambda m: m.get("method") == "hook/completed" and m["params"].get("turnId", turn_id) == turn_id,
                                 what=f"hook/completed {turn_id}", start=mark)
        done, events = wait_terminal(turn_id, mark)
        new = records()[rec_before:]
        after_list = rpc.call("thread/queue/list", {"threadId": thread_id})["data"]
        info = {"queued_submission_id": added["id"], "client_user_message_id": client_id, "turn_id": turn_id,
                "status": done["params"]["turn"].get("status"), "model_requests": requests() - req_before,
                "hook": [{"action": r["action"], "prompt": r["input"].get("prompt"), "turn_id": r["input"].get("turn_id")} for r in new],
                "assistant": assistant_items(events), "hook_events": hook_events(events),
                "hook_completed_status": (hook_done["params"].get("run") or {}).get("status"),
                "event_methods": [e["method"] for e in events], "queue_after": [q["id"] for q in after_list]}
        phases[label] = info
        checks.add(f"{label}: exactly one hook invocation for the frame by terminal", len(new) == 1 and new[0]["input"].get("prompt") == frame, json.dumps(info["hook"])[:300])
        return info

    if args.nativepeers_root:
        ctx = SimpleNamespace(args=args, rpc=rpc, checks=checks, evidence=evidence, phases=phases, thread_id=thread_id, home=home, work=work,
                              root=root, state=state, requests=requests, records=records, text_input=text_input, wait_record=wait_record,
                              wait_terminal=wait_terminal, assistant_items=assistant_items, fixture=fixture)
        return integration(ctx)

    for label, frame, action in (("blocked_decision_block", block_frame, "block"), ("blocked_continue_false", stop_frame, "stop")):
        info = queue_and_consume(label, frame, action)
        statuses = [e["run"].get("status") for e in info["hook_events"] if e["method"] == "hook/completed"]
        checks.add(f"{label}: native hook/completed reports a non-completed (blocked/stopped) run", info["hook_completed_status"] in ("blocked", "stopped") and any(s in ("blocked", "stopped") for s in statuses), f"{info['hook_completed_status']} {statuses}")
        checks.add(f"{label}: no model request and no assistant output", info["model_requests"] == 0 and not info["assistant"],
                   f"requests={info['model_requests']} assistant={info['assistant']}")
        time.sleep(0.5)  # drain only; success above already rests on positive native turn/completed + hook evidence
        checks.add(f"{label}: no late model request after terminal", requests() == 1, str(requests()))

    info = queue_and_consume("fresh_allowed", allowed_frame, "allow")
    checks.add("fresh_allowed: a model request occurs and assistant output is delivered", info["model_requests"] == 1 and len(info["assistant"]) >= 1,
               json.dumps({k: info[k] for k in ("model_requests", "assistant", "status")}))

    with fixture.lock:
        bodies = list(fixture.responses)
        undecoded, other = fixture.undecoded, list(fixture.other)
    evidence["model_requests_total"] = len(bodies)
    evidence["fixture_other_requests"] = other
    checks.add("blocked frames never reached any model request body", undecoded == 0 and not any(block_frame in b or stop_frame in b for b in bodies),
               f"bodies={len(bodies)} undecoded={undecoded}")
    checks.add("allowed frames reached the model", any(control_frame in b for b in bodies) and any(allowed_frame in b for b in bodies))
    checks.add("total model requests == control + fresh allowed only", len(bodies) == 2, str(len(bodies)))
    try:
        read = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": True})
        dump = json.dumps(read)
        evidence["thread_read"] = {"contains_block": block_frame in dump, "contains_stop": stop_frame in dump,
                                   "contains_allowed": allowed_frame in dump, "turns": len(read["thread"].get("turns", []))}
        checks.add("durable thread history never records the blocked frames", not evidence["thread_read"]["contains_block"] and not evidence["thread_read"]["contains_stop"]
                   and evidence["thread_read"]["contains_allowed"], json.dumps(evidence["thread_read"]))
    except Exception as error:  # noqa: BLE001 - informational; the model-request checks are the gate
        evidence["thread_read"] = {"error": str(error)[:300]}
    evidence["hook_records"] = [{"action": r["action"], "prompt": r["input"].get("prompt"), "session_id": r["input"].get("session_id"),
                                 "turn_id": r["input"].get("turn_id"), "event": r["input"].get("hook_event_name")} for r in records()]
    fixture.shutdown()


def integration(c):
    """Real Router/render/apply/guard against the native consumption hook; see the module docstring for the limits."""
    import asyncio
    from nativepeers.caller import Caller
    from nativepeers.journal import Journal
    from nativepeers.notification_policy import apply, status
    from nativepeers.presentation import render
    from nativepeers.process import identity
    from nativepeers.registry import Registry
    from nativepeers.routing import Router

    rpc, checks, evidence, phases, thread_id = c.rpc, c.checks, c.evidence, c.phases, c.thread_id
    barrier = c.root / "barrier"
    journal = Journal(c.state)
    sender_proc = subprocess.Popen(["sleep", "600"], start_new_session=True)  # owner process of the fixture sender
    try:
        registry = Registry(journal)
        owner = identity(rpc.pid).json()
        recipient = registry.register("codex", c.home, thread_id, "fixture-recipient-provisional", owner, str(c.work),
                                      {"kind": "fixture-native-queue"}, owner_kind="provisional-private-app-server-fixture")
        sender = registry.register("codex", c.home, str(uuid.uuid4()), "fixture-sender-provisional", identity(sender_proc.pid).json(), str(c.work),
                                   {"kind": "fixture-noop"}, owner_kind="provisional-fixture-sender")
        evidence["integration"] = {
            "provisional_fixture": True,
            "limit": "operator-seeded loaded-private-app-server policy integration; NOT interactive-standalone TUI identity qualification and NOT UI acceptance",
            "recipient": {k: recipient[k] for k in ("handle", "incarnation_id", "owner_kind", "owner")},
            "sender": {k: sender[k] for k in ("handle", "incarnation_id", "owner_kind")}, "steps": {}}
        steps = evidence["integration"]["steps"]
        r_caller = Caller("peer", recipient["handle"], recipient["incarnation_id"])
        s_caller = Caller("peer", sender["handle"], sender["incarnation_id"])
        checks.add("recipient fixture row carries the real native UUID and app-server owner, not interactive-standalone",
                   recipient["peer_key"]["native_session_id"] == thread_id and recipient["owner"]["pid"] == rpc.pid and recipient["owner_kind"] != "interactive-standalone",
                   json.dumps(recipient["owner"]))

        async def deliver_native(peer, message):
            added = await asyncio.to_thread(rpc.call, "thread/queue/add", {
                "threadId": peer["peer_key"]["native_session_id"], "input": c.text_input(render(message)), "clientUserMessageId": str(uuid.uuid4())})
            return dict(stage="ACCEPTED_QUEUED", evidence_kind="fixture-thread-queue-add", external_submission_id=added["queuedSubmission"]["id"])

        async def deliver_noop(peer, message):
            return dict(stage="ACCEPTED_QUEUED", evidence_kind="fixture-noop")

        router = Router(journal, registry, {"fixture-native-queue": deliver_native, "fixture-noop": deliver_noop})

        def send(caller, target, text, **kwargs):
            outcome = asyncio.run(router.send(caller, target, text, **kwargs))
            envelope = json.loads(journal.one("SELECT payload_json FROM messages WHERE message_id=?", (outcome["message_id"],))["payload_json"])
            return outcome, envelope, render(envelope)

        def policy(caller, action):
            return asyncio.run(apply(journal, registry, caller, action))

        def snapshot(message_id):
            return {"attempts": journal.all("SELECT * FROM attempts WHERE message_id=? ORDER BY attempt_id", (message_id,)),
                    "receipts": journal.all("SELECT * FROM receipts WHERE message_id=? ORDER BY receipt_id", (message_id,))}

        def events(subject, kind):
            return [json.loads(e["payload_json"]) | {"sequence": e["sequence"]} for e in
                    journal.all("SELECT * FROM events WHERE subject_id=? AND kind=? ORDER BY sequence", (subject, kind))]

        def observe(label, frame, mark, req0, rec0, expect_action):
            hits = c.wait_record(frame, rec0)
            record = hits[0]
            turn_id = record["input"].get("turn_id")
            hook_done = rpc.wait_for(lambda m: m.get("method") == "hook/completed" and m["params"].get("turnId", turn_id) == turn_id,
                                     what=f"hook/completed {turn_id}", start=mark)
            done, evs = c.wait_terminal(turn_id, mark)
            rpc.call("thread/read", {"threadId": thread_id})  # round trip, then count again: no late request
            info = {"turn_id": turn_id, "turn_status": done["params"]["turn"].get("status"), "hook_action": record["action"], "guard": record.get("guard"),
                    "hook_completed_status": (hook_done["params"].get("run") or {}).get("status"), "model_requests": c.requests() - req0,
                    "assistant": c.assistant_items(evs), "hook_session_id": record["input"].get("session_id"), "event_methods": [e["method"] for e in evs],
                    "owner_from_hook_ancestry": record.get("owner"), "ancestry": record.get("chain"), "peer_row": record.get("peer")}
            steps[label] = info
            checks.add(f"{label}: guard wrapper reached once with the exact prompt, action={expect_action}",
                       len(hits) == 1 and record["action"] == expect_action and record["input"].get("prompt") == frame, json.dumps({"action": record["action"], "n": len(hits)}))
            checks.add(f"{label}: hook ancestry owner is the owned app-server and matches the registered fixture owner",
                       bool(record.get("owner")) and record["owner"]["pid"] == rpc.pid and record["owner"]["start"] == owner["start"]
                       and record["input"].get("session_id") == thread_id and record["peer"]["incarnation_id"] == recipient["incarnation_id"],
                       json.dumps(record.get("owner")))
            return info

        def expect_blocked(label, info, message_id, before, reasons):
            checks.add(f"{label}: native hook/completed non-completed, zero model requests, no assistant output",
                       info["hook_completed_status"] in ("blocked", "stopped") and info["model_requests"] == 0 and not info["assistant"],
                       json.dumps({k: info[k] for k in ("hook_completed_status", "model_requests", "assistant", "turn_status")}))
            suppression = events(message_id, "notification-suppressed")
            info["suppression_events"] = suppression
            checks.add(f"{label}: exactly one separate notification-suppressed event with an expected reason",
                       len(suppression) == 1 and suppression[0].get("reason") in reasons,
                       json.dumps(suppression)[:300] + f" expected one of {sorted(reasons)}")
            after = snapshot(message_id)
            info["accepted_state_after"] = after["attempts"][0]["state"] if after["attempts"] else None
            checks.add(f"{label}: accepted transport state and receipts are byte-for-byte unchanged by the block",
                       after == before and info["accepted_state_after"] == "ACCEPTED_QUEUED", json.dumps(info["accepted_state_after"]))

        def held(label, sender_call, between, expect_reasons):
            for name in ("arm", "entered.json", "release"):
                (barrier / name).unlink(missing_ok=True)
            (barrier / "arm").write_text("armed")
            req0, rec0, mark = c.requests(), len(c.records()), rpc.mark()
            outcome, envelope, frame = sender_call()
            checks.add(f"{label}: native queue/add accepted (journal ACCEPTED_QUEUED) before close/guard",
                       outcome["state"] == "ACCEPTED_QUEUED" and c.requests() == req0, json.dumps({"state": outcome["state"], "message_id": envelope["message_id"]}))
            before = snapshot(envelope["message_id"])
            deadline = time.monotonic() + WAIT
            while not (barrier / "entered.json").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{label}: native hook never reached the barrier")
                time.sleep(0.05)
            entered = json.loads((barrier / "entered.json").read_text())
            checks.add(f"{label}: native hook entered the barrier with the exact frame; model request count still unchanged",
                       entered["prompt"] == frame and c.requests() == req0, f"hook pid {entered['pid']}")
            ordering = [f"queue/add accepted at journal state {outcome['state']}", "hook entered barrier"] + between()
            (barrier / "release").write_text("go")
            info = observe(label, frame, mark, req0, rec0, "block")
            info.update({"message_id": envelope["message_id"], "exchange_id": envelope["exchange_id"], "kind": envelope["kind"], "ordering": ordering,
                         "journal_outcome_before": outcome["state"]})
            expect_blocked(label, info, envelope["message_id"], before, expect_reasons)
            for name in ("arm", "entered.json", "release"):
                (barrier / name).unlink(missing_ok=True)
            return info, envelope

        def human(label, text):
            req0, rec0, mark = c.requests(), len(c.records()), rpc.mark()
            turn = rpc.call("turn/start", {"threadId": thread_id, "input": c.text_input(text)})["turn"]
            info = observe(label, text, mark, req0, rec0, "allow")
            checks.add(f"{label}: allowed, one model request and assistant output", info["model_requests"] == 1 and len(info["assistant"]) >= 1 and turn["id"] == info["turn_id"],
                       json.dumps({k: info[k] for k in ("model_requests", "assistant")}))
            return info

        def consumed_allowed(label, sender_call):
            req0, rec0, mark = c.requests(), len(c.records()), rpc.mark()
            outcome, envelope, frame = sender_call()
            info = observe(label, frame, mark, req0, rec0, "allow")
            info.update({"message_id": envelope["message_id"], "exchange_id": envelope["exchange_id"], "kind": envelope["kind"], "journal_state": outcome["state"]})
            checks.add(f"{label}: allowed, one model request and assistant output, journal state ACCEPTED_QUEUED unchanged",
                       info["model_requests"] == 1 and len(info["assistant"]) >= 1 and journal.one("SELECT state FROM attempts WHERE message_id=?", (envelope["message_id"],))["state"] == "ACCEPTED_QUEUED",
                       json.dumps({k: info[k] for k in ("model_requests", "assistant")}))
            return info, envelope

        R, S = recipient["handle"], sender["handle"]
        # 1. Ordinary human control while OPEN.
        human("human_control_open", f"NPG-HUMAN-{uuid.uuid4().hex[:8]}")
        # 2. Pre-close exchange X: recipient asks (to sender, fixture noop), sender's root question to recipient is consumed and answered.
        consumed_allowed("root_request_open", lambda: send(s_caller, R, "NPG root question", expect_reply=True))
        ask, ask_envelope, _ = send(r_caller, S, "NPG question X", expect_reply=True)
        steps["exchange_x_request"] = {"message_id": ask["message_id"], "exchange_id": ask["exchange_id"], "state": ask["state"]}
        # 3. Old reply natively accepted before close; close, then explicit open; guard still blocks by exchange cutoff.
        def close_then_open():
            closed = policy(r_caller, "close")
            checks.add("old reply: close is QUIET with a message cutoff covering exchange X", closed["state"] == "QUIET" and closed["message_cutoff"] > 0, json.dumps(closed))
            opened = policy(r_caller, "open")
            checks.add("old reply: policy reopened before the guard runs (cutoffs retained)", opened["state"] == "OPEN" and opened["message_cutoff"] == closed["message_cutoff"], json.dumps(opened))
            return ["apply close from the probe process with the fixture recipient (not from the native hook context)", "apply open (explicit reopen)", "barrier released"]
        held("old_reply_after_reopen", lambda: send(s_caller, "reply-author", "NPG late answer", in_reply_to=ask["message_id"], expect_reply=True), close_then_open, {"exchange-closed"})
        # 4. Informational frame accepted while OPEN, then close; guard sees QUIET at consumption.
        def close_only():
            closed = policy(r_caller, "close")
            checks.add("info: close is QUIET before the guard runs", closed["state"] == "QUIET", json.dumps(closed))
            return ["apply close from the probe process with the fixture recipient (not from the native hook context)", "barrier released"]
        held("info_accepted_then_quiet", lambda: send(s_caller, R, "NPG fyi before close"), close_only, {"exchange-closed", "quiet-informational"})
        # 5. Fresh root request admitted while QUIET with a short expiry; consumption delayed (barrier) until its recorded expiry passes.
        expires = {}

        def wait_expired():
            deadline = time.monotonic() + 15
            while time.time() < expires["at"]:
                if time.monotonic() > deadline:
                    raise TimeoutError("frame expiry never reached")
                time.sleep(0.05)
            return [f"waited until recorded expires_at {expires['at']} passed (condition wait, not a success sleep)", "barrier released"]

        def short_ttl():
            out = send(s_caller, R, "NPG short-lived request", expect_reply=True, expires_seconds=8)
            expires["at"] = out[1]["expires_at"]
            return out
        info, _ = held("expired_native_frame", short_ttl, wait_expired, {"expired"})
        checks.add("expired: policy still QUIET (a blocked frame never reopens)", status(journal, R)["state"] == "QUIET", json.dumps(status(journal, R)))
        # 6. Fresh root request after close passes, reopens, and gets a model reply.
        info, envelope = consumed_allowed("fresh_root_request_after_close", lambda: send(s_caller, R, "NPG fresh task after close", expect_reply=True))
        reopen = events(R, "notification-policy-opened")
        info["policy_opened_events"] = reopen
        checks.add("fresh root request reopened the policy (cause fresh-root-request)", status(journal, R)["state"] == "OPEN" and any(e.get("cause") == "fresh-root-request" for e in reopen), json.dumps(reopen))
        # 7. Ordinary human prompt after a later close passes and reopens.
        policy(r_caller, "close")
        human("human_control_after_close", f"NPG-HUMAN-{uuid.uuid4().hex[:8]}")
        reopen = events(R, "notification-policy-opened")
        checks.add("human prompt after close reopened the policy (cause human-prompt)", status(journal, R)["state"] == "OPEN" and any(e.get("cause") == "human-prompt" for e in reopen), json.dumps(reopen))
        checks.add("total model requests: bootstrap control + human, root, fresh root, human (five); all blocked frames zero", c.requests() == 5, str(c.requests()))
        with c.fixture.lock:
            bodies = list(c.fixture.responses)
            undecoded = c.fixture.undecoded
        blocked = [steps[k]["message_id"] for k in ("old_reply_after_reopen", "info_accepted_then_quiet", "expired_native_frame")]
        checks.add("no blocked frame id reached any model request body", undecoded == 0 and not any(m in b for b in bodies for m in blocked), f"bodies={len(bodies)} undecoded={undecoded}")
        evidence["integration"]["journal"] = {
            "messages": journal.all("SELECT message_id,exchange_id,to_key,in_reply_to FROM messages ORDER BY rowid"),
            "attempts": journal.all("SELECT message_id,state,transport FROM attempts ORDER BY started_at"),
            "suppression": journal.all("SELECT subject_id,kind,payload_json FROM events WHERE kind IN ('notification-suppressed','notification-policy-opened') ORDER BY sequence"),
            "policy": status(journal, R)}
        evidence["model_requests_total"] = c.requests()
    finally:
        journal.close()
        sender_proc.terminate()
        try:
            sender_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sender_proc.kill()
            sender_proc.wait(timeout=5)
        evidence["sender_owner_gone"] = sender_proc.returncode is not None
        c.fixture.shutdown()


def cleanup(rpc_holder, root: Path, evidence: dict):
    for rpc in rpc_holder:
        try:
            rpc.process.stdin.close()
        except OSError:
            pass
        try:
            rpc.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(rpc.pid, signal.SIGTERM)  # own session group, created by this probe
            except ProcessLookupError:
                pass
            try:
                rpc.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(rpc.pid, signal.SIGKILL)
                rpc.process.wait(timeout=5)
        rpc.stderr.close()
        evidence["app_server_exit"] = rpc.process.returncode
        try:
            os.kill(rpc.pid, 0)
            evidence["app_server_gone"] = False
        except ProcessLookupError:
            evidence["app_server_gone"] = True
    for fixture in _FIXTURES:
        try:
            fixture.shutdown()
        finally:
            fixture.server_close()
    evidence["fixture_closed"] = all(f.socket.fileno() == -1 for f in _FIXTURES)
    shutil.rmtree(root, ignore_errors=True)
    evidence["private_root_removed"] = not root.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--codex", default=str(default_codex()))
    parser.add_argument("--hook-command", help="external UserPromptSubmit command; must block the printed block/stop frames")
    parser.add_argument("--nativepeers-root", help="opt-in: exercise the actual notification_policy.guard from this NativePeers tree")
    parser.add_argument("--evidence-dir", default=str(Path(__file__).resolve().parents[2] / "evidence/scratch/notification-guard"))
    args = parser.parse_args()
    evidence, checks, rpc_holder = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, Checks(), []
    root = Path(tempfile.mkdtemp(prefix="np-notify-guard-")).resolve()
    error = None
    try:
        run(args, evidence, checks, root, rpc_holder)
    except BaseException as caught:  # noqa: BLE001 - a blocked probe is evidence too
        error = f"{type(caught).__name__}: {caught}"
        checks.add("probe ran to completion", False, error)
        if rpc_holder:
            tail = (root / "app-server.stderr")
            evidence["app_server_stderr_tail"] = tail.read_text(errors="replace")[-3000:] if tail.exists() else ""
    finally:
        cleanup(rpc_holder, root, evidence)
    checks.add("owned app-server stopped and private home removed", evidence.get("app_server_gone") is True and evidence.get("private_root_removed") is True,
               json.dumps({k: evidence.get(k) for k in ("app_server_gone", "private_root_removed")}))
    evidence["checks"], evidence["verdict"] = checks.rows, "PASS" if checks.ok else "FAIL"
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"result-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str))
    print(f"{evidence['verdict']} evidence={path}")
    return 0 if checks.ok else 1


if __name__ == "__main__":
    sys.exit(main())
