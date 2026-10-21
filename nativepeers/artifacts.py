"""Opaque, immutable artifacts with explicit readers and bounded pages."""
import base64
import hashlib
import json
import os

from .errors import require
from .models import ARTIFACT_MAX, RESULT_PAGE, new_id


class Artifacts:
    def __init__(self, journal):
        self.journal = journal

    def put(self, raw: bytes, owner, readers=(), media_type="text/plain"):
        require(len(raw) <= ARTIFACT_MAX, "PAYLOAD_TOO_LARGE", "Artifact exceeds 10 MiB")
        handle = "art_" + new_id()
        path = self.journal.root/"artifacts"/handle
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
        self.journal.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?)", (
            handle, hashlib.sha256(raw).hexdigest(), len(raw), media_type, handle, owner,
            json.dumps(list(set(readers)))))
        return handle

    def grant(self, handle, caller, readers):
        # The caller must already own or be allowed to read the immutable object.
        self.read(handle,caller,0,1)
        row=self.journal.one('SELECT allowed_readers_json FROM artifacts WHERE artifact_id=?',(handle,))
        allowed=set(json.loads(row['allowed_readers_json']));allowed.update(readers)
        self.journal.db.execute('UPDATE artifacts SET allowed_readers_json=? WHERE artifact_id=?',(json.dumps(sorted(allowed)),handle))

    def read(self, handle, caller, offset=0, limit=RESULT_PAGE):
        require(isinstance(offset, int) and offset >= 0 and isinstance(limit, int) and 0 < limit <= RESULT_PAGE,
                "PROTOCOL_ERROR", "Invalid artifact page")
        row = self.journal.one("SELECT * FROM artifacts WHERE artifact_id=?", (handle,))
        require(row is not None, "PROTOCOL_ERROR", "Unknown artifact handle")
        require(caller == row["owner_key"] or caller in json.loads(row["allowed_readers_json"]),
                "APPROVAL_BLOCKED", "Artifact is outside caller's reader grant")
        require(row["relative_path"] == handle and handle.startswith("art_"), "PROTOCOL_ERROR", "Invalid stored artifact path")
        fd = os.open(self.journal.root/"artifacts"/handle, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            require(hashlib.file_digest(f, "sha256").hexdigest() == row["sha256"], "PROTOCOL_ERROR", "Artifact content changed")
            f.seek(offset); raw = f.read(limit)
        return dict(artifact_id=handle, sha256=row["sha256"], media_type=row["media_type"],
                    byte_size=row["byte_size"], offset=offset, bytes_read=len(raw),
                    data_base64=base64.b64encode(raw).decode(),
                    next_offset=offset+len(raw) if offset+len(raw)<row["byte_size"] else None)
