# Second independent review: corrections and added capabilities

The reviewed snapshot was `f81f5c897fed3de5bbb90bd0c1e8ae0183eed81a`. Every reported defect, test/evidence correction, hypothesis and proposed convenience has a disposition below. The current product source is recorded in [pro-followup-review.json](../evidence/pro-followup-review.json), including all 42 Python source hashes and installed executable fingerprints. Canonical contract files remain unchanged.

**Implementation complete; verification qualified.** The final offline suite passes **268 tests and 388 subtests**: 203 unit tests and 65 contract tests. The existing CodexMCP manager/naming fixtures pass **23 tests**. A bounded current-source native batch passes original-parent returns and real MCP calls using Sol/xhigh and Gemini 3.8 Flash Medium. This does not certify the complete all-provider acceptance matrix or unattended operation across every variant.

## Every review item

| Item | Resolution | Verification |
|---|---|---|
| F1 — delayed cancellation targets a resumed generation | Capture the child before asynchronous cancellation, scope polling by generation, and release only the expected child. | Real controllers/fake provider: hold old cleanup, release generation 1, resume generation 2, unblock old cleanup, and require generation 2 to complete. |
| F2 — inherited bootstrap identifies an outer native worker | Ambient binding verifies the current generation's controller and recorded nearest native process. Owned hooks verify provider/UUID/process. Native-caller MCP remains ambient; explicit fixed bindings remain supported. | Compiled native-shaped processes through actual CLI/MCP and hooks, including multiple nested layers, same-provider nesting and a new serialized-turn PID. Fresh Gemini and Sol worker MCP calls also pass. |
| F3 — group wait leaves older phases armed | A group wait claims every unsubmitted phase included in the result and extends its scope while suspended. Timeout, disconnect and cancellation release all of that waiter's claims before output. | Terminal CLI/MCP and live-supervisor waits, phase addition during wait, notification suppression, timeout and real EOF. Initial task-specific reads still preserve batch notifications. |
| F4 — established wait EOF loses retained fallback | Normalize connection loss to structured supervisor loss. Only the read-only wait consults terminal state and notification ownership. Mutations are not retried. | Real sockets close after terminal commit, including abort and partial frames, through CLI/MCP. Running tasks retain a structured failure. |
| F5 — mixed startup failure holds healthy writers/capacity | Release every started generation-matched child at a failed pre-admission barrier; free capacity only after writer death is proved. | Ready provider plus failing preflight: no task submissions, writers ended, immediate corrected startup, unrelated process preserved. |
| F6 — installer helper uses another home | Pin helper `CODEX_HOME` to the configuration directory being edited and return structured missing-layer errors. | Real AppServer transport/fake executable through install and uninstall with conflicting homes; actual installed updates and idempotent second installs also pass. |
| F7 — Claude `[1m]` disappears | Separate the underlying observed model from the resolved CLI launch selection. Retain requested context modifiers across serialized turns and both resume forms, including legacy leases. | Full-ID/alias and base/suffixed echoes, second turns, manual/automated resumes and genuine mismatches. Fresh Claude inference remains BLOCKED by the reported usage limit. |
| T1 — Git-filter fault injection misses its target | Recognize operand-free `write-tree` after Git options and require exactly one injected failure. | Delta suite passes with ambient configuration and a temporary added global filter; copy/delta fixtures also pass with and without temporary filters. |
| E1 — checkpoint mixes stale current summaries | One current verification record and budget fingerprint; old checkpoints/results are linked as historical records. | Regression checks current test summaries, canonical hashes and the retained live ledger against their referenced files. |
| H1 — effective Gemini MCP availability | Inspect owned global/workspace definitions before readiness; reject missing, disabled, restricted, malformed or conflicting definitions. Preserve explicit uncertainty about native precedence. | Installed passive listing and temporary-home `disabled:true` behavior; fake startup rejects before task admission. Current native Gemini worker successfully calls the owned MCP facade. Conflicting-workspace agent-turn precedence remains unverified. |
| S1 — retained delegation discovery | `groups --mine` returns this verified parent's retained groups, worker titles/IDs, states and result counts with scoped stable pagination. | Parent filtering, completed groups, insertion during paging, invalid cursors and unchanged notification ownership. |
| S2 — artifact handoff | `artifact-export` uses existing grants/pages, verifies the digest and exclusively publishes a complete new file. Worker-delta base metadata is returned when recorded. | Binary/multi-page output, existing file/symlink preservation, interrupted/corrupt export, worker write scope and real worker-result metadata. |
| S3 — MCP inbox parity/continuation | Shared unread selection and stable cursor pages; plain list responses remain compatible. | Older unread messages behind 110 acknowledgments, equal timestamps, new inserts, scoped/invalid cursors, CLI/MCP parity and body withholding. Fresh native MCP unread/paged calls pass. |
| S4 — passive owned readiness | Doctor reports selected homes, hooks, skills, profiles, entrypoint, Codex trust and MCP definitions without repair or inference. | Missing/modified/disabled fixtures with launch/write blockers; actual installed checks PRESENT. Native effective precedence is explicitly unknown. |
| S5 — idempotent resume | Durable caller/request-ID record commits atomically with task reservation. Exact retries retrieve the original task before mutable checks and never replay input. | Lost/uncertain response, concurrent retries, completed tasks, journal reopen, changed-content conflict, additive existing-journal upgrade and CLI forwarding. |
| C1 — non-Git implementation and missing document | Explicit `isolated-copy` with selected file allowlists, frozen bytes/modes/digests, 256-file/10-MiB batch bounds and project-local owned copies; source remains outside Git. Added [implementation worker documentation](IMPLEMENTATION_WORKERS.md). | Safe/unsafe inputs, changed source, bounds, raw binary bytes, hooks/filters, patch reconstruction and public spawn through two real controllers/fake editing providers. Provider inference for copy mode is NOT_RUN. |

The report's already-implemented capability families—exact routing, retries, separate histories, frozen Git inputs, both patch views, context grants and attributed structured results—remain in place. They were not replaced or claimed as new features.

## Additional findings from cross-checking

Three neighboring defects were found and fixed during this work:

- **F2B:** MCP discovery could race native process recording. Reserved discovery/status now stays nonmutating before the PID is available. Actual `Worker.claude_turn()` interleavings with compiled native processes prove inactive startup, refusal of mutation, correct later binding and nested-owner rejection.
- **F4B:** A nonempty truncated control frame could still raise a JSON error. Responses now require their newline terminator; retained wait recovery covers a partially flushed response through both public interfaces.
- **S3B:** Cursor integers could exceed SQLite's range and escape the response envelope. Ceiling/position validation rejects oversized and nonfinite values before queries. Group first-page queries avoid an overflowing synthetic upper position.

Each focused correction was verified, committed and pushed separately. The complete suite was rerun after the final product changes.

## Current native observations

Five submitted inputs produced two original parent conversations and three independent children. The Codex parent launched Sol and Flash together; their native task/result event intervals overlap by **10.130 seconds**. Native event records establish client-side overlap, not vendor GPU scheduling.

| Parent | Original UUID | Children and result |
|---|---|---|
| Managed Codex / Sol | `01a1642d-929b-7343-9a2b-37911b79fbe4` | Sol `01a1642d-b6c5-71f2-93ee-2e5be156fbf5`; Flash `e62a4f92-3b15-44cf-8d6a-e73fc6a935e9`. Both returned attributed results to that same parent. |
| Antigravity / Flash | `b59c232f-d769-49a5-8efd-5aa024d6d399` | Sol `01a1642d-bbb9-7503-80a7-0f071ddf5580` returned its result to that same parent. |

For every child, the exact native history records successful bound `task_status` and unread/paged `peer_inbox` calls. Both parent histories record the actual tool invocation and final output containing their child UUIDs. Retained group wait claims are observed; no automatic completion push was requested in this batch. Requested/observed models are `gpt-6.1-sol` and `gemini-3.8-flash-medium`; Sol requests `xhigh`, which is not independently echoed by inference. Provider-internal tool rounds are separate from top-level input accounting and are recorded where exposed.

Executable versions remain Codex **0.163.0**, Claude **2.1.308** and Antigravity **1.4.5**. Native ChatGPT, claude.ai Max and Google sign-in routes remain unchanged. No API key substitution, credential extraction, global inbound-policy change, shared daemon, package activation or Rust inbox patch was introduced. The native queue remains the selected transport based on its separately fingerprinted prior checks; this batch did not rerun queue push.

The cumulative ledger is **148/148 submitted inputs, 0 reservations, 3524.394974/3600 active seconds**, with no outstanding requests. This batch adds five inputs and 66.342 active seconds to the retained allowance. All owned test parents, supervisors, controllers and provider writers exited. Histories and their fixture cwd are retained for safe exact resume; no paid retry was performed.

## Verification boundaries and remaining native acceptance

| Check | Status | Current scope |
|---|---|---|
| Unit/property and fake-provider contracts | PASS | 268 tests, 388 subtests; source fingerprints in the evidence record |
| External manager/naming fixtures | PASS | 23 tests, temporary update/activation fixtures; actual package unchanged |
| Installed passive configuration/installation | PASS | Expected executable hashes, owned definitions, idempotent installs, protected-file comparison and normal auth route |
| Current Sol/Flash parent/worker MCP and returns | PASS | Exact original parents and three distinct child histories, successful native tool results, concurrent mixed two-worker group |
| Fresh Claude inference / `[1m]` entitlement or context capacity | BLOCKED | Reported provider usage limit; suffix preservation itself is proven offline |
| Non-Git copy-mode native inference | NOT_RUN | Real controllers/fake provider and patch reconstruction pass; no native writing turn submitted for this new mode |
| Fresh native cancellation/resume race and multi-phase push variants | NOT_RUN | Exact failure windows are proven by offline real-process/socket fixtures; native batch checks basic wait/return only |
| Dedicated Gemini sibling exchange and active-cancel acceptance | NOT_RUN | Controller/grant fixtures pass; not inferred from basic MCP success |
| Authenticated alternate homes | NOT_RUN | Real fake-provider home pinning and installer-home fixtures pass; existing authentication is preserved |
| Full original matrix | QUALIFIED | Remaining two-worker same-provider/current-Claude combinations, real duplicate registered titles, human-draft noninterference and literal external-host-unreachable variant remain separately unaccepted |

The fresh Codex result adds current evidence for a Codex-to-Codex child inside a mixed two-worker group. It does not substitute for both full same-provider two-worker combinations. Historical PASS fields retain their original source/runtime scopes.

Antigravity idle wakeup and automatic startup naming/picker opening remain native-contract limitations in CLI 1.4.5. Its inbox runs on the next normal invocation. Private Desktop/T3/IDE backends and remote hosts remain outside supported ownership scope; no typing injection or runtime takeover was added.

## Installed conventions, preservation and rollback

[USAGE.md](USAGE.md), [IMPLEMENTATION_WORKERS.md](IMPLEMENTATION_WORKERS.md), both namespaced skills and the NativePeers section of `~/.claude/CLAUDE.md` describe the new commands and when to use them. Bytes outside the global owned section are unchanged. The Claude, Codex and Antigravity skill copies match their sources; reinstalling twice is idempotent. Targeted recovery copies are private under `backups/pro-followup-20261022/`.

Protected native configuration, shell/YOLO behavior, `features.daemon_auto_start=false`, auth-file hashes and executable hashes match the task baseline. Provider histories were appended only by the authorized disposable tests and are retained. Additional concurrent CodexMCP documentation/smoke-harness edits were observed and preserved; their actor was not established. NativePeers did not write to that checkout or activate a package. The 23-test manager result covers its identified test sources, not an independent execution of the concurrent smoke change.

To remove integration, inspect `npeer uninstall --dry-run --json` and `npeer uninstall --antigravity --dry-run --json`, then use the corresponding owned-block uninstall. Later unrelated edits are preserved. Remove only the NativePeers marker block from global `CLAUDE.md`, or restore its saved block while preserving all outside bytes. The official provider executables, accounts and histories remain. Reverting product fixes is an explicit Git operation; never restore a historic global configuration or manager package to undo NativePeers.
