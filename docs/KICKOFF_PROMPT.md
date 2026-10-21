# NativePeers — Autonomous Implementation Kickoff

**Revision 1.1 · companion contract: IMPLEMENTATION.md revision 1.1**

This complete revision supersedes the earlier kickoff and standalone addendum. `IMPLEMENTATION.md` is normative; `REVIEW.md` explains its changes but does not add scope. Confirm both canonical files are present and record their hashes before beginning.

Implement the complete native cross-harness messaging and worker-delegation system specified in `IMPLEMENTATION.md`. This is an implementation task, not a request to produce another plan and stop.

Read the entire specification before making changes. Execute its milestones as one sustained, checkpointed implementation: prove the integration seams first, build a thin end-to-end delegation path early, then finish the full acceptance suite. Make ordinary engineering decisions autonomously without routine milestone approval. Continue independent work around a bounded blocker, but never disguise a missing core capability as complete. Context compaction or a session restart is a continuation event, not permission to redesign or replay finished work.

## 1. Outcome

Leave a working local system in which independently running Claude Code and managed Codex CLI sessions can:

- Discover each other by native session title, with provider qualification when needed.
- Send requests, reply to the original sender, and sustain correctly correlated multi-message exchanges without the human supplying recipient working directories, PIDs, socket paths or UUIDs.
- Spawn concurrent independent workers of either provider, assign tasks, exchange follow-ups, collect attributed results, cancel owned work, and resume owned native histories safely.
- Let a Claude parent launch two GPT-6.1 Sol/xhigh workers and receive their results in that same Claude conversation.
- Let a Codex parent launch two Claude workers and receive their results in that same Codex conversation.
- Support the same-provider messaging and delegation combinations too.

Herdr, T3 Code, Agent Messenger, tmux, terminal keystroke injection, a GUI, and a permanent central broker must not be runtime dependencies. They can remain installed and in use for unrelated work.

Cross-provider children are independent native worker sessions with application-level parent links. Use the namespaced `worker_spawn` tool or `npeer spawn`; do not claim these are the vendor's built-in `Agent`/subagent tools or that workers appear in its private agent-navigation UI. Preserve all existing native tools.

Required v1 support is the eligible, registered Claude interactive CLI and managed Codex standalone TUI plus owned workers. Private Desktop/T3 backend ingress is conditional; another machine is outside v1. Do not weaken required native CLI support, and do not expand the build merely to make every unrelated application or remote host addressable.

## 2. Workspace and protected environment

Use a new isolated project directory, preferably:

`/Users/danny/Documents/Tools/NativePeers`

If the supplied documents are already in a suitable dedicated directory, use that rather than duplicating them. If the preferred path already contains unrelated work, choose an unused sibling directory and report it. Do not initialize a repository in my home directory or inside the completed CodexMCP project.

The authoritative existing Codex installation is managed under:

`/Users/danny/Documents/Tools/CodexMCP`

Last reported managed build:

`rust-v0.163.0-8f85dd9c6933`

Managed executable link:

`/Users/danny/.local/share/codex-mcp-manager/current/bin/codex`

Recheck these locally; do not assume the recorded version is still current.

Completed, protected functionality includes native startup session naming, empty-session discovery, native resume compatibility, dynamic MCP enable/disable/re-enable, the existing launcher, the managed patch/update mechanism, and their regression tests.

Existing commands work:

    codex -n LocalFlow Development
    codex --name "LocalFlow Development"
    codex -r LocalFlow Development

The ordinary interactive `codex` command already receives its intended YOLO behavior from the existing shell setup. The configuration contains:

    [features]
    daemon_auto_start = false

Preserve both. Do not add redundant default `--no-daemon` flags, enable the shared daemon, rewrite the shell function, install an upstream replacement Codex, or change model/account settings to accommodate a mistaken integration assumption.

The only authorized native Codex change is the additional, independently tested inbox patch described in the specification, and only if its feasibility gate proves it necessary. Integrate that patch through the existing manager without rewriting or dropping the naming/MCP patches.

Preserve native session homes, histories, authentication, existing hooks/plugins/skills, MCP registrations and enabled states, Codex Desktop compatibility, and existing Herdr/T3/Ghostty configuration.

## 3. Start with evidence and ownership

Read applicable local instructions and the whole specification. Inspect the actual command resolution in interactive and noninteractive contexts, the managed current package, existing patch manifest/update job, relevant configs, and running processes. Inspect only the local material needed for this task; do not crawl unrelated personal files.

Create a targeted protected-file inventory and semantic configuration baseline before installation. Capture recoverable copies of only files you will change. Record authentication integrity without copying or displaying credentials. Keep backups private and centralized under this project's `backups/`.

Identify any concurrent work affecting the same files. Use the manager's supported locking/activation mechanism; do not race its hourly update job or disable unrelated services. Recheck the relevant baseline immediately before applying changes.

Record every finding as user-reported, documented, source-inspected, proposed, or locally verified. Current public docs are not proof of the installed patched binary's behavior. Review the source register's verification scope; do not inherit old research claims as new local evidence.

### Durable checkpoints

Create `PROGRESS.md`, `evidence/checkpoint.json` and `evidence/live-budget.json` before mutation. Update them after every gate/milestone and live batch, before/after managed patch activation, and before a planned handoff. Record canonical document hashes, code commit/owned uncommitted changes, evidence and dependency fingerprints, original protected baseline plus authorized deltas, selected transports, active owned processes/requests, consumed/reserved test allowance, blockers and the next exact command/action.

Keep progress concise and link evidence rather than copying transcripts. Use project-scoped nonsecret commits; do not stage credentials, private backups, live transcripts or raw auth inventories. On continuation, read the checkpoint and inspect the worktree, reconcile active/uncertain native work, revalidate changed dependencies, and resume the next step. Do not reset the live-test allowance or rerun already accepted work just because the implementation agent restarted.

A legitimate provider credential refresh or concurrent manager update must be reconciled, not reverted to force an old hash match. Preserve account/home/auth mechanism and use compare-and-apply plus the manager's supported lock for configuration/package changes.

## 4. Follow the selected architecture

Use the specification's Python core, own SQLite journal, exact native identity/caller binding, provider adapters, short native hooks/skills, narrowly bound worker MCP tools, and finite-lifetime delegation supervisors.

Use the native managed Codex app-server over private stdio for workers you create. Resolve its immutable package path per worker and preserve the normal provider home and credentials. Do not use another app's provider process.

Use the installed native Claude CLI and structured headless turns for Claude workers, preserving its actual authentication route. Serialize follow-ups and resume only the exact owned native session after the previous writer has exited. Do not silently switch to an API key, use `--bare`, extract OAuth credentials, or select a bundled replacement CLI. Persistent SDK/streaming optimizations are not a prerequisite and must not change the billing/authentication contract.

For existing live conversations, deliver to their current owning runtime. Never create a second writer with `--resume` merely to inject a message. A transcript or a thread listed by another server does not establish global ownership or liveness.

Do not copy another project's entire runtime. Use existing projects as references, verify licenses before reuse, and retain attribution where required. “Native-first” describes native-backed execution; it does not remove the need for small scoped integration processes while work runs.

## 5. M1 — Prove the integration seams before the full build

Inspect installed CLI help, native hook schemas and the actual managed source; generate the installed app-server schema in scratch space. Record binary/schema/config fingerprints. Use new sacrificial sessions and scoped temporary hooks/skills, not production conversations or a premature final global install.

Implement tiny reusable probes under `tests/probes/` for these required gates:

1. **GATE-ID:** from real Claude and Codex tool executions, prove the caller's native session ID, authoritative owner and incarnation. Test same-cwd sessions and transient child shells. Missing binding is not permission to choose the newest session or claim human identity.
2. **GATE-INGRESS:** prove exact-owner Claude inbox and Codex queue delivery, recording policy, recipient topology and sender-side process effects. Distinguish accepted input from ingestion and a real reply.
3. **GATE-SUPERVISOR:** from each actual provider tool context, launch a finite harmless helper that survives the tool call returning, holds no terminal streams, and cleans its owned non-model child on lease loss. A human-shell launch alone is insufficient evidence.
4. **GATE-RETURN:** have a scoped helper send a tagged completion to each original parent through the native path and observe that same native UUID receive it. Do not substitute a new resume writer, a cloned parent or a T3 proxy thread.
5. **GATE-BOOTSTRAP:** establish the owned-provider startup/tool-profile contract, including inactive MCP bootstrap becoming bound after the emitted native ID. A hook/facade cannot block the event it needs to finish startup or double-register an owned worker.

Codex 0.163.0's external-queue watcher, sender `--no-daemon` validation and unloaded-target behavior are separate facts. Test ordinary configured recipients and explicitly standalone recipients separately, without adding `--no-daemon` to queue sends or changing the user's daemon configuration. A supported ephemeral queue writer is permissible only when it does not resume/create a second conversation writer and the original loaded owner consumes the submission.

If the native route passes the required owner/return probes, use it and do not patch Codex. If a required route reproducibly fails, implement the already-authorized narrow section-9 inbox seam and focused tests through CodexMCP, then rerun that failed probe. This M1B fallback is allowed before the full product build; do not demand the finished messaging system as a prerequisite to proving its missing transport. A written fallback choice is not a passed gate.

Validate Claude's installed wire contract and inbound policy without impersonation, borrowed tokens or global `crossSessionInbound=accept`. Test-only policy changes can characterize the transport but must be disclosed and separated from unchanged everyday-policy behavior.

Produce `evidence/feasibility.json`, `capabilities.json`, a native-queue decision record and an updated checkpoint. Probes are evidence of integration seams, not finished-feature acceptance. Pure schemas/tests may proceed around a bounded blocker; do not build a large scheduler around an unproven owner/return assumption. Count every live probe turn in the single allowance.

## 6. M2–M3 — Implement identity and native messaging

Implement the schemas, local journal, exact-title resolver, per-session incarnation, provider-home qualification, verified caller binding, sender provenance, message IDs, exchange IDs and reply correlation.

Titles are aliases; native IDs are identity. Never route by cwd, most recent transcript, fuzzy match or focused terminal. Duplicate names return explicit candidates. A resumed conversation retains its stable identity but gets a new live incarnation; a different conversation with the same title must not receive an old reply.

Implement all four message directions, explicit replies, bounded inbox/result reads, busy-next-turn semantics, accurate receipts, idempotency and unknown-delivery handling. Never equate a successful socket write or native queue submission with model processing.

Use the same routing core from the CLI and any MCP facade. Bind mutating tools to the actual caller; do not expose an arbitrary `from` field. An unbound invocation is non-privileged local origin, not proof of human authority and not an invented model reply address. Controller completion messages use their own group-scoped actor/lease, not a borrowed parent/worker identity.

For retries, consult the existing `(caller, client_request_id)` record before resolving a mutable title again. For replies, require the caller to be the recorded recipient and target the immediate referenced message author, preserving the exchange. Implement explicit reply-to-reply continuation, not perpetual routing to the exchange's first sender.

Distinguish a 64-KiB peer-delivery frame from the larger bounded local result-response channel. Held/unknown outcomes remain reconcilable. A local timeout after native admission does not prove an accepted message expired or can never execute.

Do not claim exactly-once model execution across external-system crash windows. Persist uncertain outcomes and reconcile; never blindly replay a send whose acceptance is unknown.

## 7. M4–M5 — Implement workers, delegation and results

Implement idempotent `spawn_many`, native worker naming, model/effort selection, explicit ownership leases, concurrent startup, task/result state machines, follow-ups, sibling grants, cancellation and native history retention.

The parent may be either provider. The controller is ordinary local code, not an additional reasoning model. One finite supervisor owns each delegation group and exits after work/grace. Reuse the M1-proven detached bootstrap, readiness and lease-pipe contracts. Do not install a permanent broker or login service.

Maintain logical controller ownership across Claude's serialized turn processes. A normal `-p` turn ending does not kill the worker identity or its granted child groups. Inherited hooks report observations but cannot replace the controller route or unregister it between turns. Bootstrapped MCP servers initialize in a nonmutating unbound state, then activate only after the native ID is verified.

Construct a launch-local environment that preserves the intended provider home/auth route but does not copy the parent's private binding, messaging token or `CLAUDE_ENV_FILE` into a new worker. Do not bypass provider nested-session guards by arbitrary environment deletion; establish a supported launch contract in M1.

Verify `gpt-6.1-sol` and `xhigh` through the installed catalog and bounded inference evidence. A catalog entry is not an entitlement guarantee. If unavailable, report the exact requirement failure; do not substitute another model/effort and count that as a pass. For Claude defaults, resolve the existing configured model and report what the provider actually used.

Workers must become actual separate native conversations, not two labels for one session. Launch them concurrently and collect separately attributable final results. Keep only focused task context and approved artifacts in each child; do not copy whole parent histories.

A parent must receive results through its original verified native return path or a bounded wait tool. Implement bounded waiter leases, not a permanent claim by any tool that once called `wait`. A timeout/disconnect before result output releases the claim and follows the chosen push/retain fallback. Uncertain tool-result output or an uncertain native push is reconciled, not automatically duplicated. Immutable result reads remain available without starting extra turns.

Prioritize an early integrated LIVE-07 and LIVE-08 slice once the minimum safe messaging/worker contracts exist. Do not spend the entire live allowance polishing every edge case before proving real cross-provider workers and original-parent returns. The remaining M3–M6 requirements still must be completed.

Default sibling communication is limited; grant it when the parent's task requests collaboration. Enforce concurrency/depth/time limits in code, reserve capacity atomically, reclaim only safe idle-grace capacity, and reject a slot-deadlocked launch rather than leaving a model waiting indefinitely. A worker cannot raise its authority or start unbounded descendants by sending another message. Preserve initial results; correlate later peer follow-ups separately instead of overwriting a completed task or sending the same aggregate twice.

For parallel modifying tasks, use isolated worktrees with an exact baseline. Never stash/reset/clean the original checkout or automatically merge worker changes. Preserve dirty/untracked user state and do not copy secrets automatically. Read-only analysis must be enforced by the effective tool/sandbox profile, including inherited MCP/plugin tools, not only by prompt wording. Apply restrictions only to the new worker; preserve the parent's existing global registrations and permissions.

## 8. M6 — Reliability, installation and regression verification

Implement the unit/property tests, fake-provider contract tests, installed-passive checks and bounded live suite in the specification. Fix failures rather than stopping after the first failed test.

Exercise interleaved events, partial streams, stale identities, uncertain sends, alias reuse on retry, duplicate spawn IDs, reversed/multi-hop replies, late completion events, expired waiter leases, inactive-to-bound startup, logical worker turn boundaries, crashes, parent disappearance, cancellation, quota and permission failures. Confirm cleanup touches only verified owned processes. Reconcile source/binary/config changes before reusing an earlier PASS; a current prototype probe is not automatically final-product evidence.

Run existing CodexMCP naming/launcher/manager regressions without unnecessarily rebuilding unrelated code. If the conditional Rust patch was selected, run focused Rust tests plus the established preservation suite against a candidate package before atomic activation. Do not restart active user sessions to load it; use new test sessions.

Install only namespaced owned hooks/skills/config entries and the `npeer` command. Check command collisions. Preserve unrelated config semantically, including default-valued fields and current MCP enable/disable states. A second install must be idempotent. Test owned-block uninstall against fixtures that include later unrelated edits.

Prepare daily-use documentation, exact commands, examples, supported-host matrix, limitations, update compatibility, and targeted rollback. Do not claim private Codex Desktop/T3 backend support without a supported, tested ownership path.

## 9. Live-test authorization, ordering and boundaries

This kickoff authorizes small harmless model-turn tests in new disposable native sessions and a fixture repository. It does not authorize unrelated production work or unbounded usage.

One durable verification ceiling across all stages and restarts:

- **48 top-level submitted test turns total.**
- **Eight concurrent owned workers maximum.**
- **60 minutes of active live testing.**
- Short fixture tasks, small results and supported per-run tool/turn limits.

Count initial parent prompts, worker inputs, message-triggered recipient turns, response-triggered turns, parent completion wakeups, resumed input and retries—not only the prompts a human typed. Reserve before submission and retain reservations for uncertain/accepted work. Provider-internal tool/model rounds are additional and must be recorded/capped where supported. Do not invent subscription-dollar estimates.

Use the specification's planning allocation: 10 turns for M1 integration probes, 16 reserved for the first two cross-provider two-worker delegation/return paths, 16 for remaining mandatory assertions and 6 for diagnostic retries. Suballocations can shift in the recorded plan but the total cannot increase autonomously. Do not consume the delegation reserve with optional Desktop/T3 experiments or repeated undirected retries.

Persist reservations, actual observed usage, unsettled native work and active-test intervals in `evidence/live-budget.json`. Measure elapsed active testing as the union of intervals with admitted live-test activity, including queue/provider waiting—not the sum across concurrent workers and not compilation time. Context compaction or restarting this implementing session does not reset the allowance.

Required real evidence still includes all four peer directions, successive/crossed replies, both cross-provider two-worker delegations, same-provider delegation, sibling exchange, busy/idle behavior, duplicate/stale targets, restart, owned cancellation, parent return modes and preservation regressions. A run may support multiple assertions when the same exact evidence proves them; do not weaken the tests to fit the allowance.

Require actual parent tool calls, distinct child native IDs, matching provider events/results and overlapping in-flight task intervals. Do not claim access to vendor GPU scheduling. A script that merely makes two task objects, sequential launches disguised by delayed cleanup, or a parent answering its own delegated tasks does not pass.

A PTY may be used only as a sacrificial native-TUI test harness or to simulate a human draft for noninterference testing. Product peer delivery must still use the native adapter, never synthetic typing. Prove Herdr/T3 independence in an isolated test environment without stopping unrelated active work. If a stricter host-stopped variant cannot run, mark it blocked and describe the exact narrower isolation rather than inventing proof.

At the allowance ceiling, stop new paid submissions, settle/cancel only already owned bounded test work, continue non-model implementation/tests and report remaining acceptance. Reconcile residual test queue items through supported exact-item APIs; never leave forgotten accepted prompts to execute unexpectedly later. No repeated speculative live retries.

No production-session prompt injection, credential extraction, borrowed receiver tokens, global inbound-policy changes, broad permission escalation or private-backend takeover is authorized. Disposable test policy must be explicit and distinct from the unchanged production configuration. Fixture workers must not start unrelated native recursive subagents; any observed vendor-native children/rounds are reported separately from NativePeers' owned-worker count.

## 10. Hard stop conditions and autonomy

Do not stop for ordinary implementation choices, recoverable test failures, a missing optional host mode, or a native-queue failure covered by the approved inbox patch.

Pause only the affected operation for a genuine blocker: unavailable required credentials/model, a native-policy refusal needing human action, inability to establish exclusive ownership, inaccessible necessary source, a destructive migration, an unexpected conflicting owner/edit, or an unresolved preservation regression. Exhausting the live allowance stops new live tests, not independent non-model implementation; checkpoint and report the exact acceptance remaining.

Continue unrelated authorized work, preserve diagnostic evidence, and report the smallest required intervention. Do not weaken a requirement or bypass a native permission boundary just to obtain a green test.

Never run broad home-directory cleanup, forceful Git resets, provider-history edits, blanket service kills, an unrelated Codex update, or a historic manager rollback that removes the completed naming/MCP work.

## 11. Final output and verdict

Leave the software installed and ready for its verified daily-use modes. Return a technically precise completion report containing:

1. What was implemented and the project/install paths.
2. Whether the native queue passed or the conditional patch was required, with evidence.
3. Actual provider versions, executable paths, model/effort observations and authentication route without secrets.
4. Unit/mock/passive/live results separately, with `PASS`, `FAIL`, `BLOCKED` or `NOT_RUN` per acceptance check.
5. Exact native launch, discovery, send/reply, spawn, wait, cancel, result and resume commands.
6. A concrete example of Claude spawning two GPT-6.1 Sol/xhigh workers and Codex spawning two Claude workers.
7. Evidence that results reached the original parent conversations and workers genuinely ran concurrently.
8. Preservation results for naming, empty-session discovery, resume shortcuts, dynamic MCP, YOLO, daemon configuration, authentication, history, and managed updates.
9. Exact unsupported/experimental host modes and remaining limitations.
10. Files/config keys changed, targeted rollback/uninstall instructions and any manual action genuinely still needed.
11. Canonical document/code fingerprints, final checkpoint, consumed/reserved test allowance, unresolved native attempts and the exact continuation action if a gate remains blocked.
12. Native-backed tool/skill behavior versus vendor-native subagent UI, and installed integration versus native-policy permission for unattended everyday use.

Use an unqualified overall `PASS` only when the required core messaging and delegation tests passed against the current applicable implementation/runtime fingerprints. Documentation, source inspection, file hashes, prototype probes and mocked tests cannot substitute for required live outcomes. If code is complete but an exact-model, policy or budget gate prevents live acceptance, state that distinction clearly. The review's approval of this design is not a runtime PASS.

Implement the approved specification through completion. Do not finish with another proposal, a list of future milestones, or only scaffolding. Preserve the working environment and deliver the smallest robust native system that satisfies both peer messaging and cross-provider delegation.
