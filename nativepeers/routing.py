"""One journaled send/reply core for CLI, facade, and finite controllers."""
from __future__ import annotations

import json
import time

from .adapters.native_delivery import claude_inbox, codex_queue
from .errors import PeerError, require
from .files import async_advisory_lock, canonical, sha256
from .models import PEER_FRAME, bounded_text, new_id
from .receipts import reduce_receipt


class Router:
    def __init__(self, journal, registry, adapters=None):
        self.journal, self.registry = journal, registry
        from .antigravity import queue_native_hook
        self.adapters = adapters or {"claude-inbox":claude_inbox, "codex-native-queue":codex_queue,
                                     "antigravity-native-hook":queue_native_hook}

    def outcome(self, message_id):
        message = self.journal.one("SELECT * FROM messages WHERE message_id=?", (message_id,))
        attempt = self.journal.one("SELECT * FROM attempts WHERE message_id=? ORDER BY started_at DESC LIMIT 1", (message_id,))
        return dict(message_id=message_id, exchange_id=message["exchange_id"], target=message["to_key"],
                    state=attempt["state"] if attempt else "OUTBOX_COMMITTED", attempt=attempt,
                    receipts=self.journal.all("SELECT * FROM receipts WHERE message_id=? ORDER BY at", (message_id,)))

    def authorize(self, caller, target, kind, reference=None):
        require(caller.kind in ("peer", "controller"), "CALLER_UNBOUND", "Peer sends require a verified native caller or leased controller")
        if caller.kind == "controller":
            require(kind in ("completion", "failure") and target == caller.grants["parent_key"],
                    "APPROVAL_BLOCKED", "Controller may complete only its own group to its recorded parent")
        if caller.grants.get("worker_id"):
            allowed = caller.grants.get("message_targets", [])
            sibling = self.journal.one("SELECT group_id FROM workers WHERE peer_key=?",(target,))
            sibling_allowed = bool(caller.grants.get('allow_sibling_messages') and sibling and sibling['group_id']==caller.grants.get('group_id'))
            require(target in allowed or reference is not None or sibling_allowed,
                    "APPROVAL_BLOCKED", "Worker has no grant to message that peer")

    async def send(self, caller, target, text, *, client_request_id=None, expect_reply=False,
                   in_reply_to=None, kind=None, expires_seconds=1800, artifact_refs=()):
        text = bounded_text(text)
        from .policy import load
        local=load(self.journal)
        require(0 < expires_seconds <= 86400 and len(artifact_refs) <= 16, "PROTOCOL_ERROR", "Invalid expiry or artifact count")
        request_id = client_request_id or new_id()
        original = dict(target=target,text=text,expect_reply=expect_reply,in_reply_to=in_reply_to,
                        kind=kind,expires_seconds=expires_seconds,artifact_refs=list(artifact_refs))
        digest = sha256(original)
        # Deliberately precedes mutable title resolution, permission defaults, and external I/O.
        existing = self.journal.one("SELECT * FROM send_requests WHERE caller_key=? AND client_request_id=?", (caller.key,request_id))
        if existing:
            require(existing["payload_sha256"] == digest, "IDEMPOTENCY_CONFLICT", "Request ID already has different content")
            return await self.admit(existing["message_id"], request_id)
        reference = None
        if in_reply_to:
            row = self.journal.one("SELECT payload_json FROM messages WHERE message_id=?", (in_reply_to,))
            require(row is not None, "PROTOCOL_ERROR", "Unknown referenced message")
            reference = json.loads(row["payload_json"])
            require(reference["to"]["peer_key"] == caller.key, "APPROVAL_BLOCKED", "Only the recorded recipient can reply")
            require(reference["from"]["kind"] == "peer", "PROTOCOL_ERROR", "Controller notices have no native reply address")
            target = reference["from"]["peer_key"]
        peer = self.registry.resolve(target)
        kind = "reply" if reference else (kind or ("request" if expect_reply else "notify"))
        require(kind in ("request", "notify", "reply", "completion", "failure"), "PROTOCOL_ERROR", "Unknown message kind")
        self.authorize(caller, peer["handle"], kind, reference)
        hop = reference.get("hop_count",0)+1 if reference else 0
        require(hop <= local["exchange_hops"], "CAPACITY_UNAVAILABLE", "Exchange hop limit reached")
        expiry=local["request_expiry_seconds"] if expires_seconds==1800 else expires_seconds
        require(0<expiry<=86400,"PROTOCOL_ERROR","Invalid configured message expiry")
        now, message_id, attempt_id = time.time(), new_id(), new_id()
        envelope = dict(schema_version=1,message_id=message_id,
            exchange_id=reference["exchange_id"] if reference else new_id(),kind=kind,
            **{"from":caller.actor()},to={"peer_key":peer["handle"]},
            reply_to={"peer_key":caller.key} if caller.kind=="peer" else None,
            in_reply_to=in_reply_to,created_at=now,expires_at=now+expiry,delivery_mode="queue",
            expect_reply=expect_reply,hop_count=hop,max_hops=local["exchange_hops"],
            origin="peer-agent" if caller.kind=="peer" else "group-controller",
            body={"text":text},artifact_refs=list(artifact_refs),task_id=None)
        require(len(canonical(envelope)) <= PEER_FRAME, "PAYLOAD_TOO_LARGE", "Peer frame exceeds 64 KiB")
        from .artifacts import Artifacts
        for ref in artifact_refs:
            require(isinstance(ref,str),"PROTOCOL_ERROR","Artifact references must be opaque handles")
            Artifacts(self.journal).read(ref,caller.key,0,1)
        transport = peer["transport"]["kind"]
        with self.journal.transaction() as db:
            raced = db.execute("SELECT * FROM send_requests WHERE caller_key=? AND client_request_id=?",(caller.key,request_id)).fetchone()
            if raced:
                require(raced["payload_sha256"]==digest,"IDEMPOTENCY_CONFLICT","Concurrent request has different content")
                message_id = raced["message_id"]
            else:
                if expect_reply:
                    pending=db.execute("SELECT COUNT(*) FROM messages WHERE to_key=? AND expires_at>? AND json_extract(payload_json,'$.expect_reply')=1 AND NOT EXISTS(SELECT 1 FROM messages r WHERE r.in_reply_to=messages.message_id)",(peer["handle"],now)).fetchone()[0]
                    require(pending<local["pending_messages"],"CAPACITY_UNAVAILABLE","Recipient has 32 pending requests")
                for ref in artifact_refs:Artifacts(self.journal).grant(ref,caller.key,[peer["handle"]])
                db.execute("INSERT INTO send_requests VALUES(?,?,?,?)",(caller.key,request_id,digest,message_id))
                db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?)",(message_id,envelope["exchange_id"],caller.key,peer["handle"],in_reply_to,None,canonical(envelope).decode(),sha256(envelope),now+expiry,now))
                db.execute("INSERT INTO attempts(attempt_id,message_id,target_incarnation,transport,state,started_at) VALUES(?,?,?,?,?,?)",(attempt_id,message_id,peer["incarnation_id"],transport,"OUTBOX_COMMITTED",now))
                if reference:
                    original_attempt=db.execute('SELECT attempt_id FROM attempts WHERE message_id=? ORDER BY started_at DESC LIMIT 1',(in_reply_to,)).fetchone()
                    db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)',(new_id(),in_reply_to,original_attempt['attempt_id'] if original_attempt else None,
                        'REPLY_RECORDED','explicit-bound-peer-reply',caller.key,None,canonical({'reply_message_id':message_id}).decode(),time.time()))
        return await self.admit(message_id, request_id)

    async def admit(self, message_id, request_id):
        # Only durable, definitely unsubmitted intent is retryable. Never resolve its alias again.
        outcome = self.outcome(message_id)
        if outcome['state'] not in ('OUTBOX_COMMITTED', 'ADMISSION_DEFERRED'):
            return dict(outcome, client_request_id=request_id)
        message = self.journal.one('SELECT payload_json FROM messages WHERE message_id=?', (message_id,))
        envelope = json.loads(message['payload_json'])
        target = outcome['target']
        attempt_id = outcome['attempt']['attempt_id']
        submitted = False
        try:
            async with async_advisory_lock(self.journal.root/"locks"/(sha256(target)+".lock")):
                # Another exact-ID retry may have won while this invocation waited.
                with self.journal.transaction() as db:
                    attempt = db.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
                    if attempt['state'] not in ('OUTBOX_COMMITTED', 'ADMISSION_DEFERRED'):
                        return dict(self.outcome(message_id), client_request_id=request_id)
                    peer = self.registry.verify(target, attempt['target_incarnation'])
                    require(time.time() < envelope['expires_at'], 'EXPIRED_BEFORE_SUBMISSION', 'Message expired before native admission')
                    adapter = self.adapters.get(attempt['transport'])
                    require(adapter is not None, 'UNSUPPORTED_TRANSPORT', 'Transport is not validated')
                    db.execute("UPDATE attempts SET state='SUBMITTING',error_json=NULL,ended_at=NULL WHERE attempt_id=?", (attempt_id,))
                submitted = True
                try:
                    receipt = await adapter(peer, envelope)
                    self.record(message_id, attempt_id, receipt, peer)
                except BaseException as exception:
                    self.admission_error(attempt_id, exception, submitted=True)
                    if not isinstance(exception, Exception):raise
        except BaseException as exception:
            # A lock deadline is a definite pre-submission failure. Keep the same intent retryable.
            if not submitted:
                self.admission_error(attempt_id, exception, submitted=False)
            if not isinstance(exception, Exception):raise
        return dict(self.outcome(message_id), client_request_id=request_id)

    def admission_error(self, attempt_id, exception, *, submitted):
        uncertain = submitted and (not isinstance(exception, PeerError) or exception.may_have_received)
        if uncertain:
            error = exception if isinstance(exception, PeerError) else PeerError('DELIVERY_UNKNOWN', type(exception).__name__, may_have_received=True)
            state = 'DELIVERY_UNKNOWN'
        elif isinstance(exception, PeerError):
            error = exception
            state = exception.code
        else:
            state = 'ADMISSION_DEFERRED'
            error = PeerError(state, 'Native admission has not started; retry the same client request ID', details={'cause':type(exception).__name__})
        # A contender's timeout cannot overwrite an attempt another invocation has submitted.
        condition = " AND state IN ('OUTBOX_COMMITTED','ADMISSION_DEFERRED')" if not submitted else ''
        # Independent native evidence may arrive while the adapter is still awaiting its response.
        state_update = "CASE WHEN state IN ('SUBMITTING','DELIVERY_UNKNOWN') THEN ? ELSE state END" if submitted else '?'
        self.journal.db.execute('UPDATE attempts SET state='+state_update+',ended_at=?,error_json=? WHERE attempt_id=?'+condition,
            (state,time.time(),canonical(error.json()).decode(),attempt_id))

    def record(self,message_id,attempt_id,receipt,peer):
        with self.journal.transaction() as db:
            current=db.execute('SELECT state FROM attempts WHERE attempt_id=?',(attempt_id,)).fetchone()
            state=reduce_receipt(current['state'],receipt['stage'])
            db.execute("UPDATE attempts SET state=?,external_submission_id=COALESCE(?,external_submission_id),ended_at=? WHERE attempt_id=?",(state,receipt.get("external_submission_id"),time.time(),attempt_id))
            db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)",(new_id(),message_id,attempt_id,receipt["stage"],receipt["evidence_kind"],peer["peer_key"]["native_session_id"],receipt.get("native_turn_id"),canonical(receipt).decode(),time.time()))

    def inbox(self,caller,limit=20,unread=False):
        require(caller.kind=="peer","CALLER_UNBOUND","Inbox requires a bound native session")
        require(0<limit<=100,"PROTOCOL_ERROR","Inbox limit must be 1 to 100")
        predicate = " AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.message_id=messages.message_id AND r.stage='MODEL_REPORTED_PROCESSED')" if unread else ''
        rows=self.journal.all("SELECT payload_json FROM messages WHERE to_key=?"+predicate+" ORDER BY created_at DESC LIMIT ?",(caller.key,limit))
        messages=[json.loads(r["payload_json"]) for r in rows]
        peer=self.registry.get(caller.key)
        if peer['owner_kind']=='interactive-standalone':
            visible=[]
            for message in messages:
                receipt=self.outcome(message['message_id'])
                known=receipt['state'] in ('INGESTED','MODEL_REPORTED_PROCESSED','REPLY_RECORDED','REPLY_SUBMITTED') or any(r['stage'] in ('INGESTED','MODEL_REPORTED_PROCESSED','REPLY_RECORDED') for r in receipt['receipts'])
                visible.append(message if known else {'message_id':message['message_id'],'delivery_state':receipt['state'],'body_available':False,'reason':'Native admission or inbound policy has not established ingestion.'})
            messages=visible
        return messages

    def ack(self,caller,message_id):
        row=self.journal.one("SELECT to_key FROM messages WHERE message_id=?",(message_id,))
        require(row and row["to_key"]==caller.key and caller.kind=="peer","APPROVAL_BLOCKED","Only the recorded recipient can acknowledge")
        if not self.journal.one("SELECT receipt_id FROM receipts WHERE message_id=? AND stage='MODEL_REPORTED_PROCESSED'",(message_id,)):
            self.journal.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?)",(new_id(),message_id,None,"MODEL_REPORTED_PROCESSED","explicit-model-report",None,None,"{}",time.time()))
        return self.outcome(message_id)
