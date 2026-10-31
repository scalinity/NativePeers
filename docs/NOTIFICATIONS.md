# Quiet close-out notifications

Notifications that a native queue accepts can be processed hours later, after the task has already been reported. This policy keeps those late updates out of the conversation and retains them for explicit reading. Normal active collaboration is unchanged while a session is open.

## Owned workflow

At a new human task, run `npeer notifications open --json`. Immediately before the final task report, run `npeer notifications close --json`, confirm `ok:true` with `data.state:"QUIET"`, then write the useful final report last. Do not close when you are only returning to wait for a pending worker or reply. Late notices, replies, acknowledgments and completions are retained; do not answer them with another user-facing acknowledgment. There is no other user ceremony.

This applies to interactive sessions only. Owned workers report their task normally, and their controller owns completions and follow-ups. A worker's `open` or `close` is refused with `APPROVAL_BLOCKED`, though `status` stays readable. Owned-worker MCP facades do not list `peer_notifications`. Controller-routed follow-ups to owned workers ignore any stale policy row, while expiry and ownership checks still apply.

Bound MCP callers use `peer_notifications` with the required `action` of `open`, `close` or `status`. `npeer notifications status --json` and `npeer whoami --json` (`notification_policy`) report `state`, `configured`, `message_cutoff`, `route_cutoff`, `closed_at`, `opened_at` and `written_by_incarnation`. Every action applies only to the verified calling peer, never to a chosen target.

## Rules

- Unconfigured peers are `OPEN`.
- The first close, or a close from `OPEN`, records high-water cutoffs for journal messages and completion routes. A repeated close while already `QUIET` changes nothing, so it never absorbs a newer request.
- Open keeps the cutoffs. Exchanges and completion phases that existed at close stay quiet after reopening, including a phase whose completion message is produced later.
- While `QUIET`, only an unexpired new root request passes: `kind:"request"`, no `in_reply_to`, in an exchange created after the cutoff. A reply that itself requests a reply belongs to its old exchange.
- Expired input is never admitted, whether or not the peer ever closed.
- State is keyed by the stable peer key, so a resumed incarnation of the same native session keeps its cutoffs. Only the verified current incarnation may change it.
- Opening never replays retained messages.

## Live owned-worker follow-ups

Each committed follow-up has a separate task and completion phase. A message from the owning parent requests push when `expect_reply` is true or the group is push mode; otherwise its phase is wait mode. Existing mirror delivery ownership remains intact. Send/status `followup` names the task, phase, mode, current terminal/active state and exact collection commands. Notification records expose `push_configured`, so passive parent-route readiness never implies every phase is configured to push. A task-specific wait claims only its singleton follow-up phase; initial/batch and accepted/uncertain completions keep their existing attribution. Quiet retention applies to the newly created phase as usual, and original input/accepted receipts remain unchanged.

## Dispositions

`Router.admit` applies the policy under the recipient admission lock before any adapter side effect. Input that was definitely never submitted gets the `RETAINED` state and a receipt with `stage:"RETAINED"`, `evidence_kind:"notification-policy"` and `details.reason`, which is one of `expired`, `exchange-closed`, `completion-phase-closed` or `quiet-informational`. A retained message is never submitted later, whether by a same-ID retry, reopening, or an inbox, status or result read. Send and reply report it as a successful retained disposition, not a delivery error. Policy-retained and guard-suppressed requests do not count toward the pending-request cap. Held, rejected and ordinary uncertain requests still count. A retained controller completion leaves its route `RETAINED` and releases its definitely unsubmitted live-verification reservation, so it consumes no submitted turn. Accepted and uncertain reservations stay reserved, and genuine delivery failures keep their existing zero-turn settlement.

Accepted or uncertain transport state is never rewritten. A frame already accepted into a native queue is handled at consumption (below). That writes a separate `notification-suppressed` event, which `status` shows as `suppression` (reason, attempt state, target incarnation and observed incarnation), and never claims `INGESTED` or model processing. Group status and results show it as `delivery.suppression` next to the unchanged route and transport states.

`npeer inbox --retained --json` (with `--unread`, `--page`, `--limit` and `--cursor`; MCP `peer_inbox` with `retained:true`) reads only bodies this policy retained or suppressed for the verified recipient. It never reads native-held, rejected or ordinary uncertain bodies. Its cursors are not valid for ordinary inbox reads, and reads add no receipts. Ordinary inbox and status visibility are unchanged.

## Consumption guard and provider coverage

The owned guards act only on a prompt that exactly equals the journal's rendered NativePeers frame, addressed to this exact native recipient. Matching includes provider, selected home, native UUID and current owner PID/start. The lookup ID comes only from the final references section, and the whole prompt must equal the stored render. Inside its transaction the policy guard rechecks the current live owner and incarnation. Suppression records a separate event without changing the accepted transport receipt.

Managed Codex runs the trusted `QueuedInput` hook before starting a queued turn, compaction, prompt telemetry or history. A strict `{"decision":"consume"}` consumes that queue item through the native durable deletion path. No turn, user item, hook lifecycle row, assistant output or work footer is created. FIFO dispatch continues to the next item; an explicit selected start returns `{"turn":null}` for consumption. Only successful synchronous queue handlers can consume; ordinary block/stop output, invalid JSON, errors, disabled or untrusted handlers cannot silently discard input. Ordinary `UserPromptSubmit` hooks and genuine block diagnostics retain their normal behavior.

A cold exact-session resume can receive queued input before its first turn's `SessionStart`. The native `QueuedInput` observation may register a previously unseen owner only after authenticating its terminal, executable ancestry and native history header. The registration transaction refuses a previously observed writer and a conflicting live owner. It preserves the stable peer's existing cutoffs. The policy guard itself never registers or rebinds an owner.

A registered Codex owner keeps its receiving guard when managed current changes to another immutable executable. The guard authenticates the exact recorded live owner, native UUID and home, and refuses to cross a nearer native process. This lookup only filters received input; new registration and execution approval still require their normal current-runtime checks. A CLI without `QueuedInput` can block a late frame visibly. Exact-session resume on the qualified managed package supplies silent native consumption.

Claude uses the owned `UserPromptSubmit` block and `hookSpecificOutput.suppressOriginalPrompt:true`, which keeps the blocked body out of its block message. Its native block notice remains provider behavior; Codex's queue-only silent-consumption qualification does not establish a Claude visible-tail guarantee.
- Any other prompt, including quoted or forged frames, passes unchanged and reopens a quiet peer without clearing cutoffs. An allowed fresh root request also reopens it. A generic `UserPromptSubmit` is not proof of human origin: Claude documents it for cross-session messages and scheduled or background turns too. Running `notifications open` explicitly at a new human task remains the reliable protocol.
- Another recipient's frame neither blocks nor reopens.
- A hook event with a missing or non-string prompt does nothing: no decision and no reopen.
- Hook errors never block human input.
- If the recipient lock is not available within one second, the decision still runs inside the immediate journal transaction and records `recipient_lock:false`.

Grok `PreToolUse` and Antigravity `PreInvocation` filter pending frames before emission, and filtered frames are never marked as output. A top-level Grok prompt reopens the peer; Antigravity hook activity never reopens it. Neither hook inbox can wake an idle session; an explicit `notifications open` is always available.

| Path | Source support | Native qualification |
|------|----------------|----------------------|
| Claude interactive, native inbox | Pre-submission retention; owned `UserPromptSubmit` guard with `suppressOriginalPrompt` | The live guard was checked on one SessionStart-registered interactive TUI (below). Its blocks passed with no assistant turn; the original run is recorded FAIL only for an early transcript flush. [Claude's hooks documentation](https://code.claude.com/docs/en/hooks#userpromptsubmit) says `UserPromptSubmit` fires for cross-session messages to main conversations. |
| Codex standalone, native queue | Pre-submission retention; trusted native `QueuedInput` consumption before turn startup; exact-owner prompt guard | Issue #7 installed qualification PASS: 44 mixed queued frames across warm drain and cold exact-session resume, reopen and permitted controls; no late chat/hook/work rows or model requests. See [current verification](VERIFICATION.md). |
| Grok hook inbox | Retention; `PreToolUse` filter; prompt reopen | Not qualified; no Grok live turn ran for this change |
| Antigravity hook inbox | Retention; `PreInvocation` filter | The current executable has a preexisting `CAPABILITY_MISMATCH` and is not reapproved here. |
| Controller completions | Route `RETAINED`; unsubmitted reservation released; accepted frames consumed silently | PASS: three genuine Codex worker/controller phases independently qualify OPEN push, QUIET retention and accepted-before-close consumption. |

A configured hook definition does not prove that a running native process has loaded it. Codex also requires installer-recorded native hook trust, and readiness reports the guard as `passive-configuration-only` with `live_verified:false`. The match is exact: if a native transport ever changed the frame text, the prompt would be treated as human input and pass.

## Verification

Current issue #7 installed source, native/TUI, genuine worker and rollback results are in [verification](VERIFICATION.md) and [the scoped record](../evidence/issue-7-silent-queue.json). The following dated checks retain their original scope.

### October 26 historical qualification

Source tests cover the stable-peer policy, receiving guards, provider hook filters, owned installers and schema upgrade. The final repaired worktree suite passed **764 tests and 742 subtests**, with four Linux-only skips, in **250.98 seconds**; the final main suite passed the same counts in **272.25 seconds**. The final upgrade review and parent focused run each passed 40 tests, including seven upgrade regressions. Earlier 757-test passes, the first run's two stale publication-fingerprint failures, and the baseline usage-source timeout plus isolated pass remain separately recorded in `evidence/quiet-notifications-20261026.json`.

The actual installed close check found a known empty obsolete policy table keyed by peer and incarnation. Startup now atomically repairs only that exact empty layout; populated or unfamiliar layouts are preserved and refused, and read-only access never migrates. Actual installed close, identical repeated close and reopen with retained cutoffs pass. The original installed failure remains in the record.

The parent ran a native receiver seam probe on the immutable Codex 0.164.0 executable (SHA-256 `3998a0e51bb7e4a2c8da905e9ba2f9e149c81ae44c487de59cf1e7296c04dadc`). `thread/queue/add` dispatched automatically. `decision:block` and `continue:false` each produced zero model requests and no assistant output, and a fresh allowed input produced one model request. The run made two localhost Responses fixture requests in total and sent no external model input. Blocked bodies were absent from requests and history, and the owned app-server exited. The topology was a private loaded app-server, not interactive TUI acceptance, so this proves only the native blocking seam. Raw output stays ignored under `evidence/scratch/quiet-notifications-20261026/native-seam/`. No runtime approval changed.

The parent then ran the actual shared guard in the owned native `UserPromptSubmit` wrapper on the same executable:

- An informational frame accepted into the queue before close was blocked after close, and its accepted transport state and receipts were unchanged.
- Expired input, and an old reply arriving after reopen, were also blocked.
- A fresh root request and human input still produced output.
- The run made five localhost model requests and sent no external model input.
- The native UUID and owner PID/start matched the registered provisional fixture owner.

This used an operator-seeded receiver on a loaded private app-server; it is not normal standalone-TUI identity qualification or UI acceptance. Raw output stays ignored under `evidence/scratch/quiet-notifications-20261026/native-policy/`. The actual-policy run passed again on the final source, with 5 localhost requests and no external input. Successful Codex probes made 17 localhost requests in total (seam 2, policy 5, final policy 5, repaired-source policy 5). Earlier failed diagnostic runs are recorded separately, and there is no cumulative full-session count.

The parent ran a live Claude check on a genuinely SessionStart-registered interactive TUI, through the actual `hooks.observe`, with the exact frame, native UUID and owner PID/start matching:

- An accepted-before-close quiet frame and an expired frame were both blocked with no assistant turn and no `Stop`, and transport state and receipts were unchanged.
- After an explicit reopen, one allowed control produced one native `Stop` with the expected final message.
- The original result, `native-claude/result-20261026T153735.json`, stays **FAIL**, only because the transcript text had not flushed by `Stop`. The later read-only collection, `native-claude/collection-20261026T155315-27c35705.json`, passed: the exact native transcript contains that text once.
- The run used normal startup and authentication, with per-launch `dontAsk` and cross-session inbound acceptance disclosed, and no global configuration change.
- Claude model request counts were not instrumented.

## Prior installation qualification

The October26 installation qualified: the namespaced Claude and Codex `UserPromptSubmit` guards, native Codex trust for the third owned hook, owned skill updates for Claude, Codex and Antigravity, and the exact shared instruction block. Repeat installs (default and Antigravity) perform zero operations, the Grok install plan is empty, and an uninstall dry-run names only owned entries. `runtime.json` bytes are unchanged (sha256 `e05c92e9457f32dc2847db24d4b3fa4653748800c201a935b2b51a7093ccbb86`). Non-owned settings and trust are semantically unchanged, `daemon_auto_start=false` is kept, and all Grok files are unchanged. Bytes outside the shared block are exact (sha256 `e9aea8e67b5e81201a9e79e88a5643704a5e8de0a3e95a054fcb09baf4163c83`), and the protected `fix/review-2885497` branch is unchanged. An installed guard is a passive definition, not proof that an already running process has loaded it; a normal resume may be needed.

## Review dispositions

All independent review findings are fixed with source regressions; none were waived. An independent reviewer confirmed R1–R4 fixed (58 targeted tests, 89 subtests), then R1–R6 fixed (183 tests, 195 subtests in the integration tree). The final recheck approved R1–R7 (75 tests, 15 subtests); the journal correction passed an independent 40-test review:

- **R1 budget, fixed.** Quiet-retained completions consumed a turn. They now call `release_unsubmitted` instead of a zero-turn settle; the regression uses a real cap-1 ledger.
- **R2 metadata ID lookup, fixed.** The footer ID could be hidden by body lines. Eight or more `Message:` body lines could push the footer ID out of the lookup. The ID now comes from the final references section.
- **R3 post-lock owner revalidation, fixed.** A stale owner could act after supersession. A superseded owner's prompt could reopen the resumed policy after the lock wait. The guard now revalidates the current owner inside its transaction.
- **R4 suppressed pending cap, fixed.** Suppressed requests held pending capacity. Requests suppressed at consumption still counted toward the pending cap; they are now excluded, unless the attempt is held or rejected. A malformed hook with a missing prompt is now a no-op.
- **Receipt schema contract, fixed.** `schemas/receipt.v1.json` now lists `RETAINED`. A test validates an actual policy-retained receipt and requires the enum to match every reducible stage.
- **R5 owned-worker lifecycle, fixed.** A worker could close its own policy and turn later parent follow-ups into `RETAINED`. Open and close now require an interactive peer, workers no longer see `peer_notifications`, and owned-worker admission ignores stale policy rows.
- **R6 completion diagnostics, fixed.** Group status and results dropped the guard's suppression for completion messages. They now include it as an optional `delivery.suppression`.
- **R7 Grok prompt reopen after supersession, fixed.** The Grok `UserPromptSubmit` reopen captured its peer before the lock wait. A hook superseded during that wait could reopen the resumed policy under the old incarnation. It now applies the shared guard's in-transaction current-owner rule before reopening.

- **R8 installed journal upgrade, fixed.** The known empty incarnation-keyed policy table is repaired atomically before writes. Populated and unknown layouts remain intact, read-only access never migrates, and close uses explicit columns. Seven regressions, the exact measured-DDL fixture and actual installed close/reopen pass.
