"""Bind operations to hook-observed owners or an explicitly scoped lease."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import stat
import time

from .errors import require
from .process import ancestry, matches


@dataclass(frozen=True)
class Caller:
    kind: str
    key: str
    incarnation: str | None = None
    grants: dict = field(default_factory=dict)

    def actor(self):
        if self.kind == "peer":
            return dict(kind="peer", peer_key=self.key, incarnation_id=self.incarnation)
        if self.kind == "controller":
            return dict(kind="controller", group_id=self.grants["group_id"], generation=self.grants["generation"])
        return dict(kind="external-local", source_label="unbound local process")


def read_binding(journal, binding: Path):
    require(binding.parent.resolve() == (journal.root/"bindings").resolve(), "CALLER_UNBOUND", "Binding is outside the private binding directory")
    fd = os.open(binding, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077,
                "CALLER_UNBOUND", "Unsafe binding file")
        raw = os.read(fd, 65537)
        require(len(raw) <= 65536, "CALLER_UNBOUND", "Oversize binding")
        value = json.loads(raw)
    finally:
        os.close(fd)
    require(value.get("state") == "BOUND", "CALLER_NOT_READY", "Native identity is not bound yet")
    require(value.get("expires_at", 0) > time.time() and matches(value["owner"]),
            "CALLER_UNBOUND", "Binding lease or owner expired")
    return value


def native_ancestor(owner, ancestors):
    return any(all(owner[k]==candidate[k] for k in ('pid','pgid','uid','start','executable')) for candidate in ancestors)


def bind_caller(journal, registry, *, fixed_binding=None, allow_external=True):
    path = fixed_binding or os.getenv("NPEER_BINDING") or os.getenv("NPEER_BOOTSTRAP")
    if path:
        value = read_binding(journal, Path(path))
        if value["kind"] == "controller":
            require(fixed_binding is not None, "CALLER_UNBOUND", "Controller actor requires its fixed lease")
            return Caller("controller", "controller:"+value["group_id"]+":"+str(value["generation"]), grants=value)
        peer = registry.verify(value["peer_key"], value["incarnation_id"])
        if fixed_binding is None:
            ancestors=[p.json() for p in ancestry()]
            require(native_ancestor(peer["owner"], ancestors), "CALLER_UNBOUND", "Native owner is outside caller ancestry")
            if peer['owner_kind']=='interactive-standalone':
                binaries={(Path.home()/name).resolve() for name in (
                    '.local/bin/agy','.local/bin/claude','.local/share/codex-mcp-manager/current/bin/codex')}
                binaries.add(Path(peer['owner']['executable']))
                nearest=next((p for p in ancestors if Path(p['executable']) in binaries),None)
                require(nearest is not None and native_ancestor(peer['owner'],[nearest]),
                        'CALLER_UNBOUND','Interactive binding belongs to an enclosing native session')
        grants=value.get('grants',{})
        if peer['owner_kind']=='interactive-standalone':
            grants=dict(grants,profiles=peer.get('policy',{}).get('profiles',['analysis']))
        return Caller("peer", peer["handle"], peer["incarnation_id"], grants)
    native = os.getenv("CODEX_THREAD_ID")
    ancestors = [p.json() for p in ancestry()]
    # A nested CLI cannot borrow an enclosing conversation's verified identity.
    binaries={(Path.home()/name).resolve():provider for provider,name in (
        ('antigravity','.local/bin/agy'),('codex','.local/share/codex-mcp-manager/current/bin/codex'),
        ('claude','.local/bin/claude'))}
    nearest=next((p for p in ancestors if Path(p['executable']) in binaries),None)
    if nearest and binaries[Path(nearest['executable'])]=='antigravity':
        candidates=registry.for_owner('antigravity',nearest)
        require(len(candidates)==1,'CALLER_UNBOUND','Antigravity owner has no unique hook-observed native session')
        p=candidates[0]
        return Caller('peer',p['handle'],p['incarnation_id'],p.get('policy',{}))
    if nearest and binaries[Path(nearest['executable'])]=='claude':
        require(False,'CALLER_UNBOUND','Claude tool has no hook-provided binding')
    if native:
        candidates = [p for p in registry.for_native('codex',native) if native_ancestor(p['owner'],ancestors)
                      and (nearest is None or native_ancestor(p['owner'],[nearest]))]
        require(len(candidates) == 1, "CALLER_UNBOUND", "Tool thread has no unique hook-observed native owner")
        p = candidates[0]
        return Caller("peer", p["handle"], p["incarnation_id"], p.get("policy", {}))
    require(allow_external, "CALLER_UNBOUND", "This operation requires a verified native caller")
    return Caller("external-local", "external-local")
