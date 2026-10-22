# Review-fix revalidation — 2026-10-21

All three critical review fixes passed revalidation against the current implementation. The Python production modules were unchanged during the retest. The offline suite passed **60 tests and 16 subtests**, and two fresh native integration cases passed. [Machine-readable live proof](../evidence/reviews/live-retest.json).

| Critical finding | Verification | Result |
|---|---|---|
| RV-01: stale install/uninstall merge could overwrite a concurrent edit | Deterministic concurrent edits during both operations; newer file preserved byte for byte with `OWNERSHIP_CONFLICT` | **PASS — offline** |
| RV-04: a task wait could consume the batch notification | Each real parent waited for task A; the batch route remained available; one aggregate reached and was reported by that original native parent | **PASS — native, both providers** |
| RV-05: task cancellation could interrupt unrelated accepted work | Queued canceled input never entered a native turn; initial work completed; a native-started followup was canceled while its queued successor survived and completed in the same native history | **PASS — native, both providers** |

The original [review](../evidence/reviews/2026-10-21.md) remains a historical pre-fix verdict. [Fix commits and validation](../evidence/review-fixes.json) record all ten resolved findings.

## Actual native conversations

| Parent | Original parent UUID | Independent worker UUIDs |
|---|---|---|
| Claude Code 2.1.307 | `56337388-5a29-44fb-aab7-78df616e7dbb` | Codex `01a160ea-188b-75d3-b076-6d4c1020e5b2`, `01a160ea-187d-78f1-b090-64afde37e04d` |
| Managed Codex 0.163.0 | `01a160e8-deb4-76a2-9f90-c3e41e78f85e` | Claude `1760c8ef-c1bc-4cec-88b7-1dda53a99c33`, `8f8889a1-067e-4d29-8a3a-f31f8b820b39` |

Both initial task intervals overlap within each pair. The original parent histories contain the actual tool invocation, matching native completion input, worker nonces/UUIDs and attributed response. This proves client-side native concurrency and original-parent return. Vendor GPU scheduling is not observed.

Both Codex workers requested and reported `gpt-6.1-sol`; the installed catalog and native submitted configuration used `xhigh`. The inference backend does not independently echo effort. Claude's existing `sonnet` default resolved to `claude-sonnet-5-5`. Native subscription authentication routes and homes were retained.

## Scope, policy and cleanup

Claude had updated independently from 2.1.306 to 2.1.307. Testing first used a provisional receipt in a new disposable NativePeers state. After successful native verification, only the NativePeers-owned production compatibility receipt was updated to admit that verified executable. Authentication integrity and routes match their private baseline. No provider settings were written or restored by the retest.

A subsequent concurrent edit added three `permissions.allow` entries in Claude settings at **8:11:40 PM EDT**, after both test parents' final responses. Removing only those entries in memory reproduces the exact pretest file hash, establishing that other settings and owned hooks were preserved. The originating process is not attributed. The current permissions were retained, along with normal native `.claude.json` cache changes. This later edit does not describe the permission set under which the recorded native tests ran. [Protected comparison](../evidence/reviews/retest-protected.json).

A new Claude fixture directory required the native folder-trust decision. That owned launch was stopped before model activity; the already trusted disposable fixture was then reused. The retest did not widen global trust, inbound policy or permission mode. The stopped launch remains conservatively counted against the test allowance.

The additional authorization permitted at most 16 slots: **13 consumed, 3 unused**. There were 12 observed additional model inputs plus the conservatively counted pre-model launch. The cumulative journal is **61/64 consumed, 0 reserved, 0 outstanding**. Active testing added **120.674 seconds**, for **876.950 seconds total** across the retained ledger. Provider waiting is included; offline preparation is excluded.

All four new worker controllers and both finite group supervisors exited. Both registered test parents and the pre-model launch were stopped using verified owned process identities. Exact Codex queue reads found no residual submissions. Histories and immutable results remain retained; unrelated processes were not signaled. [Cleanup](../evidence/reviews/retest-cleanup.json), [protected files](../evidence/reviews/retest-protected.json).

The retest uses [the bounded parent harness](../tests/live/review_fixes.py) and [the non-model native-history collector](../tests/live/review_collect.py). Launch actions submit real model input and require the recorded authorization and remaining budget; collection submits none.

This closes the review-critical revalidation. Complete original acceptance remains qualified on the remaining checks in [acceptance.json](../evidence/acceptance.json); unsupported or unrun variants are not promoted by these focused tests.
