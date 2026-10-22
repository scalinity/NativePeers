## Code Review: NativePeers reliability corrections — four Astra/xhigh reviewers

### Summary

Four independent read-only lanes reviewed `c165dbe2a009f55c42c990635a8e0f3b6539314b` against `75f714d`. The deduplicated review found **one critical issue, twelve warnings and four suggestions**. No reviewer ran tests, builds or native inference. Findings below describe the reviewed snapshot; the resolution table will record subsequent verified fixes.

### 🔴 Critical Issues (Must Fix)

1. **A13 — Controller death can orphan its native provider** — `nativepeers/supervisor.py:94`. DB2-C1. Clean the exact generation-matched native process independently of whether waiting for the controller times out.

### 🟡 Warnings (Should Fix)

- **A01 — Retain unconfirmed receipt after native history lookup failure** (CR1-W1 / DB1-W2). See the corresponding full report below for the trigger, location and requested correction.
- **A02 — Isolate selected-home offline fixtures from ambient provider settings** (CR1-W2). See the corresponding full report below for the trigger, location and requested correction.
- **A03 — Renew only verified compatibility receipts on no-op installation** (CR1-W3). See the corresponding full report below for the trigger, location and requested correction.
- **A06 — Preserve confirmed ingestion after late adapter failure** (DB1-W1). See the corresponding full report below for the trigger, location and requested correction.
- **A07 — Exclude all registered native provider homes from context imports** (DB1-W3). See the corresponding full report below for the trigger, location and requested correction.
- **A09 — Preserve both rename paths in patch discovery** (CA1-W1). See the corresponding full report below for the trigger, location and requested correction.
- **A10 — Normalize tracked Git attributes in worker delta capture** (CA1-W2). See the corresponding full report below for the trigger, location and requested correction.
- **A11 — Retain completed results after recursive optional JSON** (CA1-W3). See the corresponding full report below for the trigger, location and requested correction.
- **A14 — Refresh idle activity after notification awaits** (DB2-W1). See the corresponding full report below for the trigger, location and requested correction.
- **A15 — Renew live supervisor deadline consistently on owned resume** (DB2-W2). See the corresponding full report below for the trigger, location and requested correction.
- **A16 — Fence completion admission by group lifetime** (DB2-W3). See the corresponding full report below for the trigger, location and requested correction.
- **A17 — Settle definitely unsubmitted completion reservations on retention or wait** (DB2-W4). See the corresponding full report below for the trigger, location and requested correction.

### 🔵 Suggestions (Consider)

- **A04 — Require observable artifact consumption in native fixture** (CR1-S1).
- **A05 — Describe structured reports and worker deltas in result schema** (CR1-S2).
- **A08 — Bound artifact offsets by stored size** (DB1-S1).
- **A12 — Exercise selected homes through real fake-provider launches** (CA1-S1).

### ✅ Good Practices

- Immutable request lookup precedes mutable alias resolution; uncertain native input is not replayed.
- Selected workspaces and provider homes retain explicit identity and provenance.
- Worker-reported checks remain distinct from independently verified checks.

### Verdict

[x] ❌ Request Changes (critical issue at the reviewed snapshot)

## Resolution ledger

All 17 review findings are addressed. Four neighboring corrections are also retained: A08F direct-peer errors, A18 native Gemini MCP wrapper admission, A04F installed native event observation, and A19 shutdown/resume serialization. Each focused fix was verified and pushed. The read-only verdict above describes the reviewed snapshot, not a second agent approval of later code.

| ID | Finding | Status | Commit |
| --- | --- | --- | --- |
| A01 | Retain unconfirmed receipt after native history lookup failure | FIXED | `85b2893` |
| A02 | Isolate selected-home offline fixtures from ambient provider settings | FIXED | `e89319b` |
| A03 | Renew only verified compatibility receipts on no-op installation | FIXED | `4e6ac1c` |
| A04 | Require observable artifact consumption in native fixture | FIXED | `222346b` |
| A05 | Describe structured reports and worker deltas in result schema | FIXED | `b157f7e` |
| A06 | Preserve confirmed ingestion after late adapter failure | FIXED | `1013dbc` |
| A07 | Exclude all registered native provider homes from context imports | FIXED | `c30f759` |
| A08 | Bound artifact offsets by stored size | FIXED | `04a7593` |
| A09 | Preserve both rename paths in patch discovery | FIXED | `e41727b` |
| A10 | Normalize tracked Git attributes in worker delta capture | FIXED | `88e75df` |
| A11 | Retain completed results after recursive optional JSON | FIXED | `0d1aeba` |
| A12 | Exercise selected homes through real fake-provider launches | FIXED | `cbcb261` |
| A13 | Clean exact native provider after controller death | FIXED | `d606538` |
| A14 | Refresh idle activity after notification awaits | FIXED | `9cd8ae3` |
| A15 | Renew live supervisor deadline consistently on owned resume | FIXED | `60a290b` |
| A16 | Fence completion admission by group lifetime | FIXED | `e552792` |
| A17 | Settle definitely unsubmitted completion reservations on retention or wait | FIXED | `e950705` |
| A08F | Preserve direct-peer protocol errors through artifact reader fallback | FIXED | `99f3d74` |
| A18 | Admit only namespaced NativePeers calls through native Gemini MCP wrapper | FIXED | `51c52a2` |
| A04F | Observe installed owned native history and actual tool event variants | FIXED | `e70dfaa` |
| A19 | Serialize idle shutdown against owned resume admission | FIXED | `7b3a6f7` |

Final offline verification: **203 tests and 252 subtests PASS**; manager/naming fixtures: **23 PASS**. Current-source Sol/Flash original-parent results, concurrent implementation/analysis workers, real artifact tool calls, exact Gemini resume and original-Codex native queue completion pass. Claude current-source inference remains **BLOCKED** by usage limit. Exact fingerprints and scoped assertions: [astra4-review.json](../evidence/astra4-review.json).

Git discovery can execute clean/process filters before hashing; capture therefore suppresses external filters throughout and marks unsupported custom-filter deltas unavailable. Python 3.14’s C JSON decoder accepts the originally suggested nesting depth; the regression uses the standard-library Python decoder to exercise a genuine recursion failure below the byte ceiling.

The first stronger native check exposed the Gemini wrapper contract; the next exposed a real shutdown/resume race. Failed observations remain recorded. One fixture preflight used an inconsistent local time limit and admitted no workers; corrected continuation reused each original parent. An interrupted resume remains an immutable unknown-execution result with both writers dead and no replay.

## Per-agent reports (verbatim)

<details><summary>DB1 — gpt-6-astra / xhigh</summary>

## Code Review: DB1 — Identity, routing, message correctness and authorization

### Summary

Static review of changes from `75f714d29d94b10ae8562f3114e21a3ab22d9953` to `c165dbe`, with product source at `7de98896ab48a945c02c91558b66d2f5687432f0`, found three scoped issues involving receipt preservation, refresh failures, and artifact import exclusions. Findings are based on source and test inspection; no tests, linters, builds, native inference, or source modifications were performed.

### 🔴 Critical Issues (Must Fix)

None.

### 🟡 Warnings (Should Fix)

1. **DB1-W1 — A late adapter failure can downgrade confirmed ingestion.**  
   **Location:** [nativepeers/routing.py:153](/Users/danny/Documents/Tools/NativePeers/nativepeers/routing.py:153), with the concurrent observation path at [nativepeers/reconcile.py:78](/Users/danny/Documents/Tools/NativePeers/nativepeers/reconcile.py:78).

   **Problem:** When `submitted=True`, `admission_error()` updates the attempt without checking its current state. A reachable sequence is: transport submission starts; the recipient consumes the message; another invocation of the new `status --refresh` path records `INGESTED`; the original adapter subsequently times out or loses its response. The error handler then overwrites `INGESTED` with `DELIVERY_UNKNOWN`. Owned workers also record ingestion independently through `Worker.ingested()`, creating the same ordering possibility.

   **Consequence:** The ingestion receipt survives, but the top-level state and exact-ID retry result regress to uncertainty despite stronger evidence already being recorded. CLI send can consequently return exit 6 for a message whose ingestion has been confirmed. The current receipt reducer protects successful receipt updates, but this error path bypasses it.

   **Suggestion:** Make error updates preserve stronger evidence atomically, using the receipt reducer or a guarded state update. Retain the transport error as diagnostic information without replacing an established admission/ingestion state. Add an offline regression in which ingestion is recorded while the adapter is pending, followed by an adapter exception. This is a source-derived race; its frequency was not measured.

2. **DB1-W2 — Codex metadata lookup failures escape the new receipt-refresh interface.**  
   **Location:** [nativepeers/reconcile.py:39](/Users/danny/Documents/Tools/NativePeers/nativepeers/reconcile.py:39), exposed through [nativepeers/core.py:44](/Users/danny/Documents/Tools/NativePeers/nativepeers/core.py:44).

   **Problem:** For a Codex recipient without `native_transcript`, reconciliation starts an app-server and calls `thread/read`. Owned Codex worker registration does not populate that transcript field, so this is a normal branch. The new unavailable-history handling only catches `OSError` around the later file read. A native lookup rejection raises `ProviderError`, and startup/read timeouts also escape the reconciliation fallback.

   **Trigger and consequence:** If a retained message targets an owned Codex history that is unavailable, or the metadata helper fails, `status MESSAGE_ID --refresh` cannot return its already-known outcome. `ProviderError` is a `RuntimeError`, which the CLI’s error handlers do not catch; the command can terminate with a traceback instead of its JSON envelope. The MCP facade likewise does not convert this into a retained, unconfirmed observation. The stored message remains intact, but the refresh response loses the useful IDs and existing state.

   **Suggestion:** Handle expected metadata lookup and availability failures around app-server construction/start/read, perform cleanup, and return the original outcome with a bounded `UNCONFIRMED` explanation. Preserve explicit identity/path mismatch failures and cancellation behavior. Extend the offline refresh fixtures to cover `thread/read` rejection and startup timeout; the current test covers file-opening failure only.

3. **DB1-W3 — Artifact import does not exclude other registered sessions’ custom provider homes.**  
   **Location:** [nativepeers/artifacts.py:60](/Users/danny/Documents/Tools/NativePeers/nativepeers/artifacts.py:60).

   **Problem:** `private_roots` includes the NativePeers state directory, the caller’s provider home, and home variables present in the current process. It omits provider homes already registered for other sessions.

   **Trigger and consequence:** Suppose a registered Claude session uses a custom home such as `/Users/danny/NativeHomes/claude-work`, while import runs from a Codex session without `CLAUDE_CONFIG_DIR` in its environment. A regular file at that Claude home’s `projects/<project>/<UUID>.jsonl` has neither a blocked path component nor a blocked transcript basename. The importer therefore accepts a native transcript, contrary to its documented exclusion. That copy can subsequently be granted to workers through `context_refs`. This finding is conditional on the described alternate-home setup; no private homes or transcripts were inspected.

   **Suggestion:** Include canonical provider homes already recorded in `peer_instances` when constructing the excluded roots, without reading provider configuration or transcript contents. Add a fixture with two registered provider homes, no cross-provider environment variable, and a UUID-named Claude transcript; assert rejection while retaining normal brief imports.

### 🔵 Suggestions (Consider)

1. **DB1-S1 — Bound artifact offsets before passing them to the file API.**  
   **Location:** [nativepeers/artifacts.py:101](/Users/danny/Documents/Tools/NativePeers/nativepeers/artifacts.py:101) and [nativepeers/artifacts.py:111](/Users/danny/Documents/Tools/NativePeers/nativepeers/artifacts.py:111).

   **Current:** This pre-existing reader accepts any nonnegative Python integer as an offset. With a valid readable artifact, an offset such as `10**100` passes validation and reaches `f.seek()`, which raises `OverflowError`; neither facade converts that exception into the documented error envelope.

   **Better:** After checking the reader grant, validate the offset against the stored artifact size, allowing the exact end offset, or explicitly handle offsets beyond the end without seeking. Return `PROTOCOL_ERROR` for unsupported offsets and add a focused boundary fixture. This is an adjacent input-validation improvement, not a regression introduced by the selected-file importer.

### ✅ Good Practices

- Exact-ID retries consult the immutable request record before resolving aliases, retain the original target and attempt, and recheck target incarnation and expiry under the recipient admission lock.
- The retry path excludes uncertain and already-submitted states. Its pre-admission error update also guards against a competing invocation that has already submitted the attempt.
- Replies verify the recorded recipient, target the immediate message author, preserve the exchange ID, and reject controller notices as peer reply addresses.
- Caller binding checks native ownership and incarnation; worker messaging grants distinguish parent targets, authorized siblings, and correlated replies.
- Unread filtering now occurs before the SQL limit, addressing the acknowledged-message starvation case covered by the new fixture.
- Selected-file import uses descriptor-relative, no-follow traversal, regular-file checks, bounded reads, and change detection. Artifact reader grants and immutable-copy behavior have focused offline fixtures; those fixtures were inspected, not executed.

### Verdict

[x] Request Changes

</details>

<details><summary>DB2 — gpt-6-astra / xhigh</summary>

## Code Review: DB2 — Lifecycle, completion ownership and runtime reliability

### Summary

Reviewed pinned HEAD `c165dbe2a009f55c42c990635a8e0f3b6539314b` against `75f714d29d94b10ae8562f3114e21a3ab22d9953`, including the owned lifecycle modules, callers, specifications and relevant tests; findings below distinguish retained baseline defects from gaps in the new completion behavior. This was source inspection only: no tests, builds, native inference or source modifications were performed.

### 🔴 Critical Issues (Must Fix)

1. **DB2-C1 — A dead worker controller can leave its native provider running.**  
   **Location:** [nativepeers/supervisor.py:94](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:94), [nativepeers/supervisor.py:129](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:129).  
   **Problem:** Kill or crash a worker controller while its Claude/Antigravity provider is still executing. The provider runs in a separate process group. `read_worker()` records the failure but does not clean up that provider. Later, `release_worker()` inspects and signals `native_process` only when waiting for the **controller** times out. An already-dead controller returns immediately, so that cleanup branch is skipped. This defect predates the baseline and remains in the changed lifecycle path. The new crash fixture explicitly performs its own provider kill in [tests/contract/test_lifecycle_shutdown.py:136](/Users/danny/Documents/Tools/NativePeers/tests/contract/test_lifecycle_shutdown.py:136), rather than asserting production cleanup.
   
   **Risk:** The native provider can continue inference or workspace operations after its task is reported interrupted and its supervisor exits. Recovery refuses to release a still-live provider, so the orphan also obstructs owned resume. Whether a particular native provider exits voluntarily after losing its output reader is provider-dependent; NativePeers currently provides no cleanup guarantee for this path.
   
   **Fix:** On unexpected controller exit, inspect the matching worker/generation manifest and perform bounded cleanup of the exact recorded native process group independently of the controller’s exit status. Confirm both writers have stopped before releasing ownership/capacity. Extend the crash fixture with a provider that remains alive after controller loss and assert production cleanup before fixture teardown.

### 🟡 Warnings (Should Fix)

1. **DB2-W1 — A follow-up admitted during notification can be canceled by the idle-grace timer.**  
   **Location:** [nativepeers/supervisor.py:328](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:328), [nativepeers/supervisor.py:333](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:333), [nativepeers/supervisor.py:345](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:345).  
   **Problem:** `monitor()` snapshots `active=False`, then awaits `notify()`. During that await, `enqueue()` can admit a follow-up and clear `finished_at` at line 242. When notification returns, the monitor uses its stale `active` value to set `finished_at` again. The subsequent grace-expiration check does not require the group to remain idle. With the supported `followup_grace_seconds=0`, the accepted successor can be canceled immediately; with the default grace, a sufficiently long follow-up is interrupted after ten minutes despite still being active. This race predates the baseline and remains in the modified monitor.
   
   **Suggestion:** Re-read task activity after notification awaits, clear `finished_at` whenever work is active, and require a fresh idle check before grace-based shutdown. Add a controlled interleaving where notification pauses, a successor is admitted, and notification then finishes.

2. **DB2-W2 — Live-supervisor resume persists a renewed lifetime but continues using the old deadline.**  
   **Location:** [nativepeers/recovery.py:86](/Users/danny/Documents/Tools/NativePeers/nativepeers/recovery.py:86), [nativepeers/supervisor.py:171](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:171), [nativepeers/supervisor.py:326](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:326).  
   **Problem:** Resume a released worker while its group supervisor remains alive, for example while another worker has active follow-ups. `resume_worker()` renews `policy.created_at` and the worker lease; the supervisor reloads that policy. However, `monitor()` calculated its local `deadline` once before entering the loop. Resuming near the original deadline therefore admits a task with renewed persisted limits and then cancels it at the old deadline. Resuming through a newly launched supervisor uses the renewed timestamp instead. This inconsistency is retained from the baseline.
   
   **Suggestion:** Make the runtime deadline agree with the persisted resume policy. If resume renews the group lifetime, update the monitor’s deadline when accepting the resume. If the original deadline must remain authoritative, preserve it in policy and reject or explicitly constrain the resume before reserving/submitting the new task. Cover both live-supervisor and restarted-supervisor resume paths.

3. **DB2-W3 — Deferred completion retries can submit native input after the group lifetime expires.**  
   **Location:** [nativepeers/supervisor.py:332](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:332), [nativepeers/supervisor.py:335](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:335).  
   **Problem:** The new retry path leaves a definitely unsubmitted completion `UNCLAIMED` and keeps the supervisor alive. On each iteration, however, `monitor()` calls `notify()` **before** checking the deadline. If the recipient’s admission lock becomes available after the group expires, the next retry can submit a completion and wake the parent before the monitor notices expiration. A retry started just before expiry can also cross it while awaiting the admission lock. This contradicts the documented behavior that a notice is retained when the lifetime ends before admission.
   
   **Suggestion:** Check lifetime before attempting notifications and carry the remaining deadline through admission so expiry can be checked again after acquiring the recipient lock, before native submission. Retain the completion when that deadline has passed. Add cases for expiry immediately before retry and while waiting for admission.

4. **DB2-W4 — Abandoning a deferred completion leaves its verification reservation outstanding indefinitely.**  
   **Location:** [nativepeers/supervisor.py:308](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:308), [nativepeers/supervisor.py:318](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:318), [nativepeers/supervisor.py:357](/Users/danny/Documents/Tools/NativePeers/nativepeers/supervisor.py:357), [nativepeers/notifications.py:16](/Users/danny/Documents/Tools/NativePeers/nativepeers/notifications.py:16).  
   **Problem:** With verification-budget tracking enabled, notification reserves and admits a turn before calling the router. If admission is deferred, the reservation correctly survives retries. But there is no settlement path when a terminal waiter subsequently claims that `UNCLAIMED` completion, or when shutdown converts it to `RETAINED`. No native submission occurred, yet the reservation remains in `outstanding_native_requests`; its active-time interval continues accumulating and can exhaust later verification allowance. The new test checks successful retry reuse, but not abandonment or transfer to a waiter.
   
   **Suggestion:** When notification ownership transfers to a waiter or a deferred notice is permanently retained, release the reservation only after confirming the corresponding attempt is still definitely unsubmitted. Close its active interval and return the unused turn allowance. Preserve reservations for accepted or uncertain native submissions. Add tests for deferred-then-wait and deferred-then-shutdown.

### 🔵 Suggestions (Consider)

None beyond the scoped fixes and regression cases above.

### ✅ Good Practices

- Queued successors now require a generation-matched `QUEUED → RUNNING` claim, and shutdown settles unstarted work without replaying uncertain native input.
- Completion retries preserve the original delivery ID, and shared waiter claims prevent terminal result reads from automatically triggering another push.
- Task-specific waits preserve the separate initial batch notification, including a one-worker batch.
- Provider homes are pinned through launch, registration and owned resume; process signaling checks recorded ownership rather than using broad process-name termination.
- The added lifecycle fixtures distinguish unconfirmed submissions from unstarted tasks, and worker-reported findings/checks remain explicitly marked as independently unverified.

### Verdict

[x] Request Changes

</details>

<details><summary>CA1 — gpt-6-astra / xhigh</summary>

## Code Review: CA1 — Workspaces, native launch profiles and provider home integrity

### Summary

Reviewed the assigned source at `c165dbe`, including the changes since `75f714d29d94b10ae8562f3114e21a3ab22d9953`, enclosing functions, callers, specifications, and relevant test files; found three concrete defects in patch and result capture. Findings are based on read-only inspection: no tests, linters, builds, or native model turns were run.

### 🔴 Critical Issues (Must Fix)

None.

### 🟡 Warnings (Should Fix)

1. **Rename handling can resurrect the original filename and report changes from a worker that made none.**

   **Problem:** [nativepeers/worktrees.py:81](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:81) discovers frozen input paths using `git diff --name-only` with rename detection enabled. For a detected rename, this returns the destination path without the source path. The same issue affects current changes at [line 72](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:72) and final delta selection at [line 108](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:108).

   **Trigger and consequence:** Stage a rename from `old.py` to `new.py` in the parent checkout, then launch an editing worker that leaves its input unchanged. The frozen input tree correctly contains only `new.py`. However, `_working_tree()` starts from baseline HEAD, which contains `old.py`, and updates only the discovered destination at [lines 154–155](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:154). Its constructed tree therefore contains **both files**. Comparing that tree with the frozen input produces a worker delta that adds `old.py` back. The reconstruction patch also selects only the destination, omitting removal of the original. A staged or committed rename performed by the worker has the corresponding path-loss problem.

   **Suggestion:** Add `--no-renames` to all three name-discovery diffs so additions and deletions both enter the path set, or parse rename-aware status records and retain both paths. Add regressions for an inherited staged rename with a no-op worker and a worker-created staged rename. Replay both patch views and assert that only the destination exists; the inherited no-op case must have an empty worker delta.

   **Confidence:** High from the source trace; not executed.

2. **The new worker delta compares normalized Git input with unnormalized working-file bytes.**

   **Problem:** The input tree is constructed from HEAD and a Git-generated tracked patch at [nativepeers/worktrees.py:140](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:140). Those tracked blobs use Git’s canonical representation. Output capture instead hashes file bytes with `hash-object --no-filters` at [line 171](/Users/danny/Documents/Tools/NativePeers/nativepeers/worktrees.py:171), bypassing attribute-driven conversions.

   **Trigger and consequence:** A parent has an uncommitted edit to a tracked file governed by `text eol=crlf`. The materialized worker file has CRLF endings, while its frozen Git blob has normalized LF endings. Even when the worker does nothing, that path enters capture through `input_paths`, and its raw CRLF bytes differ from the frozen blob. The result incorrectly reports a nonempty worker delta and attributes line-ending changes to the worker. Custom clean/smudge transformations create the same representation mismatch.

   **Suggestion:** Use the same representation for tracked blobs on both sides of the comparison. Apply the corresponding built-in Git attribute/EOL normalization before inserting current tracked content into the private index. If a custom transformation cannot be represented consistently without executing an external filter, return an explicit delta-unavailable reason for that case. Add a `text eol=crlf` fixture covering an inherited dirty file with no worker changes, followed by a real worker edit; assert an empty no-op delta and correct replay of the actual edit.

   **Confidence:** High for the CRLF case from the source trace; not executed.

3. **A malformed optional report can prevent retention of an otherwise successful final answer.**

   **Problem:** [nativepeers/worker_report.py:28](/Users/danny/Documents/Tools/NativePeers/nativepeers/worker_report.py:28) calls `json.loads(raw)`, but the handler at [line 30](/Users/danny/Documents/Tools/NativePeers/nativepeers/worker_report.py:30) catches only `ValueError` and `TypeError`. Deeply nested JSON raises `RecursionError`, which is not covered. The 16 KiB size limit does not prevent this.

   **Trigger and consequence:** A provider completes successfully but appends a `nativepeers-result` object containing roughly 1,100 nested arrays in a field. That fits within the byte limit but exceeds the normal decoder recursion limit. `extract()` is called at [nativepeers/worker_process.py:165](/Users/danny/Documents/Tools/NativePeers/nativepeers/worker_process.py:165), outside the provider-turn exception handler and before the result artifact is written at [line 187](/Users/danny/Documents/Tools/NativePeers/nativepeers/worker_process.py:187). The parsing exception escapes into worker failure handling, so the successful final text is not retained in its task result. This contradicts the optional parser’s intended fallback.

   **Suggestion:** Treat `RecursionError` as malformed optional structure and return a limitation while preserving the original text. Add a parser regression with nested JSON below the size limit and a `Worker.task()` regression asserting that the completed result and raw final answer remain available when structured extraction rejects it. This is an uncommon malformed-output case, but the fix is small and directly protects result retention.

   **Confidence:** High from the exception and persistence paths; not executed.

### 🔵 Suggestions (Consider)

1. **Exercise alternate-home preservation at the provider launch boundary.**

   **Current:** [tests/unit/test_worker_models.py:69](/Users/danny/Documents/Tools/NativePeers/tests/unit/test_worker_models.py:69) checks alternate-home defaults, leases, profile builders, identity registration, and resume metadata. However, its spawn helper mocks `AppServer` and `ensure_supervisor` at [lines 48–50](/Users/danny/Documents/Tools/NativePeers/tests/unit/test_worker_models.py:48), and the test calls `bootstrap()`/`bind()` directly. It does not exercise the actual environment passed through the supervisor/controller/provider launch chain.

   **Better:** Add an offline fake-provider contract test for initial launch and automated resume with conflicting ambient `CODEX_HOME`/`CLAUDE_CONFIG_DIR` values. Assert the selected home in the child environment, profile configuration, and registered identity. The implementation appears consistently wired through these paths; this would protect that capability against regressions without consuming native inference or implying that authentication and entitlement have been verified.

### ✅ Good Practices

- Writing batches freeze selected untracked bytes and modes once, then recheck them before admission. The source index and dirty checkout are preserved.
- Delta construction uses a temporary Git index, and oversized or timed-out captures have explicit unavailable reasons instead of returning truncated patches as complete.
- Claude and Codex homes are canonicalized and retained in worker leases. Defaults, profile configuration, native registration, automated resume, and manual resume commands use the recorded home.
- Provider tool restrictions are applied to worker launches, with effective-tool checks before normal task processing and no global permission rewrite.
- Structured findings and checks are explicitly marked `worker-reported` and `independently_verified:false`.
- The inspected tests cover selected-input mutation, executable modes, patch replay, inherited dirty state, secret/symlink exclusions, and capture limits. These are useful assertions; their execution was outside this review.

### Verdict

[x] Request Changes

</details>

<details><summary>CR1 — gpt-6-astra / xhigh</summary>

## Code Review: CR1 — Public API, installation, schemas, test and documentation integration

### Summary

Static review of HEAD `c165dbe` against `75f714d29d94b10ae8562f3114e21a3ab22d9953` found two issues affecting the new public interfaces and their fixtures, plus one explicitly identified pre-existing installation defect. Product source matches `7de98896ab48a945c02c91558b66d2f5687432f0`; no source modifications, tests, linters, builds, or native model turns were performed.

### 🔴 Critical Issues (Must Fix)

None.

### 🟡 Warnings (Should Fix)

1. **Receipt refresh can escape the public JSON error contract when Codex history lookup fails.**

   **Location:** [nativepeers/core.py:44](/Users/danny/Documents/Tools/NativePeers/nativepeers/core.py:44), [nativepeers/reconcile.py:39](/Users/danny/Documents/Tools/NativePeers/nativepeers/reconcile.py:39).

   **Problem:** The new public refresh path directly awaits the observer:

   ```python
   if refresh:
       from .reconcile import observe
       return await observe(self.journal,self.registry,self.router,handle)
   ```

   When a Codex recipient descriptor lacks `native_transcript`, the observer starts an app-server and calls `thread/read`. Owned Codex workers have such descriptors: their registration at `worker_process.py:71` does not supply a transcript path. If the native history is unavailable and `thread/read` returns an error, `AppServer.call()` raises `ProviderError`, a `RuntimeError`. That exception is outside the observer’s file-read error handling, outside the CLI’s `PeerError`/`ValueError`/`OSError` handlers, and outside the MCP facade’s `PeerError` handler.

   **Trigger and consequence:** Run `status MESSAGE_ID --refresh` for a message to an owned Codex worker whose native history cannot be retrieved. The CLI can terminate with a traceback instead of its JSON envelope; MCP loses the normal NativePeers response. This contradicts `docs/USAGE.md:77`, which says unavailable history leaves delivery unconfirmed. This is a statically established error path; native behavior was not exercised.

   **Suggestion:** Handle provider lookup failures in the observation path and return the retained message outcome with an explicit unavailable/unconfirmed reconciliation reason. Keep identity mismatches as errors and preserve the no-replay behavior. Add offline coverage for a `thread/read` provider error and app-server initialization failure through both public facades.

2. **Selected-home support makes offline fixtures depend on the launching process’s real provider configuration.**

   **Location:** [tests/unit/test_worker_models.py:48](/Users/danny/Documents/Tools/NativePeers/tests/unit/test_worker_models.py:48), [tests/unit/test_artifacts_and_authority.py:116](/Users/danny/Documents/Tools/NativePeers/tests/unit/test_artifacts_and_authority.py:116).

   **Problem:** These fixtures replace `Path.home()` but leave `CODEX_HOME` and `CLAUDE_CONFIG_DIR` inherited:

   ```python
   with patch('nativepeers.delegation.Path.home', return_value=self.home), \
        patch('nativepeers.delegation.AppServer', return_value=client), \
        patch.object(self.delegation, 'ensure_supervisor', new_callable=AsyncMock):
       return await self.delegation.spawn(self.caller, request)
   ```

   The changed `provider_home()` explicitly prefers those environment variables, so the fixture’s temporary settings are bypassed whenever an override is present.

   **Trigger and consequence:** Launch the suite with a `CODEX_HOME` whose configured effort is `xhigh`. The worker-model fixture advertises only `high`, so its ordinary default-resolution test can raise `EFFORT_UNSUPPORTED`. The context-import fixture similarly expects its temporary `fixture-model` configuration but can read a different configured model. These are environment-dependent failures, and the offline fixtures read real provider settings unnecessarily. No test was run to establish the current machine’s outcome.

   **Suggestion:** Set both provider-home variables to the temporary fixture directories for the lifetime of each affected fixture and restore them during cleanup. Let the alternate-home test override those fixture values explicitly. Audit adjacent default-resolution fixtures for the same assumption.

3. **An unchanged installation cannot renew a stale compatibility receipt after a verified executable update. — Pre-existing at the baseline**

   **Location:** [nativepeers/install.py:135](/Users/danny/Documents/Tools/NativePeers/nativepeers/install.py:135), with receipt installation at [nativepeers/install.py:170](/Users/danny/Documents/Tools/NativePeers/nativepeers/install.py:170).

   **Problem:** Installation returns before examining or renewing compatibility state when hooks, skills, and trust already match:

   ```python
   if prior and not plan['operations'] and trusted and prior.get('state')=='INSTALLED' and not prior.get('uninstalled'):
       return dict(plan,installed=True,changed=False,manifest=str(self.manifest))
   ```

   **Trigger and consequence:** After a Claude executable update, complete the affected verification and update the executable evidence while leaving the integration payload unchanged. Re-running `install` returns `installed:true, changed:false` before reaching `record_verified()`. The runtime receipt remains stale, so `verify()` continues rejecting affected operations and the new doctor command continues reporting a mismatch. This also affects restoration of a missing runtime receipt when the installation manifest and owned files remain intact.

   This control-flow defect exists in `75f714d`; it is reported under the assigned installation/integration scope, not attributed to this diff.

   **Suggestion:** Include compatibility-receipt differences in the installation plan. Before taking the no-op return, validate the already-recorded verification evidence against the current executable and update only a missing or differing receipt. Preserve the existing refusal to certify an unverified executable. The Antigravity installer already handles receipt updates separately from configuration changes.

### 🔵 Suggestions (Consider)

1. **Make the native context-import scenario distinguish artifact access from ordinary file access.**

   **Location:** [tests/live/pro_review.py:99](/Users/danny/Documents/Tools/NativePeers/tests/live/pro_review.py:99), [tests/live/pro_review.py:109](/Users/danny/Documents/Tools/NativePeers/tests/live/pro_review.py:109).

   **Current:** The harness imports `brief.txt` and grants its artifact handle, but its task also says:

   ```python
   task=('Read '+str(FIXTURE/'brief.txt')+' and report its exact contents and product VALUE * 6. '
   ```

   The original file remains readable, and editing workers additionally receive it through the untracked-file allowlist. The assertions check the answer and resulting code, not whether the worker successfully called `artifact_read`.

   **Trigger and consequence:** A worker can complete this scenario using ordinary file tools even if its native MCP artifact-reading path is broken. Existing offline reader-grant tests remain useful; this scenario simply does not establish native artifact consumption.

   **Better:** In a future authorized run of this same scenario, place a distinct required value only in the imported artifact and assert the corresponding native `artifact_read` call/result. Reuse the existing worker turn. No additional native run is requested in this review phase.

2. **Describe the new structured result fields in the published result schema.**

   **Location:** [schemas/result.v1.json:17](/Users/danny/Documents/Tools/NativePeers/schemas/result.v1.json:17), [nativepeers/worker_report.py:14](/Users/danny/Documents/Tools/NativePeers/nativepeers/worker_report.py:14).

   **Current:** The new extraction code defines bounded findings and check entries, including `source:"worker-reported"` and `independently_verified:false`, but `result.v1.json` does not describe `findings`, `verification.tests_run`, or the newly documented worker-delta fields. They pass only because additional properties are unrestricted.

   **Trigger and consequence:** A consumer using the checked-in result schema cannot discover or validate those advertised shapes and provenance fields.

   **Better:** Add optional schema definitions for the new fields while retaining compatibility with older results. Use representative emitted results in offline contract coverage so documentation, extraction, and the public schema stay aligned.

### ✅ Good Practices

- CLI and MCP send/reply operations now use the same `delivery_error()` classifier, and the added fixtures check preservation of message IDs, request IDs, and definite versus uncertain outcomes.
- Context import uses bounded reads, regular-file checks, descriptor-based traversal, and substitution/change detection. Its tests cover both directory and leaf symlinks, source changes, private paths, and selected-worker grants.
- Worker leases record the selected provider home, and both automatic resume and surfaced manual commands carry that home explicitly.
- Structured worker claims remain labeled as worker-reported and independently unverified; malformed structure preserves the provider’s final text.
- Installation uses owned-block updates and compare-before-write checks, with coverage for preserving concurrent hook edits and locally modified skills.

### Verdict

[x] Request Changes

</details>
