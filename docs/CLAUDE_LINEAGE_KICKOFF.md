# NativePeers Lineage for Claude Code — Autonomous Implementation Kickoff

**Revision 1.0 · companion contract: `docs/CLAUDE_LINEAGE_MOD.md` revision 1.0**

`docs/CLAUDE_LINEAGE_MOD.md` is normative. This kickoff tells you how to execute it. Confirm both files are present in `/Users/danny/Documents/Tools/NativePeers/docs/` and record their SHA-256 hashes before beginning.

This is an implementation task, not a request for another plan.

Read the entire specification, then the parts of `docs/IMPLEMENTATION.md` it cites (§§6, 7, 8.3, 10, 11, 12, 17), plus `PROGRESS.md`, `docs/RUNTIME.md`, `docs/RUNTIME_UPDATES.md`, `docs/NOTIFICATIONS.md`, `docs/WORKER_VISIBILITY.md`, `docs/CLAUDE_CONVENTIONS.md` and `docs/CODEGRAPH_CONVENTIONS.md`.

**How to run it:**
- Execute the milestones as one sustained, checkpointed implementation:
  1. resolve the M0 gates on this Mac;
  2. build a thin end-to-end slice (one Codex worker mirrored into a Claude Code CLI session);
  3. finish the full acceptance suite.
- Make ordinary engineering decisions autonomously.
- Continue independent work around a bounded blocker, but never present a missing core capability as complete.
- Context compaction or a restart is a continuation event, not permission to redesign or replay finished work.

## 1. Outcome

Leave a working system on this Mac in which:

- **Native rows.** In any interactive Claude Code session (the CLI in macOS Terminal, and the Claude desktop app's local Code sessions), NativePeers workers of all four providers (Codex, Claude, Antigravity, Grok) appear as **native subagents** in the tasks list below the prompt, labeled with provider, model, effort and title.
- **Live transcript.** Selecting a row shows that worker's live transcript in the native agent view: the brief, assistant text, every external tool call with bounded output, file edits with diffs, and the final result.
- **Interaction.** Typing while viewing a worker sends it a follow-up exactly once:
  - a running worker queues it (or steers, where verified);
  - a completed worker resumes the same native conversation.

  The parent model can do the same with `SendMessage`. Esc interrupts that worker's turn, stopping the agent cancels its task, and Stop all cancels this parent's groups and their descendants.
- **Delegation.** The parent model delegates natively with `Agent(subagent_type:"np:<provider>")`, choosing exact models and efforts through the `np` header and `mcp__np__catalog`. Workers created through the existing `npeer spawn` or MCP `worker_spawn` are adopted into the same view automatically. They are display-only for their initial tasks, whose delivery stays with NativePeers.
- **Exactly-once delivery.** Each task generation's completion reaches the parent exactly once, through the owner recorded at creation (spec §6.4):
  - the native notification for Agent-tool spawns and mod-forwarded follow-ups;
  - NativePeers' own push or wait for adopted initial tasks.

  A lapsed owner falls back to push or retain, with the uncertainty discipline intact.
- **Zero Claude inference.** Shadow agents never call a Claude model. Every fault path fails closed, and this is measured, not assumed.
- **Lineage band and pane.** These show the cross-harness extras, and serve as the degraded-mode viewer.

**Hard boundaries:**
- No patching, wrapping or reverse engineering of the Claude Code binary or its bundled JavaScript.
- T3 Code, Herdr, tmux, terminal injection and GUIs are not runtime dependencies.
- No machine-wide daemon: the bridge is session-scoped.
- No change to non-Claude parent paths.

## 2. Workspace, branch and protected environment

- **Repository:** `/Users/danny/Documents/Tools/NativePeers`. At spec time `main` was clean at `0ee014f` with origin `main` in sync. Recheck it; do not assume.
- **Work location:** a feature worktree under `.worktrees/` on branch `feat/claude-lineage-mod`. `main` is the live checkout: the installed `/Users/danny/.local/bin/npeer` runs it. Do not edit or install from `main` until your PR is merged.
- **The two documents:** this spec and kickoff arrive **untracked** in the `main` checkout's `docs/`. Copy them unchanged into your feature branch as its first commit, and verify the hashes. Before merging, move the untracked originals in the `main` checkout to `backups/claude-lineage/` (gitignored) after verifying the committed copies match. Otherwise git can refuse to merge over untracked files.
- **Commits:** project-scoped and nonsecret. **No `Co-Authored-By` trailers or any other agent attribution, in commits or the PR.**
- **Finishing:** open the PR, merge it to `main` (do not leave it open or as a draft), then remove the feature worktree and branches after verifying nothing is lost, as the repo's earlier work did.
- **Git ignores:** `.claude/` stays fully gitignored.
- **Claude Code:**
  - Record `claude --version`, the resolved executable path and its SHA-256.
  - The mod API is early access. The engine's own declarations are authoritative.
  - Load the `plugin-authoring` skill to get this build's `types/claude-code.d.ts` and reference. **Develop the mod in the repo**, never in the session's dev-mods folder.
  - Test headless with `--plugin-dir`, and interactive disposable sessions with `--plugin-dir` or `CLAUDE_CODE_PLUGIN_DIRS`.
  - Run `claude plugin validate` and `tsc -p` continuously.
- **Preserve:**
  - every installed NativePeers integration: hooks, skills, the shared instruction block, `compat/runtime.json` approvals, notification policy and evidence pointers;
  - all provider homes, histories, authentication, MCP registrations and plugin states;
  - T3 Code, Herdr, Ghostty and CodexMCP setups.
- **Configuration changes** (for example `~/.claude/settings.json`) are merge-only, backed up under `backups/claude-lineage/`, and recorded in an uninstall manifest.

## 3. Start with evidence and ownership

**Evidence labels.** Use the specification's labels: U, D, S, P, X and L. The X findings (X1–X14) were reproduced in a separate Linux container on Claude Code 2.1.314. They are **not** L until you reproduce them here. Never relabel D, S or X as L. An unrun check is not a pass.

**Durable checkpoints.** Before any mutation, create a new top section in `PROGRESS.md`, plus `evidence/claude-lineage-checkpoint.json` and `evidence/claude-lineage-live-budget.json`.
- Update them after each gate, milestone, live batch and planned handoff.
- Record:
  - document hashes;
  - branch and commit;
  - Claude Code version and fingerprint;
  - gate decisions;
  - active owned processes (bridges, workers);
  - consumed and reserved live allowance;
  - blockers;
  - the next exact action.
- On continuation:
  1. read the checkpoint;
  2. reconcile active or uncertain worker work and any running bridges;
  3. resume.

**Concurrent changes.** Identify concurrent changes to files you will touch. The repo is actively used, and its runtime auto-renew can update approvals. Use compare-and-apply.

## 4. Follow the selected architecture

**Implement** shadow agents answered by the mod's `turn.step` hook, NativePeers' `nativepeers.activity.v1` log, the session-scoped `npeer bridge` over a private Unix socket, mirror leases with surfacing and ack delivery ownership, and the `np` mod at `integrations/claude/mod/np/`.

**Mandatory engine rules** (spec §5 and X10–X12):
- Every non-shadow loop passes through: `return yield* next(e)`.
- Every `turn.step` path, and its `.catch` handler, returns a complete `TurnStepResult`. A bare `return` or `undefined` makes the hook absent, and the engine then makes a real model request (X10).
- Every `$.process.run` passes `timeoutMs` (default 30 s, max 10 min).
- The `turn.step` registration carries a `.catch` async-generator fail-closed handler.
- Every wait that can exceed milliseconds is a `$` call in flight (`$.http.fetch` with `socketPath`, or `$.process.run`). Never await a plain promise across time.
- `$` is passed only to top-level functions.
- The mirror tool never executes anything and never calls `next`.
- Shadows have no other tools.
- The worker-mode guard keeps the mod inert inside NativePeers-owned Claude workers.

**Spawn origins.** Spawn shadows only from the origins the spec allows. Path A goes through the engine's own Agent call via `agent.spawn` `next(e)`. Path B uses `tool.call` observer hooks. Path C uses origins proven in gate G5, or routes through a mod tool call.

**Ownership of logic.** Provider-specific parsing lives in NativePeers (Python), driven by recorded fixtures and pinned to runtime approvals. The mod consumes only the activity schema and the bridge API. Do not read provider transcripts from the mod.

**T3 Code** (`github.com/pingdotgg/t3code` @ `028ed851d552`) is a design reference only: the patterns in spec §16. Copy no code. Check the license before reusing anything. The REA toolkit is not to be used to patch any product.

## 5. M0: prove the seams on this Mac first

**Step 0: prove the tripwire (gate G0).**
1. Start the local stub: it answers HTTP 400 on every request, counts requests and never logs headers.
2. Point `ANTHROPIC_BASE_URL` at it.
3. Run a deliberate fault-without-`.catch` case. It must hit the stub and bill nothing.

Until G0 passes, harness runs count against the live budget.

**Step 1: reproduce X1–X14** with the zero-API headless harness pattern (spec Appendix A and §12, item 3):
- `env -i HOME=<tmp>`;
- `ANTHROPIC_BASE_URL` pointed at the tripwire;
- a test driver mod answering main-loop steps.

Assert every run reports `duration_api_ms == 0`, `total_cost_usd == 0`, empty `modelUsage` and zero tripwire hits.

**Step 2: resolve gates G1–G13** (spec §13). Record each gate's observation, decision and chosen fallback in `evidence/claude-lineage-m0.json`. Record the minimum engine version in `compat/claude-mod.json`.

**Interactive gates** (G1–G6, G8–G11):
- Use a disposable interactive `claude` session in a fixture repository, driven through a sacrificial PTY harness, which is permitted only as a test harness.
- Load the mod via `--plugin-dir`.
- Drive the main loop with the test driver mod wherever possible, so the parent spends no model turns.
- Capture screen text for the tasks list, the agent view and the band.

**Desktop gates** (G7, and the desktop half of AC9):
- Prepare a short scripted checklist.
- Use computer use if it is available. Otherwise make **one** consolidated request to me, with exact steps and what to report back.
- Continue all other work meanwhile.

**Fallbacks.** A failed gate selects its documented fallback; it does not stop the build. If both G1 and G2 fail, switch to the documented degraded design (band and pane viewer with its own input), record that decision prominently, and continue.

## 6. M1–M2: NativePeers changes

**M1:**
- Implement the activity recorder, the Codex and Claude normalizers, `activity_index` and `npeer activity`, as spec §6.1–6.2 define them.
- Derive exact provider event names from the installed runtimes (Codex's generated app-server schema for the leased executable; observed Claude stream-json; existing recordings), never from memory.
- Record fixtures under `tests/fixtures/activity/`.
- The recorder must never block reading provider stdout.

**M2:**
- Implement `npeer bridge`: binding through the existing interactive ancestry checks, a bearer token, every API endpoint, and exit on owner death, heartbeat loss or idle (not stdin EOF).
- Implement per-generation delivery ownership, mirror leases and the route states (§6.4), and input, interrupt and cancel (§6.5).
- Add the spawn `mirror` field, the CLI equivalents and `doctor` reporting.
- Add race tests: expiry versus surfacing versus ack, duplicate inputs, and ownership conflicts.

**Both milestones:**
- The existing suite must stay green. Record its count and duration as before.
- Any pre-existing failures noted in `PROGRESS.md` are reported separately, unrelated, and are not "fixed" by weakening tests.

## 7. M3–M5: the mod and breadth

**M3:**
- Agent types, Path A, the mirror engine with its mandatory `.catch`, the mirror tool and guards, compaction skip, final answer, surfacing and ack.
- The zero-API integration harness passes.
- Then the live thin slice: one real Codex worker spawned by a real parent-model `Agent(np:codex)` call in a disposable CLI session, watched end to end.

**M4:**
- Input capture and forwarding (exactly once, keyed by the row's `uuid`).
- Esc, kill and Stop all mapping per G4.
- Provenance labels, the `ToolUse` renderer, the Lineage band, the `/np` commands, status and toasts.
- Mod tests on both the `terminal` and `desktop` surfaces.

**M5:**
- Grok and Antigravity normalizers.
- Path B adoption, and Path C attach and re-attach.
- Backfill readers (Codex and Claude first).
- The pane, with lineage tree and degraded viewer.

## 8. M6: desktop parity, installation, documentation, acceptance

- **Desktop:** owner recognition, by exact path and hash under runtime approval, per G7.
- **Install:**
  - `npeer install --claude-mod` registers `integrations/claude/mod/np` (from the merged `main` checkout) as a local folder marketplace and installs `np` at user scope, so it loads in both the CLI and desktop sessions.
  - It must be idempotent (a repeat install performs zero operations), with a dry-run and an uninstall manifest.
  - Add a "mod compatibility" verdict to `runtime-check --provider claude`: `claude plugin validate` plus the zero-API harness on a new Claude version.
- **Text updates:** the Claude skill, `docs/CLAUDE_CONVENTIONS.md` and the shared instruction block (prefer `np:*` when listed). Add `docs/LINEAGE_USAGE.md` with native macOS Terminal examples.
- **Measurement:** measure the §11 performance budgets.
- **Acceptance:** run AC1–AC14 and record each with evidence.

## 9. Live-test authorization and budget

**Zero-API harness runs are unlimited once gate G0 passes, and only with the tripwire set.** They submit no model input, and the tripwire proves it. Any tripwire hit stops harness use until the cause is fixed.

**Live model input is limited to disposable sessions and a fixture repository.** One durable ceiling covers all stages and restarts:

| Limit | Ceiling |
|---|---|
| Real parent-model turns in disposable Claude Code sessions | 12 total |
| Top-level worker task inputs across all providers (initial tasks, follow-ups, resumes, retries) | 32 total |
| Concurrent owned workers | 6 |
| Active live testing | 75 minutes |

**Planning allocation:**
- 4 worker inputs for M3's thin slice;
- 12 for the four-provider fan-out (AC1/AC2) and its follow-ups (AC4/AC5);
- 8 for interrupt, stop, Stop all, adoption and re-attach;
- 8 for diagnostics.

Suballocations may shift; the totals may not increase autonomously.

**Accounting:**
- Reserve before submission, in `evidence/claude-lineage-live-budget.json`, including uncertain or accepted work.
- Count provider-internal rounds separately where they are observable.
- Do not invent dollar estimates.

**Fallthrough:** any detected shadow fallthrough (a real Claude request for a shadow) stops live testing until the cause is fixed and the harness proves it closed.

**Not authorized:**
- prompt injection into production sessions;
- credential extraction or token borrowing;
- global permission changes beyond the single scoped mirror-tool rule that gate G6 may require;
- changes to production conversations;
- unbounded retries.

## 10. Hard stops and autonomy

**Do not stop for:** ordinary design choices, recoverable test failures, a gate covered by its documented fallback, or a missing optional host mode.

**Pause only the affected operation for:**
- an engine version below the recorded minimum;
- an unverifiable caller binding or worker ownership;
- a destructive migration;
- an unexpected conflicting edit to protected files;
- an unexplained shadow fallthrough;
- a preservation regression.

**Then:** continue unrelated work, preserve diagnostic evidence, and report the smallest required intervention.

**Never:**
- patch or wrap Claude Code;
- kill unrelated processes;
- force-push or hard-reset;
- delete or edit native histories;
- bypass a native permission boundary to get a green test.

## 11. Final output and verdict

Leave the mod and the NativePeers changes merged to `main` and installed for daily use. Return a technically precise report covering:

1. what was implemented, with project and install paths;
2. the Claude Code version, the recorded minimum engine version and each gate's observation and decision (G0–G13);
3. unit, mod, harness and live results separately, with PASS, FAIL, BLOCKED or NOT_RUN for each of AC1–AC14;
4. the zero-API evidence (harness records and live shadow usage) and the exactly-once delivery evidence (journal routes, absence of push frames for mirror-owned generations, the fallback run);
5. a concrete four-provider fan-out transcript excerpt, with distinct native IDs and overlapping task intervals;
6. CLI versus desktop behavior, and any degraded mode in force;
7. performance measurements against §11;
8. files and configuration keys changed, install and uninstall commands, and rollback steps;
9. remaining limitations and exact continuation actions;
10. document, code and runtime fingerprints, the final checkpoint and consumed live allowance;
11. the merged PR and commit.

**Verdict:** use an unqualified overall `PASS` only when AC1–AC8 and AC10–AC14 passed live or in the harness against the final fingerprints, and AC9 passed or its documented degraded mode was explicitly accepted. Documentation, source inspection, prototypes and mocked tests cannot substitute for required outcomes.

Implement the specification through completion. Do not finish with another proposal, a list of future milestones, or only scaffolding.
