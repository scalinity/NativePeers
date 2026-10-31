# Issue 7 — installed and qualified (2026-10-30)

- [x] Silent native queue disposition before model, history or visible work rows; strict exact-recipient policy and separate suppression retained.
- [x] Registered-owner guard survives managed selection changes without approving an old runtime for new work.
- [x] Normal source PRs merged, candidate assembled through 22 package gates, guarded activation and supported four-hook reinstall.
- [x] Fresh installed standalone TUI 44-frame warm/cold drain, reopen and exact resume; genuine OPEN/QUIET/accepted-before-close native worker phases.
- [x] Complete installed suite: 1119 tests / 1056 subtests, 4 skips; bounded independent Codex rechecks have no remaining findings.

Exact source/package fingerprints, failed attempts, commands, limits and rollback are in [verification](docs/VERIFICATION.md) and [the public scoped record](evidence/issue-7-silent-queue.json). External paid-model and other-provider UI checks remain NOT_RUN for this change.

---

# Evaluation backlog — installed acceptance recorded; full-score goal open (2026-10-30)

- [x] Integrate separate logical-input/native-round/wall budgets; retain deliberate failed attempts and later phases with their actual counters.
- [x] Close issues #3, #10, #11, #12, #13 and #14 with scoped installed/native checks.
- [x] Complete useful native Claude packet reviews, reproduce and fix the absent completed-result marker in PR #24, and independently recheck the patch.
- [x] Qualify installed compact CLI/bound-MCP reads and the installed bounded-review recipe without Bash escalation or global permission changes.
- [x] Consume issue #7's installed qualification: 44 explicitly framed standalone controls and three separate genuine worker/controller phases, with retained results and visible-tail checks.
- [x] Complete the real selected-path/runtime peer exchange; distinguish admission, native ingestion, model-reported processing and actual disjoint integration.
- [x] Append the fixed-rubric evaluation: **94.5/100 with 100% coverage**. Preserve the historical **64.5/100** baseline and prior partial records.
- [ ] Resolve [#28's live-worker follow-up delivery gap](https://github.com/scalinity/NativePeers/issues/28) and earn the remaining full-score anchors through actual work; [tracker #16](https://github.com/scalinity/NativePeers/issues/16) remains open.

[Installed acceptance and operation scopes](evidence/np-eval-acceptance.json) · [Issue #7 qualification](evidence/issue-7-silent-queue.json). The local evaluation report is `np-eval-2026-10-30-nativepeers-acceptance-01a18f75`; private history and native transcripts are not published. Source-suite results retain their exact source, and localhost routing/UI fixtures do not establish external paid inference. The remaining score deductions cover follow-up delivery, optional report-format recovery and incomplete measurement of total orchestration cost.

---

# NativePeers Lineage for Claude Code (in progress, 2026-10-30)

Contract: docs/CLAUDE_LINEAGE_MOD.md rev 1.0 (sha256 e9e03790…40b9) · kickoff docs/CLAUDE_LINEAGE_KICKOFF.md rev 1.0 (sha256 af4724f4…b769). Branch `feat/claude-lineage-mod` in `.worktrees/claude-lineage-mod`, base `0ee014f`. Claude Code 2.1.314 (sha256 21d6e7b9…32ed).

- [ ] M0: tripwire, X1–X14 and gates G0–G13 (`evidence/claude-lineage-m0.json`); minimum engine 2.1.309 in `compat/claude-mod.json`.
  - [x] G0 tripwire PASS: a deliberate fallthrough sent 6 refused requests to the stub, cost 0. Under a temporary HOME no credential resolves on this Mac, so harness runs use a dummy key; the tripwire count, not `duration_api_ms`, is authoritative.
  - [x] X1–X14 reproduced locally (L), with deltas: a CPU-bound hook spin unloads the plugin and falls through despite `.catch`; `$.agent.list()` has no `spawnedBy`; follow-up framing has a suffix.
  - [x] G1–G6, G8–G11, G13 decided. Shadows run on a model id no API serves, the only supported way found to keep the engine's agent summaries from billing them; Esc and `x` both kill a shadow; spawns only from hook frames; silent steps abort at 600 s; registry mirrored to `$.store` for `/clear` and `--resume`.
  - [ ] G7 desktop: engine 2.1.309 recorded and headless-verified; rendering and interaction need a person at the desktop app.
  - [ ] G12 for the production mod (validate and `tsc -p`).
- [ ] M1: activity recorder, Codex/Claude normalizers, `activity_index`, `npeer activity`.
- [ ] M2: `npeer bridge`, delivery ownership, mirror leases, input/interrupt/cancel.
- [ ] M3: `np` mod core and the live thin slice.
  - [x] Mod at `integrations/claude/mod/np`: validates, `tsc -p` clean, 20 `claude plugin test` tests (planner, grammar, capture, renderers on terminal and desktop).
  - [x] `nativepeers/bridge.py` server (token, routes, owner/heartbeat/idle exit) and a replay backend: zero-API harness passes Path A, a parent follow-up (one forward, generation 2, second notification), refusals and bridge death (fail-closed).
  - [x] PTY run over the replay backend: native tasks row `np:codex  GPT-6.1 Sol high · Codebase review`, band, agent view with replayed tool rows; only refused `np-mirror-*` summary requests at the tripwire.
  - [ ] Live slice with a real Codex worker (needs the M2 native backend).
- [ ] M4: input forwarding, stop mapping, UI.
- [ ] M5: Grok/Antigravity normalizers, adoption, attach, backfill, pane.
- [ ] M6: desktop, installer, docs, performance, acceptance AC1–AC14, merge.

[Checkpoint](evidence/claude-lineage-checkpoint.json) · [Live budget](evidence/claude-lineage-live-budget.json)

---

# Native CLI updates renew on first use (2026-10-27)

- [x] Cause: renewal of a supported vendor patch needed an explicit `runtime-check --apply`, and every admission check refused a changed hash, so each native CLI update failed the first `npeer` call until someone ran it (Claude 2.1.312 passed every bounded check and sat unrenewed after the updater switched the launcher).
- [x] `compatibility.verify` now renews once on first use for the selected Claude, Antigravity or Grok executable, running the same bounded checks as `--apply` under `locks/auto-renew.lock`; a refusal is recorded and not retried for the same bytes, a transient failure backs off 60 seconds.
- [x] Strict paths unchanged: verification against an already pinned worker runtime, another explicit path, revoked or retired runtimes, managed Codex, `doctor` and plain `runtime-check`.
- [x] Six offline tests (first use, refusal not retried, selected-only, backoff, revoked/retired, worker pin); with the switch off four of them fail. The one existing assertion of strict refusal now names `renew=False`.
- [x] Live: `npeer whoami` through the fixed code renewed Claude 2.1.312 in 3.2 seconds with zero model input; native acceptance stays `NOT_RUN`.
- [ ] Three tests in the full unit suite (`test_unbound_forged_metadata_never_becomes_human`, one `test_cancellation` Claude subtest, one `test_readonly_artifacts` subtest) fail on unmodified main inside a Claude Code session; they are unrelated to this change.

[Current update workflow](docs/RUNTIME_UPDATES.md) · [Verification record](evidence/runtime-auto-renew.json)

---

# Routine CLI updates — issue 4 source PR (2026-10-26)

- [x] Add one bounded `runtime-check --provider … --apply` path for supported vendor patches; no qualification model input, hash copying or reinstall.
- [x] Bind signature, version/help and marker canaries to one hashed temporary executable snapshot; compare the selected runtime and complete prior approval under the runtime lock.
- [x] Keep admitted updates visibly `SUPPORTED_UPDATE` with native acceptance `NOT_RUN`; retain separate quota/status-line/MCP/parent-return scope.
- [x] Fix and independently recheck every required finding from the Grok 4.7 and Grok 4.7 Build Fast reviews, including swap/restore, version/refusal diagnostics and quota detail attribution.
- [x] Pass 102 focused tests / 88 subtests and the complete corrected source suite: 792 tests / 742 subtests, four Linux-only skips.
- [x] Pass real signed-binary snapshot checks and temporary approval/admission transitions for Claude 2.1.311 and Antigravity 1.4.7, with zero qualification model inputs.
- [ ] Merge and any daily runtime approval transition remain owner actions. Main is the live checkout and was not edited or installed by this PR.

[Source-only verification record](evidence/runtime-approvals-issue-4.json) · [Current update workflow](docs/RUNTIME_UPDATES.md). The PR records the final publication-head suite and cleanup. Native inference, original-parent ingestion, bound MCP, effective permission and quota-source checks are not implied by this record; historical checks retain their original source/runtime scope.

---

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
