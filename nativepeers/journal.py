"""NativePeers' own SQLite journal. Never opens a provider database for writing."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import stat
import time

from .files import advisory_lock, atomic_json, canonical, private_directory
from .models import new_id

POLICY_TABLE = """notification_policies (peer_key TEXT PRIMARY KEY,state TEXT NOT NULL,message_cutoff INTEGER NOT NULL,
 route_cutoff INTEGER NOT NULL,closed_at REAL,opened_at REAL,incarnation_id TEXT NOT NULL)"""
POLICY_COLUMNS = (('peer_key','TEXT',0,1),('state','TEXT',1,0),('message_cutoff','INTEGER',1,0),('route_cutoff','INTEGER',1,0),
                  ('closed_at','REAL',0,0),('opened_at','REAL',0,0),('incarnation_id','TEXT',1,0))
# Known obsolete shape observed in a live version-1 journal: composite (peer_key, incarnation_id) key.
# Compared with whitespace runs collapsed; repaired only while empty. Anything else is refused.
LEGACY_POLICY_SQL = ("CREATE TABLE notification_policies (peer_key TEXT NOT NULL,incarnation_id TEXT NOT NULL,state TEXT NOT NULL, "
                     "message_cutoff INTEGER NOT NULL,route_cutoff INTEGER NOT NULL,closed_at REAL,opened_at REAL,PRIMARY KEY(peer_key,incarnation_id))")
LEGACY_POLICY_COLUMNS = (('peer_key','TEXT',1,1),('incarnation_id','TEXT',1,2),('state','TEXT',1,0),('message_cutoff','INTEGER',1,0),
                         ('route_cutoff','INTEGER',1,0),('closed_at','REAL',0,0),('opened_at','REAL',0,0))


def policy_shape(db):
    """(kind, sql) for the notification policy table; kind is 'absent', 'canonical', 'legacy' or 'unknown'."""
    found = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='notification_policies'").fetchone()
    if not found:
        return 'absent', None
    columns = tuple((c[1], c[2].upper(), c[3], c[5]) for c in db.execute('PRAGMA table_info(notification_policies)'))
    extras = db.execute("SELECT count(*) FROM sqlite_master WHERE tbl_name='notification_policies' AND type!='table' "
                        "AND NOT (type='index' AND name LIKE 'sqlite_autoindex_%')").fetchone()[0]
    if columns == POLICY_COLUMNS and not extras:
        return 'canonical', found[0]
    if columns == LEGACY_POLICY_COLUMNS and ' '.join(found[0].split()) == LEGACY_POLICY_SQL and not extras:
        return 'legacy', found[0]
    return 'unknown', found[0]


def policy_mismatch(shape):
    from .errors import PeerError
    return PeerError('CAPABILITY_MISMATCH', f'Unsupported notification policy table ({shape}); no migration applied')


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
CREATE TABLE IF NOT EXISTS task_attempts (task_id TEXT NOT NULL REFERENCES tasks(task_id),attempt_index INTEGER NOT NULL,
 worker_id TEXT NOT NULL REFERENCES workers(worker_id),state TEXT NOT NULL,result_artifact_id TEXT NOT NULL,
 PRIMARY KEY(task_id,attempt_index));
CREATE TABLE IF NOT EXISTS group_provider_limits (group_id TEXT NOT NULL REFERENCES groups(group_id),provider TEXT NOT NULL,
 provider_home TEXT NOT NULL,error_json TEXT NOT NULL,PRIMARY KEY(group_id,provider,provider_home));
CREATE TABLE IF NOT EXISTS resume_requests (caller_key TEXT NOT NULL,client_request_id TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(task_id),response_json TEXT NOT NULL,
 PRIMARY KEY(caller_key,client_request_id));
CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,subject_id TEXT NOT NULL,
 kind TEXT NOT NULL,payload_json TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts (artifact_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,byte_size INTEGER NOT NULL,
 media_type TEXT NOT NULL,relative_path TEXT NOT NULL,owner_key TEXT NOT NULL,allowed_readers_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leases (lease_id TEXT PRIMARY KEY,owner_key TEXT NOT NULL,scope_json TEXT NOT NULL,
 expires_at REAL NOT NULL,revoked_at REAL);
CREATE TABLE IF NOT EXISTS usage_observations (observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
 provider TEXT NOT NULL,home TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,
 observed_at REAL NOT NULL,payload_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS usage_scope ON usage_observations(provider,home,scope,observation_id);
CREATE TABLE IF NOT EXISTS task_usage (task_id TEXT NOT NULL,attempt_index INTEGER NOT NULL,
 worker_id TEXT NOT NULL,provider TEXT NOT NULL,home TEXT NOT NULL,model TEXT NOT NULL,
 effort TEXT,started_at REAL NOT NULL,completed_at REAL NOT NULL,payload_json TEXT NOT NULL,
 PRIMARY KEY(task_id,attempt_index));
CREATE TABLE IF NOT EXISTS activity_index (worker_id TEXT NOT NULL,generation INTEGER NOT NULL,task_id TEXT NOT NULL,
 first_seq INTEGER NOT NULL,last_seq INTEGER NOT NULL,terminal_seq INTEGER,PRIMARY KEY(worker_id,generation,task_id));
CREATE TABLE IF NOT EXISTS @POLICY@;
CREATE INDEX IF NOT EXISTS messages_exchange ON messages(exchange_id);
PRAGMA user_version=1;
""".replace("@POLICY@",POLICY_TABLE)


class Journal:
    @classmethod
    def existing(cls, root, *, timeout=.05):
        """Short telemetry writes to an already installed schema; never initialize at startup."""
        root=Path(root).resolve();database=root/'journal.sqlite3'
        if not database.is_file() or database.is_symlink():raise PermissionError('Usage journal is not installed')
        self=cls.__new__(cls);self.root=root
        self.db=sqlite3.connect(database.as_uri()+'?mode=rw',uri=True,timeout=timeout,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA busy_timeout='+str(int(timeout*1000)))
        self.host_id=json.loads((root/'host.json').read_text())['host_id']
        return self

    def __init__(self, root: Path | None = None, *, read_only=False):
        if read_only:
            self.root = Path(root or os.getenv("NPEER_HOME", Path.home()/".local/state/nativepeers")).absolute()
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError(f"Expected an owned private directory: {self.root}")
            database = self.root/"journal.sqlite3"
            if not database.is_file() or database.is_symlink():
                raise PermissionError("NativePeers journal is not installed or is symlinked")
            # Keep SQLite's live WAL locking: immutable=1 can miss current reader grants.
            # Live readers need readable WAL/SHM sidecars. Never fall back to an
            # immutable snapshot when a read-only filesystem prevents their creation.
            self.db = None
            try:
                self.db = sqlite3.connect(database.as_uri()+"?mode=ro", uri=True, timeout=5, isolation_level=None)
                self.db.row_factory = sqlite3.Row
                if self.db.execute("PRAGMA user_version").fetchone()[0] != 1:
                    from .errors import PeerError
                    raise PeerError('CAPABILITY_MISMATCH','Unsupported read-only journal schema; no migration applied')
                shape = policy_shape(self.db)[0]
                if shape not in ('absent', 'canonical'):
                    raise policy_mismatch(shape)
                self.host_id = json.loads((self.root/"host.json").read_text())["host_id"]
            except sqlite3.Error as error:
                if self.db:self.db.close()
                from .errors import PeerError
                raise PeerError('CAPABILITY_MISMATCH','Read-only journal is unavailable; readable live WAL/SHM files may be required',
                                details={'cause':str(error)[:256]}) from None
            except BaseException:
                if self.db:self.db.close()
                raise
            return
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
            try:
                self._repair_empty_legacy_policy()
                self.db.executescript(SCHEMA)
            except BaseException:
                self.db.close()
                raise
        host = self.root/"host.json"
        with advisory_lock(self.root/"locks/host.lock"):
            if not host.exists():
                atomic_json(host, {"host_id": new_id()})
            self.host_id = json.loads(host.read_text())["host_id"]

    def _repair_empty_legacy_policy(self):
        """Under the schema lock: recreate only the known obsolete policy table, and only while it holds no rows."""
        if policy_shape(self.db)[0] in ('absent', 'canonical'):
            return
        self.db.execute("BEGIN IMMEDIATE")
        try:
            shape = policy_shape(self.db)[0]
            if shape == 'legacy' and not self.db.execute("SELECT EXISTS(SELECT 1 FROM notification_policies)").fetchone()[0]:
                self.db.execute("DROP TABLE notification_policies")
                self.db.execute("CREATE TABLE " + POLICY_TABLE)
            elif shape not in ('absent', 'canonical'):
                raise policy_mismatch(shape if shape != 'legacy' else 'obsolete with existing rows')
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

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
