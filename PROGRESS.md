# Spawn limits issue 3 — source change for review

- [x] Separate omitted-request defaults (1800 seconds / 8 logical inputs / 128 native rounds) from optional operator ceilings; retain existing policy keys as ceilings.
- [x] Remove the implicit group cutoff, reject an explicit shorter group ceiling before planning, and preserve requested limits through resume and fallback.
- [x] Expose effective limits and sources in spawn checks and doctor; identify configured ceiling errors and native-round exhaustion.
- [x] Document defaults, ceilings and all four providers' counter meanings; retain schema version 1 and validate every spawn example.
- [x] Pass the unchanged focused baseline: 56 tests / 62 subtests. Pass the candidate focused checks: 127 tests / 174 subtests, including the unchanged 30000 ms wait bound.
- [x] Reproduce and correct the independent Sol review finding: parent-loss shutdown records a JSON-safe unset deadline. Recheck lifecycle, profile, fallback, selected-home and supervisor behavior: 45 tests / 34 subtests.

The pull request records the final-head complete-suite result and independent-review dispositions. The installed-verification fingerprint checks remain unchanged and detect candidate source drift from the installed snapshot; evidence is not regenerated for this unmerged source change. Installed-runtime acceptance is not run. Main remains the installed runtime and is updated only after pull-request review and merge. Runtime-update approval interruptions are tracked separately in issue 4.

---

# Quiet close-out notifications — implemented, verified and published

- [x] Stable-peer close-out policy: `notifications open|close|status`, MCP `peer_notifications`, durable cutoffs, pre-submission `RETAINED`, and `inbox --retained`.
- [x] Consumption guard in the owned Claude/Codex `UserPromptSubmit` hook, Grok/Antigravity pending-frame filters, installer/readiness support, owned instructions and docs.
- [x] R1 budget, fixed: quiet-retained completions consumed a turn; they now release the unsubmitted reservation.
- [x] R2 metadata ID lookup, fixed: the footer ID now comes from the final references section.
- [x] R3 post-lock owner revalidation, fixed: a superseded owner can no longer change the resumed policy.
- [x] R4 suppressed pending cap, fixed: guard-suppressed requests no longer hold pending capacity.
- [x] R5 owned-worker lifecycle, fixed: only interactive peers can open or close, and owned-worker follow-ups ignore stale policy rows.
- [x] R6 completion diagnostics, fixed: group status and results include `delivery.suppression`.
- [x] R7 Grok prompt reopen, fixed: a Grok hook superseded during the lock wait no longer reopens the resumed policy.
- [x] Receipt schema now lists `RETAINED`.
- [x] Native Codex 0.164.0 blocking seam and actual guard integration passed on an operator-seeded private loaded app-server. This is not standalone-TUI qualification. The final-source policy rerun also passed, with 5 localhost requests.
- [x] Native Claude live guard blocks passed on a SessionStart-registered interactive TUI. The original result is FAIL only for an early transcript flush; the read-only supplemental collection passed.
- [x] First full integration run: 754 passed, 4 skipped, 742 subtests. Its 2 failures were stale generated fingerprint records; no product test failed.
- [x] Final review approved R1–R7 (75 tests, 15 subtests). The parent's final focused run passed 100 tests and 57 subtests.
- [x] Final repaired worktree suite: 764 passed, 4 Linux-only skips, 742 subtests, 250.98s; earlier source checks are retained separately.
- [x] Feature source `d999a58` published; record commit `1907b5e` fast-forwarded local main.
- [x] Installed the Claude/Codex guards, native Codex trust for the third hook, owned skills and the shared instruction block. Repeat installs perform zero operations, and runtime approvals, non-owned settings, Grok files and protected state are unchanged.
- [x] Final repaired main suite: 764 tests and 742 subtests, four Linux-only skips, 272.25s. Verified source is published on main.
- [x] R8 journal upgrade: repair only the known empty obsolete table; preserve/refuse populated or unknown layouts. Seven regressions, independent 40-test review and actual installed close/repeated-close/reopen pass. Original failures remain recorded.
- [x] Remove the integration worktree and local/remote feature branch; archive and retire both owned worker worktrees with verified recovery copies. Preserve native histories and protected state.

No finding was waived. The summary record is evidence/quiet-notifications-20261026.json, and details are in docs/NOTIFICATIONS.md; raw reports and probe output stay in ignored evidence/scratch/quiet-notifications-20261026/.

# Grok Build provider — integrated and published

- [x] Implement fourth-provider identity, trusted hook inbox, fixed worker profiles, exact executable/model attribution, resume/cancel and owned-turn usage.
- [x] Address both GPT-6.1 Sol reviews, independent Claude Opus review and follow-up; retain verbatim findings and dispositions.
- [x] Complete the recorded disposable live checks and installer/global-control/Unicode-path acceptance.
- [x] Pass complete worktree suite: 721 tests / 740 subtests, four Linux-only skips, 364.96 seconds.
- [x] Clear the config-preservation hold through explicit finalization authorization, preserving the current config and independently updated Codex approval.
- [x] Fast-forward main, install the owned Grok integration and shared block, and pass the complete main suite: 721 tests / 740 subtests, four skips, 273.96 seconds.
- [x] Push main, remove the merged feature worktree and local/remote branch, prune, and retain verified recovery content with reasons.

The original qualification record is **PARTIAL**: historical live input is 28/24, the complete initial history/authentication inventory is missing, Grok→Codex live acceptance is unrun, and Antigravity live messaging is blocked by its existing runtime mismatch. These limits remain in [the record](evidence/grok-20261026.json). The current merge/push/cleanup request is complete; no further live input is part of this run.

---

# Open issues 1 and 2 — complete

- [x] Expose parent push readiness separately from worker/quota/MCP readiness across CLI/MCP preflight, spawn, wait, status/result and caller identity.
- [x] Retain exact per-generation delivery failure and later native receipt details without replay.
- [x] Restore read-only CLI context access, live WAL reader grants and exact worker shell binding; preserve native sandbox denial.
- [x] Document/reject waits above 30000 ms and name offending worker budget fields, requests and ceilings.
- [x] Qualify Claude 2.1.310 worker/inbox paths, verify signature and original-parent ingestion, and perform a guarded Claude-only approval transition.
- [x] Complete two independent Opus 5.5 reviews, remediate findings and recheck final notes.
- [x] Stable broader suite: 641 tests / 656 subtests, four Linux-only skips, zero failures. Three publication-pointer tests and seven final focused completion tests pass separately.
- [x] Integrate/publish product commit e5c1aa3463dae6cea296f34c6d2924afed3916ba; retain native histories and verified recovery archives for the two finished implementation lanes.

Current record: [evidence/open-issues-20261023.json](evidence/open-issues-20261023.json). Claude quota and universal bound MCP readiness remain separate; unrelated runtime approvals and sessions are preserved.

---

# Review ce4a4ac corrections

- [x] C01 Preserve split-index recovery and verify reconstruction before retirement (31 focused tests).
- [x] C03 Bound control-socket replies and finite supervisor shutdown (61 Mac tests / 31 subtests; 4 Linux socket tests; committed-reply retry control).
- [x] C02 Separate later process inspection from executable failure (41 Mac focused tests; 7 Linux tests including all 4 Mac-skipped Linux schedules).
- [x] Complete suite: 617 tests / 620 subtests, 4 Linux-only skips in 357.39 seconds; all skipped gates pass separately on Linux; integrated publication and session cleanup.

---

# Review 1211303 corrections

- [x] Preserve legitimate Git configuration during inspection and normalization (55 focused tests / 38 subtests).
- [x] Preserve retained Claude layout during batch admission and recommendations (89 focused tests / 39 subtests).
- [x] Force captured and uncaptured inherited-pipe cleanup schedules (5 focused tests / 4 subtests).
- [x] Complete offline suite: 606 tests / 620 subtests, zero failures/skips, 300.36 seconds; integrated main, scoped publication, review ZIP/prompt and session cleanup.

---

# Review 2885497 corrections

- [x] F01 Isolate every Git mutation from ambient repository/index/object routing.
- [x] F02 Bound control delivery and close leases independently of pipe progress.
- [x] F03 Retain controller ownership immediately after successful creation.
- [x] F04 Apply recipient visibility to status, inbox and context references.
- [x] F05 Initialize runtime approvals atomically without replacing concurrent approval.
- [x] F06/F09 Share authoritative model-scoped quota supersession.
- [x] F07 Preserve selected Claude layout through general usage lookup.
- [x] F08 Bound catalog descendants and output cleanup.
- [x] F10 Enforce one combined frozen-input byte budget.
- [x] Fix isolated-copy identity and sibling-order test prerequisites.
- [x] Full verification: 568 tests / 619 subtests; native metadata checks and zero-operation installed plans; review publication isolated from concurrent main evidence; session worktree cleanup.

---

# Review 2551947 corrections

- [x] R01 Pin every observed Claude model, including custom IDs.
- [x] R02 Track app-server descendants and prove pipe EOF independently.
- [x] R03 Use birth-based owned liveness and positive generation closure.
- [x] R04 Remap active local quota demand during admission.
- [x] R05 Preserve quota proof measurement time through cached/suppressed paths.
- [x] R06 Resolve current home labels consistently after policy changes.
- [x] R07 Retain Claude layout intent for registry discovery.
- [x] R08 Require compatible forecast window duration/type.
- [x] R09 Preserve Antigravity approvals during integration maintenance.
- [x] R10 Assess exact models independently of display limits.
- [x] R11 Reconcile refusals using the exact pool measurement clock.
- [x] H01/H02 Verify owned usage layout and usage-transport birth safety.
- [x] H03/H04 Inspect planner closure and group cleanup dispositions.
- [x] H05/H06 Verify missing bindings and shared alias equivalence.
- [x] H07/H08 Measure and bound preparation/repeated local reads.
- [x] S01/S02 Explain resume blockers and exact pre-input selection.
- [x] S03 Generate an operation-scoped evidence index with full file-class coverage.
- [x] S04 Bound isolated-input preparation and report selected size.
- [x] S05 Complete missing-binding diagnostics.
- [x] S06 Benchmark repeated reads and apply only supported optimizations.
- [x] Correct stale budget and historical summary wording.
- [x] Full tests, scoped native checks, conventions, publication and cleanup.

---

# Review ff8c8c2 corrections — complete

Source: independent review of ff8c8c20770e79317d807336c2ad6258889984ef. All findings and suggestions are in scope; native acceptance remains operation-specific.

- [x] F01 Preserve implicit and explicit Claude configuration layouts.
- [x] F02 Retain descendant ownership through bounded worker cleanup.
- [x] F03 Scope and reconcile group quota suppression.
- [x] F04 Verify the first Claude alias family.
- [x] F05 Reconcile pool demand atomically before task input.
- [x] F06 Preserve authoritative terminal error precedence.
- [x] F07 Classify Antigravity discovery/install failures accurately.
- [x] F08 Preserve passive measurement clocks per pool.
- [x] F09 Retain definite logical-task budget failures.
- [x] F10 Reject invalid account-pace forecast samples.
- [x] F11 Share canonical home-label resolution.
- [x] F12 Preserve independently approved runtime receipts.
- [x] F13 Require exact evidence for native pool membership.
- [x] S01 Show operation-specific readiness in doctor.
- [x] S02 Explain incomplete caller binding precisely.
- [x] S03 Expose remaining logical task slots.
- [x] S04 Generate and validate current evidence pointers.
- [x] S05 Coalesce quota-reader throttles with bounded retry hints.
- [x] S06 Preflight isolated-copy Git identity.
- [x] Full suite 483/574, manager fixtures 23, native CLI/MCP quota parity, exact-history Flash review delivery, owned cleanup, installed preservation and conventions. Publication follows the generated current source pointer.

---

# NativePeers progress

Live usage-source implementation and installation are complete at product commit `58b6a4b627cec652c785d787996c79d5ef776122`. Final offline verification: **460 tests / 556 subtests PASS**; external manager/naming fixtures: **23 PASS**. Actual native CLI quota reads pass for Claude, Codex and Antigravity with zero model inputs.

- [x] Terminal provider errors, source/auth separation and synthetic stream precedence.
- [x] Active acquisition generations/TTL/coalescing, clocks, exact-scope refusal reconciliation and quota units.
- [x] Native Antigravity usage/credits and exact-build freshness proof.
- [x] Authorized native Claude usage-only PTY, normal config layout, modern terminal parsing, exact no-history/zero-token checks.
- [x] Provider/home admission refresh, shared deadlines, sparse pool preservation and shutdown/readiness race corrections.
- [x] CLI/MCP schemas, owned skills/global instructions, source/runtime publication and protected-environment checks.

Known verification scope: real stdio MCP probe is CALLER_NOT_READY; offline protocol/caller parity passes. Full native inference/messaging/delegation matrix is not reaccepted by quota reads. No model input or budget reset occurred. Worker-history visibility and the separate Pro review remain independent.

Details: docs/LIVE_USAGE_SOURCES.md and evidence/live-usage-sources.json. Private baseline/captures: evidence/private/live-usage/. Backups: backups/live-usage/. No worktree or native worker team was created; regenerable large binary indexes were removed.
