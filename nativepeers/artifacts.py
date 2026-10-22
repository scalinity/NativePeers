"""Opaque, immutable artifacts with explicit readers and bounded pages."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat

from .errors import PeerError, require
from .models import ARTIFACT_MAX, RESULT_PAGE, new_id


def _selected_file_bytes(path, max_bytes):
    # Walk with directory descriptors so replacing any component with a symlink
    # cannot redirect the import after validation. Nonblocking opens reject FIFOs.
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode), 'PROTOCOL_ERROR', 'Selected artifact must be a regular file without symlinks')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode), 'PROTOCOL_ERROR', 'Selected artifact must be a regular file')
            require((info.st_dev, info.st_ino) == (before.st_dev, before.st_ino),
                    'PROTOCOL_ERROR', 'Selected artifact was replaced before import')
            require(before.st_size <= max_bytes, 'PAYLOAD_TOO_LARGE', 'Selected artifact exceeds the byte limit')
            raw = stream.read(max_bytes + 1)
            after = os.fstat(stream.fileno())
        require(len(raw) <= max_bytes, 'PAYLOAD_TOO_LARGE', 'Selected artifact exceeds the byte limit')
        require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns) and len(raw) == after.st_size,
                'PROTOCOL_ERROR', 'Selected artifact changed during import')
        return raw
    finally:
        os.close(directory)


class Artifacts:
    def __init__(self, journal):
        self.journal = journal

    def add_file(self, path, caller, *, max_bytes=ARTIFACT_MAX):
        from .registry import Registry
        from .worktrees import sensitive

        require(caller.kind == 'peer' and caller.incarnation, 'CALLER_UNBOUND',
                'Artifact import requires a verified native caller')
        peer = Registry(self.journal).verify(caller.key, caller.incarnation)
        require(type(max_bytes) is int and 0 < max_bytes <= ARTIFACT_MAX,
                'PROTOCOL_ERROR', 'Artifact byte limit must be between 1 and 10485760')
        path = Path(path).expanduser().absolute()
        require('..' not in path.parts, 'PROTOCOL_ERROR', 'Parent traversal is not allowed for artifact import')
        if caller.grants.get('worker_id') or peer['owner_kind'] == 'owned-worker-controller':
            require(path.is_relative_to(Path(peer['cwd'])), 'APPROVAL_BLOCKED',
                    'Workers can import artifacts only from their owned cwd')
        private_roots = [self.journal.root.resolve(), Path(peer['home'])]
        private_roots.extend(Path(row['home']).resolve() for row in self.journal.all(
            "SELECT DISTINCT json_extract(descriptor_json,'$.home') AS home FROM peer_instances"))
        private_roots.extend(Path(os.environ[name]).expanduser().resolve()
                             for name in ('CODEX_HOME', 'CLAUDE_CONFIG_DIR') if os.environ.get(name))
        private_names = {'.ssh', '.aws', '.azure', '.codex', '.claude', '.gemini', 'gcloud',
                         '.netrc', '.npmrc', '.pypirc', '.git-credentials', '.pgpass',
                         'credentials', 'credentials.json', 'token.json', 'tokens.json', 'transcripts',
                         'id_dsa', 'id_ecdsa', '.secrets', 'secrets.json', 'secrets.yaml', 'secrets.yml'}
        require(not sensitive(str(path).lower()) and not any(part.lower() in private_names for part in path.parts)
                and path.suffix.lower() not in ('.pem', '.key', '.p12', '.pfx', '.kdbx')
                and not (path.suffix.lower() == '.jsonl' and
                         (path.stem.lower() in ('transcript', 'transcripts') or path.name.lower().startswith('rollout-')))
                and not any(path.is_relative_to(root) for root in private_roots),
                'APPROVAL_BLOCKED', 'Credentials, private native state and transcripts cannot be imported')
        try:
            raw = _selected_file_bytes(path, max_bytes)
        except (OSError, ValueError) as error:
            raise PeerError('PROTOCOL_ERROR', 'Selected artifact could not be opened safely') from error
        handle = self.put(raw, caller.key, media_type='application/octet-stream')
        return dict(artifact_id=handle, sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw),
                    media_type='application/octet-stream')

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
        require(offset <= row["byte_size"], "PROTOCOL_ERROR", "Artifact offset exceeds stored size")
        require(row["relative_path"] == handle and handle.startswith("art_"), "PROTOCOL_ERROR", "Invalid stored artifact path")
        fd = os.open(self.journal.root/"artifacts"/handle, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            require(hashlib.file_digest(f, "sha256").hexdigest() == row["sha256"], "PROTOCOL_ERROR", "Artifact content changed")
            f.seek(offset); raw = f.read(limit)
        return dict(artifact_id=handle, sha256=row["sha256"], media_type=row["media_type"],
                    byte_size=row["byte_size"], offset=offset, bytes_read=len(raw),
                    data_base64=base64.b64encode(raw).decode(),
                    next_offset=offset+len(raw) if offset+len(raw)<row["byte_size"] else None)

    def export(self,handle,path,caller):
        """Write one granted immutable artifact to a new local file, atomically."""
        from .registry import Registry
        require(caller.kind=='peer' and caller.incarnation,'CALLER_UNBOUND','Artifact export requires a verified native caller')
        peer=Registry(self.journal).verify(caller.key,caller.incarnation)
        path=Path(path).expanduser().absolute()
        require('..' not in path.parts and path.name not in ('','.','..'),'PROTOCOL_ERROR','Invalid artifact output path')
        path=path.parent.resolve(strict=True)/path.name
        if caller.grants.get('worker_id') or peer['owner_kind']=='owned-worker-controller':
            require('workspace' in caller.grants.get('profiles',[]) and path.is_relative_to(Path(peer['cwd'])),
                    'APPROVAL_BLOCKED','Worker export requires its writing profile and owned cwd')
        def page(offset):
            try:return self.read(handle,caller.key,offset,RESULT_PAGE)
            except PeerError as error:
                if error.code!='APPROVAL_BLOCKED' or not caller.grants.get('worker_id'):raise
                return self.read(handle,caller.grants['worker_id'],offset,RESULT_PAGE)
        first=page(0)  # Check the reader grant before opening an output directory.
        directory=None;temporary='.npeer-export-'+new_id()
        try:
            directory=os.open(path.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for part in path.parts[1:-1]:
                following=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=following
            fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=directory)
            digest=hashlib.sha256();offset=0;current=first
            with os.fdopen(fd,'wb') as stream:
                while True:
                    require(current['offset']==offset and current['sha256']==first['sha256'] and current['byte_size']==first['byte_size'],
                            'PROTOCOL_ERROR','Artifact changed during export')
                    raw=base64.b64decode(current['data_base64'],validate=True)
                    require(len(raw)<=RESULT_PAGE and offset+len(raw)<=ARTIFACT_MAX,'PAYLOAD_TOO_LARGE','Artifact export exceeds its bounds')
                    stream.write(raw);digest.update(raw);offset+=len(raw)
                    if current['next_offset'] is None:break
                    require(raw and current['next_offset']==offset,'PROTOCOL_ERROR','Artifact page did not advance')
                    current=page(offset)
                require(offset==first['byte_size'] and digest.hexdigest()==first['sha256'],'PROTOCOL_ERROR','Exported artifact digest differs')
                stream.flush();os.fsync(stream.fileno())
            # A hard link publishes the complete file without replacing an existing target.
            os.link(temporary,path.name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
            metadata=self.journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='worker-delta-artifact' ORDER BY sequence DESC LIMIT 1",(handle,))
            result=dict(artifact_id=handle,path=str(path),sha256=first['sha256'],byte_size=offset)
            if metadata:result['worker_delta_base']=json.loads(metadata['payload_json'])['input_tree']
            return result
        finally:
            if directory is not None:
                try:os.unlink(temporary,dir_fd=directory)
                except FileNotFoundError:pass
                os.close(directory)
