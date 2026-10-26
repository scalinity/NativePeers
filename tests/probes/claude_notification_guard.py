#!/usr/bin/env python3
"""Disposable probe: does the installed Claude Code run UserPromptSubmit for NativePeers frames delivered
through its native cross-session inbox, and does the ACTUAL NativePeers guard keep quiet/expired frames out?

Topology (one fresh owned interactive Claude in a private PTY):
  * the real NativePeers hook path runs inside Claude's own SessionStart/UserPromptSubmit hooks through a small
    private wrapper that calls nativepeers.hooks.observe('claude', ...) against a PRIVATE NPEER_HOME journal:
    SessionStart registers the real native UUID, exact owner PID/birth and CLAUDE_CODE_MESSAGING_SOCKET;
    UserPromptSubmit runs the actual notification_policy.guard for the exact registered owner;
  * frames are real stored envelopes produced by the real Router/presentation.render and handed to the native inbox by
    the real claude_inbox socket codec. The sender is this probe process acting as the finite fixture CONTROLLER
    (Caller kind=controller, completion frames to the native receiver as its recorded parent). No Claude/Codex sender is
    invented or registered; this is a fixture-controller protocol test, NOT end-to-end native sender qualification;
  * close/open use the real notification_policy.apply with the recipient's caller key, issued from this probe process
    (not from the Claude session). The ordering is recorded.
The per-session setting crossSessionInbound=accept is placed ONLY in this launch's --settings overlay (disclosed
disposable fixture); nothing global is changed. Normal implicit settings sources are preserved (so any installed user hooks
also run and the probe records how many registrations the UUID got); configured plugins are disabled only through this
launch's enabledPlugins overlay, using plugin names (never values) from the selected Claude user settings; permission rules
are untouched and --permission-mode dontAsk applies to this launch only. No prompt argument is passed. Inference is capped
at ONE harmless allowed control: a controller completion frame sent after an explicit fixture policy reopen. It does not
qualify a fresh peer root request on Claude (a controller cannot originate one); that behavior is covered by the Codex
actual-policy probe and the shared-guard unit tests. Blocked frames are expected to cause no assistant output; the probe
proves it from the native transcript assistant-line count and Stop hook events, not from request instrumentation, and
makes no zero-API-request claim.

Native-contract limits kept explicit: this measures the installed binary only; if cross-session formatting differs from
presentation.render(message) the exact-frame guard will not match and the probe reports FAIL with the harmless fixture
payload for analysis (it never weakens frame ownership). Onboarding/trust/login prompts, a nested-launch guard, an
unapproved binary hash, missing hooks or a missing messaging socket produce a precise BLOCKED verdict.
Scope not covered: old-reply-after-reopen and fresh-peer-root (Codex actual-policy probe/unit tests), peer-originated senders, and human typing into the PTY.

Post-run collection: the allowed-control assertion originally required an assistant transcript line the moment the Stop hook
arrived, which raced the native transcript flush. The live run now waits (bounded) for persistence before asserting. For an
already-finished run, `--collect-existing EVIDENCE_JSON` performs a READ-ONLY post-exit collection (it never launches Claude,
sends anything, or rewrites the original evidence): it re-verifies the original result, requires the exact-UUID transcript to
sit at <selected Claude home>/projects/<project>/<uuid>.jsonl, parses only assistant TEXT parts (never printing text other
than comparing against the expected marker; thinking/signatures/raw prompts/auth data are never read into output), and writes a
NEW receipt labelled supplemental post-exit verification that retains the original harness failure. It makes no API-request
instrumentation claim.

Usage (outside any Claude/sandbox environment):
  /Users/danny/Documents/Tools/NativePeers/.venv/bin/python tests/probes/claude_notification_guard.py \\
      --nativepeers-root PATH [--claude PATH] [--approved-sha256 HEX] [--workspace PATH]
  python tests/probes/claude_notification_guard.py --collect-existing EVIDENCE_JSON [--evidence-dir DIR]   # read-only, no launch
"""
from __future__ import annotations

import argparse
import collections
import fcntl
import hashlib
import json
import os
import pty
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
import uuid
from pathlib import Path

APPROVED_SHA256 = "ffb64331bc0ee1b69efb7c2c428bd3ab516bc31b75bf60886b356c97c1c25ffd"
DEFAULT_WORKSPACE = "/Users/danny/Documents/Tools/NativePeers"
WAIT = 60.0
BLOCK_PATTERNS = {  # classification only; raw screen text is never persisted
    "trust-prompt": ("do you trust", "trust the files", "trust this folder", "trust this workspace"),
    "login-prompt": ("select login method", "sign in", "log in to", "/login", "paste code", "authentication required"),
    "onboarding": ("choose the text style", "welcome to claude", "let's get started", "terminal setup"),
}
WRAPPER_SCRIPT = r'''#!/usr/bin/env python3
# Private Claude hook wrapper: records every event, then runs the REAL nativepeers.hooks.observe path.
import json, os, sys, time
from pathlib import Path
cfg = json.load(open(sys.argv[1]))
sys.path.insert(0, cfg["nativepeers_root"])
raw = sys.stdin.buffer.read(65537)
event = json.loads(raw)
name = event.get("hook_event_name")


def record(kind, **extra):
    with open(cfg["records"], "a") as stream:
        stream.write(json.dumps({"ts": time.time(), "pid": os.getpid(), "ppid": os.getppid(), "event": name, "kind": kind, **extra}) + "\n")


if name == "Stop":
    record("observed", payload={k: event.get(k) for k in ("session_id", "transcript_path", "stop_hook_active")},
           last_assistant_message=(event.get("last_assistant_message") or "")[:200])
    sys.exit(0)
barrier = Path(cfg["barrier"])
if name == "UserPromptSubmit" and (barrier / "arm").exists():
    # Bounded fixture barrier: parent observes native inbox acceptance before closing the policy, then releases.
    (barrier / "entered.json").write_text(json.dumps({"prompt": event.get("prompt"), "pid": os.getpid(), "ts": time.time()}))
    deadline = time.monotonic() + cfg["barrier_timeout"]
    while not (barrier / "release").exists():
        if time.monotonic() > deadline:
            record("barrier-timeout"); print("probe barrier timeout", file=sys.stderr); sys.exit(1)
        time.sleep(0.05)
try:
    from nativepeers.hooks import observe
    from nativepeers.journal import Journal
    from nativepeers.process import ancestry
    journal = Journal()  # private NPEER_HOME inherited from the launch environment
    try:
        result = observe("claude", event, journal)
    finally:
        journal.close()
    printed = result if (name == "UserPromptSubmit" and isinstance(result, dict) and result.get("decision") == "block") else None
    record("observe", result=result, printed=printed, prompt=event.get("prompt") if name == "UserPromptSubmit" else None,
           session_id=event.get("session_id"), transcript_path=event.get("transcript_path"), cwd=event.get("cwd"),
           source=event.get("source"), socket_env=os.getenv("CLAUDE_CODE_MESSAGING_SOCKET"),
           config_dir_env=os.getenv("CLAUDE_CONFIG_DIR"), ancestry=[p.json() for p in ancestry()[1:]][:8])
    if printed:
        print(json.dumps(printed))  # exactly what hooks.main prints
except SystemExit:
    raise
except Exception as error:
    record("error", error=type(error).__name__, detail=str(error)[:300])
    print("NativePeers registration unavailable: " + type(error).__name__, file=sys.stderr)
'''


class Blocked(Exception):
    """Environment/native policy refused: a precise BLOCKED verdict, not a product failure."""


class Checks:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, name, ok, detail=""):
        self.rows.append({"check": name, "ok": bool(ok), "detail": detail})
        print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail else ""), flush=True)
        return ok

    @property
    def ok(self):
        return bool(self.rows) and all(r["ok"] for r in self.rows)


class Pty:
    """One owned PTY child; reader keeps only a bounded sanitized tail for prompt classification."""

    def __init__(self, binary, argv, env, cwd):
        self.tail = collections.deque(maxlen=40000)
        self.lock = threading.Lock()
        pid, fd = pty.fork()
        if pid == 0:  # child: exec the exact binary, no shell
            try:
                os.chdir(cwd)
                os.execve(binary, argv, env)
            finally:
                os._exit(127)
        self.pid, self.fd, self.exited = pid, fd, None
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", "", data.decode(errors="replace"))
            with self.lock:
                self.tail.extend(text.lower())

    def classify(self):
        with self.lock:
            screen = "".join(self.tail)
        return next((kind for kind, words in BLOCK_PATTERNS.items() if any(w in screen for w in words)), None)

    def poll(self):
        if self.exited is None:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                self.exited = status
        return self.exited


def plugin_names() -> list[str]:
    """Names only (never values) of plugins enabled in the selected Claude user settings; parsed in memory, never printed."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        data = json.loads((base / "settings.json").read_text())
    except (OSError, ValueError):
        return []
    plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
    return sorted(str(name) for name in plugins) if isinstance(plugins, dict) else []


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def wait_until(condition, timeout, what, step=0.1, abort=None):
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if abort:
            abort()
        if time.monotonic() > deadline:
            raise TimeoutError(f"{what} not observed within {timeout}s")
        time.sleep(step)


KNOWN_EARLY = "exactly one model reply (one Stop event) and at least one assistant transcript line after the allowed control"
MARKER = "NPG-CLAUDE-OK"


def contained_transcript(path, native_id) -> Path:
    """Exact-UUID transcript inside the selected Claude home's projects tree (symlinks refused)."""
    uuid.UUID(native_id)
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError("transcript path is a symlink")
    resolved = candidate.resolve(strict=True)
    projects = (Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser().resolve() / "projects").resolve()
    if resolved.name != native_id + ".jsonl" or resolved.parent.parent != projects or not resolved.is_file():
        raise ValueError("transcript is not <selected Claude home>/projects/<project>/<uuid>.jsonl for the exact UUID")
    return resolved


def read_transcript(resolved: Path, limit=32 * 1024 * 1024):
    """Entries as (index, type, assistant text parts, timestamp, raw line kept in memory only). Only text parts are extracted."""
    if resolved.stat().st_size > limit:
        raise ValueError("transcript exceeds the read limit")
    raw = resolved.read_bytes()
    entries = []
    for index, line in enumerate(raw.decode(errors="replace").splitlines()):
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        texts = []
        if obj.get("type") == "assistant":
            content = message.get("content")
            if isinstance(content, str):
                parts = [content]
            elif isinstance(content, list):
                parts = [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str)]
            else:
                parts = []
            texts = [t for t in parts if t.strip()]
        entries.append((index, obj.get("type"), texts, obj.get("timestamp"), line))
    return raw, entries


def iso_epoch(value):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def collect_existing(args):
    """Read-only supplemental verification of a finished run; writes a NEW receipt and never touches the original."""
    source = Path(args.collect_existing).resolve(strict=True)
    source_raw = source.read_bytes()
    original = json.loads(source_raw)
    checks, info = Checks(), {}
    receipt = {"label": "SUPPLEMENTAL POST-EXIT VERIFICATION (read-only; no Claude launch, no send, no native history change)",
               "collected": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "source_evidence": {"path": str(source), "sha256": hashlib.sha256(source_raw).hexdigest(), "size": len(source_raw)},
               "original_verdict_retained": original.get("verdict"), "info": info,
               "claims": "observed one native allowed reply and no model output during blocked phases; no API-request instrumentation and no zero-API-request claim for Claude"}
    failed = [c["check"] for c in original.get("checks", []) if not c.get("ok")]
    info["original_failed_checks"] = failed
    checks.add("original harness verdict is FAIL and its only failed check is the known early-transcript assertion",
               original.get("verdict") == "FAIL" and failed == [KNOWN_EARLY], json.dumps(failed)[:300])
    checks.add("original cleanup proven: Claude, captured descendants gone and private data removed",
               original.get("cleanup_proven") is True and original.get("claude_gone") is True and original.get("descendants_gone") is True
               and original.get("private_root_removed") is True, "")
    steps = original.get("steps", {})
    control = steps.get("allowed_control_after_explicit_reopen", {})
    checks.add("original recorded the accepted-before-close, expired and allowed-control steps",
               set(steps) == {"accepted_then_quiet", "expired_native_frame", "allowed_control_after_explicit_reopen"}, json.dumps(sorted(steps)))
    hooks = original.get("hook_records", [])
    stops = [r for r in hooks if r.get("event") == "Stop"]
    replies = [str(x or "").strip() for x in control.get("stop_last_assistant_message") or []]
    checks.add("exactly one native Stop with last_assistant_message equal to the expected marker", len(stops) == 1 and replies == [MARKER], json.dumps({"stops": len(stops), "replies": replies}))
    native_id = (original.get("session") or {}).get("native_id", "")
    try:
        resolved = contained_transcript((original.get("transcript") or {}).get("path", ""), native_id)
        raw, entries = read_transcript(resolved)
        error = None
    except (OSError, ValueError) as caught:
        resolved, raw, entries, error = None, b"", [], f"{type(caught).__name__}: {str(caught)[:200]}"
    checks.add("transcript path is the exact-UUID file under the selected Claude projects tree and was read read-only", error is None, error or resolved.name)
    texts = [(index, text, ts) for index, kind, parts, ts, _ in entries if kind == "assistant" for text in parts]
    info["transcript"] = {"path": str(resolved) if resolved else None, "sha256": hashlib.sha256(raw).hexdigest() if resolved else None, "size": len(raw),
                          "entries": len(entries), "assistant_text_parts": len(texts)}
    matching = [t for t in texts if t[1].strip() == MARKER]
    checks.add("exactly one assistant text part in the native transcript and it equals the marker", len(texts) == 1 and len(matching) == 1, f"parts={len(texts)} matching={len(matching)}")
    if matching:
        index, _, ts = matching[0]
        control_id = control.get("message_id", "")
        control_index = next((i for i, _kind, _p, _t, line in entries if control_id and control_id in line), None)
        prompts = [r for r in hooks if r.get("event") == "UserPromptSubmit" and r.get("kind") == "observe"]
        control_ts, stop_ts, assistant_ts = (prompts[-1].get("ts") if prompts else None), (stops[0].get("ts") if stops else None), iso_epoch(ts)
        after_control = None if control_index is None else index > control_index
        in_window = None if None in (control_ts, stop_ts, assistant_ts) else bool(control_ts - 2 <= assistant_ts <= stop_ts + 60)
        info["correlation"] = {"transcript_line_after_control_frame": after_control, "assistant_timestamp_within_control_to_stop_window": in_window,
                               "control_to_assistant_seconds": None if None in (control_ts, assistant_ts) else round(assistant_ts - control_ts, 2),
                               "assistant_to_stop_seconds": None if None in (stop_ts, assistant_ts) else round(stop_ts - assistant_ts, 2)}
        checks.add("the matching assistant text follows the allowed control frame (transcript order, else timestamp window)",
                   after_control is True or (after_control is None and in_window is True), json.dumps(info["correlation"]))
    blocked = [steps.get(k, {}).get("message_id") for k in ("accepted_then_quiet", "expired_native_frame")]
    info["blocked_frame_ids_present_in_transcript"] = {"informational_only": True, "count": sum(1 for b in blocked if b and any(b in line for *_x, line in entries))}
    receipt["checks"] = checks.rows
    receipt["verdict"] = "SUPPLEMENTAL_PASS" if checks.ok else "SUPPLEMENTAL_FAIL"
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"collection-{time.strftime('%Y%m%dT%H%M%S')}-{receipt['source_evidence']['sha256'][:8]}.json"
    with path.open("x") as stream:  # never overwrites; the original result is only ever read
        stream.write(json.dumps(receipt, indent=2, default=str))
    print(f"{receipt['verdict']} receipt={path}")
    return 0 if checks.ok else 1


def run(args, evidence, checks, root, owned):
    npr = Path(args.nativepeers_root).resolve(strict=True)
    for needed in ("hooks.py", "notification_policy.py", "presentation.py", "routing.py", "registry.py"):
        if not (npr / "nativepeers" / needed).is_file():
            raise Blocked(f"--nativepeers-root lacks nativepeers/{needed}")
    sys.path.insert(0, str(npr))
    evidence["nativepeers_root"] = str(npr)
    evidence["nativepeers_sources"] = {n: sha256(npr / "nativepeers" / n) for n in ("hooks.py", "notification_policy.py", "presentation.py", "routing.py")}
    from nativepeers.launch import launch_environment
    try:
        env = launch_environment("claude")  # preserves native auth; refuses nested Claude, which is never cleared
    except RuntimeError as error:
        raise Blocked(f"nested Claude launch guard: {error}") from None
    binary = Path(args.claude).resolve(strict=True)
    digest = sha256(binary)
    version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30, env=env).stdout.strip()
    evidence["executable"] = {"path": str(binary), "sha256": digest, "version": version, "approved_sha256": args.approved_sha256}
    if digest != args.approved_sha256:
        raise Blocked("Claude executable differs from the approved SHA-256; refusing to launch")
    default_binary = (Path.home() / ".local/bin/claude").resolve()
    if default_binary != binary:
        raise Blocked(f"hooks.observe owner match requires ~/.local/bin/claude -> {binary}, found {default_binary}")
    workspace = Path(args.workspace).resolve(strict=True)

    state = root / "np-home"
    state.mkdir(mode=0o700)
    barrier = root / "barrier"
    barrier.mkdir()
    hooks_dir = root / "hooks"
    hooks_dir.mkdir()
    records_path = root / "hook-records.jsonl"
    (hooks_dir / "wrapper.py").write_text(WRAPPER_SCRIPT)
    (hooks_dir / "wrapper.json").write_text(json.dumps({"nativepeers_root": str(npr), "records": str(records_path), "barrier": str(barrier), "barrier_timeout": 90}))
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(hooks_dir / 'wrapper.py'))} {shlex.quote(str(hooks_dir / 'wrapper.json'))}"
    handler = [{"hooks": [{"type": "command", "command": command, "timeout": 120}]}]
    native_id, title = str(uuid.uuid4()), "np-notify-guard-" + uuid.uuid4().hex[:8]
    plugins = plugin_names()
    settings = {"crossSessionInbound": "accept", "enabledPlugins": {name: False for name in plugins},
                "hooks": {"SessionStart": handler, "UserPromptSubmit": handler, "Stop": handler}}
    (root / "settings.json").write_text(json.dumps(settings))
    evidence["session"] = {"native_id": native_id, "title": title, "workspace": str(workspace),
                           "inbound_setting": "crossSessionInbound=accept via this launch's --settings overlay only (disclosed disposable fixture)",
                           "permission_mode": "dontAsk for this tool-less launch only; native permission rules untouched",
                           "plugins_disabled_in_overlay": len(plugins), "setting_sources": "normal implicit sources preserved",
                           "topology": "one owned interactive Claude in a private PTY; hooks run through hooks.observe; fixture-controller sender (no registered Claude/Codex sender)"}
    argv = [str(binary), "--name", title, "--session-id", native_id, "--model", "sonnet", "--permission-mode", "dontAsk",
            "--settings", str(root / "settings.json"), "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--tools", ""]
    env.update(NPEER_HOME=str(state), TERM="xterm-256color")
    env.pop("CLAUDE_ENV_FILE", None)

    from nativepeers.caller import Caller
    from nativepeers.journal import Journal
    from nativepeers.notification_policy import apply, status
    from nativepeers.presentation import render
    from nativepeers.process import identity, signal_owned
    from nativepeers.registry import Registry
    from nativepeers.routing import Router
    import asyncio

    journal = Journal(state)
    owned["journal"] = journal
    from nativepeers.process import descendants, matches, signal_captured
    owned["proc"] = dict(identity=identity, signal_owned=signal_owned, descendants=descendants, matches=matches, signal_captured=signal_captured)
    child = Pty(str(binary), argv, env, str(workspace))
    owned["pty"] = child
    try:  # captured right after fork (may still be the pre-exec image); refreshed after native registration or at cleanup
        owned["owner"] = identity(child.pid).json()
    except (OSError, ProcessLookupError):
        owned["owner"] = None
    evidence["claude_owner_at_fork"] = owned["owner"]

    def records():
        return [json.loads(l) for l in records_path.read_text().splitlines()] if records_path.exists() else []

    def guard_abort():
        kind = child.classify()
        if kind:
            raise Blocked(f"native {kind} appeared; the probe never grants trust, logs in or changes settings")
        if child.poll() is not None:
            raise Blocked(f"Claude exited early (status {child.exited}) before the probe finished")

    # SessionStart through the real observe path registers the exact native identity.
    def registered():
        row = journal.one("SELECT descriptor_json FROM peer_instances WHERE native_id=? AND status='live'", (native_id,))
        return json.loads(row["descriptor_json"]) if row else None
    try:
        recipient = wait_until(registered, 90, "SessionStart registration", abort=guard_abort)
    except TimeoutError:
        errors = [r for r in records() if r["kind"] == "error"]
        started = [r for r in records() if r["event"] == "SessionStart"]
        raise Blocked("SessionStart registration unavailable: " + json.dumps(errors[-1:] or started[-1:] or "no hook records (hooks unsupported or not loaded?)")) from None
    start = next(r for r in records() if r["event"] == "SessionStart" and r["kind"] == "observe")
    evidence["session_start"] = {"session_id": start["session_id"], "socket_env": start["socket_env"], "config_dir_env_set": bool(start["config_dir_env"]),
                                 "source": start["source"], "result": start["result"], "owner": recipient["owner"], "transcript_path": start["transcript_path"]}
    checks.add("SessionStart reported the exact fresh UUID, the PTY child as owner (PID/birth) and a messaging socket",
               start["session_id"] == native_id and recipient["owner"]["pid"] == child.pid and recipient["owner"]["start"] == owned["owner"]["start"]
               and recipient["owner_kind"] == "interactive-standalone" and recipient["transport"]["kind"] == "claude-inbox" and bool(start["socket_env"]),
               json.dumps({"owner": recipient["owner"], "kind": recipient["owner_kind"], "transport": recipient["transport"]["kind"]}))
    transcript_path = start.get("transcript_path")

    # Same PID, birth and UID as the process captured at fork: adopt the actual native executable/pgid for exact cleanup.
    if owned["owner"] and all(recipient["owner"].get(k) == owned["owner"].get(k) for k in ("pid", "start", "uid")):
        owned["owner"] = recipient["owner"]
    rows = journal.all("SELECT incarnation_id,owner_kind,status FROM peer_instances WHERE native_id=?", (native_id,))
    evidence["registration_rows_for_uuid"] = [{"owner_kind": r["owner_kind"], "status": r["status"]} for r in rows]

    registry = Registry(journal)
    group_id = "np-notify-guard-fixture-" + uuid.uuid4().hex[:8]
    controller_identity = identity(os.getpid()).json()
    evidence["fixture_controller"] = {"group_id": group_id, "generation": 1, "process": controller_identity,
                                      "owns_native_child": child.pid, "note": "finite probe controller; fixture-controller protocol test, not native sender qualification"}

    def live_recipient():
        row = journal.one("SELECT descriptor_json FROM peer_instances WHERE native_id=? AND status='live' ORDER BY last_seen DESC LIMIT 1", (native_id,))
        return json.loads(row["descriptor_json"])

    def controller():
        return Caller("controller", f"controller:{group_id}:1", None,
                      {"group_id": group_id, "generation": 1, "parent_key": live_recipient()["handle"], "fixture": "notification-guard probe controller"})

    router = Router(journal, registry)  # real adapters: claude-inbox socket codec for the recipient

    def send(text, **kwargs):
        target = live_recipient()["handle"]
        outcome = asyncio.run(router.send(controller(), target, text, kind="completion", **kwargs))
        envelope = json.loads(journal.one("SELECT payload_json FROM messages WHERE message_id=?", (outcome["message_id"],))["payload_json"])
        return outcome, envelope, render(envelope)

    def policy(action):
        peer = live_recipient()
        return asyncio.run(apply(journal, registry, Caller("peer", peer["handle"], peer["incarnation_id"]), action))

    def snapshot(message_id):
        return {"attempts": journal.all("SELECT * FROM attempts WHERE message_id=? ORDER BY attempt_id", (message_id,)),
                "receipts": journal.all("SELECT * FROM receipts WHERE message_id=? ORDER BY receipt_id", (message_id,))}

    def suppression(message_id):
        return [json.loads(e["payload_json"]) for e in journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='notification-suppressed'", (message_id,))]

    def assistant_lines():
        path = transcript_path
        if not path:
            return None
        resolved = Path(path).resolve()
        if resolved.name != native_id + ".jsonl" or not resolved.is_file():
            return 0  # created lazily: not present means no assistant output yet
        count = 0
        for line in resolved.read_text(errors="replace").splitlines():
            try:
                count += json.loads(line).get("type") == "assistant"
            except json.JSONDecodeError:
                continue
        return count

    def stops():
        return [r for r in records() if r["event"] == "Stop"]

    steps = evidence.setdefault("steps", {})

    def prompt_record(frame, since):
        return wait_until(lambda: next((r for r in records()[since:] if r["event"] == "UserPromptSubmit" and r["kind"] == "observe" and r["prompt"] == frame), None),
                          WAIT, "UserPromptSubmit record for the exact frame", abort=guard_abort)

    def clear_barrier():
        for name in ("arm", "entered.json", "release"):
            (barrier / name).unlink(missing_ok=True)

    def held_blocked(label, sender_call, between, reasons):
        clear_barrier()
        (barrier / "arm").write_text("armed")
        since, assistants0, stops0 = len(records()), assistant_lines(), len(stops())
        outcome, envelope, frame = sender_call()
        before = snapshot(envelope["message_id"])
        step = steps[label] = {"message_id": envelope["message_id"], "exchange_id": envelope["exchange_id"], "kind": envelope["kind"],
                               "delivery_state": outcome["state"], "ordering": [f"native inbox submission returned state {outcome['state']}"]}
        checks.add(f"{label}: real native inbox submission completed (state {outcome['state']}) and was not retained pre-submission",
                   outcome["state"] not in ("RETAINED", "OUTBOX_COMMITTED"), json.dumps({"state": outcome["state"]}))
        try:
            entered = json.loads(wait_until(lambda: (barrier / "entered.json").exists() and (barrier / "entered.json").read_text(), WAIT,
                                            "native UserPromptSubmit reaching the barrier", abort=guard_abort))
            step["ordering"].append("native UserPromptSubmit hook entered the barrier")
            exact = entered["prompt"] == frame
            step["hook_prompt_matches_render"] = exact
            step["hook_prompt_sample"] = None if exact else (entered["prompt"] or "")[:1500]  # harmless fixture payload for format analysis
            step["ordering"] += between()
        finally:
            (barrier / "release").write_text("go")
        checks.add(f"{label}: native hook prompt is exactly presentation.render(stored envelope)", step["hook_prompt_matches_render"],
                   "format differs; sample retained in evidence" if not step["hook_prompt_matches_render"] else frame[:60])
        record = prompt_record(entered["prompt"], since)
        step.update({"guard_result": record["result"], "printed": record["printed"], "owner_in_hook": [a["pid"] for a in record.get("ancestry", [])][:4]})
        checks.add(f"{label}: actual guard blocked, printed decision:block, and requested suppressOriginalPrompt",
                   bool(record["printed"]) and record["printed"].get("decision") == "block"
                   and (record["printed"].get("hookSpecificOutput") or {}).get("suppressOriginalPrompt") is True, json.dumps(record["printed"])[:300])
        events = suppression(envelope["message_id"])
        step["suppression_events"] = events
        checks.add(f"{label}: separate notification-suppressed event with an expected reason", len(events) == 1 and events[0].get("reason") in reasons,
                   json.dumps(events)[:300] + f" expected {sorted(reasons)}")
        after = snapshot(envelope["message_id"])
        checks.add(f"{label}: accepted transport state and receipts unchanged by the block", after == before, json.dumps([a["state"] for a in after["attempts"]]))
        checks.add(f"{label}: no assistant transcript lines and no Stop hook caused by the blocked frame", assistant_lines() == assistants0 and len(stops()) == stops0,
                   json.dumps({"assistant_lines": assistant_lines(), "stops": len(stops())}))
        clear_barrier()
        if not (step["hook_prompt_matches_render"] and record["printed"]):
            raise RuntimeError(f"{label}: frame was not kept out; stopping before any further send to cap inference")

    try:
        # A. Controller completion frame accepted by the native inbox while OPEN, then closed before the hook's guard runs.
        def close_only():
            closed = policy("close")
            checks.add("completion frame: policy QUIET before the guard runs", closed["state"] == "QUIET", json.dumps(closed))
            return ["apply close from the probe process with the recipient's caller key (fixture action; not from the Claude session)", "barrier released"]
        held_blocked("accepted_then_quiet", lambda: send("NPG-CLAUDE notice accepted before close"), close_only, {"exchange-closed", "quiet-informational"})

        # B. Short-lived frame accepted while OPEN (after an explicit fixture reopen), policy closed, then held until its stored expiry passes.
        reopened = policy("open")
        checks.add("fixture explicit reopen before the expired case", reopened["state"] == "OPEN", json.dumps(reopened))
        expires = {}

        def short():
            out = send("NPG-CLAUDE short-lived notice", expires_seconds=12)
            expires["at"] = out[1]["expires_at"]
            return out

        def close_then_wait_expired():
            closed = policy("close")
            checks.add("expired: policy QUIET at consumption", closed["state"] == "QUIET", json.dumps(closed))
            wait_until(lambda: time.time() >= expires["at"], 30, "recorded expiry", abort=guard_abort)
            return ["apply close from the probe process", f"waited (condition) until stored expires_at {expires['at']} passed", "barrier released"]
        held_blocked("expired_native_frame", short, close_then_wait_expired, {"expired"})
        checks.add("expired: policy still QUIET (a blocked frame never reopens)", status(journal, live_recipient()["handle"])["state"] == "QUIET", "")

        # C. ONE harmless allowed control AFTER an explicit fixture reopen: a controller completion frame (not a fresh peer root request).
        reopened = policy("open")
        checks.add("fixture explicit reopen before the allowed control", reopened["state"] == "OPEN", json.dumps(reopened))
        since, stops0 = len(records()), len(stops())
        outcome, envelope, frame = send("NPG-CLAUDE control: reply with exactly NPG-CLAUDE-OK and nothing else. Do not use tools.")
        record = prompt_record(frame, since)
        step = steps["allowed_control_after_explicit_reopen"] = {"message_id": envelope["message_id"], "delivery_state": outcome["state"], "guard_result": record["result"],
                                                                 "label": "controller completion frame after explicit reopen; does not qualify a fresh peer root request"}
        checks.add("allowed control after explicit reopen reached the native hook with the exact frame and was not blocked", record["printed"] is None,
                   json.dumps(record["result"])[:200])
        wait_until(lambda: len(stops()) > stops0, 180, "Stop hook after the allowed control", abort=guard_abort)
        step["stop_last_assistant_message"] = [x.get("last_assistant_message") for x in stops()[stops0:]]
        def persisted_texts():
            try:
                return [t for _i, kind, parts, _ts, _l in read_transcript(contained_transcript(transcript_path, native_id))[1] if kind == "assistant" for t in parts]
            except (OSError, ValueError, TypeError):
                return []
        try:  # the native transcript is flushed after Stop: wait boundedly for positive persistence, never sleep-for-success
            wait_until(lambda: persisted_texts(), 45, "assistant text persisted in the exact-UUID transcript")
        except TimeoutError:
            pass
        texts = [t.strip() for t in persisted_texts()]
        step["persisted_assistant_text_matches_marker"] = texts == [MARKER]
        checks.add("exactly one Stop and exactly one persisted assistant text equal to the marker (bounded transcript-flush wait)",
                   len(stops()) == 1 and step["stop_last_assistant_message"] == [MARKER] and texts == [MARKER],
                   json.dumps({"stops": len(stops()), "assistant_text_parts": len(texts), "matches": texts == [MARKER]}))
        opened = [json.loads(e["payload_json"]) for e in journal.all("SELECT payload_json FROM events WHERE subject_id=? AND kind='notification-policy-opened'", (live_recipient()["handle"],))]
        step["policy_opened_events"] = opened
        checks.add("policy is OPEN via explicit-open and the allowed frame did not reopen or close it", status(journal, live_recipient()["handle"])["state"] == "OPEN"
                   and all(e.get("cause") == "explicit-open" for e in opened), json.dumps(opened))
    finally:
        evidence["hook_records"] = [{k: r.get(k) for k in ("event", "kind", "ts", "prompt", "printed", "error", "detail")} for r in records()]
        evidence["transcript"] = {"path": transcript_path, "retained": True, "note": "native history created by the fixture; no verified exact-UUID native delete API was used, so it is retained and never edited"}
        try:
            evidence["journal"] = {"attempts": journal.all("SELECT message_id,state,transport FROM attempts ORDER BY started_at"),
                                   "suppressed": journal.all("SELECT subject_id,payload_json FROM events WHERE kind='notification-suppressed' ORDER BY sequence"),
                                   "policy": status(journal, live_recipient()["handle"])}
        except Exception as error:  # noqa: BLE001 - evidence only
            evidence["journal"] = {"error": type(error).__name__}


def cleanup(owned, root, evidence):
    """Positive, bounded: stop only the child this probe forked (verified by PID+birth+UID), then captured descendants."""
    child, proc = owned.get("pty"), owned.get("proc")
    captured = {}
    if child is not None and proc is not None:
        record = owned.get("owner")
        if child.poll() is None:  # unreaped, so this PID is still ours; refresh to the actual native executable/pgid
            try:
                current = proc["identity"](child.pid).json()
                if record is None or all(current.get(k) == record.get(k) for k in ("pid", "start", "uid")):
                    record = owned["owner"] = current
            except (OSError, ProcessLookupError):
                pass
            try:
                captured = {r["pid"]: r for r in proc["descendants"]([record])} if record else {}
            except Exception:  # noqa: BLE001 - capture is best effort; the group signal below still applies
                captured = {}
        for sig, grace in ((signal.SIGTERM, 8), (signal.SIGKILL, 5)):
            if child.poll() is not None:
                break  # reaped: never signal a historical PID/group
            try:
                if record:
                    proc["signal_owned"](record, sig, group=True)
            except Exception:  # noqa: BLE001 - identity changed or already gone: never signal a stranger
                pass
            deadline = time.monotonic() + grace
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.1)
        for sig, grace in ((signal.SIGTERM, 3), (signal.SIGKILL, 3)):
            live = [r for r in captured.values() if proc["matches"](r)]
            if not live:
                break
            for r in live:
                try:
                    proc["signal_captured"](r, sig)
                except Exception:  # noqa: BLE001
                    pass
            deadline = time.monotonic() + grace
            while any(proc["matches"](r) for r in live) and time.monotonic() < deadline:
                time.sleep(0.1)
        evidence["claude_exit"] = child.exited
        evidence["claude_gone"] = child.poll() is not None
        evidence["captured_descendants"] = len(captured)
        evidence["descendants_gone"] = not any(proc["matches"](r) for r in captured.values())
        try:
            os.close(child.fd)
        except OSError:
            pass
    if owned.get("journal"):
        try:
            owned["journal"].close()
        except Exception:  # noqa: BLE001
            pass
    shutil.rmtree(root, ignore_errors=True)
    evidence["private_root_removed"] = not root.exists()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--nativepeers-root", help="NativePeers tree providing the real hooks/guard (required unless --collect-existing)")
    parser.add_argument("--collect-existing", metavar="EVIDENCE_JSON", help="read-only post-exit collection for a finished run; writes a NEW supplemental receipt, never launches Claude")
    parser.add_argument("--claude", default=str(Path.home() / ".local/share/claude/versions/2.1.310"))
    parser.add_argument("--approved-sha256", default=APPROVED_SHA256)
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE)
    parser.add_argument("--evidence-dir", default=str(Path(__file__).resolve().parents[2] / "evidence/scratch/claude-notification-guard"))
    args = parser.parse_args()
    if args.collect_existing:
        return collect_existing(args)
    if not args.nativepeers_root:
        parser.error("--nativepeers-root is required unless --collect-existing is used")
    evidence, checks, owned = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, Checks(), {}
    root = Path(tempfile.mkdtemp(prefix="np-claude-guard-")).resolve()
    verdict_override = None
    try:
        run(args, evidence, checks, root, owned)
    except Blocked as blocked:
        verdict_override = "BLOCKED"
        evidence["blocked_reason"] = str(blocked)[:600]
        print("BLOCKED :: " + evidence["blocked_reason"], flush=True)
    except BaseException as caught:  # noqa: BLE001 - recorded as FAIL evidence
        checks.add("probe ran to completion", False, f"{type(caught).__name__}: {str(caught)[:400]}")
    finally:
        cleanup(owned, root, evidence)
    cleaned = (evidence.get("private_root_removed") is True and evidence.get("claude_gone", True) is True and evidence.get("descendants_gone", True) is True)
    checks.add("owned Claude and captured descendants stopped; PTY, journal closed; private fixture data removed", cleaned,
               json.dumps({k: evidence.get(k) for k in ("claude_gone", "descendants_gone", "captured_descendants", "private_root_removed")}))
    evidence["checks"] = checks.rows
    evidence["cleanup_proven"] = cleaned
    # A BLOCKED verdict is only valid when cleanup is also proven.
    evidence["verdict"] = ("BLOCKED" if cleaned else "FAIL") if verdict_override else ("PASS" if checks.ok else "FAIL")
    out = Path(args.evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"result-{time.strftime('%Y%m%dT%H%M%S')}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str))
    print(f"{evidence['verdict']} evidence={path}")
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
