"""Versioned read-only native names; native history is never used as liveness."""
import json
from pathlib import Path
import subprocess


def codex_name(home: Path, native_id):
    # Source: installed release's rollout/src/session_index.rs. Latest exact ID wins.
    path = home/"session_index.jsonl"
    try:
        with path.open("rb") as f:
            f.seek(0, 2); end = f.tell(); start = max(0, end-4*1024*1024)
            f.seek(start)
            if start: f.readline()
            lines = f.read().splitlines()
        for line in reversed(lines):
            value = json.loads(line)
            if value.get("id") == native_id:
                return value.get("thread_name")
    except (FileNotFoundError, ValueError, OSError):
        pass
    return None


def claude_sessions(home: Path,*,explicit=None):
    import os
    from ..launch import apply_home
    env = apply_home('claude',home,os.environ,explicit=explicit)
    process = subprocess.run([str(Path.home()/".local/bin/claude"), "agents", "--json"],
                             env=env, capture_output=True, text=True, timeout=3)
    return json.loads(process.stdout) if process.returncode == 0 else []


def claude_name(home, native_id, pid):
    for session in claude_sessions(home):
        if session.get("sessionId") == native_id and session.get("pid") == pid:
            return session.get("name")
    return None
