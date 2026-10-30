#!/usr/bin/env python3
r"""Opt-in issue-7 acceptance on two *standalone native TUIs*, never worker turns.

Run with the selected tree's Python (pyte is required for UI qualification)::

  .venv/bin/python tests/probes/quiet_close_tui.py --codex /absolute/codex \
    --nativepeers-root /absolute/NativePeers \
    --run /absolute/NativePeers/evidence/scratch/quiet-tui-NEW

--codex defaults to the managed launcher's selected executable. Native homes and NPEER_HOME
are fresh/private. HOME remains real because product hooks deliberately match
the managed current executable. A nonselected candidate is BLOCKED, never
selected/approved by this probe. No credentials or provider config are copied.

Only localhost Responses is configured. Raw compressed bodies, decoded JSON,
unmodified ANSI bytes, terminal frames, native history, command/source hashes,
queue receipts and process births are retained. External network traffic is
not instrumented; zero external *requests* is therefore never asserted.

The sender's real native shell binds itself normally and calls the real Router
and CLI queue adapter. Completion envelopes use explicitly labeled private
completion-route/result fixtures: no worker, controller authority, registration
or receipt is fabricated. This qualifies completion-frame handling, not worker
execution or controller lifecycle. The metadata-only app-server is used solely
for native hook trust and queue/list; it never starts/resumes a model thread.

Two distinct batches each contain 17 progress and 5 completion fixtures. The
warm batch drains completely. During the cold batch the birth-verified receiver
is frozen after a suppression prefix and killed deliberately, retaining its
original queued submission IDs for exact-UUID resume without human input.
No accepted frame is ever resubmitted. Missing the partial-drain window fails.

Exit 0 = all checks passed; 1 = failure; 2 = blocked prerequisite. No live run
is performed by --help or import. `_helper` is an internal native-shell entry.
"""
from __future__ import annotations

import argparse
import asyncio
import codecs
import errno
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pty
import queue
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

WAIT = 60
STOP_WAIT = 3
ROWS, COLS = 48, 160
ACTIVE = re.compile(r"(?:esc to interrupt|working(?:\s|\.|…)|thinking(?:\s|\.|…)|\bqueued\b|\bpending\b)", re.I)


def require_quiet_frame(frame, final):
    text = "\n".join(frame.get("chat_tail", []) + frame["lines"])
    if final and text.count(final) > 1:
        raise RuntimeError("The identifiable final was rendered again after its original boundary")
    tail = text.split(final, 1)[-1] if final and final in text else text
    forbidden = ("Blocked by hook", "NativePeers notification retained", "completion-phase-closed",
                 "notification-phase-closed", "retained notification", "retention notice")
    if any(value.lower() in tail.lower() for value in forbidden):
        raise RuntimeError("Late hook/retention row appeared after the final report")
    if len(re.findall(r"Worked for", tail, re.I)) > 1:
        raise RuntimeError("An extra work footer appeared after the final report")
    if ACTIVE.search("\n".join(frame["lines"][-5:])):
        raise RuntimeError("Native work/queued footer appeared after final report")


class Blocked(RuntimeError):
    pass


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + str(time.time_ns()))
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def eastern():
    return datetime.now(ZoneInfo("America/New_York")).isoformat()


def decode_body(raw, encoding):
    if encoding in (None, "", "identity"):
        decoded = raw
    elif encoding == "gzip":
        decoded = gzip.decompress(raw)
    elif encoding == "zstd":
        try:
            from compression import zstd
        except ImportError:
            import zstandard
            decoded = zstandard.ZstdDecompressor().decompress(raw)
        else:
            decoded = zstd.decompress(raw)
    else:
        raise ValueError(f"Unsupported Content-Encoding: {encoding}")
    return json.loads(decoded)


def input_text(payload):
    """Only input content, excluding tool schemas and instructions."""
    return json.dumps(payload.get("input", []), ensure_ascii=False)


def last_user_text(payload):
    users = [item for item in payload.get("input", [])
             if isinstance(item, dict) and item.get("role") == "user"]
    return json.dumps(users[-1].get("content", ""), ensure_ascii=False) if users else ""


def shell_tool(payload, command):
    names = {tool.get("name") for tool in payload.get("tools", [])}
    if "exec_command" in names:
        return "exec_command", {"cmd": command, "login": False, "yield_time_ms": 1000, "max_output_tokens": 1000}
    raise Blocked(f"No supported native shell tool; declarations: {sorted(str(n) for n in names)}")


class Model(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, run):
        super().__init__(("127.0.0.1", 0), ModelHandler)
        self.run = run
        self.lock = threading.Lock()
        self.records, self.errors, self.plans = [], [], {}

    def count(self):
        with self.lock:
            return len(self.records)

    def plan(self, marker, command, done, final):
        with self.lock:
            self.plans[marker] = {"command": command, "done": str(done), "final": final, "started": False}

    def answer(self, payload):
        latest = last_user_text(payload)
        with self.lock:
            matches = [(marker, plan) for marker, plan in self.plans.items() if marker in latest]
            if len(matches) != 1:
                # A leaked notification must leave visible, decisive failure evidence.
                return {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "QCTUI_UNPLANNED_MODEL_INPUT"}]}
            marker, plan = matches[0]
            if not plan["started"]:
                name, arguments = shell_tool(payload, plan["command"])
                plan["started"] = True
            elif Path(plan["done"]).exists():
                result = json.loads(Path(plan["done"]).read_text())
                if not result.get("ok"):
                    raise RuntimeError(f"Native shell helper failed: {result}")
                return {"type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": plan["final"]}]}
            else:
                outputs = [str(item.get("output", "")) for item in payload.get("input", [])
                           if isinstance(item, dict) and item.get("type") == "function_call_output"]
                text = outputs[-1] if outputs else ""
                match = re.search(r'(?:session ID\s+|"session_id"\s*:\s*)(\d+)', text, re.I)
                if not match or "write_stdin" not in {t.get("name") for t in payload.get("tools", [])}:
                    raise RuntimeError("Helper not finished and native tool supplied no resumable session ID")
                name, arguments = "write_stdin", {"session_id": int(match[1]), "chars": "", "yield_time_ms": 1000,
                                                   "max_output_tokens": 1000}
        return {"type": "function_call", "name": name, "arguments": json.dumps(arguments),
                "call_id": "qctui-" + str(time.time_ns())}


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        with self.server.lock:
            self.server.errors.append({"unexpected": "GET", "path": self.path})
        self.send_error(404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with self.server.lock:
            number = len(self.server.records) + 1
            record = {"number": number, "at": eastern(), "path": self.path,
                      "encoding": self.headers.get("Content-Encoding"), "raw_sha256": hashlib.sha256(raw).hexdigest()}
            self.server.records.append(record)
        stem = self.server.run / f"model-{number:04}"
        stem.with_suffix(".body").write_bytes(raw)
        try:
            if self.path != "/v1/responses" or self.headers.get("Authorization"):
                raise RuntimeError("Unexpected endpoint or authorization header on credential-free fixture")
            payload = decode_body(raw, record["encoding"])
            save(stem.with_suffix(".json"), payload)
            item = self.server.answer(payload)
            item.update(id=f"fixture-item-{number}", status="completed")
            response = {"id": f"fixture-response-{number}", "status": "completed", "output": [item],
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
            events = [{"type": "response.created", "response": {"id": response["id"]}},
                      {"type": "response.output_item.done", "output_index": 0, "item": item},
                      {"type": "response.completed", "response": response}]
            save(stem.with_suffix(".response.json"), events)
            body = "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as error:
            with self.server.lock:
                self.server.errors.append({"number": number, "error": repr(error)})
            self.send_error(500)


def process_rows():
    result = subprocess.run(["/bin/ps" if sys.platform == "darwin" else "/usr/bin/ps",
                             "-axo", "pid=,ppid=,pgid=,stat="], capture_output=True, text=True, timeout=2, check=True)
    return [{"pid": int(pid), "ppid": int(ppid), "pgid": int(pgid), "state": state}
            for pid, ppid, pgid, state in (line.split() for line in result.stdout.splitlines()) if not state.startswith("Z")]


class Family:
    """Retain every captured birth, including children after their owner dies.

    Only freshly birth-verified processes are signaled. Unknown inspection and
    unverified processes in the original process group refuse cleanup success.
    Output EOF is checked separately by the PTY/stdio owners.
    """
    def __init__(self, process):
        from nativepeers.process import identity
        self.process = process
        self.birth = identity(process.pid).json()
        self.members = {(process.pid, self.birth["start"]): self.birth}
        self.last_capture = 0
        self.cleanup = None
        self.capture_errors, self.signals = [], []

    def capture(self, force=False):
        from nativepeers.process import descendants
        if force or time.monotonic() - self.last_capture >= .05:
            self.last_capture = time.monotonic()
            try:
                # Keep all retained live births as roots, not just the native owner.
                captured = descendants(list(self.members.values()))
                self.members.update({(r["pid"], r["start"]): r for r in captured})
            except Exception as error:
                self.capture_errors.append({"at": eastern(), "error": repr(error)})

    def freeze(self):
        from nativepeers.process import owned_status, signal_captured
        self.capture(force=True)
        if owned_status(self.birth) != "LIVE" or not signal_captured(self.birth, signal.SIGSTOP):
            raise RuntimeError("Could not freeze the exact owned receiver birth")
        self.signals.append({"owner": self.birth, "signal": "SIGSTOP", "sent": True})

    def stop(self, abrupt=False):
        from nativepeers.process import owned_status, signal_captured
        self.capture(force=True)
        for sig in ((signal.SIGKILL, signal.SIGKILL) if abrupt else (signal.SIGTERM, signal.SIGKILL)):
            for member in reversed(list(self.members.values())):
                if owned_status(member) == "LIVE":
                    sent = signal_captured(member, sig)
                    self.signals.append({"owner": member, "signal": sig.name, "sent": sent})
            deadline = time.monotonic() + STOP_WAIT
            while time.monotonic() < deadline:
                self.process.poll()
                # A dead leader never stops inspection/signaling of retained children.
                self.capture(force=True)
                if all(owned_status(r) == "DEAD" for r in self.members.values()):
                    break
                time.sleep(.05)
        states = [{"owner": r, "state": owned_status(r)} for r in self.members.values()]
        unknown = [r for r in states if r["state"] == "UNKNOWN"]
        unverified = []
        try:
            rows = process_rows()
            known_pids = {r["owner"]["pid"] for r in states if r["state"] == "LIVE"}
            unverified = [r for r in rows if r["pgid"] == self.birth["pgid"] and r["pid"] not in known_pids]
        except Exception as error:
            unknown.append({"scope": "former-process-group", "error": repr(error)})
        self.cleanup = {"members": states, "exit_code": self.process.poll(),
                        "unknown_liveness": unknown, "unverified_in_former_group": unverified,
                        "output_eof": None,
                        "capture_errors": self.capture_errors, "signals": self.signals, "abrupt": abrupt,
                        "confirmed_dead": self.process.poll() is not None and not unknown and not unverified
                        and not self.capture_errors and all(r["state"] == "DEAD" for r in states)}
        return self.cleanup


def abort_constructor(process, run, role, error):
    """Stop only this Popen child if native birth inspection failed.

    Its unreaped child PID is owned. No unverified process group is signaled;
    descendant and output cleanup stay explicitly uncertain and refuse a pass.
    """
    record = {"status": "FAIL", "error": repr(error), "pid": process.pid,
              "ownership": "child returned by this Popen; no native birth established",
              "descendant_cleanup": "UNVERIFIED", "output_eof": False, "signals": []}
    try:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if process.poll() is None:
                process.send_signal(sig)
                record["signals"].append(sig.name)
            try:
                record["exit_code"] = process.wait(timeout=STOP_WAIT)
                break
            except subprocess.TimeoutExpired:
                continue
        record["child_dead"] = process.poll() is not None
    except BaseException as cleanup_error:
        record["cleanup_error"] = repr(cleanup_error)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            stream.close()
    try:
        save(run / f"{role}.constructor-failure.json", record)
    except BaseException as save_error:
        error.add_note("Launch record could not be saved: " + repr(save_error))
    error.add_note("Owned constructor failure: " + json.dumps(record))


class Tui(Family):
    def __init__(self, command, cwd, env, run, role, emulator):
        master, slave = pty.openpty()
        process = None
        self.master, self.role, self.run, self.eof = master, role, run, False
        self.raw = self.timeline = None
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            self.raw = (run / f"{role}.ansi").open("wb", buffering=0)
            self.timeline = (run / f"{role}.frames.jsonl").open("w")
            self.screen = emulator.HistoryScreen(COLS, ROWS, history=10000)
            self.stream = emulator.Stream(self.screen)
            self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
            self.frames, self.offset, self.audit = [], 0, None
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                       start_new_session=True, close_fds=True)
            super().__init__(process)
            save(run / f"{role}.launch.json", {"command": command, "cwd": str(cwd), "owner": self.birth})
        except BaseException as error:
            if process is not None:
                abort_constructor(process, run, role, error)
            os.close(master)
            for stream in (self.raw, self.timeline):
                if stream is not None:
                    stream.close()
            raise
        finally:
            os.close(slave)

    def pump(self):
        self.capture()
        if self.eof:
            return
        if select.select([self.master], [], [], 0)[0]:
            try:
                data = os.read(self.master, 65536)
            except OSError as error:
                if error.errno != errno.EIO:
                    raise
                data = b""
            if not data:
                self.eof = True
                return
            self.raw.write(data)
            self.offset += len(data)
            self.stream.feed(self.decoder.decode(data))
            # Answer only terminal device queries, never an application prompt.
            if b"\x1b[6n" in data:
                os.write(self.master, f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R".encode())
            if b"\x1b[c" in data or b"\x1b[0c" in data:
                os.write(self.master, b"\x1b[?1;2c")
            frame = {"at": eastern(), "offset": self.offset, "lines": self.screen.display[:]}
            frame["chat_tail"] = ["".join(cell.data for _, cell in sorted(line.items()))
                                  for line in list(self.screen.history.top)[-80:]]
            self.frames.append(frame)
            self.timeline.write(json.dumps(frame, ensure_ascii=False) + "\n")
            self.timeline.flush()
            if self.audit:
                self.audit(frame)

    def text(self):
        return "\n".join(self.screen.display)

    def send(self, text):
        # Human input to this owned fixture only; no terminal injection into other sessions.
        os.write(self.master, b"\x1b[200~" + text.encode() + b"\x1b[201~")
        time.sleep(.2)
        os.write(self.master, b"\r")

    def stop(self, abrupt=False):
        cleanup = super().stop(abrupt=abrupt)
        terminal_errors = []
        deadline = time.monotonic() + 3
        while not self.eof and time.monotonic() < deadline:
            try:
                self.pump()
            except Exception as error:
                # Keep draining and retaining raw bytes after a failed audit;
                # the failure still refuses acceptance and never masks capture.
                terminal_errors.append(repr(error))
            time.sleep(.02)
        cleanup["pty_eof"] = self.eof
        cleanup["output_eof"] = self.eof
        cleanup["terminal_errors"] = terminal_errors
        os.close(self.master)
        self.raw.close()
        self.timeline.close()
        return cleanup


class Metadata(Family):
    """Native protocol client restricted to configuration inventory and queue reads."""
    ALLOWED = {"initialize", "hooks/list", "config/read", "config/batchWrite", "thread/queue/list"}

    def __init__(self, binary, cwd, env, run, role):
        self.log = self.errors = None
        process = None
        try:
            self.log = (run / f"{role}.rpc.jsonl").open("w")
            self.errors = (run / f"{role}.stderr").open("wb")
            command = [str(binary), "app-server", "--listen", "stdio://"]
            save(run / f"{role}.launch.json", {"command": command, "cwd": str(cwd), "scope": "metadata-only"})
            process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=self.errors, start_new_session=True, close_fds=True)
            self.items, self.eof, self.sequence = queue.Queue(), False, 0
            super().__init__(process)
            self.reader = threading.Thread(target=self.read, daemon=True)
            self.reader.start()
        except BaseException as error:
            if process is not None:
                abort_constructor(process, run, role, error)
            for stream in (self.log, self.errors):
                if stream is not None:
                    stream.close()
            raise

    def start(self):
        self.call("initialize", {"clientInfo": {"name": "quiet-close-tui-metadata", "version": "1"},
                                 "capabilities": {"experimentalApi": True}})
        self.write({"method": "initialized", "params": {}})

    def read(self):
        for raw in self.process.stdout:
            self.log.write(json.dumps({"received": raw.decode(errors="replace")}) + "\n")
            self.log.flush()
            try:
                self.items.put(json.loads(raw))
            except ValueError:
                self.items.put({"invalid_json": True})
        self.eof = True
        self.items.put({"eof": True})

    def write(self, value):
        self.log.write(json.dumps({"sent": value}) + "\n")
        self.log.flush()
        self.process.stdin.write((json.dumps(value) + "\n").encode())
        self.process.stdin.flush()

    def call(self, method, params):
        if method not in self.ALLOWED:
            raise ValueError("Metadata client cannot create/load/run native threads")
        self.sequence += 1
        self.write({"id": self.sequence, "method": method, "params": params})
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline:
            self.capture()
            try:
                item = self.items.get(timeout=.2)
            except queue.Empty:
                continue
            if item.get("eof") or item.get("invalid_json"):
                raise RuntimeError(f"Native metadata client failed: {item}")
            if item.get("id") == self.sequence:
                if "error" in item:
                    raise RuntimeError(f"{method}: {item['error']}")
                return item["result"]
        raise TimeoutError(method)

    def stop(self):
        # Keep the owner alive until Family captures its children. Closing stdin
        # first can orphan startup helpers before their births are recorded.
        result = super().stop()
        self.process.stdin.close()
        self.reader.join(3)
        result["stdout_eof"] = self.eof
        result["output_eof"] = self.eof
        self.log.close()
        self.errors.close()
        return result


def private_env(home, state, run, source):
    # Whitelist, never clone provider secrets, binding/bootstrap or real thread IDs.
    return {"HOME": str(Path.home()), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "USER": os.environ.get("USER", ""), "LANG": "en_US.UTF-8", "TERM": "xterm-256color",
            "CODEX_HOME": str(home), "NPEER_HOME": str(state), "PYTHONPATH": str(source),
            "TMPDIR": str(run / "tmp"), "NO_PROXY": "127.0.0.1,localhost"}


def select_codex(requested):
    selected = (Path.home() / ".local/share/codex-mcp-manager/current/bin/codex").resolve(strict=True)
    if requested is None:
        return selected, {"route": "normal managed current selection"}
    path = Path(requested).expanduser() if os.sep in requested else Path(shutil.which(requested) or requested)
    path = path.resolve(strict=True)
    if path == selected:
        return selected, {"route": "explicit managed current executable"}
    # The normal installed shell launcher selects this same immutable package.
    # Resolve only that known route, never execute arbitrary wrappers to guess.
    normal = Path.home() / ".local/bin/codex"
    if normal.exists() and path == normal.resolve() and path.name == "codex-launcher":
        text = path.read_text()
        state = Path.home() / ".local/share/codex-mcp-manager"
        if (state / "official-mode").exists() or 'exec "$state/current/bin/codex" "$@"' not in text:
            raise Blocked("Normal launcher does not select managed current; parent must settle selection")
        return selected, {"route": "normal installed launcher resolved to managed current", "launcher": str(path),
                          "launcher_sha256": digest(path)}
    raise Blocked("Candidate is not managed current; parent must select it under its normal guard. No bypass attempted.")


def prepare_run(raw, source):
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts or path.exists() or path.is_symlink():
        raise Blocked("--run must be a fresh absolute path without '..'")
    parent = path.parent.resolve(strict=True)
    if parent != path.parent:
        raise Blocked("--run parent must not traverse symlinks")
    path = parent / path.name
    if not path.is_relative_to(source):
        raise Blocked("--run must stay inside the selected project's ignored evidence directory")
    ignored = subprocess.run(["git", "-C", str(source), "check-ignore", "--quiet", str(path)], check=False)
    if ignored.returncode != 0:
        raise Blocked("--run must be git-ignored before the probe starts; no ignore settings are changed")
    path.mkdir(mode=0o700)
    return path


def product_cli(config, *arguments):
    result = subprocess.run([sys.executable, "-m", "nativepeers", *arguments, "--json"],
                            capture_output=True, text=True, timeout=WAIT)
    value = json.loads(result.stdout)
    if result.returncode or not value.get("ok"):
        raise RuntimeError({"arguments": arguments, "exit": result.returncode, "response": value,
                            "stderr": result.stderr})
    with (Path(config["run"]) / "native-cli.jsonl").open("a") as stream:
        stream.write(json.dumps({"at": eastern(), "arguments": arguments, "response": value}) + "\n")
    return value["data"]


def batch_specs(token, batch):
    if batch not in ("warm", "cold"):
        raise ValueError("Unknown fresh batch")
    return [{"kind": kind, "number": number,
             "request_id": f"qctui-{token}-{batch}-{kind}-{number}",
             "body": f"QCTUI_{batch.upper()}_{kind.upper()}_{number:02}_{token}",
             "expires_seconds": 5 if kind == "progress" and number in (0, 1, 15, 16) else 1800}
            for kind, count in (("progress", 17), ("completion", 5)) for number in range(count)]


def queue_ids(value):
    if not isinstance(value, dict) or not isinstance(value.get("data"), list) or value.get("nextCursor"):
        raise RuntimeError("Native queue inventory is incomplete or paginated")
    identifiers = [item.get("id") for item in value["data"] if isinstance(item, dict)]
    if len(identifiers) != len(value["data"]) or any(not isinstance(i, str) or not i for i in identifiers) or len(set(identifiers)) != len(identifiers):
        raise RuntimeError("Native queue inventory lacks unique submission IDs")
    return set(identifiers)


def original_queue_ids(rows):
    identifiers = [row["outcome"]["attempt"].get("external_submission_id") for row in rows]
    if any(not isinstance(i, str) or not i for i in identifiers) or len(set(identifiers)) != len(rows):
        raise RuntimeError("Original accepted batch lacks unique native queue IDs")
    return set(identifiers)


def cold_survivors(rows, frozen_ids, persisted_ids, events, now):
    if not frozen_ids or len(frozen_ids) >= len(rows) or not frozen_ids <= original_queue_ids(rows):
        raise RuntimeError("Cold cutover requires a proper nonempty subset of original native queue IDs")
    if persisted_ids != frozen_ids:
        raise RuntimeError("Original pending native queue IDs changed during receiver shutdown")
    old_suppressed = {event["subject_id"] for event in events}
    if not 3 <= len(old_suppressed) < len(rows):
        raise RuntimeError("Cold cutover requires a suppression prefix and unsuppressed originals")
    pending = [row for row in rows if row["outcome"]["attempt"]["external_submission_id"] in persisted_ids
               and row["outcome"]["message_id"] not in old_suppressed]
    if not any(row["kind"] == "completion" for row in pending) or not any(
            row["kind"] == "progress" and row["envelope"]["expires_at"] < now for row in pending):
        raise RuntimeError("Cold resume requires original unsuppressed expired and closed-completion items still persisted")
    return pending


def inventory_hooks(groups, listing, path, command):
    hooks = [h for entry in listing["data"] for h in entry["hooks"]
             if h.get("sourcePath") == str(path) and h.get("command") == command]
    normalized = lambda name: re.sub(r"[^a-z]", "", name.lower())
    events = [normalized(h.get("eventName", "")) for h in hooks]
    if len(hooks) != len(groups) or set(events) != {normalized(name) for name in groups}:
        raise Blocked("Native inventory did not expose every dynamically desired hook exactly once")
    return hooks


def native_tail(home, native_id, final, forbidden=()):
    """Read owned native history, locating its actual completed turn boundary."""
    for path in sorted((home / "sessions").rglob("*.jsonl")):
        raw = path.read_text()
        lines = raw.splitlines(keepends=True)
        if lines and not lines[-1].endswith("\n"):
            lines.pop()  # An in-progress append is not completion evidence.
        records = [json.loads(line) for line in lines]
        if not records or records[0].get("payload", {}).get("id") != native_id:
            continue
        if any(body in raw for body in forbidden):
            raise RuntimeError("Original suppressed body was persisted in native history")
        boundaries = [n for n, record in enumerate(records)
                      if record.get("type") == "event_msg" and record.get("payload", {}).get("type") == "task_complete"
                      and record["payload"].get("last_agent_message") == final]
        if boundaries:
            return {"path": str(path), "completed_record": boundaries[0],
                    "turn_id": records[boundaries[0]]["payload"].get("turn_id"),
                    "after": records[boundaries[0] + 1:]}
    return None


def require_native_silence(home, native_id, final, forbidden):
    tail = native_tail(home, native_id, final, forbidden)
    if tail is None:
        raise RuntimeError("Native history did not establish the identifiable final turn's completion")
    for record in tail["after"]:
        if record.get("type") == "compacted" or record.get("payload", {}).get("type") in (
                "task_started", "turn_started", "task_complete", "turn_complete", "context_compacted"):
            raise RuntimeError("Original queued input started native work or compaction after the completed final")
    return tail


async def helper_action(config, action, role):
    """Runs exclusively as a deterministic model-issued native shell tool."""
    allowed = {"identity", "open", "close", "status", "retained", "active", "push", "root",
               "queue-batch-warm", "queue-batch-cold", "hold-close-warm", "hold-close-cold"}
    if action not in allowed:
        raise ValueError("Unsupported fixture action")
    from nativepeers.caller import bind_caller
    from nativepeers.journal import Journal
    from nativepeers.registry import Registry
    from nativepeers.routing import Router
    from nativepeers.presentation import render
    from nativepeers.artifacts import Artifacts
    run = Path(config["run"])
    if Path(os.environ["NPEER_HOME"]).resolve() != run / "peer-state":
        raise RuntimeError("Helper refused nonfixture NPEER_HOME")
    if Path(os.environ["CODEX_HOME"]).resolve() != run / (role + "-home"):
        raise RuntimeError("Helper refused nonfixture CODEX_HOME")
    journal = Journal()
    try:
        registry = Registry(journal)
        caller = bind_caller(journal, registry)
        peer = registry.verify(caller.key, caller.incarnation)
        if peer["owner_kind"] != "interactive-standalone":
            raise RuntimeError("Helper requires native SessionStart-registered standalone owner")
        who = product_cli(config, "whoami")
        if action == "identity":
            save(run / f"{role}-identity.json", who)
            return who
        if action.startswith("hold-close-"):
            batch = action.removeprefix("hold-close-")
            opened = product_cli(config, "notifications", "open")
            if opened["state"] != "OPEN":
                raise RuntimeError("Receiver native shell did not open before the fresh batch")
            save(run / f"barrier-{batch}-entered.json", who)
            deadline = time.monotonic() + 120
            while not (run / f"barrier-{batch}-release").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned close barrier was not released")
                await asyncio.sleep(.05)
            first = product_cli(config, "notifications", "close")
            second = product_cli(config, "notifications", "close")
            if first != second or first["state"] != "QUIET":
                raise RuntimeError("Repeated close was not identical QUIET")
            save(run / f"close-{batch}-policy.json", first)
            return first
        if action in ("open", "close", "status"):
            return product_cli(config, "notifications", action)
        router = Router(journal, registry)
        receiver = json.loads((run / "receiver-identity.json").read_text())["key"]
        if action.startswith("queue-batch-") or action in ("active", "push", "root"):
            batch = action.removeprefix("queue-batch-") if action.startswith("queue-batch-") else None
            specs = batch_specs(config["token"], batch) if batch else [{
                "kind": action, "request_id": f"qctui-{config['token']}-{action}-0",
                "body": f"QCTUI_{action.upper()}_00_{config['token']}", "expires_seconds": 1800}]
            rows = []
            for spec in specs:
                kind, request, body = spec["kind"], spec["request_id"], spec["body"]
                if journal.one("SELECT 1 FROM send_requests WHERE caller_key=? AND client_request_id=?", (caller.key, request)):
                    raise RuntimeError("Fresh fixture batch refused an already used request ID")
                artifacts = []
                if kind in ("completion", "push"):
                    # Explicit fixture rows, never leased/controller callers or worker processes.
                    group = "fixture-completion-" + request
                    journal.db.execute("INSERT INTO completion_routes VALUES(?,?,?,?,?,?,?,?,?)",
                                       (group, 1, receiver, "push", "UNCLAIMED", None, None, request, 1))
                    artifacts = [Artifacts(journal).put(("FIXTURE RESULT " + body).encode(), caller.key, [receiver])]
                result = await router.send(caller, receiver, body, client_request_id=request,
                                           kind="completion" if kind in ("completion", "push") else None,
                                           expect_reply=action == "root", expires_seconds=spec["expires_seconds"],
                                           artifact_refs=artifacts)
                envelope = json.loads(journal.one("SELECT payload_json FROM messages WHERE message_id=?",
                                                  (result["message_id"],))["payload_json"])
                rows.append({"batch": batch, "request_id": request, "kind": kind, "body": body, "outcome": result, "envelope": envelope,
                             "frame": render(envelope), "artifacts": [Artifacts(journal).read(a, caller.key) for a in artifacts]})
                if result["state"] != "ACCEPTED_QUEUED":
                    save(run / f"{action}-partial.json", rows)
                    raise RuntimeError(f"Real queue admission was {result['state']}")
            save(run / f"{action}.json", rows)
            return {"accepted": len(rows), "message_ids": [r["outcome"]["message_id"] for r in rows]}
        if action == "retained":
            before = journal.all("SELECT * FROM receipts ORDER BY receipt_id")
            inbox = product_cli(config, "inbox", "--retained", "--limit", "100")
            rows = [row for batch in ("warm", "cold")
                    for row in json.loads((run / f"queue-batch-{batch}.json").read_text())]
            actual = {m["message_id"]: m for m in inbox}
            for row in rows:
                if actual.get(row["outcome"]["message_id"]) != row["envelope"]:
                    raise RuntimeError("Retained body differs from accepted envelope")
                for artifact in row["artifacts"]:
                    if Artifacts(journal).read(artifact["artifact_id"], caller.key) != artifact:
                        raise RuntimeError("Retained immutable fixture result changed")
            if before != journal.all("SELECT * FROM receipts ORDER BY receipt_id"):
                raise RuntimeError("Retained reads changed receipts")
            return {"bodies": len(rows), "results": sum(len(r["artifacts"]) for r in rows)}
        raise ValueError(action)
    finally:
        journal.close()


def helper_main(argv):
    config_path, action, role, done = argv
    config = json.loads(Path(config_path).read_text())
    source = Path(config["source"])
    sys.path.insert(0, str(source))
    try:
        result = asyncio.run(helper_action(config, action, role))
    except BaseException as error:
        save(done, {"ok": False, "error": repr(error), "traceback": traceback.format_exc()})
        raise
    save(done, {"ok": True, "result": result})
    print("QCTUI_HELPER_DONE " + action, flush=True)


def run_probe(args, report, run, resources):
    source = Path(args.nativepeers_root).resolve(strict=True)
    sys.path.insert(0, str(source))
    from nativepeers.install import Installer
    from nativepeers.journal import Journal
    from nativepeers.models import new_id
    binary, selection = select_codex(args.codex)
    report["executable"] = {"requested": args.codex, "path": str(binary), "sha256": digest(binary),
                            "selection": selection}
    try:
        import pyte
    except ImportError as error:
        raise Blocked("pyte is unavailable in this Python; rendered UI acceptance is not verified") from error
    report["source"] = {"root": str(source), "files": {str(p.relative_to(source)): digest(p)
                          for p in sorted((source / "nativepeers").rglob("*.py"))}}
    hook_entry = source / "bin/npeer-hook"
    hook_text = hook_entry.read_text()
    if "export PYTHONPATH=" + str(source) + "${PYTHONPATH" not in hook_text:
        raise Blocked("Selected tree's generated npeer-hook targets another source; parent must use its actual installed package")
    report["source"]["hook_entry_sha256"] = digest(hook_entry)
    report["source"]["python"] = {"path": sys.executable, "sha256": digest(sys.executable)}
    report["limitations"] = ["Completion routes/results are labeled fixtures; no worker/controller lifecycle is qualified.",
                             "External network is not instrumented; only the configured localhost model is counted."]
    report["actual_worker_controller_push"] = {"outcome": "not_run", "reason":
        "Separate parent-owned acceptance after exact runtime approval; this probe never creates workers or copies approvals."}
    token = new_id().replace("-", "")[:12]
    (run / "tmp").mkdir()
    state, cwd = run / "peer-state", run / "workspace"
    cwd.mkdir()
    model = Model(run)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    model.thread = thread
    thread.start()
    resources.append(("fixture", model))
    report["fixture"] = {"url": f"http://127.0.0.1:{model.server_port}/v1", "pid": os.getpid()}
    config = {"run": str(run), "source": str(source), "binary": str(binary), "token": token}
    config_path = run / "helper-config.json"
    save(config_path, config)
    environments = {}
    for role in ("receiver", "sender"):
        home = run / f"{role}-home"
        home.mkdir(mode=0o700)
        env = private_env(home, state, run, source)
        environments[role] = env
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
name = "Credential-free localhost acceptance fixture"
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
            groups = installer.desired_hooks("codex")
            expected = installer.command_for("codex")
        finally:
            journal.close()
        if "QueuedInput" not in groups or "UserPromptSubmit" not in groups:
            raise Blocked("Selected NativePeers source lacks the desired QueuedInput and UserPromptSubmit hooks")
        # Use the product's desired commands verbatim. Do not wrap observe/guard.
        (home / "hooks.json").write_text(json.dumps({"hooks": {k: [v] for k, v in groups.items()}}))
        rpc = Metadata(binary, cwd, env, run, role + "-trust")
        resources.append(("metadata", rpc))
        rpc.start()
        listing = rpc.call("hooks/list", {"cwds": [str(cwd)]})
        hooks = inventory_hooks(groups, listing, home / "hooks.json", expected)
        read = rpc.call("config/read", {"includeLayers": True, "cwd": str(cwd)})
        layer = next(l for l in read["layers"] if l["name"].get("type") == "user" and not l["name"].get("profile"))
        if Path(layer["name"]["file"]).resolve() != home / "config.toml":
            raise RuntimeError("Refusing native config write outside private CODEX_HOME")
        rpc.call("config/batchWrite", {"filePath": str(home / "config.toml"), "expectedVersion": layer["version"],
                                      "reloadUserConfig": True, "edits": [{"keyPath": "hooks.state", "value": {
                                          h["key"]: {"enabled": True, "trusted_hash": h["currentHash"]} for h in hooks},
                                          "mergeStrategy": "replace"}]})
        trusted = rpc.call("hooks/list", {"cwds": [str(cwd)]})
        actual = [h for entry in trusted["data"] for h in entry["hooks"] if h["key"] in {x["key"] for x in hooks}]
        if len(actual) != len(hooks) or not all(h["enabled"] and h["trustStatus"] == "trusted" for h in actual):
            raise RuntimeError("Native desired hook trust is not effective")
        report.setdefault("native_hooks", {})[role] = actual
        cleanup = rpc.stop()
        resources.remove(("metadata", rpc))
        report.setdefault("cleanup", []).append({"role": role + "-trust", **cleanup})
        if not cleanup["confirmed_dead"] or not cleanup["stdout_eof"]:
            raise RuntimeError("Hook-trust process cleanup lacks death/EOF proof")
    version = subprocess.run([str(binary), "--version"], cwd=cwd, env=environments["receiver"],
                             capture_output=True, text=True, timeout=20)
    report["executable"]["version"] = version.stdout.strip()
    if version.returncode:
        raise Blocked("Selected executable --version failed")
    tuis = []

    def pump_until(predicate, description, seconds=WAIT):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for tui in tuis:
                tui.pump()
                if tui.process.poll() is not None:
                    raise RuntimeError(f"Owned {tui.role} exited during {description}")
            if model.errors:
                raise RuntimeError(f"Local fixture errors: {model.errors}")
            if predicate():
                return
            time.sleep(.03)
        raise TimeoutError(description)

    def dwell(seconds):
        end = time.monotonic() + seconds
        pump_until(lambda: time.monotonic() >= end, "bounded delayed native drain", seconds + 5)

    serial = 0

    def plan(role, action):
        nonlocal serial
        serial += 1
        marker = f"QCTUI_COMMAND_{token}_{serial}_{role}_{action}"
        done = run / f"helper-{serial:02}-{action}.json"
        final = f"QCTUI_FINAL_{token}_{serial}_{action}"
        command = shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(config_path), action, role, str(done)])
        model.plan(marker, command, done, final)
        return marker, done, final

    def command(tui, action):
        marker, done, final = plan("receiver" if tui.role.startswith("receiver") else "sender", action)
        tui.send(marker)
        pump_until(lambda: done.exists() and final in tui.text()
                   and not ACTIVE.search("\n".join(tui.screen.display[-5:])), f"native shell {action} and rendered idle final")
        value = json.loads(done.read_text())
        if not value.get("ok"):
            raise RuntimeError(value)
        report.setdefault("commands", []).append({"role": tui.role, "action": action, "done": str(done), "final": final})
        return value["result"]

    for role in ("receiver", "sender"):
        marker, done, final = plan(role, "identity")
        tui = Tui([str(binary), "--no-daemon", "--name", f"Issue 7 private {role} {token}", marker],
                  cwd, environments[role], run, role, pyte)
        resources.append(("tui", tui))
        tuis.append(tui)
        pump_until(lambda: done.exists() and final in tui.text(), role + " normal registration/native shell")
        identity = json.loads((run / f"{role}-identity.json").read_text())
        if identity["owner"]["pid"] != tui.process.pid or identity["owner"]["start"] != tui.birth["start"]:
            raise RuntimeError("Native CLI caller is not the owned standalone TUI PID/birth")
        report.setdefault("identities", {})[role] = identity
    receiver, sender = tuis
    native_id = report["identities"]["receiver"]["native_session_id"]
    queue_reader = Metadata(binary, cwd, environments["receiver"], run, "receiver-queue-reader")
    resources.append(("metadata", queue_reader))
    queue_reader.start()
    all_rows = []

    def read_queue(label):
        # Inventory only: never load a thread or mutate/start/delete a queue item.
        value = queue_reader.call("thread/queue/list", {"threadId": native_id, "limit": 100})
        save(run / f"{label}-queue.json", value)
        return queue_ids(value)

    def queue_empty(label):
        identifiers = read_queue(label)
        if identifiers:
            raise RuntimeError(f"Original native queue items remain at {label}: {sorted(identifiers)}")

    def no_late_model():
        for record in model.records:
            path = run / f"model-{record['number']:04}.json"
            if not path.exists():
                raise RuntimeError("A counted model request has no decoded body")
            if any(row["body"] in input_text(json.loads(path.read_text())) for row in all_rows):
                raise RuntimeError("An original late frame leaked into an actual model request")

    def audit_body(frame):
        text = "\n".join(frame.get("chat_tail", []) + frame["lines"])
        if any(row["body"] in text for row in all_rows) or "QCTUI_UNPLANNED_MODEL_INPUT" in text:
            raise RuntimeError("Original late body or unsolicited answer rendered in native chat/status/footer")

    quiet_final = [None]

    def audit_quiet(frame):
        audit_body(frame)
        require_quiet_frame(frame, quiet_final[0])

    def suppressed(journal, rows):
        identifiers = [row["outcome"]["message_id"] for row in rows]
        placeholders = ",".join("?" for _ in identifiers)
        return journal.all("SELECT subject_id,payload_json,sequence FROM events WHERE kind='notification-suppressed' "
                           f"AND subject_id IN ({placeholders}) ORDER BY sequence", tuple(identifiers))

    def verify_originals(label, rows, complete=True):
        journal = Journal(state, read_only=True)
        try:
            from nativepeers.routing import Router
            from nativepeers.registry import Registry
            router = Router(journal, Registry(journal))
            outcomes = [router.outcome(row["outcome"]["message_id"]) for row in rows]
            for old, new in zip(rows, outcomes):
                if new["state"] != "ACCEPTED_QUEUED" or new["attempt"] != old["outcome"]["attempt"] or new["receipts"] != old["outcome"]["receipts"]:
                    raise RuntimeError("Original accepted transport state/receipts were rewritten")
                stored = journal.one("SELECT payload_json FROM messages WHERE message_id=?", (new["message_id"],))
                if json.loads(stored["payload_json"]) != old["envelope"]:
                    raise RuntimeError("Original accepted message body/result references changed")
                if {receipt["stage"] for receipt in new["receipts"]} & {"INGESTED", "MODEL_REPORTED_PROCESSED"}:
                    raise RuntimeError("False ingestion receipt on an original suppressed frame")
                if complete and not new.get("suppression"):
                    raise RuntimeError("No exact product guard suppression for an original frame")
            if complete:
                reasons = [outcome["suppression"]["reason"] for outcome in outcomes]
                if any(reasons[n] != "expired" for n in (0, 1, 15, 16)):
                    raise RuntimeError("The prefix and tail short-expiry frames were not expired at consumption")
                if any(reasons[n] != "completion-phase-closed" for n in range(17, 22)):
                    raise RuntimeError("All five original completion-route phases must be closed at consumption")
            save(run / f"{label}-outcomes.json", outcomes)
            return outcomes
        finally:
            journal.close()

    def begin_batch(batch):
        receiver.audit = audit_body  # The next intentional human turn may show work.
        marker, done, final = plan("receiver", "hold-close-" + batch)
        quiet_final[0] = final
        receiver.send(marker)
        pump_until(lambda: (run / f"barrier-{batch}-entered.json").exists(), batch + " receiver native shell barrier")
        command(sender, "queue-batch-" + batch)
        rows = json.loads((run / f"queue-batch-{batch}.json").read_text())
        if len(rows) != 22 or [r["kind"] for r in rows].count("progress") != 17 or [r["kind"] for r in rows].count("completion") != 5:
            raise RuntimeError("Fresh batch requires 17 progress + 5 completion admissions")
        if any(row["outcome"]["state"] != "ACCEPTED_QUEUED" for row in rows):
            raise RuntimeError("A fresh batch item was not actually accepted into the native queue")
        for field in ("request_id",):
            current = {row[field] for row in rows}
            if len(current) != 22 or current & {row[field] for row in all_rows}:
                raise RuntimeError("Warm and cold batches reused request IDs")
        messages = {row["outcome"]["message_id"] for row in rows}
        if len(messages) != 22 or messages & {row["outcome"]["message_id"] for row in all_rows}:
            raise RuntimeError("Warm and cold batches reused accepted message IDs")
        if original_queue_ids(rows) & {row["outcome"]["attempt"]["external_submission_id"] for row in all_rows}:
            raise RuntimeError("Warm and cold batches reused native queue IDs")
        all_rows.extend(rows)
        if read_queue(batch + "-accepted-before-close") != original_queue_ids(rows):
            raise RuntimeError("The complete original batch was not pending while the receiver held its native tool")
        expire = max(rows[n]["envelope"]["expires_at"] for n in (0, 1, 15, 16))
        pump_until(lambda: time.time() > expire + .1, batch + " short-expiry frames expire after acceptance", 15)
        report.setdefault("batches", {})[batch] = {"accepted_file": str(run / f"queue-batch-{batch}.json"),
                                                  "original_queue_ids": sorted(original_queue_ids(rows)), "final": final}
        completed = [False]
        def audit_completed_tail(frame):
            audit_body(frame)
            if not completed[0] and final in "\n".join(frame["lines"]):
                completed[0] = native_tail(run / "receiver-home", native_id, final) is not None
            if completed[0]:
                audit_quiet(frame)
        receiver.audit = audit_completed_tail
        (run / f"barrier-{batch}-release").touch()
        return rows, done, final

    warm_rows, warm_done, warm_final = begin_batch("warm")
    pump_until(lambda: warm_done.exists() and warm_final in receiver.text()
               and native_tail(run / "receiver-home", native_id, warm_final) is not None,
               "warm repeated close and rendered natively completed final")
    before = model.count()
    receiver.audit = audit_quiet
    audit_quiet({"lines": receiver.screen.display[:]})
    dwell(args.drain_seconds)
    if model.count() != before:
        raise RuntimeError("Warm batch produced model work after the quiet final")
    verify_originals("warm-complete", warm_rows)
    no_late_model()
    queue_empty("warm-complete")
    save(run / "warm-native-tail.json", require_native_silence(run / "receiver-home", native_id, warm_final,
                                                               [row["body"] for row in all_rows]))
    report["checks"] = ["warm: 22 original ACCEPTED_QUEUED frames (17 progress, 5 completion) fully drained",
                         "identical repeated native close and rendered idle final",
                         "expired prefix/tail and closed completion phases; original receipts/bodies unchanged"]
    receiver.audit = audit_body
    opened = command(receiver, "open")
    warm_policy = json.loads((run / "close-warm-policy.json").read_text())
    if opened["state"] != "OPEN" or opened["message_cutoff"] != warm_policy["message_cutoff"]:
        raise RuntimeError("Explicit reopen lost the warm cutoff")
    receiver.audit = audit_quiet
    before = model.count()
    dwell(args.drain_seconds)
    if model.count() != before:
        raise RuntimeError("Explicit reopen generated unsolicited model work")
    queue_empty("explicit-reopen")
    verify_originals("warm-after-reopen", warm_rows)

    cold_rows, cold_done, cold_final = begin_batch("cold")
    monitor = Journal(state, read_only=True)
    cutover = {}
    try:
        def freeze_after_prefix():
            events = suppressed(monitor, cold_rows)
            if len(events) < 3 or not cold_done.exists() or cold_final not in receiver.text() or native_tail(
                    run / "receiver-home", native_id, cold_final) is None:
                return False
            if ACTIVE.search("\n".join(receiver.screen.display[-5:])):
                raise RuntimeError("Cold queued-input suppression disturbed the completed work footer")
            receiver.freeze()
            cutover["events"] = events
            cutover["at"] = eastern()
            cutover["pty_offset"] = receiver.offset
            if len(events) >= len(cold_rows):
                raise RuntimeError("Missed the partial-drain window; no cold batch replacements may be submitted")
            return True

        pump_until(freeze_after_prefix, "cold rendered final and suppression prefix before owned freeze")
    finally:
        monitor.close()
    receiver.audit = audit_quiet
    frozen_ids = read_queue("cold-frozen-before-stop")
    expected_ids = original_queue_ids(cold_rows)
    suppressed_ids = {event["subject_id"] for event in cutover["events"]}
    pending_unsuppressed = [row for row in cold_rows
                            if row["outcome"]["attempt"]["external_submission_id"] in frozen_ids
                            and row["outcome"]["message_id"] not in suppressed_ids]
    if not frozen_ids or not frozen_ids <= expected_ids or len(frozen_ids) >= 22 or not pending_unsuppressed:
        raise RuntimeError("Cold cutover lacks a consumed prefix plus still-pending original unsuppressed native IDs")
    cutover["remaining_original_queue_ids"] = sorted(frozen_ids)
    cutover["remaining_unsuppressed_message_ids"] = [row["outcome"]["message_id"] for row in pending_unsuppressed]
    save(run / "cold-cutover.json", cutover)
    verify_originals("cold-before-stop", cold_rows, complete=False)
    no_late_model()
    save(run / "cold-native-tail-before-stop.json", require_native_silence(run / "receiver-home", native_id, cold_final,
                                                                          [row["body"] for row in all_rows]))
    cleanup = receiver.stop(abrupt=True)
    resources.remove(("tui", receiver))
    tuis.remove(receiver)
    report["cleanup"].append({"role": "receiver-cold-abrupt-shutdown", **cleanup})
    if not cleanup["confirmed_dead"] or not cleanup["output_eof"] or cleanup.get("terminal_errors"):
        raise RuntimeError("Cold receiver shutdown lacks authoritative birth/liveness/PTY EOF proof")
    persisted_ids = read_queue("cold-owner-dead-before-resume")
    # A hook child already running when SIGSTOP arrived may finish before it is
    # killed. Classify survivors only after every captured birth is dead.
    monitor = Journal(state, read_only=True)
    try:
        events_before_resume = suppressed(monitor, cold_rows)
    finally:
        monitor.close()
    pending_unsuppressed = cold_survivors(cold_rows, frozen_ids, persisted_ids, events_before_resume, time.time())
    cutover["events_before_resume"] = events_before_resume
    cutover["remaining_unsuppressed_message_ids"] = [row["outcome"]["message_id"] for row in pending_unsuppressed]
    save(run / "cold-cutover.json", cutover)
    report["cold_cutover"] = cutover
    snapshot = run / "cold-history-before-resume"
    snapshot.mkdir()
    history = []
    for number, path in enumerate(sorted((run / "receiver-home" / "sessions").rglob("*.jsonl"))):
        saved = snapshot / f"{number:04}.jsonl"
        shutil.copyfile(path, saved)
        history.append({"path": str(path), "snapshot": str(saved), "sha256": digest(saved)})
    if not history:
        raise RuntimeError("Cold shutdown left no native history to preserve")
    report["cold_native_history"] = history
    before = model.count()
    receiver = Tui([str(binary), "--no-daemon", "resume", native_id], cwd, environments["receiver"], run, "receiver-resume", pyte)
    tuis.append(receiver)
    resources.append(("tui", receiver))
    receiver.audit = audit_quiet
    monitor = Journal(state, read_only=True)
    try:
        pump_until(lambda: len(suppressed(monitor, cold_rows)) == 22, "original cold queue items suppressed on exact-UUID native resume")
    finally:
        monitor.close()
    dwell(args.drain_seconds)
    if model.count() != before:
        raise RuntimeError("Cold native resume or original persisted queue items started model work")
    if cold_final not in receiver.text():
        raise RuntimeError("Exact resume did not faithfully render the saved cold final chat tail")
    queue_empty("cold-resume-complete")
    verify_originals("cold-resume-complete", cold_rows)
    verify_originals("warm-after-cold-resume", warm_rows)
    no_late_model()
    save(run / "cold-native-tail-after-resume.json", require_native_silence(run / "receiver-home", native_id, cold_final,
                                                                           [row["body"] for row in all_rows]))
    for record in history:
        if not Path(record["path"]).read_bytes().startswith(Path(record["snapshot"]).read_bytes()):
            raise RuntimeError("Native history before resume was changed or removed")
    journal = Journal(state, read_only=True)
    try:
        row = journal.one("SELECT * FROM peer_instances WHERE native_id=? AND pid=? AND process_start=? AND status='live' AND owner_kind='interactive-standalone'",
                          (native_id, receiver.process.pid, receiver.birth["start"]))
        policy = journal.one("SELECT * FROM notification_policies WHERE peer_key=?", (report["identities"]["receiver"]["key"],))
        cold_policy = json.loads((run / "close-cold-policy.json").read_text())
        if row is None or row["incarnation_id"] == report["identities"]["receiver"]["incarnation_id"] or not policy or policy["state"] != "QUIET" or policy["message_cutoff"] != cold_policy["message_cutoff"]:
            raise RuntimeError("Cold queued input did not preserve QUIET/cutoffs on a normally registered resumed owner")
        events = {event["subject_id"]: json.loads(event["payload_json"]) for event in suppressed(journal, cold_rows)}
        for message_id in cutover["remaining_unsuppressed_message_ids"]:
            event = events[message_id]
            if event.get("observed_incarnation") != row["incarnation_id"] or event.get("owner") != [receiver.process.pid, receiver.birth["start"]]:
                raise RuntimeError("Persisted original frame was not suppressed by the resumed verified owner")
        report["resume_before_human"] = {"registration": row, "policy": policy}
    finally:
        journal.close()
    receiver.audit = audit_body
    # Fresh human input must execute after quiet. This also verifies the resumed
    # UUID through the real native shell and current SessionStart incarnation.
    command(receiver, "identity")
    resumed = json.loads((run / "receiver-identity.json").read_text())
    if resumed["native_session_id"] != native_id or resumed["owner"]["pid"] != receiver.process.pid or resumed["incarnation_id"] == report["identities"]["receiver"]["incarnation_id"]:
        raise RuntimeError("Same-ID native resume did not bind a fresh verified incarnation")
    report["resumed_identity"] = resumed
    retained = command(receiver, "retained")
    if retained != {"bodies": 44, "results": 10}:
        raise RuntimeError("Both fresh batches must retain 44 bodies and 10 immutable fixture results")
    command(receiver, "close")
    # A fresh root request is allowed while QUIET; it must visibly reach chat.
    active_marker = f"QCTUI_ROOT_00_{token}"
    root_done = run / "root-received.json"
    model.plan(active_marker, shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(config_path),
                                        "status", "receiver", str(root_done)]), root_done, "QCTUI_ROOT_RECEIVED")
    command(sender, "root")
    pump_until(lambda: root_done.exists() and "QCTUI_ROOT_RECEIVED" in receiver.text(), "fresh root request after quiet")
    if json.loads(root_done.read_text())["result"]["state"] != "OPEN":
        raise RuntimeError("Fresh root request did not reopen policy")
    active_done = run / "active-received.json"
    model.plan(f"QCTUI_ACTIVE_00_{token}", shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(config_path),
                                                    "status", "receiver", str(active_done)]), active_done, "QCTUI_ACTIVE_RECEIVED")
    command(sender, "active")
    pump_until(lambda: active_done.exists() and "QCTUI_ACTIVE_RECEIVED" in receiver.text(), "active progress delivery control")
    pump_until(lambda: not ACTIVE.search("\n".join(receiver.screen.display[-5:])), "active control settles")
    push_done = run / "push-received.json"
    model.plan(f"QCTUI_PUSH_00_{token}", shlex.join([sys.executable, str(Path(__file__).resolve()), "_helper", str(config_path),
                                                  "status", "receiver", str(push_done)]), push_done, "QCTUI_PUSH_RECEIVED")
    command(sender, "push")
    pump_until(lambda: push_done.exists() and "QCTUI_PUSH_RECEIVED" in receiver.text(), "active completion push control")
    report["checks"] += ["explicit reopen preserves cutoffs without submitting any old frame", "cold: original pending native IDs survive receiver death and drain silently on same-ID resume",
                           "human native shell input after quiet", "retained 44 original bodies and 10 immutable fixture results unchanged",
                           "allowed fresh root request after quiet", "active progress and fixture completion-frame native delivery controls"]
    if digest(binary) != report["executable"]["sha256"]:
        raise RuntimeError("Selected executable changed during qualification")
    if any(digest(source / name) != expected for name, expected in report["source"]["files"].items()):
        raise RuntimeError("Selected product source changed during qualification")
    if model.errors:
        raise RuntimeError(model.errors)
    no_late_model()
    report["model"] = {"localhost_responses": model.count(), "undecoded": 0, "errors": model.errors}
    report["native_history"] = [{"path": str(p), "sha256": digest(p)} for role in ("receiver", "sender")
                                 for p in sorted((run / f"{role}-home" / "sessions").rglob("*.jsonl"))]
    if not report["native_history"]:
        raise RuntimeError("Native history was not retained")


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--codex", help="Explicit selected executable or PATH launcher (default: managed current executable)")
    result.add_argument("--nativepeers-root", required=True, help="Selected source tree, never modified")
    result.add_argument("--run", required=True, help="Fresh absolute git-ignored evidence directory inside selected root")
    result.add_argument("--drain-seconds", type=float, default=8, help="Delay after each final/reopen/resume, at least 5 (default: 8)")
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_helper":
        helper_main(argv[1:])
        return 0
    args = parser().parse_args(argv)
    if not 5 <= args.drain_seconds <= 30:
        parser().error("--drain-seconds must be between 5 and 30")
    source = Path(args.nativepeers_root).expanduser().resolve(strict=True)
    run = prepare_run(args.run, source)
    report = {"started_at": eastern(), "status": "RUNNING", "command": [sys.executable, str(Path(__file__).resolve()), *argv],
              "cwd": str(Path.cwd()), "probe_sha256": digest(__file__), "run": str(run),
              "scope": "standalone native PTY; credential-free localhost Responses; real hook-observed callers"}
    resources = []
    code = 1
    try:
        run_probe(args, report, run, resources)
        report["status"], code = "PASS", 0
    except BaseException as error:
        report.update(status="BLOCKED" if isinstance(error, Blocked) else "FAIL", error=repr(error), traceback=traceback.format_exc())
        code = 2 if isinstance(error, Blocked) else 1
    finally:
        for kind, resource in reversed(resources):
            try:
                if kind == "fixture":
                    report["model_records"] = resource.records
                    report["model_errors"] = resource.errors
                    resource.shutdown()
                    resource.server_close()
                    resource.thread.join(3)
                    if resource.thread.is_alive() or resource.socket.fileno() != -1:
                        raise RuntimeError("Owned localhost fixture cleanup unconfirmed")
                    continue
                cleanup = resource.stop()
                report.setdefault("cleanup", []).append({"role": getattr(resource, "role", kind), **cleanup})
                if not cleanup["confirmed_dead"] or not cleanup.get("output_eof") or cleanup.get("terminal_errors"):
                    raise RuntimeError("Owned process/output cleanup unconfirmed")
            except BaseException as error:
                report.setdefault("cleanup_errors", []).append(repr(error))
                report["status"], code = "FAIL", 1
        report["finished_at"] = eastern()
        save(run / "qualification.json", report)
    print(json.dumps({"status": report["status"], "report": str(run / "qualification.json"), "error": report.get("error")}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
