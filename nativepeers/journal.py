"""NativePeers' own SQLite journal. Never opens a provider database for writing."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

from .files import advisory_lock, atomic_json, canonical, private_directory
from .models import new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS peer_instances (
 peer_key TEXT NOT NULL, incarnation_id TEXT PRIMARY KEY, native_id TEXT NOT NULL,
 provider TEXT NOT NULL, home_id TEXT NOT NULL, pid INTEGER NOT NULL, process_start TEXT NOT NULL,
 title TEXT, owner_kind TEXT NOT NULL, descriptor_json TEXT NOT NULL, status TEXT NOT NULL, last_seen REAL NOT NULL);
CREATE INDEX IF NOT EXISTS peers_alias ON peer_instances(title,provider,status);
CREATE INDEX IF NOT EXISTS peers_native ON peer_instances(native_id,home_id,status);
CREATE INDEX IF NOT EXISTS peers_owner ON peer_instances(provider,pid,process_start,status);
CREATE TABLE IF NOT EXISTS send_requests (caller_key TEXT,client_request_id TEXT,payload_sha256 TEXT NOT NULL,
 message_id TEXT NOT NULL,PRIMARY KEY(caller_key,client_request_id));
CREATE TABLE IF NOT EXISTS messages (message_id TEXT PRIMARY KEY,exchange_id TEXT,from_actor_key TEXT NOT NULL,
 to_key TEXT NOT NULL,in_reply_to TEXT,task_id TEXT,payload_json TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
 expires_at REAL NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS attempts (attempt_id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(message_id),
 target_incarnation TEXT NOT NULL,transport TEXT NOT NULL,external_request_id TEXT,external_submission_id TEXT,
 state TEXT NOT NULL,started_at REAL,ended_at REAL,error_json TEXT);
CREATE INDEX IF NOT EXISTS attempts_message ON attempts(message_id);
CREATE TABLE IF NOT EXISTS receipts (receipt_id TEXT PRIMARY KEY,message_id TEXT NOT NULL REFERENCES messages(message_id),
 attempt_id TEXT,stage TEXT NOT NULL,evidence_kind TEXT NOT NULL,source_native_id TEXT,source_turn_id TEXT,details_json TEXT,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS spawn_requests (caller_key TEXT,client_request_id TEXT,spec_sha256 TEXT NOT NULL,
 group_id TEXT NOT NULL,PRIMARY KEY(caller_key,client_request_id));
CREATE TABLE IF NOT EXISTS groups (group_id TEXT PRIMARY KEY,parent_key TEXT NOT NULL,policy_json TEXT NOT NULL,
 state TEXT NOT NULL,owner_manifest TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS completion_routes (group_id TEXT NOT NULL,completion_generation INTEGER NOT NULL,
 parent_key TEXT NOT NULL,mode TEXT NOT NULL,state TEXT NOT NULL,waiter_id TEXT,lease_deadline REAL,
 delivery_id TEXT,claimed_generation INTEGER,PRIMARY KEY(group_id,completion_generation,parent_key));
CREATE TABLE IF NOT EXISTS workers (worker_id TEXT PRIMARY KEY,group_id TEXT REFERENCES groups(group_id),peer_key TEXT,
 provider TEXT NOT NULL,native_id TEXT,role_title TEXT NOT NULL,model_requested TEXT,model_observed TEXT,
 effort_requested TEXT,cwd TEXT NOT NULL,worktree_id TEXT,state TEXT NOT NULL,generation INTEGER NOT NULL,lease_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY,worker_id TEXT NOT NULL REFERENCES workers(worker_id),
 request_message_id TEXT,native_turn_id TEXT,state TEXT NOT NULL,result_artifact_id TEXT,terminal_reason TEXT,
 generation INTEGER NOT NULL,task_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resume_requests (caller_key TEXT NOT NULL,client_request_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),response_json TEXT NOT NULL,
 PRIMARY KEY(caller_key,client_request_id));
CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,subject_id TEXT NOT NULL,
 kind TEXT NOT NULL,payload_json TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,byte_size INTEGER NOT NULL,
 media_type TEXT NOT NULL,relative_path TEXT NOT NULL,owner_key TEXT NOT NULL,allowed_readers_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leases (lease_id TEXT PRIMARY KEY,owner_key TEXT NOT NULL,scope_json TEXT NOT NULL,
 expires_at REAL NOT NULL,revoked_at REAL);
PRAGMA user_version=1;
"""


class Journal:
    def __init__(self, root: Path | None = None):
        self.root = private_directory(root or Path(os.getenv("NPEER_HOME", Path.home()/".local/state/nativepeers")))
        for name in ("peers", "bindings", "workers", "artifacts", "locks", "groups", "compat"):
            private_directory(self.root/name)
        with advisory_lock(self.root/"locks/schema.lock"):
            database = self.root/"journal.sqlite3"
            if database.is_symlink():
                raise PermissionError("Symlinked NativePeers journal")
            self.db = sqlite3.connect(database, timeout=5, isolation_level=None)
            database.chmod(0o600)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA busy_timeout=5000")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("CAPABILITY_MISMATCH: unsupported journal schema; no migration applied")
            self.db.executescript(SCHEMA)
        host = self.root/"host.json"
        with advisory_lock(self.root/"locks/host.lock"):
            if not host.exists():
                atomic_json(host, {"host_id": new_id()})
            self.host_id = json.loads(host.read_text())["host_id"]

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def one(self, sql, params=()):
        row = self.db.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql, params=()):
        return [dict(r) for r in self.db.execute(sql, params)]

    def event(self, subject, kind, payload):
        self.db.execute("INSERT INTO events(subject_id,kind,payload_json,at) VALUES(?,?,?,?)",
                        (subject, kind, canonical(payload).decode(), time.time()))

    def close(self):
        self.db.close()
