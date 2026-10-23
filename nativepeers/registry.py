"""Exact aliases over verified live incarnations, with separate logical owners."""
from __future__ import annotations

import json
from pathlib import Path
import time

from .errors import require, PeerError
from .files import canonical
from .models import PeerKey, home_id, new_id, title
from .process import matches


class Registry:
    def __init__(self, journal):
        self.journal = journal

    def register(self, provider, home: Path, native_id, native_title, owner, cwd,
                 transport, *, owner_kind="interactive-standalone", policy=None,
                 incarnation=None, capabilities=None):
        require(matches(owner), "TARGET_GONE", "Native owner is no longer alive")
        key = PeerKey(self.journal.host_id, provider, home_id(home), native_id)
        incarnation = incarnation or new_id()
        from .policy import home_label
        label=home_label(self.journal,home)
        descriptor = dict(schema_version=1, peer_key=key.json(), handle=key.handle,
                          incarnation_id=incarnation, title=title(native_title) if native_title else None,
                          title_source=("nativepeers-worker-role" if provider=='antigravity' and owner_kind=='owned-worker-controller' else "native-explicit") if native_title else "unknown", owner=owner,
                          owner_kind=owner_kind, cwd=str(Path(cwd).resolve()), transport=transport,
                          lifecycle="unknown", policy=policy or {}, capabilities=capabilities or {},
                          home=str(home.resolve()), home_label=label, last_evidence_at=time.time())
        with self.journal.transaction() as db:
            db.execute("UPDATE peer_instances SET status='replaced' WHERE (peer_key=? OR (pid=? AND process_start=?)) AND status='live'",
                       (key.handle, owner["pid"], owner["start"]))
            db.execute("INSERT INTO peer_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (key.handle, incarnation, native_id, provider, key.home_id, owner["pid"], owner["start"],
                        descriptor["title"], owner_kind, canonical(descriptor).decode(), "live", time.time()))
        return descriptor

    def get(self, handle, *, live=True):
        row = self.journal.one("SELECT * FROM peer_instances WHERE peer_key=? ORDER BY last_seen DESC LIMIT 1", (handle,))
        require(row is not None, "TARGET_OFFLINE", "Peer is not registered")
        descriptor = json.loads(row["descriptor_json"])
        if live:
            require(row["status"] == "live" and matches(descriptor["owner"]), "TARGET_OFFLINE", "Registered native owner is offline")
        return descriptor

    def for_owner(self,provider,owner,native_id=None):
        rows=self.journal.all('SELECT descriptor_json FROM peer_instances WHERE provider=? AND pid=? AND process_start=? AND status=\'live\''+
            (' AND native_id=?' if native_id is not None else ''),
            (provider,owner['pid'],owner['start'])+((native_id,) if native_id is not None else ()))
        if not rows or not matches(owner):return []
        return [peer for row in rows if (peer:=json.loads(row['descriptor_json']))['owner']==owner]

    def for_native(self,provider,native_id):
        rows=self.journal.all("SELECT descriptor_json FROM peer_instances WHERE provider=? AND native_id=? AND status='live'",(provider,native_id))
        return [peer for row in rows if matches((peer:=json.loads(row['descriptor_json']))['owner'])]

    def list(self, provider=None, include_offline=False):
        rows = self.journal.all("SELECT * FROM peer_instances ORDER BY last_seen DESC")
        claude_metadata={}
        if provider in (None,'claude') and any(r['provider']=='claude' and r['status']=='live' and r['owner_kind']=='interactive-standalone' for r in rows):
            from .adapters.metadata import claude_sessions
            homes={json.loads(r['descriptor_json'])['home'] for r in rows if r['provider']=='claude' and r['status']=='live' and r['owner_kind']=='interactive-standalone'}
            for home in homes:
                try:
                    for s in claude_sessions(Path(home)):claude_metadata[(home,s.get('sessionId'),s.get('pid'))]=s.get('name')
                except Exception:pass
        peers, seen = [], set()
        for row in rows:
            if row["peer_key"] in seen:
                continue
            seen.add(row["peer_key"])
            peer = json.loads(row["descriptor_json"])
            alive = row["status"] == "live" and matches(peer["owner"])
            if alive and peer['owner_kind']=='interactive-standalone':
                if peer['peer_key']['provider']=='codex':
                    from .adapters.metadata import codex_name
                    native_title=codex_name(Path(peer['home']),peer['peer_key']['native_session_id'])
                elif peer['peer_key']['provider']=='antigravity':
                    from .antigravity import summaries
                    names=summaries(native_id=peer['peer_key']['native_session_id'],home=Path(peer['home']))
                    native_title=names[0]['title'] if names else None
                else:native_title=claude_metadata.get((peer['home'],peer['peer_key']['native_session_id'],peer['owner']['pid']))
                if native_title and native_title!=peer['title']:
                    peer['title']=title(native_title);peer['title_source']='native-explicit'
                    self.journal.db.execute('UPDATE peer_instances SET title=?,descriptor_json=? WHERE incarnation_id=?',(peer['title'],canonical(peer).decode(),peer['incarnation_id']))
            if (include_offline or alive) and (provider is None or peer["peer_key"]["provider"] == provider):
                peer["online"] = alive
                if peer['owner_kind']=='owned-worker-controller':
                    worker=self.journal.one('SELECT state FROM workers WHERE peer_key=?',(peer['handle'],))
                    if worker:peer['lifecycle']=worker['state'].lower()
                peers.append(peer)
        return peers

    def resolve(self, selector):
        require(isinstance(selector, str), "PROTOCOL_ERROR", "Target must be a string")
        if selector.startswith("np1_"):
            return self.get(selector)
        provider, label, alias = None, None, selector
        require('://' not in selector,'REMOTE_HOST_UNSUPPORTED','Only local native peers are addressable')
        if ":" in selector:
            qualifier, possible_alias = selector.split(":", 1)
            possible_provider=qualifier.split('@',1)[0]
            if possible_provider in ('codex','claude','antigravity'):
                provider=possible_provider;alias=possible_alias
                if '@' in qualifier:label=qualifier.split('@',1)[1]
        alias = title(alias)
        candidates = [p for p in self.list(provider) if p["title"] == alias and (label is None or p["home_label"] == label)]
        require(candidates, "TARGET_OFFLINE", "No eligible live peer has that exact native title")
        require(len(candidates) == 1, "AMBIGUOUS_TARGET", "Several live peers have that exact native title",
                details=[{k:p[k] for k in ("handle", "title", "home_label", "cwd", "lifecycle", "peer_key")} for p in candidates])
        return candidates[0]

    def verify(self, handle, incarnation):
        peer = self.get(handle)
        require(peer["incarnation_id"] == incarnation, "TARGET_REBOUND", "Peer resumed; resolve the target again before submission")
        return peer
