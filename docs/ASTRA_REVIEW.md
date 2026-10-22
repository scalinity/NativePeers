# Astra review and fixes

Two parallel specialists reviewed the extension from `7317c6ec397938a11597740ca2d774678aa48eec` through `c6e2347e272db172c3d5b0f852cbefb1f5739ece`, both using `gpt-6-astra` with `xhigh`. DB1 reviewed bugs and integration; CA1 reviewed architecture, permissions, performance, tests and data integrity. Both completed within the ten-minute review budget and were closed. The read-only phase ran no tests and changed no files.

All **12 findings** were addressed, verified and pushed separately. Interrupted-install ownership was corroborated by both reviewers. The original review's Request Changes verdict below describes the pre-fix snapshot; fixes have focused regressions and the final offline suite passed **107 tests and 63 subtests**. This was one paired review followed by an address pass, not a second independent review of the fixes.

| ID | Severity | Finding | Resolution | Commit(s) |
|---|---|---|---|---|
| R01 | critical | Advance Antigravity inbox after emitted rows | FIXED | [df54519](https://github.com/scalinity/NativePeers/commit/df54519fc9841b039401cc03c9cf1d34deab51b2) |
| R02 | critical | Reject enclosing interactive bindings | FIXED | [a3bad32](https://github.com/scalinity/NativePeers/commit/a3bad3202245c3e9a7c84a91294926ec4c11321f), [434ea56](https://github.com/scalinity/NativePeers/commit/434ea56106da05ccf76d230988a15891578d5334) |
| R03 | warning | Recover interrupted install ownership | FIXED | [5b7e35b](https://github.com/scalinity/NativePeers/commit/5b7e35be7bbaadce060b9fa31e4db5dceb164892), [8b7ac03](https://github.com/scalinity/NativePeers/commit/8b7ac034f753ccf01d651013fd91e85040906ca3) |
| R04 | warning | Provider-specific receipt reconciliation | FIXED | [04e27d4](https://github.com/scalinity/NativePeers/commit/04e27d40abc61fe5553aa4d3f036305e1f0a6d60) |
| R05 | warning | Bound catalog timeout cleanup | FIXED | [32cf6db](https://github.com/scalinity/NativePeers/commit/32cf6db53a28fcbfa35b25850032613254959620) |
| R06 | warning | Validate hook inputs before registry mutation | FIXED | [072b5c5](https://github.com/scalinity/NativePeers/commit/072b5c587e58f3df2c4ac2165e9b414d4907a2ff) |
| R07 | warning | Verify native Antigravity ingress compatibility | FIXED | [077a65c](https://github.com/scalinity/NativePeers/commit/077a65c01222ac47154b1e790e25f527c7c7997e) |
| R08 | warning | Target facade identity lookup without metadata discovery | FIXED | [76ece36](https://github.com/scalinity/NativePeers/commit/76ece36798117bf163919e22dc7e95e84e0f202e), [9bbb3ed](https://github.com/scalinity/NativePeers/commit/9bbb3edf9d9fe15c142217d3b707d82d03b8b966) |
| R09 | warning | Bound patch capture resources | FIXED | [dd53f00](https://github.com/scalinity/NativePeers/commit/dd53f0042d8066b0b9e2b64943738e92511b9b25) |
| R10 | warning | Cover hook and stream negative boundaries | FIXED | [49758c9](https://github.com/scalinity/NativePeers/commit/49758c98395c5a721185b051a6ceead23f8fae32) |
| R11 | suggestion | Signal notification waiter readiness explicitly | FIXED | [a038eb0](https://github.com/scalinity/NativePeers/commit/a038eb06568a8c2259c50b086fb825e299385cfb) |
| R12 | suggestion | Isolate test filesystem mutations | FIXED | [68ef14c](https://github.com/scalinity/NativePeers/commit/68ef14c86c97ec0ea71f157dd29b3496eecdd702) |

The address pass also closed two related gaps: terminal installation/uninstallation state recovers after its final manifest write fails, and a nested Codex session cannot borrow an enclosing Codex session's `CODEX_THREAD_ID`. Exact owner lookups use an SQLite index and avoid native title discovery. Test fixtures now keep installation backups and executable mode changes inside temporary directories. Patch capture retains bounded bytes, stops on size/time limits and leaves a clear retained-worktree outcome.

## Verification after fixes

| Class | Result | Scope |
|---|---|---|
| Unit/property and fake-provider contracts | PASS | `.venv/bin/python -m pytest -q`: 107 tests and 63 subtests, including actual hook-entry subprocess calls against temporary non-model fixtures |
| Existing CodexMCP regressions | PASS | 23 manager/naming tests; candidate compilation/activation messages are simulated temporary fixtures, not an actual Codex update |
| Installed passive | PASS | Exact current caller has workspace grants; both normal and Antigravity install dry-runs have zero operations; protected configuration hashes unchanged during checks |
| Original native model acceptance | Historical, scoped | Prior 95/98 ledger and private native observations retained; **no new vendor-inference acceptance inputs were submitted in this address pass** |
| Current-source native acceptance after fixes | NOT_RUN | Offline and passive checks do not re-certify every earlier native outcome |
| Full original acceptance / raw old configuration attribution | Qualified / BLOCKED as recorded | Existing variants and retained unrelated settings drift remain documented in VERIFICATION.md and acceptance.json |

The two requested Astra review agents were normal review work, recorded separately from NativePeers native acceptance tests. No new NativePeers-owned model workers or native queued inputs were created by the address pass. The durable live ledger remains 95 consumed, zero reserved and zero unsettled. Private reviewer captures, fixture output and JUnit data stay in the ignored project directory; this document publishes the nonsecret findings and resolution.

<details>
<summary>Original unified review at c6e2347 (before fixes)</summary>

## Code Review: NativePeers model, Antigravity and implementation-worker extension

### Summary
Reviewed the fixed range `7317c6ec397938a11597740ca2d774678aa48eec...c6e2347e272db172c3d5b0f852cbefb1f5739ece` with two parallel specialists, DB1 debugger and CA1 code auditor, both `gpt-6-astra` at `xhigh`. The static review found two critical issues, eight warnings and two suggestions; interrupted-install ownership was independently corroborated. The 53-file scope used a tiered review: confidence is strongest on Tier 1, false negatives remain possible in Tier 2, and Tier 3 received metadata acknowledgment only.

**Scope (so the reader knows what was and wasn't reviewed):**
- **Tier 1, detailed:** `nativepeers/antigravity.py`, `nativepeers/antigravity_install.py`, `nativepeers/caller.py`, `nativepeers/delegation.py`, `nativepeers/hooks.py`, `nativepeers/install.py`, `nativepeers/mcp_server.py`, `nativepeers/worker_process.py`, `nativepeers/worktrees.py`, `nativepeers/profiles.py`, `nativepeers/recovery.py`, `nativepeers/registry.py`, `nativepeers/compatibility.py`, `schemas/spawn.v1.json`. New native modules received full review; modified files received changed-line/context/enclosing-function/direct-caller review.
- **Tier 2, targeted skim/coverage:** `README.md`, `bin/npeer-antigravity-hook`, `docs/ANTIGRAVITY.md`, `docs/INSTALLATION.md`, `docs/RUNTIME.md`, `docs/USAGE.md`, `docs/VERIFICATION.md`, `examples/codex-to-two-claude.json`, `examples/implementation-workers.json`, `examples/two-gemini-workers.json`, `integrations/claude/skills/nativepeers/SKILL.md`, `integrations/codex/skills/nativepeers/SKILL.md`, `integrations/zsh/gemini.zsh`, `nativepeers/cli.py`, `nativepeers/launch.py`, `nativepeers/models.py`, `nativepeers/routing.py`, `nativepeers/supervisor.py`, `tests/contract/test_antigravity_stream.py`, `tests/contract/test_bootstrap.py`, `tests/contract/test_notifications.py`, `tests/contract/test_worker_lease_cleanup.py`, `tests/fixtures/fake_antigravity.py`, `tests/live/antigravity_paths.py`, `tests/live/collect_antigravity.py`, `tests/unit/test_antigravity.py`, `tests/unit/test_compatibility_receipts.py`, `tests/unit/test_delegation.py`, `tests/unit/test_gemini_shell.py`, `tests/unit/test_hook_identity.py`, `tests/unit/test_nested_caller.py`, `tests/unit/test_worker_models.py`, `tests/unit/test_writing_results.py`. These files were not uniformly deep-reviewed.
- **Tier 3, acknowledged only:** `PROGRESS.md`, `evidence/acceptance.json`, `evidence/capabilities.json`, `evidence/checkpoint.json`, `evidence/fingerprints.json`, `evidence/live-budget.json`.
- **Out of scope:** personal configs, authentication, native histories, private evidence, earlier review reports, remote/private Desktop backends, enterprise/fleet governance and speculative redesign.
- **Not run during review:** tests, linters, type-checker, builds or native model acceptance. Reviewers made no file changes. The later address phase has its own verification record.

### 🔴 Critical Issues (Must Fix)

**Introduced in this changeset:**

1. **Previously emitted messages prevent the Antigravity inbox from advancing past ten messages** — [nativepeers/antigravity.py:112](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity.py#L112) _(unique to DB1's lane)_ `R01`

   - **Problem:** The query applies `LIMIT 10` before the loop excludes messages with an `antigravity-hook-output-unconfirmed` event. Emitting a message deliberately leaves its attempt in `SUBMITTED_UNCONFIRMED`, so previously emitted rows remain eligible for the next query. Explicit acknowledgments and replies also leave that attempt state unchanged: they insert receipts in `routing.py`.
   - **Risk:** Queue eleven small, unexpired messages for one registered Antigravity incarnation → the first `PreInvocation` selects and emits messages 1–10 → the next invocation selects those same ten rows and skips all of them → message 11 is never considered while the earlier rows remain eligible. Later peer requests and controller completions are therefore blocked until earlier messages expire, and can expire themselves without being offered to the recipient.
   - **Fix:** Exclude previously emitted messages **inside the SQL query, before the limit**, while preserving the honest unconfirmed receipt state. Replace the pending selection with:

     ```python
     pending = journal.all(
         """
         SELECT m.message_id, m.payload_json, a.attempt_id
         FROM messages m
         JOIN attempts a USING(message_id)
         WHERE m.to_key = ?
           AND a.target_incarnation = ?
           AND m.expires_at > ?
           AND a.state = 'SUBMITTED_UNCONFIRMED'
           AND NOT EXISTS (
               SELECT 1
               FROM events e
               WHERE e.subject_id = m.message_id
                 AND e.kind = 'antigravity-hook-output-unconfirmed'
           )
         ORDER BY m.created_at
         LIMIT 10
         """,
         (peer['handle'], peer['incarnation_id'], time.time()),
     )
     ```

     Add a regression case with eleven messages: the first invocation must emit ten, the second must emit the remaining message, and subsequent invocations must emit none. The existing test exercises only one message and therefore misses this boundary.

2. **The native-caller facade can attribute a nested Antigravity session to its outer Claude parent** — [nativepeers/mcp_server.py:51](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/mcp_server.py#L51), [nativepeers/caller.py:51](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/caller.py#L51) _(only CA1 — DB1 was in scope but didn't flag)_ `R02`

   - Problem: The new `--native-caller` facade delegates identity resolution to `bind_caller()`, which accepts an ambient `NPEER_BINDING` before reaching the new nearest-native-owner checks. For an interactive binding, it establishes only that the recorded owner appears **somewhere** in the ancestry. This permits incorrect session authorization and attribution, **CWE-863**.
   - Risk: Concrete failure trace: a registered Claude session exports its binding through `hooks.py:90–92` → a nested Antigravity invocation retains that environment variable → its native-caller facade reads the outer Claude binding → the outer Claude process satisfies the ancestor check → the facade returns a bound caller for Claude rather than Antigravity. A subsequent `peer_send` records Claude in the message’s `from` and `reply_to` fields; other operations likewise use Claude’s grants and ownership. The `gemini` wrapper clears these variables, but the public `agy` invocation and core identity resolver do not enforce that prerequisite. `test_nested_caller.py:20` explicitly clears both binding variables, so its tests bypass this case.
   - Fix: Apply nearest-native-owner validation to ambient **interactive** bindings before returning the caller. Preserve the separate controller-owned worker binding contract. For example, replace the existing non-fixed-binding ancestry check with:

     ```python
     if fixed_binding is None:
         ancestors = [p.json() for p in ancestry()]
         require(
             native_ancestor(peer["owner"], ancestors),
             "CALLER_UNBOUND",
             "Native owner is outside caller ancestry",
         )
         if peer["owner_kind"] == "interactive-standalone":
             native_binaries = {
                 (Path.home() / relative).resolve()
                 for relative in (
                     ".local/bin/agy",
                     ".local/bin/claude",
                     ".local/share/codex-mcp-manager/current/bin/codex",
                 )
             }
             nearest = next(
                 (
                     process
                     for process in ancestors
                     if Path(process["executable"]) in native_binaries
                 ),
                 None,
             )
             require(
                 nearest is not None
                 and native_ancestor(peer["owner"], [nearest]),
                 "CALLER_UNBOUND",
                 "Interactive binding belongs to an enclosing native session",
             )
     ```

     Add regression cases with a valid outer binding and a different nearest native owner. Assert that the CLI rejects that binding and the facade refuses mutations; retain positive cases for the correct interactive owner and owned-worker bootstrap.

**Pre-existing:** None reported.

### 🟡 Warnings (Should Fix)

**Introduced in this changeset:**

1. **Interrupted installation loses ownership of files already written, including after retry** — [nativepeers/antigravity_install.py:99](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity_install.py#L99) _(corroborated: both agents)_ `R03`

   - Problem: All configuration writes precede the single manifest write at line 107. If a later write fails, earlier changes exist without durable ownership records. Retrying does not necessarily recover ownership: already-correct JSON blocks are skipped at line 91, and already-correct files are omitted from `operations` at line 92. A retry can therefore report successful installation while the manifest owns only the remaining files; uninstall skips the unrecorded hook/MCP blocks at line 74.
   - Suggestion: Persist installation progress and reconcile interrupted operations against their expected before/after content. Follow the existing installer’s `INSTALLING` state and incremental manifest updates in `install.py:145–162`, while also handling an already-applied operation during recovery. Add fault-injection tests that fail the second or third write, retry, then uninstall; assert that every installed NativePeers block is removed and unrelated configuration survives.


2. **Antigravity receipt recovery falls through to the Codex transcript parser** — [nativepeers/antigravity.py:107](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity.py#L107) _(unique to DB1's lane)_ `R04`

   - **Problem:** The new registration path supplies an Antigravity `native_transcript`, making these peers reachable through the existing recovery flow. However, [reconcile.py:52](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/reconcile.py#L52) distinguishes only Claude from “everything else”; that fallback requires a Codex `response_item` with `payload.role == 'user'`. The extension’s own [Antigravity transcript reader](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/live/collect_antigravity.py#L19) uses a different format: `USER_INPUT`, top-level `content`, and `step_index`. Consequently, recovery silently ignores Antigravity input records and cannot establish ingestion through this path. This is an integration mismatch introduced by adding the third provider, rather than a missing optional live check.
   - **Suggestion:** Dispatch reconciliation explicitly by provider and parse verified Antigravity input records using their native format and exact envelope matching. If the relevant ephemeral hook content has no verifiable persisted input record, return that specific reconciliation limitation instead of silently applying the Codex parser. Cover the supported Antigravity record shape with a fixture.


3. **The catalog timeout can become an unbounded wait during cleanup** — [nativepeers/antigravity.py:44](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity.py#L44) _(unique to DB1's lane)_ `R05`

   - **Problem:** `catalog()` bounds `process.communicate()` to 30 seconds, but its exception handler then calls `process.terminate()` followed by an unbounded `await process.wait()`. If the native CLI does not exit on SIGTERM, the spawn request remains stuck in cleanup. This happens during catalog discovery, before the group supervisor and worker time limits can protect the operation.
   - **Suggestion:** Bound the termination grace period, escalate to `kill()` when necessary, and reap the process while preserving the original timeout or cancellation. Handle a concurrent process exit without replacing the original error. Add a subprocess fixture that ignores SIGTERM to exercise this failure path.

4. **Rejected transcript validation leaves a committed peer registration behind** — [nativepeers/antigravity.py:102](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity.py#L102) _(only CA1 — DB1 was in scope but didn't flag)_ `R06`

   - Problem: `registry.register()` executes before validating `transcriptPath`. Registration commits the new live peer and marks an existing peer for that owner as replaced. If the transcript lies outside the provider home or lacks the supplied UUID, line 106 raises `OWNERSHIP_CONFLICT`, but those registry changes remain committed. A subsequent hook with the same owner/UUID takes the `existing` branch and skips that validation altogether.
   - Suggestion: Resolve and validate the transcript and other required registration fields before calling `register()`. Add a test starting with a valid registered peer, submit a mismatched transcript/UUID event, and assert that rejection preserves the original incarnation and creates no replacement peer. This is a concrete persistence defect on a rejected-input branch; its occurrence with actual native hook payloads was not established during this review.


5. **Interactive Antigravity ingress bypasses the compatibility receipt boundary** — [nativepeers/antigravity.py:68](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/antigravity.py#L68), [nativepeers/routing.py:17](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/routing.py#L17) _(only CA1 — DB1 was in scope but didn't flag)_ `R07`

   - Problem: Worker startup verifies the Antigravity executable, but interactive registration and pending-message injection do not. The new adapter is also absent from `Core.__init__()`’s compatibility wrappers, which cover only Claude and Codex at `core.py:19–33`. Following an Antigravity executable update, interactive hooks can continue registering and injecting pending messages without comparing the executable to the installed receipt. This diverges from the documented rule that changed native runtimes block affected delivery until reviewed.
   - Suggestion: Verify the actual observed Antigravity executable before admitting interactive registration or injecting queued content. Queueing may remain a local operation, but the native-ingress boundary needs the check. Add a temporary-executable test that changes the fingerprint after installation and asserts that registration/injection is refused while queued messages remain retained.


6. **The idle facade repeatedly performs full discovery and native metadata reads** — [nativepeers/mcp_server.py:49](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/mcp_server.py#L49) _(unique to CA1's lane)_ `R08`

   - Problem: For the new no-binding mode, `watch()` calls `names()` every 100 ms, which calls `binding_state()` → `bind_caller()` → `Registry.list('antigravity')`. That list loads the entire `peer_instances` history and refreshes metadata before filtering by provider. Consequently, an idle Antigravity facade repeatedly checks other providers and can read up to 4 MiB of Codex session-index data per live Codex peer per poll through `adapters/metadata.py:7–19`. The discovery component is **O(N)** per poll in retained registry rows, with additional native metadata I/O; these synchronous operations run on the MCP event loop.
   - Suggestion: Separate identity lookup from user-facing discovery. Resolve the exact owner with a targeted registry query and validate its incarnation/liveness without refreshing titles. Keep title refresh in discovery operations. Add a contract test asserting that repeated bound-state checks do not call native title discovery. No performance timings were measured in this review.


7. **Patch-size enforcement occurs after unbounded, potentially quadratic capture** — [nativepeers/worktrees.py:37](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/worktrees.py#L37), [nativepeers/worker_process.py:175](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/nativepeers/worker_process.py#L175) _(unique to CA1's lane)_ `R09`

   - Problem: `changes()` captures each new file’s complete diff and appends it to immutable `bytes` with `patch += ...`. For `N` similarly sized patches of size `S`, repeated copying is **O(N² × S)**, in addition to launching one Git process per untracked file. The 10 MiB artifact limit is checked only after the complete patch has been assembled. A worker producing a large generated tree can therefore spend substantial memory and time constructing a patch that will immediately be discarded. Capture also runs synchronously after the model-turn timeout context has ended.
   - Suggestion: Bound capture while reading Git output, stop retaining patch bytes once the artifact limit is exceeded, and return the existing explicit “inspect retained worktree” outcome. Use a set for exclusions and avoid repeatedly copying the accumulated patch. Add cases for many new files, a single oversized file, and a combined patch crossing the limit; verify that result metadata remains available and the worktree is retained.


8. **Critical negative branches lack focused coverage, and the stream fixture bypasses the hook boundary** — [tests/contract/test_antigravity_stream.py:20](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/contract/test_antigravity_stream.py#L20), [tests/fixtures/fake_antigravity.py:17](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/fixtures/fake_antigravity.py#L17) _(unique to CA1's lane)_ `R10`

   - Problem: The stream fixture directly inserts `native-hook-observed` into the journal, so that contract test cannot establish that the real hook validates ownership or supplies the observation correctly. Static inspection also found these specific gaps:
     - **`queue_native_hook`** has no test reference exercising the adapter route; the inbox unit test manually inserts an attempt already in `SUBMITTED_UNCONFIRMED`.
     - **`catalog`** has no focused offline test for parsing, authentication failure, nonzero exit, or timeout. The live harness reaches catalog discovery indirectly, which does not cover those branches.
     - **`verify_tool_gate`** is exercised positively through `Worker.prepare()`, but the supplied tests do not exercise missing/modified global hooks or conflicting project/native-settings overrides.
     - **`antigravity.main`’s error branch** is not tested for the owned-worker `PreToolUse` deny response.
     - **`Worker.antigravity_turn`** lacks focused cases for missing hook observation, mismatched conversation IDs, display-name model normalization, and canceled/interrupted terminal results.
     - Inbox edge cases with **11 pending messages**, expired messages, and the **64 KiB aggregate limit** are absent.
   - Suggestion: Add deterministic offline tests for these boundaries, including at least one subprocess test invoking the actual hook entry point against temporary state. Keep the existing fake-stream test for framing and follow-up behavior, but do not use its synthetic journal record as evidence that hook enforcement is integrated. These are static coverage findings, not claims that the existing tests fail.

**Pre-existing:** None reported; the third-provider reconciliation gap is newly reachable through this extension.

### 🔵 Suggestions (Consider)

**Introduced in this changeset:**

1. **Replace timing-based readiness in the notification regression test** — [tests/contract/test_notifications.py:71](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/contract/test_notifications.py#L71) _(unique to CA1's lane)_ `R11`

   - Current: The test starts the waiter, sleeps for 10 ms, then checks notification state and completes the task. The elapsed delay does not establish that the waiter reached the intended point.
   - Better: Use an `asyncio.Event` or a small instrumented boundary to signal waiter readiness before asserting state. This makes the scheduling condition explicit without claiming an unobserved execution-order failure.


2. **Keep new test filesystem mutations inside temporary fixtures** — [tests/unit/test_antigravity.py:61](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/unit/test_antigravity.py#L61), [tests/contract/test_antigravity_stream.py:25](https://github.com/scalinity/NativePeers/blob/c6e2347e272db172c3d5b0f852cbefb1f5739ece/tests/contract/test_antigravity_stream.py#L25) _(unique to CA1's lane)_ `R12`

   - Current: The Antigravity installer test substitutes the home and journal but leaves `antigravity_install.ROOT` pointing at the real checkout, so installation/uninstallation writes backup directories under the project. The stream contract also calls `chmod()` on the repository’s fixture executable.
   - Better: Provide a temporary installation root containing the necessary skill fixture, and copy the fake executable into the temporary directory before changing its mode. This follows the isolation pattern already used by `test_install.py:22` and `test_writing_results.py:18`.

**Pre-existing:** None reported.

### ✅ Good Practices
- Requested/observed models remain distinct and observed models persist across native follow-ups and resume.
- Tool profiles enumerate grants, native summaries use parameterized read-only SQLite access, and native admission remains distinct from model ingestion.
- Worktree tests apply real patches including safe new files while checking original dirty-state preservation.
- Current documentation separates scoped live proof from unrun acceptance variants.

### Verdict
- [x] ❌ Request Changes (critical issues)
- [ ] ⚠️ Approve with Suggestions
- [ ] ✅ Approve


</details>
