# NativePeers Lineage for Claude Code — Implementation Specification

**Revision 1.0 · 2026-10-30 · companion: `CLAUDE_LINEAGE_KICKOFF.md` revision 1.0**

**Status:** implementation-ready design. Its feasibility core was reproduced in a separate Linux container (label **X**, section 2). It is not an implementation, and nothing in it is target-Mac acceptance.

**Relationship to existing contracts:** this document extends `IMPLEMENTATION.md`. Identity, caller binding, the journal, supervisors, ownership, the uncertainty discipline and the evidence rules all remain normative. One clause changes. `IMPLEMENTATION.md` §11.4 and §17.3 say not to promise that workers appear in Claude's built-in Agent UI. That was correct when there was no supported seam. Claude Code's function-hooks API ("mods") now provides one. For **Claude Code parents only**, this document supersedes that non-promise. Codex, Antigravity and Grok parents are unchanged.

**Working names:**
- **Feature:** NativePeers Lineage.
- **Mod:** plugin name `np`, giving agent types `np:codex`, `np:claude`, `np:antigravity` and `np:grok`, tools `mcp__np__mirror` and `mcp__np__catalog`, and the command `/np`.
- **New NativePeers surfaces:** `nativepeers.activity.v1`, `npeer bridge`, `npeer activity`, `npeer input`, `npeer interrupt` and `npeer mirror …`.

---

## 0. Executive decision

**Each NativePeers worker becomes a real Claude Code subagent.** Every worker task that belongs to a Claude Code parent session is represented in that session by a **shadow agent**:
- A shadow is a genuine Claude Code subagent of a mod-registered type.
- The mod answers every one of its model steps from the worker's normalized activity stream, through the `turn.step` hook.
- The shadow never calls a Claude model. Its transcript is a faithful, bounded projection of the worker's native session: the brief, assistant text, every external tool call with its input and output, file changes and the final result.

**Claude Code then supplies the rest of the experience natively**, for Codex, Grok, Antigravity and Claude workers alike:
- the tasks list below the prompt;
- the agent transcript view;
- Enter-to-message routing to the viewed agent;
- background completion notifications to the parent model, for shadows the engine's Agent tool started and for resumed runs (X8, X9, X14);
- SendMessage addressing;
- resumption by message;
- SDK task events that the desktop app consumes.

**NativePeers remains the system of record.** It owns spawn, supervision, ownership, worktrees, quotas, fallback, results, artifacts and every provider-specific detail. It gains three things:
- a normalized per-worker **activity log**;
- a **session-scoped bridge** that serves the mod over a private Unix socket;
- **delivery ownership per task generation**, with a mirror lease, so each completion reaches the parent exactly once: through the engine's native notification where it provides one, otherwise through NativePeers' existing push or retain.

**The mod draws a Lineage band, plus an optional pane.** They show what the native list cannot: provider, model and effort; the lineage tree and descendants; Stop all; patch preview; and native IDs. They also act as the degraded-mode viewer whenever shadows are unavailable.

**Out of scope:**
- no patching of the Claude Code binary;
- no reverse engineering;
- no runtime dependency on T3 Code or Herdr;
- no new machine-wide daemon (the bridge lives exactly as long as the Claude session's mod).

---

## 1. Intended experience

### 1.1 Fan-out (T3 Code parity)

The person types in an ordinary `claude` session (CLI or the desktop app's Code tab):

> Spin up delegates for GPT-6.1 Sol high in Codex, Grok 4.7 in Grok Build, Gemini 4 Argon in Antigravity and Sonnet 5.5 in Claude. Tell them all to explore the codebase and find interesting things, as well as some easy wins.

**The parent model:**
1. reads the injected Lineage section (§7.3);
2. optionally calls `mcp__np__catalog` once to confirm exact model slugs;
3. issues four parallel `Agent` calls with `subagent_type` set to `np:codex`, `np:grok`, `np:antigravity` and `np:claude`. Each prompt starts with an `np` header that names the model and effort, followed by a self-contained brief.

**What happens next:**
- Within about a second, four rows appear in the native tasks list, the same list the person already uses (illustrative):

```text
 ● main
 ○ np:codex        GPT-6.1 Sol high · codebase review          1m 12s · ↓ 18.4k tokens
 ○ np:grok         Grok 4.7 · codebase review                    58s · ↓ 9.1k tokens
 ○ np:antigravity  Gemini 4 Argon high · codebase review          41s · ↓ 6.0k tokens
 ○ np:claude       Sonnet 5.5 · codebase review                 1m 03s · ↓ 12.7k tokens
```

- The Lineage band above the prompt summarizes the cross-harness state: `⟐ np · 4 delegates · 4 running · [Stop all] [Pane]`.
- The parent ends its turn. Results arrive as native background-agent notifications, and the parent synthesizes them, as with any Claude subagent.

### 1.2 Drill-down

The person selects a row with the native keys (↓, then Enter) or presses `[Open]` in the band or pane. The main view switches to that agent's transcript. The desktop app has no main view to switch (X17): there, pressing a worker in the NativePeers pane shows its conversation, with a field to message it (§7.9), and the band's `[Open]` is not offered.

- **First row:** the brief, labeled as sent by the main agent (T3's "Sent by another agent"), collapsible.
- **Streaming text:** the worker's assistant text, as markdown.
- **Tool rows:** each external tool call is a native-looking row, for example `⏺ exec  pytest -q` and `⎿ 42 passed · exit 0 · 1m 30s`. Edits show as rows with diffs (expanded with ctrl+o).
- **Live reasoning:** reasoning summaries show while running, where the provider exposes them. They are not recorded.
- **Header chip:** the band becomes `↳ Subagent of main · codex · gpt-6.1-sol · high · analysis · 2m 41s · [Interrupt] [Stop] [Resume cmd]`.

### 1.3 Interaction

**Typing while viewing a running worker:**
- The message goes to that worker as a follow-up, exactly once.
- By default it waits until the worker's current turn ends. Providers that support steering (Codex `turn/steer`) can steer it instead.
- The worker's response then streams into the same view.

**Typing to a completed worker:**
- The same native conversation is resumed (same native ID, via NativePeers' controller or `resume-worker`).
- The new turn streams in, and the parent receives a second native notification (T3's follow-up semantics).

**Parent follow-ups:** the parent model can follow up with `SendMessage` to the agent. It takes the same path, with provenance `parent-model`.

**Stopping:**
- **Esc** in a worker view interrupts only that worker's current turn.
- **Stopping the agent** from the tasks list (or `TaskStop`) cancels only that worker's task.
- **`[Stop all]` / `/np stop all`** cancels all of this parent's active groups and cascades to their descendants.
- **Esc in the main view** interrupts the parent's turn only. Workers keep running, as native background agents do.

### 1.4 Existing NativePeers flows are adopted

When the parent follows the existing skill and runs `npeer spawn` (Bash) or calls the MCP `worker_spawn`, the mod adopts the new workers into shadows. The person sees the same rows and can open and message them.

Their **initial** completions keep NativePeers' own delivery (push or wait, as requested). A shadow the mod spawns itself does not notify the parent on its first completion (X8), so for those tasks the shadow is display-only. Follow-ups typed into an adopted shadow are delivered natively, like any resumed run (X9; gate G13).

### 1.5 Desktop

The same mod, installed at user scope, loads into the desktop app's local Code sessions:
- Shadows are ordinary subagents, so they produce the same SDK task events (`task_started`, `task_progress`, `task_notification`) that the desktop app renders.
- The pane carries the band's summary on the `desktop` surface. It opens beside the conversation when a delegate starts, and no band draws above the prompt (`band: always` draws it there too).
- A desktop session in Auto ends every shadow run with `SubagentHandback` (§7.5.2), as the engine requires there (X15).
- Desktop parity has preconditions, gated in M0 (§13): NativePeers must recognize desktop-hosted engines, and `$.process`/`$.http` must be available in that host.

---

## 2. Evidence baseline

### 2.1 Labels

These are `IMPLEMENTATION.md` §2.1's labels, plus one new label (X):

| Label | Meaning |
|---|---|
| **U** | User-reported, for example the screenshot of the tasks list. |
| **D** | Documented by the engine's own API declarations: `claude-code.d.ts`, written by Claude Code 2.1.314, and the plugin-authoring reference. |
| **S** | Source-inspected: the NativePeers repository at `0ee014f` (2026-10-27), and T3 Code at `028ed851d552eda86d8c28a758de3d8157b80d56` (2026-10-30, read by a research agent). |
| **P** | Proposed in this document. |
| **X** | Externally reproduced in a separate Linux container: Claude Code 2.1.314, headless `claude -p --plugin-dir`, clean `env -i` home. **Not target-Mac evidence.** |
| **L** | Locally verified on the target Mac by the implementing agent, with an evidence file. |

Never relabel D, S or X as L.

### 2.2 What X established

Four throwaway mods (`npspike` to `npspike4`; Appendix A) were run headless against Claude Code 2.1.314. A test-only `turn.step` hook answered the main loop's own steps, so the driver needed no model either. An independent review agent then re-checked the findings and ran its own Path A probe (X14).

| # | Finding | Evidence |
|---|---|---|
| X1 | `$.agent.register({ name:'mirror', …, tools:[…], model:'haiku', omitClaudeMd:true })` returns `{ agent:'npspike:mirror' }`. `$.agent.spawn({ subagentType:'npspike:mirror', prompt, description })` called from a `tool.call` hook starts a **background** subagent. The stream shows `task_started` with `task_type:'local_agent'`, `subagent_type`, `description` and `is_backgrounded:true`. `$.agent.list()` lists it with `spawnedBy`. | stream-json + mod log |
| X2 | A **different** hook, `turn.step`, saw every step of that subagent (`e.agentId` set) and answered alone with `text`, `tool`, `input` and `stop` chunks. **No model request was made:** `duration_api_ms: 0`, `total_cost_usd: 0`, all usage zero. The engine labeled messages `msg_hook_*`. | result record |
| X3 | Yielding a tool call to a plugin tool (`mcp__npspike__mirror_exec`, registered with `$.tool.register`) made the engine call the mod's `tool.call` hook. Its `{ result: '…' }` was recorded as a native `tool_use`/`tool_result` pair in the subagent transcript, with `parent_tool_use_id` linking to the spawn. **No permission prompt or check surfaced in `-p`.** | stream-json |
| X4 | Plugin tools are served by an engine-hosted local HTTP MCP server (debug log: `$.tool.register … served at 127.0.0.1:<port>/<secret>/npspike`). A mirror tool call held **90 s** completed normally; there was no MCP timeout. | mod log `mirror_exec.end ms:90004` |
| X5 | A subagent step held **130 s** inside the hook (waiting on `$.process.run(['sleep','130'], { timeoutMs: 180000 })`) completed with `next.signal.aborted === false`, with `CLAUDE_ENABLE_STREAM_WATCHDOG=1` set. `$.process.run` defaults to a 30 s timeout and caps at 10 minutes (D). | mod log |
| X6 | A `thinking` chunk yielded by the hook was **not recorded** in the subagent transcript. Only text and `tool_use` blocks were appended. | `session.append` log |
| X7 | `usage` reported on the stop chunk surfaced in `turn.complete.usage` (output tokens summed, model id replaced by the agent's resolved model). It was **not billed** (`modelUsage` excluded it). | result record |
| X8 | Every completion emitted an SDK `task_notification` stream event (`summary` = the shadow's final text, plus an `output_file`). **The parent conversation, however, did not receive the first completion of a shadow the mod spawned with `$.agent.spawn`.** The debug log shows `[enqueueAgentNotification] skipped … reason=already-notified`, and spikes 2 and 3 show no `<task-notification>` in the parent. The plugin's spawn call consumes that first answer. | debug log + stream |
| X9 | `$.session.send({ to:{ agentId }, text })` to a **completed** shadow returned `{ isDelivered:true }`. It raised `agent.spawn` with `resume:{agentId}`, appended a user row (`door:'prompt'`, `origin.kind:'coordinator'`, text prefixed `The coordinator sent a message while you were working:\n`), and started a new turn (`turn.step` index 0) under the same task id and a new `run_id`. **That resumed run's completion did reach the parent conversation**: a `queue` record, then `prompt.submit` with `origin.kind:'task-notification'` and `turnId` set, delivered as `<task-notification>…`. | mod log + stream |
| X10 | A `turn.step` hook that throws mid-stream **without** `.catch` falls through to a **real model request**: the shadow's Haiku answered the placeholder system prompt verbatim, and it cost **$0.00027**. With `.catch` written as an async generator **that returns a complete `TurnStepResult`**, both a throw (`next.error.kind:'throw'`) and a budget overrun (`'timeout'`, at exactly 10 s) were contained: the handler's chunks became the answer and **no API request was made**. The return is load-bearing. The review probe's `.catch` yielded text and stop but returned nothing, and it fell through to real requests. A main hook with a bare `return` fails as "wrong shape (no result)" (D: a handler returning `undefined` leaves the hook absent). | spike2 vs spike3; review probe |
| X11 | Awaiting a plain JavaScript promise inside a hook consumes the 10 s own-time budget. Awaiting a `$` call does not. All mirror waits must therefore be `$` calls in flight. | spike3 `stall` |
| X12 | `claude plugin validate` refuses passing `$` to a closure. It accepts a top-level function declaration, a top-level `const f = async (…) => …`, or a function imported from the mod's own files. It refuses a top-level `const` that merely aliases another function. | validator (spikes and review) |
| X13 | With `ANTHROPIC_BASE_URL` pointed at a local **tripwire** stub (HTTP 400, request counter, no header logging), a deliberate fault fallthrough sent all 6 of its model requests to the stub. The run showed `total_cost_usd: 0`, `modelUsage {}`, and a synthetic `API Error: 400 …` row in the shadow. Running without credentials does **not** prevent billing: spike 2 billed with `apiKeySource:"none"`. | tripwire log + result |
| X14 | The review probe's main-loop hook yielded a native **`Agent` tool call** (Path A), and the first completion **did** reach the parent conversation (`queue` record, then `prompt.submit` with origin `task-notification`). The same probe had a hook bug and made real, billed requests; that does not affect this observation. | review probe log |
| X15 | In a session in **auto mode** (the desktop app's Auto, or `--permission-mode auto`), the engine requires every subagent run to end with a `SubagentHandback({ message })` call: plain text at the end is not delivered. It opens the run with a note row (`session.append` door `note`, `origin.kind:'engine'`), and after a run that ends on plain text it appends `[handback-send-enforce] …` and asks for another step. Seen on 2.1.309 (the desktop app's engine) and 2.1.314. | desktop session `5a5bfe52` shadow transcript; gate G6 capture; `run_handback.py` control on the pre-fix mod |
| X16 | Only auto mode's classifier may allow `SubagentHandback`: a `tool.check` allow is refused (`Only the auto-mode classifier can allow SubagentHandback: an allow of type 'hook' is not the classifier's verdict`), and an agent type with a `tools` list must name the tool. With no verdict available the engine delivers the report flagged. | `run_handback.py` on 2.1.309 and 2.1.314 |
| X17 | On the desktop surface `$.ui.view({ agentId })` answers `{ deny: 'no main view here' }`; the app's Background tasks show a run's transcript without a field to message it, and hide it once it ends. The engine heads a plugin's toasts with the plugin's name. | desktop run, 2026-10-30; terminal captures showing `np: np: interrupt` |
| X18 | A background agent gets periodic `agent_summary` side requests made with its own model: for a shadow, the unserved `np-mirror-*` model, which the API answers with an error and no content. They occur only while the agent runs. | `run_handback.py` control: 10 requests during a 90 s hang |
| X19 | The desktop app shows a run's `SubagentHandback` message as its last message. A shadow makes no model request, so the engine's server-side review of the subagent's work has no verdict: the report arrives flagged ("the server-side auto mode classifier … was unavailable … Please carefully verify the subagent's actions and output before acting on them"), and a parent model may check the result again. | desktop run, 2026-10-30 11:49 PM EDT |
| X20 | In a fresh home, Claude Code 2.1.314 starts sessions in auto mode by default ("Auto mode is now Claude Code's default permission mode"), so handback runs (X15) are the ordinary case. | `run_pane_conversation.py`, 2026-10-31 |

**Verification cost.** Two runs made billed Claude requests: one deliberate fallthrough in spike 2 ($0.00027), and the review probe with a hook bug ($0.079). Those two are why the tripwire (X13) is mandatory for every harness run.

### 2.3 Facts the design relies on that are D (declared, not exercised)

- `$.ui.view({ agentId })` puts an agent's transcript in the main view. It works only while answering the person's own input, such as a Button press, a pick or a typed command.
- The viewed agent takes the prompt's next Enter.
- `AbovePrompt` and `Pane` sites receive `e.props.view.agentId`.
- `AbovePrompt` is raised on terminal and desktop. `Pane` is raised on every surface. `Spinner` is raised on terminal, desktop and vscode.
- `HookBudget`: `ms: 10_000` of the hook's **own** time, `catchMs: 1_000`, `lingerMs: 5_000`. The clock stops while `next(e)` or any `$` call is in flight, except `$.clock` waits. Past the budget the hook is absent: its `.catch` is asked, otherwise `next(e)` runs on its behalf. **For `turn.step`, "`next(e)` on its behalf" is a real model call.**
- A subagent spawned through `$.agent.spawn` "steps past that hook, as its tool calls do; every other hook sees its steps." This is the re-entry rule.
- `$.process` is "CLI only".
- `$.http.fetch(url, { socketPath })` reaches a Unix socket.
- `$.session.id()` is the transcript file's name, which is the native session UUID.
- `$.env.get` exists.
- `session.append` fires once per stored row, with `uuid`, `door`, `origin` and `agentId`.
- `session.receive` and `session.send` carry `agentId`.
- `session.compact` carries `agentId` and can `{ skip }`.
- `tool.check` can answer any verdict.
- `tool.describe` can reword a tool and defer it, but cannot hide it.
- `agent.offer` can hide a type from the model.
- `AgentStatus` includes `idle`, `completed`, `failed` and `killed`. A message may resume an ended agent.
- `turn.complete` carries `isAborted` and `reason`. `TurnCompleteReason` is a closed set (`answer`, `aborted`, `refusal`, `error`), and `isAborted` means `reason === 'aborted'`. It cannot tell a person's Esc from a kill or an engine-side abort; only `AgentStatus: 'killed'` identifies a kill.
- A `.catch` handler's return within its grace is the hook's result. `undefined` leaves the hook absent, and `next(e)` then runs on its behalf.
- `CommandSpec.immediate` lets a `/command` run while a turn is in flight. Without it, the command waits for the turn to end.
- `session.append` door `queue` is not a conversation row: it is a message-queue record's text.
- After `/clear`, the process continues under a **new session id**, and no `session.start` fires.
- In plan mode a `tool.check` hook can only tighten a call not shown to be a read. Subagents inherit the parent's permission mode unless `AgentSpec.permissionMode` sets one.
- `$.ui.notify` sends at once through the person's channel and answers `no-surface` in desktop and SDK hosts. `$.ui.copy` has no path on remote surfaces.
- `$.http.fetch`'s `socketPath` is described as "one per session, in a private directory".
- `AgentSpec.background: true` makes every spawn of the type run in the background. `autoCompactWindow` can only lower the window.

### 2.4 Still unknown: these become M0 gates (§13)

- **Interactive TUI:** how the tasks list renders a plugin-spawned shadow (type, description, activity text, tokens), and what feeds its activity text.
- **Person routing:** that typing while viewing a shadow routes to it, and through which event chain (`session.receive` and/or `prompt.submit`, with what origin and framing).
- **Esc and stop:** what Esc and kill produce for a shadow (`turn.complete.reason`, `AgentStatus`).
- **Duration limits:** 20-minute mirror tool calls and 30-minute silent steps; behavior under `maxTurns` (how steps count).
- **Spawn origins:** whether a shadow spawned from a timer, a `ui.press` or a `command.run` is still seen by the mod's `turn.step` hook.
- **Permissions:** whether mirror tools prompt in `default`, `acceptEdits` and `plan` modes interactively.
- **Desktop:**
  - rendering of shadows, and of the band and pane;
  - availability of `$.process` and `$.http`;
  - the engine executable's path;
  - whether `CLAUDE_CODE_MESSAGING_SOCKET` is present.
- **Hot reload:** what happens during a held step.
- **Session lifecycle:** what remains visible after `claude --resume` and after `/clear` (which changes the session id).
- **Notification by origin:** whether the parent conversation receives completions for shadows started by the Agent tool (X14: yes), by `$.agent.spawn` (X8: not for first runs), for runs resumed by the person typing, and for follow-ups queued while running.
- **Tripwire on the Mac:** that `ANTHROPIC_BASE_URL` routing catches every model request under the Mac's credential sources (Keychain OAuth), before any harness run counts as free.

### 2.5 NativePeers facts this design builds on (S, from `0ee014f`)

**Controllers already read each provider's live stream.** They extract only results:
- **Codex:** `turn/start` with `approvalPolicy:'never'`. The controller handles `item/completed` (`agentMessage`), `turn/completed`, `thread/tokenUsage/updated` and `account/rateLimits/updated`, and interrupts with `turn/interrupt`.
- **Claude:** `-p --output-format stream-json --verbose`; `system`, `assistant` and `result` events.
- **Antigravity:** `--output-format stream-json`.
- **Grok:** a native stream with `tool_call`, `error` and `end`.

No normalized activity is persisted. The journal's `events` table holds lifecycle and status categories only. `delegation.py` derives `progress.last_native_activity` from it.

**Claude caller binding:**
- The `SessionStart` hook registers `peer_instances`, writes `bindings/<id>` and appends `NPEER_HOME`/`NPEER_BINDING` to `CLAUDE_ENV_FILE`.
- Registration **requires `CLAUDE_CODE_MESSAGING_SOCKET`**.
- `bind_caller` requires the registered native owner process to be in the caller's ancestry.
- `native_binaries()` recognizes only `~/.local/bin/claude` for Claude.

**Defaults and existing contracts:**
- `completion_mode` is `push` or `wait`. Parent push goes through Claude's native inbox socket.
- An interactive parent's exit cancels its active owned work unless `survive_parent=true` (§10.4).
- Owned-worker MCP facades are named `nativepeers`. The mod is deliberately **not** named `nativepeers`, so `mcp__np__*` cannot collide with `mcp__nativepeers__*`.
- `.claude/` is gitignored.
- `tests/unit/test_evidence_checkpoint.py` hashes only the docs listed in its evidence document, so new docs are safe.

---

## 3. Goals, non-goals, acceptance boundary

### 3.1 Required

1. **Native visibility:** NativePeers workers of all four providers appear in a Claude Code parent's native tasks list while they run, labeled with provider, model, effort and title.
2. **Live transcript:** selecting one shows its live, faithful transcript in the native agent view, in the CLI and the desktop app.
3. **Native interaction:**
   - The person can message a worker from that view: running workers queue (or steer where supported), and completed workers resume the same native conversation.
   - The parent model can do the same with `SendMessage`.
   - Esc interrupts, and stop cancels.
4. **Native spawning:** the parent model can delegate natively with `Agent(subagent_type:"np:<provider>")`, choosing exact models and efforts.
5. **Adoption:** workers created through the existing `npeer spawn`/`worker_spawn` paths are adopted automatically.
6. **Exactly-once delivery:** each task generation's completion reaches the parent exactly once, through one owner recorded at creation (§6.4):
   - the native notification for Agent-tool spawns and mod-forwarded follow-ups;
   - NativePeers' own push or wait for adopted initial tasks.

   A lapsed owner falls back to push or retain, under NativePeers' uncertainty discipline.
7. **Zero Claude inference:** shadows consume no Claude inference, verified by measurement. Every failure path fails closed.
8. **Cross-harness extras:** the Lineage band and pane show the extras, and degrade gracefully.
9. **Preservation:** every existing NativePeers behavior, test, protected baseline and non-Claude parent path is preserved.

### 3.2 Not required, and not to be added silently

- patching, wrapping or reverse engineering the Claude Code binary or its bundled JavaScript;
- T3 Code, Herdr, tmux, terminal injection or a GUI as runtime dependencies;
- showing Claude workers inside Codex, Antigravity or Grok UIs;
- mirroring approvals (workers run under fixed NativePeers profiles; Codex uses `approvalPolicy:'never'`);
- cloud, remote or mobile sessions;
- nested shadows for grandchildren (descendants appear in the band and pane tree, read-only, in v1);
- a batch `delegate` tool (parallel `Agent` calls cover it, so this is optional later);
- coalescing parallel `Agent` calls into one NativePeers group (optional later; v1 uses one group per call);
- changing NativePeers' parent-exit policy.

---

## 4. Architecture

### 4.1 Components

| Component | Where | Responsibility | Lifetime |
|---|---|---|---|
| Activity recorder and normalizers | `nativepeers/activity/` | Turn each provider's live stream into `nativepeers.activity.v1` events. Persist them append-only per worker, with redaction and bounds. | Inside each worker controller |
| History backfill readers | `nativepeers/activity/backfill_*.py` | Read native histories read-only, version-gated, for workers recorded before the activity log existed (or when the log is missing). | On demand |
| Bridge | `npeer bridge` (`nativepeers/bridge.py`) | Session-scoped HTTP/1.1 over a private Unix socket:<br>• verify the Claude caller once;<br>• serve lineage, activity long-polls, spawn, input, interrupt, cancel, catalog and artifacts;<br>• hold mirror leases. | Spawned by the mod with `$.process.spawn`. Killed with the mod; also exits on Claude owner death, heartbeat loss or idle. |
| Delivery ownership | `nativepeers/mirror.py` + journal migration | A per-generation delivery owner plus the mirror lease, for exactly-once delivery with push or retain fallback. | Durable rows |
| The `np` mod | `integrations/claude/mod/np/` | Agent types, shadow mirror engine, mirror tool, guards, input forwarding, Lineage band and pane, commands, state. | Each interactive Claude Code session, at user scope |
| Skill and convention updates | `integrations/claude/skills/nativepeers/SKILL.md`, `docs/CLAUDE_CONVENTIONS.md` | Prefer `np:*` agent types when they are listed. | Installed text |
| Installer | `npeer install --claude-mod` / `npeer uninstall` | Register the mod folder as a local marketplace and install it at user scope, merge-only, with an uninstall manifest. | One-shot |

### 4.2 Data flow

```text
Claude Code session (CLI or desktop local session)
  main loop ──Agent(subagent_type:"np:codex", prompt:"```np {...}``` brief")──┐
                                                                               ▼
  np mod: agent.spawn hook ─ preflight ─▶ bridge POST /v1/spawn{check} ──▶ deny or allow
          next(e) → shadow agentId ─────▶ bridge POST /v1/spawn{claude_agent_id}
                                                     │
                                         NativePeers spawn_many → supervisor → controller
                                                     │                                 │
                                         provider stream ─▶ activity recorder ─▶ activity/<worker>.ndjson
                                                                                        │
  shadow loop: turn.step hook ◀── GET /v1/workers/:id/activity?after=seq&wait_ms ◀──────┘
     yields text / thinking / mirror tool_use / final answer
     mirror tool.call hook ◀── awaits the matching tool.completed (long-poll)
  person types in the shadow view ─▶ shadow user row ─▶ session.append hook captures uuid
     ─▶ next turn.step ─▶ POST /v1/workers/:id/input{client_request_id: row uuid}
  shadow completes ─▶ native task notification to the main loop (Path A: X14)
     ─▶ POST /v1/mirror/ack ─▶ completion route MIRROR_DELIVERED (no push)
```

### 4.3 Why shadow agents: rejected alternatives

| Alternative | Why not |
|---|---|
| **Lookalike panel only** (draw our own list in `AbovePrompt` or a `Pane`) | It is not the native list. A mod cannot add rows to the tasks list: there is no render site for it (D: `RenderComponent` has none), and `$.ui.view` refuses an id the list does not show. Messaging would need a custom input field. **Kept as the degraded mode.** |
| **Proxy Claude subagent** (a Haiku agent that calls `npeer` tools) | It spends tokens, behaves nondeterministically, and its transcript is the proxy's rather than the worker's. |
| **Agent teams / teammates** | Claude-only; teammates are Claude loops or panes and cannot host foreign harnesses. |
| **MCP channels** | They deliver to the main conversation only: no per-agent view or lineage. |
| **Patching Claude Code's bundled JS** (tweakcc-style) or the binary | Breaks on every update, conflicts with NativePeers' runtime-approval regime, and is unnecessary now that a supported API exists. |
| **T3 Code as a runtime host** | Violates NativePeers' independence requirement (§4.4 there). T3 is a design reference only (§16). |
| **Reading provider transcripts directly in the mod** | It would duplicate four version-sensitive parsers in TypeScript, outside NativePeers' runtime-approval and fixture regime. Normalization belongs in NativePeers. |

---

## 5. Claude Code engine contract (what the mod may rely on)

**Minimum engine:** the first version where M0 passes on the target Mac. Record it in `compat/claude-mod.json`.

**Version check:** at `session.start`, the mod reads `$.session.version()`. Below the recorded minimum, or on an unrecorded major API change, it activates only the degraded band. It must not register agent types.

**Types:** come from the engine. Load the plugin-authoring skill and read `types/claude-code.d.ts`, or read `<mod>/.claude-plugin/types/` once the mod is loaded. Never hand-copy them.

| Need | Engine surface | Constraint |
|---|---|---|
| Register shadow types | `$.agent.register(spec)` in `session.start` → `np:<name>` | Use AgentSpec fields only; the schema validates them. |
| Spawn shadow (Path A) | the engine's own Agent call through our `agent.spawn` hook's `next(e)` | Not a `$.agent.spawn`, so no re-entry skip. |
| Spawn shadow (B/C) | `$.agent.spawn` from a hook that is **not** `turn.step`, mirror `tool.call` or `tool.check` | Re-entry rule (D). Origins beyond `tool.call` are an M0 gate. Its first completion does not reach the parent (X8), so NativePeers keeps delivery for those tasks. |
| Answer shadow steps | `on('turn.step', async function*(…))`, answering alone | Pass through untouched for every non-shadow loop (`return yield* next(e)`). |
| Fail closed | `.catch(async function*(…))` on that registration | Mandatory (X10). The body and the handler must both **return a complete `TurnStepResult`** on every path. `undefined` or a bare `return` makes the hook absent, which is a real model call. |
| Mirror tool | `$.tool.register({ name:'mirror', isDeferred:true, … })` → `mcp__np__mirror`, answered by its own `tool.call` registration | `{ result }` only. Never pass to `next`. |
| Waits | `$.http.fetch(…, { socketPath })` long-polls of at most 25 s, looped; or `$.process.run` with an explicit `timeoutMs` (default 30 s, max 10 min) | Never await a plain promise across time (X11). |
| Inputs to shadows | `session.append` (agentId, uuid, door, origin), plus `session.send`/`session.receive` for raw text | Rows are never refusable; capture, don't block. |
| Completion to parent | engine-native for Agent-tool spawns (X14) and resumed runs (X9); **not** for the first run of a `$.agent.spawn` shadow (X8) | The delivery owner (§6.4) follows from this. |
| Resume by message | engine-native (X9) | |
| Compaction | `session.compact`, `{ skip }` for shadows | |
| Views | `$.ui.view({ agentId })` from a press, pick or command | Person-initiated only. |
| UI | `ui.render` on `AbovePrompt`, `Pane`, `ToolUse` (our tool), `UserMessage` (shadow provenance) and `Spinner` (shadow, if M0 shows it is used) | Trees validated per surface; size to `bodyColumns`. |
| Commands | `$.command.register` (with `immediate: true` for stop, interrupt, open and pane) + `command.run` | Without `immediate`, a command waits for the turn to end. |
| State | `$.state` (session, survives hot reload), `$.store` (across sessions, at most 4 MiB) | Literal `{ plugin:'np', key }` refs; contract in `types/index.d.ts`. |
| Host process | `$.process.spawn` (bridge), `$.process.run` (CLI fallback) | CLI only; desktop is an M0 gate. |
| Validator | pass `$` only to top-level function declarations, top-level `const f = async (…) => …`, or imported functions | X12. |

---

## 6. NativePeers additions

### 6.1 Activity log: `nativepeers.activity.v1`

Each worker controller writes a normalized event for every meaningful provider observation it already reads, plus the few it currently ignores (item starts, deltas, reasoning summaries, command output).

**Storage**
- **Location:** `$NPEER_HOME/activity/<worker_id>.ndjson`. The file is append-only, mode `0600`, in a directory with mode `0700`.
- **Ordering:** `seq` is strictly increasing per worker, across task generations and resumes. A `activity_index(worker_id, generation, task_id, first_seq, last_seq, terminal_seq)` journal table records turn boundaries for fast seeks.
- **Writer:** the controller is the only writer. A dedicated asyncio task appends lines. It fsyncs at `task.terminal`, and at least every 2 s while events flow.
- **Readers:** never block the writer. A torn final line is skipped until it completes.
- **Bounds (defaults; configurable in `config.toml [activity]`):**
  - an event's `data` is at most 64 KiB serialized;
  - tool output inline is at most 16 KiB, as head and tail with `truncated:true` and `bytes`;
  - a worker's log is at most 64 MiB. Past that, older segments rotate to `<worker_id>.<n>.ndjson.gz`, and readers transparently read rotated segments for backfill.
- **Full outputs:** stored as artifacts only when policy allows, through the existing `Artifacts` store, readable by the parent. Otherwise the event carries `output.artifact_id:null` with `reason`.
- **Redaction:** apply NativePeers' existing secret redaction (the same rules as captured stderr) before persisting text. Native histories in provider homes are never modified.
- **Retention:** follows workspace/history retention. `history-cleanup` and `cleanup --plan` list activity logs too, and never delete one for an active or resumable worker.

**Event envelope**

```json
{"schema":"nativepeers.activity.v1","seq":128,"at":"2026-10-30T17:12:01.123Z",
 "worker_id":"…","group_id":"…","task_id":"…","generation":2,"native_turn":"…|null","item":"…|null",
 "kind":"tool.started","data":{…},
 "source":{"provider":"codex","runtime_sha256":"…","event":"item/started"}}
```

**Kinds and data**

| Kind | `data` |
|---|---|
| `task.started` | `{origin: parent-model\|human\|controller\|fallback, client_request_id, input_preview}` |
| `message.delta` | `{text}`: assistant text increments (where the provider streams them) |
| `message.completed` | `{text, is_final_candidate: bool}`: whole assistant message (always emitted) |
| `reasoning.delta` | `{text}`: provider-exposed reasoning summaries only; never hidden chain of thought |
| `tool.started` | `{kind: exec\|edit\|read\|search\|web\|mcp\|plan\|agent\|other, title, input}` (`input` bounded and redacted) |
| `tool.progress` | `{text}`: optional output increments, display-only |
| `tool.completed` | `{status: ok\|error\|declined\|interrupted, exit_code?, duration_ms?, output:{text, truncated, bytes, artifact_id?}}` |
| `files.changed` | `{paths[], stats:{added, removed}, diff:{text, truncated, artifact_id?}}` |
| `plan.updated` | `{items:[{text, status}]}` |
| `usage` | `{input_tokens?, output_tokens?, cached_input_tokens?, reasoning_tokens?, cumulative: bool}` |
| `input.queued` | `{client_request_id, origin, mode: queue\|steer\|resume, preview}` |
| `input.accepted` | `{client_request_id, native_turn}` |
| `task.state` | `{state, reason?}` (QUEUED / RUNNING / WAITING / APPROVAL_BLOCKED …, per `IMPLEMENTATION.md` §12.1) |
| `fallback.started` | `{from:{provider, model}, to:{provider, model}, reason}` |
| `task.terminal` | `{status: completed\|interrupted\|failed\|canceled\|timed_out, final_text, result_artifact_id?, error?:{code, message}}` |
| `gap` | `{from_seq?, reason}`: lost or unparseable events, never silent |

**Semantic rules**
- `task.terminal` is the **only** completion signal. It is emitted once per task generation, after the controller's existing terminal logic decides the outcome; it never replaces that logic.
- `tool.started` and `tool.completed` pair by `item`. An unpaired start at terminal gets a synthesized `tool.completed{status:'interrupted'}`.
- Events from an older generation never follow a newer generation's `task.started`. This is `IMPLEMENTATION.md` §12.1's generation rule.

### 6.2 Provider normalizers

Derive exact method and field names **from the installed runtime**, never from memory:
- **Codex:** the generated app-server schema of the leased executable.
- **Claude:** stream-json observed from the installed CLI.
- **Grok and Antigravity:** NativePeers' existing native-stream recordings.

Normalizers are pure functions, `(provider event) → [activity events]`, unit-tested against recorded fixtures in `tests/fixtures/activity/<provider>/`.

| Provider | Source | Mapping notes |
|---|---|---|
| **Codex** (app-server JSON-RPC) | Notifications the controller already reads, plus item lifecycle and delta notifications from the installed schema (expected families: `item/started`, `item/completed`, agent-message deltas, reasoning-summary deltas, command-output deltas, `turn/completed`, `thread/tokenUsage/updated`) | Item types map as follows: `agentMessage`→message; `reasoning`→reasoning; `commandExecution`→exec; `fileChange`→edit plus `files.changed`; `mcpToolCall`→mcp; `webSearch`→web; plan/todo→`plan.updated`. Steering capability comes from the schema (`turn/steer`). |
| **Claude** (stream-json) | `assistant` content blocks (`text`, `tool_use`, `thinking`), `user` `tool_result`, `result` | `tool_use` names map as: Bash→exec; Edit/Write/MultiEdit→edit plus `files.changed`; Read/Glob/Grep→read or search; WebFetch/WebSearch→web; Task/Agent→agent; MCP→mcp; TodoWrite→`plan.updated`. Thinking blocks are summarized only if the CLI exposes them, as `reasoning.delta`. Add `--include-partial-messages` for live deltas **only** as a validated adapter optimization, recorded in the runtime approval. Otherwise mirror per message. |
| **Antigravity** (`agy … --output-format stream-json`) | The existing parsed stream | Map message, tool and usage events per the recorded fixtures. Unknown event types become `gap`, `{reason:'unmapped'}`, plus a debug count. |
| **Grok Build** | The existing `Stream` (`tool_call`, `error`, `end`, …) | Same discipline. Usage comes from the existing `session_usage`. |

**Backfill readers** (M5) produce the same events from native histories:
- Claude JSONL under the selected `CLAUDE_CONFIG_DIR`;
- Codex rollout JSONL under the worker's `CODEX_HOME`;
- the Grok session directory;
- Antigravity, only if a stable history exists. Otherwise a single `gap{reason:'no-history-reader'}`.

They are read-only and version-gated. Their output carries `source.event:'backfill'`.

### 6.3 Session bridge: `npeer bridge`

**Launch.** The mod spawns:

```text
npeer bridge --claude-session <uuid> --protocol 1 --json
```

It uses `$.process.spawn` with the session cwd and a minimal environment.

**Startup sequence**
1. **Bind the caller.** Resolve the registered live Claude peer whose native ID is `<uuid>`. Require that peer's owner process to be in the bridge's ancestry, using exactly `bind_caller`'s interactive checks (`native_ancestor`, `nearest_native`). No new trust root is created. Missing or ambiguous gives `CALLER_UNBOUND` with the existing diagnostics.
2. **Open the socket.**
   - Create `/tmp/npeer-<uid>/` with mode `0700`, checking its owner and that it is not a symlink. Create the socket as `b-<first 12 hex of SHA-256 of the Claude session id>.sock` with mode `0600`. The name is stable for the session, so a restarted bridge reuses the same `socketPath` (D: one per session). Remove a stale socket file only after a connect attempt fails and its owner checks pass.
   - Mint a 32-byte random bearer token.
   - Print one JSON line, `{"ready":true,"socket":"…","token":"…","protocol":1,"peer":"np1_…","capabilities":{…}}`.
   - The mod reads it from the stream. Only the mod holds the token.
3. **Serve** HTTP/1.1 (asyncio; standard library only, or a pinned minimal dependency).
4. **Exit** on any of:
   - the engine killing the child (D: `$.process.spawn`'s loop ends when the module unloads or reloads);
   - the Claude owner process exiting (checked every 2 s);
   - no `POST /v1/heartbeat` for 60 s (the mod sends one every 15 s from `$.clock.every`);
   - `POST /v1/shutdown`;
   - 10 minutes idle with no leases and no requests.

   Do **not** use stdin EOF as a liveness signal: `$.process.spawn` closes the child's stdin after writing `input`, or immediately when no input is given (D). On exit the bridge closes the socket, unlinks it and releases leases. Leases stay valid until TTL, so a hot-reloading mod can re-attach.

**Status:** this is not a daemon. It is one process per Claude session that has the mod enabled. Record it in `doctor` as `bridge: session-scoped`.

**API.** Every request needs `Authorization: Bearer <token>`. Every response is the CLI JSON contract (`schema_version`, `ok`, `data`, `error`, `request_id`). Long-polls cap `wait_ms` at 25000. Every mutating call takes a `client_request_id` and is idempotent under the journal's existing rules.

| Method and path | Purpose |
|---|---|
| `GET /v1/hello` | Versions, bound peer, capabilities (steer per provider, backfill readers, activity schema). |
| `POST /v1/heartbeat` | Mod liveness, every 15 s. Missing for 60 s means the bridge exits and stops renewing leases. |
| `GET /v1/catalog` | Providers, exact models and efforts, profiles and quota summary: the same data as `spawn --check` and `usage`, cached. Backs `mcp__np__catalog`. |
| `GET /v1/lineage?cursor=&wait_ms=` | Groups, workers and tasks of this parent (and descendants, read-only), each with status, provider, model requested and observed, effort, title, timestamps, usage and mirror owner. Returns changes since `cursor`. |
| `POST /v1/spawn` | `{client_request_id, check_only?, claude_agent_id?, worker:{…spawn worker spec…}, mirror:{owner:'claude-mod', claude_agent_id}}`. Wraps the existing `spawn_many` (one worker, one group in v1), applying the caller's profile ceiling. |
| `GET /v1/bindings/:agent_id?wait_ms=` | The worker binding for a shadow: `{worker_id, group_id, task_id, generation}`, `pending` or `failed{error}`. |
| `POST /v1/mirror/attach` | `{client_request_id, worker_id, claude_agent_id, display_only:true}`: take the lease for Path B and C. Exclusive per worker. Delivery of the worker's existing generations stays native (§6.4). |
| `POST /v1/mirror/surfacing` | `{worker_id, task_id, generation}`: the shadow is about to yield the final answer. |
| `POST /v1/mirror/ack` | `{worker_id, task_id, generation}`: the shadow completed; the parent was notified natively. |
| `POST /v1/mirror/fault` | `{claude_agent_id, kind, message}`: recorded only; no state change to the worker. |
| `GET /v1/workers/:id/activity?after=&wait_ms=&max=&item=` | Activity events after `seq` (max 512 per response), optionally filtered to one `item`. Reads rotated segments transparently. Returns `gap` when the requested range is gone. |
| `POST /v1/workers/:id/input` | `{client_request_id, text, origin: human-claude-view\|parent-model, mode: queue\|steer, delivery_owner: mirror\|native}`. Returns `{disposition: queued\|steered\|resumed\|refused, reason?, task_id, generation}` (§6.5). |
| `POST /v1/workers/:id/interrupt` | `{client_request_id}`: interrupt only the current task turn (§6.5). |
| `POST /v1/workers/:id/cancel` | `{client_request_id, reason}`: cancel that worker's active task. |
| `POST /v1/groups/:id/cancel` | `{client_request_id, reason}`: the existing group cancel, which cascades to owned descendants. |
| `GET /v1/workers/:id/result` | The existing immutable result. |
| `GET /v1/artifacts/:id?offset=&limit=` | The existing bounded artifact read. |
| `GET /v1/workers/:id/resume-command` | The existing exact manual resume command. |
| `POST /v1/shutdown` | Orderly exit. |

**CLI equivalents** (same handlers, same JSON) let the mod degrade to `$.process.run` and keep humans in control:
- `npeer activity WORKER_ID --after N [--wait-ms N] [--item ID] --json`
- `npeer input WORKER_ID --message-file F --origin … --mode queue|steer --client-request-id ID --json`
- `npeer interrupt WORKER_ID --client-request-id ID --json`
- `npeer mirror attach|surfacing|ack|release|status … --json`

### 6.4 Delivery ownership and the mirror lease

Every task generation gets exactly one **delivery owner**. It is recorded when the generation is created and changes afterwards only through the fallback below.

| Generation created by | Delivery owner | Why |
|---|---|---|
| Path A: `Agent(np:*)` through the mod (§7.4) | `mirror`: the engine's native notification tells the parent | X14: Agent-tool spawns notify the parent on their first completion. |
| A follow-up the mod forwarded (`/v1/workers/:id/input`) | `mirror` when gate G13 confirms that the shadow run carrying it notifies the parent; otherwise `native` | X9: resumed runs notify the parent. |
| Path B or C (adopted from `npeer spawn` or MCP `worker_spawn`, attach, re-attach) | `native`: NativePeers' existing `push` or `wait`, as requested | X8: a shadow the mod spawns itself does not notify the parent on its first completion. The shadow is display-only for these tasks. |
| Anything without a live mirror (CLI, MCP, other parents) | `native` | Unchanged behavior. |

```text
lease (per worker, exclusive):   ATTACHED --renew--> ATTACHED --expire/release--> DETACHED
route for a mirror-owned generation, at terminal:
   MIRROR_PENDING --surfacing--> MIRROR_SURFACING --ack--> MIRROR_DELIVERED
        |                               |
        | lease expires (never surfaced)| lease expires (surfaced, ack unknown)
        v                               v
   fallback: push re-arms if the group   NOTIFICATION_UNKNOWN (no automatic push;
   asked for push, else retain           result readable; IMPLEMENTATION.md §12.2)
```

- **Lease mechanics.** The bridge holds the lease and renews it every 10 s (TTL 30 s) while mod heartbeats arrive and the Claude owner is alive. `mirror` ownership requires a lease. Display-only attachment uses the same lease, for exclusivity.
- **No push for mirror-owned generations while leased.** The existing completion-budget reservation and its release semantics apply unchanged.
- **Path A fallback is retain, never push.** Path A spawns use `completion_mode:"wait"`, and the parent model can still run `npeer result`. A surprise inbox frame would duplicate the Agent notification the parent may already have.
- **Native-owned generations** (Path B and C initial tasks) are delivered by NativePeers exactly as today. The shadow's final row ends `Delivered by NativePeers · npeer result <group>` rather than claiming the delivery.
- **Exclusivity.** A second session attempting to attach is refused with `MIRROR_OWNED`. Read-only viewing in that session's pane stays available.

### 6.5 Input, interrupt and cancel semantics

**Input to a running task** (`mode:"queue"`, the default): the existing controller enqueue, queue-after-current-turn (`IMPLEMENTATION.md` §8.3).

**Steering** (`mode:"steer"`) is accepted only where the provider capability is verified, which in v1 is Codex `turn/steer` per the installed schema. Otherwise the input is queued with `disposition:"queued"` and `reason:"steer-unsupported"`.

**Input to a terminal task:**
- If the controller is in follow-up grace, enqueue a new task generation.
- If the worker is released, run the existing owned-resume path (`resume-worker` semantics: same native ID, ownership checks, unarchive prerequisite), returning `disposition:"resumed"`.
- Ownership conflicts return `OWNERSHIP_CONFLICT`. They are never retried as another writer.

**Provenance.** Record `origin` (`human-claude-view` or `parent-model`) on the message and task. Authority is always the bound parent's. Provenance only labels the input.

**Idempotency.** The mod derives the `client_request_id` from the shadow row's `uuid`. A duplicate returns the original disposition.

**Delivery owner for follow-up generations.** Each input request carries `delivery_owner` (`mirror` or `native`). The mod chooses it from gate G13 (§6.4), and the bridge records it on the new generation.

**Interrupt** stops only the current task turn, through the existing provider interrupt (Codex `turn/interrupt`, the owned process-group interrupt for Claude, the existing paths for Grok and Antigravity).
- Task outcome: `interrupted` with a partial result.
- The worker stays resumable.
- No group cancel follows.

**Cancel** is the existing task cancel for one worker. The existing group cancel (cascading) is unchanged.

### 6.6 Spawn additions

- **Worker fields.** Add optional `mirror:{owner:"claude-mod", claude_session, claude_agent_id}` to the worker or group spec and schema (`spawn.v1.json` minor revision; old specs stay valid).
- **Unchanged checks.** `check_only` reuses `spawn --check`. Profile ceilings follow the caller's observed permission mode, exactly as today: plan mode cannot launch workspace profiles.
- **Defaults when the header omits fields:**
  - `permission_profile:"analysis"` with `workspace.mode:"inherit-read-only"`;
  - `permission_profile:"workspace"` implies `workspace.mode:"isolated-worktree"` (Git) or explicit `isolated-copy`;
  - model and effort come from `config.toml [claude_mod.defaults.<provider>]`, otherwise the provider default as today. No model is substituted.
- **Idempotency.** `client_request_id` for Path A is `np:<claude_session>:<claude_agent_id>`.

### 6.7 Desktop host recognition

- **Claude owners.** Extend `native_binaries()` and the hook registration so that desktop-hosted Claude Code engines are recognized Claude owners. Recognize them by **exact resolved executable path and SHA-256** under NativePeers' existing runtime approval (`runtime-check --provider claude`). The executable path is discovered in M0 (gate G7).
- **No messaging socket.** If a desktop session lacks `CLAUDE_CODE_MESSAGING_SOCKET`, register it with `transport:{kind:"none"}` and `capabilities.mirror:true`.
  - Mirror-only parents are allowed to spawn.
  - Their completion fallback is *retain*: there is no inbox to push to.
  - Their `whoami` says so.

### 6.8 Worker-mode environment and skill updates

- **Worker detection.** Owned Claude workers already receive `NPEER_BOOTSTRAP`. The mod treats it, or a controller binding, as **worker mode** and stays inert (§7.2).
- **Skill.** Update `integrations/claude/skills/nativepeers/SKILL.md` and `docs/CLAUDE_CONVENTIONS.md`: when `np:*` agent types are listed, prefer `Agent(subagent_type:"np:<provider>")` for delegations the person may want to watch. Keep `npeer spawn` for batch groups, sibling collaboration and isolated-copy inputs. Those are adopted automatically.

---

## 7. The `np` mod

### 7.1 Layout and manifest

```text
integrations/claude/mod/np/
  .claude-plugin/plugin.json      name "np", version, description, "types": "./types/index.d.ts", userConfig
  hooks/hooks.json                { "modules": ["./register.tsx"] }
  hooks/register.tsx              wiring only: on(...) registrations; $ is passed only to top-level functions (X12)
  hooks/bridge.ts                 spawn/handshake/reconnect; typed fetch helpers (all waits are $ calls)
  hooks/mirror.ts                 PURE planner: (activity batch, shadow state) → step plan (chunks, tool refs, end reason)
  hooks/inputs.ts                 capture/forward logic (pure parts tested)
  hooks/ui.tsx                    band, pane, ToolUse and UserMessage renderers (surface-portable trees)
  hooks/agents.ts                 agent specs, header grammar parser, labels
  types/index.d.ts                PluginState contract (§7.10)
  tests/*.test.tsx                claude plugin test suites (terminal + desktop surfaces)
  README.md, LICENSE              (validator/directory hygiene; private use)
```

**`userConfig`** (non-secret, editable in the config menu):

| Field | Values | Default |
|---|---|---|
| `followupMode` | `queue`, `steer-if-supported` | `queue` |
| `showReasoning` | `live`, `off` | `live` |
| `mirrorOutputBytes` | number | `8192` |
| `band` | `auto`, `always`, `off` | `auto` |
| `adoption` | `auto`, `ask`, `off` | `auto` |
| `npeerPath` | path | `/Users/danny/.local/bin/npeer` |
| `allowFromClaudeSubagents` | boolean | `false` |
| `notifyOnCompletion` | `off`, `long-only` | `off` |

`band: auto` draws the band on the terminal; on the desktop app the pane beside the conversation carries it. `always` draws the band on every surface, and `off` draws none.

### 7.2 Activation, and worker-mode guard

`session.start`:
1. Call `next(e)` first.
2. **Inert in worker sessions.** If `await $.env.get('NPEER_BOOTSTRAP')` is set, or the session is non-interactive and NativePeers reports it as an owned worker: register nothing, show nothing, return.
3. **Activate only with a person present.** Activate when any of these holds:
   - `e.isInteractive` is true (the REPL);
   - a client surface attaches later (`session.attach` with `surface` set to `desktop` or `vscode`). A desktop-hosted session may start with `surface:null` and attach afterwards, so steps 4–10 must also run from the first such `session.attach`, idempotently;
   - the explicit test switch `NP_MOD_HEADLESS=1` is set (integration harness only).

   Otherwise stay inert. Headless `-p` and SDK runs with no attached surface include CI and NativePeers' own workers.
4. **Check the engine version** (§5).
5. **Start the bridge** with `$.process.spawn` in a detached loop, as in the documented pattern. Read the ready line, then store `{socket, token}` in module memory and `bridge` in `$.state`.
   - On failure: `bridge.state = 'down'`, a one-time toast (`NativePeers bridge unavailable: <reason>. Run /np doctor.`), and no agent types.
6. **Register agent types** `np:codex`, `np:claude`, `np:antigravity` and `np:grok`, only for providers the catalog reports usable. Spec for each:

   ```js
   { name, description: '<when to delegate; header grammar; catalog tool>',
     prompt: SHADOW_PLACEHOLDER_PROMPT, tools: ['mcp__np__mirror', 'SubagentHandback'], model: 'haiku', effort: 'low',
     omitClaudeMd: true, background: true,
     permissionMode: <gate G6 decision; never inherited>, maxTurns: <gate G8 decision> }
   ```

   - `background: true` keeps a foreground `Agent` call from blocking the parent's turn for the worker's whole life. The parent ends its turn and is notified.
   - Do not set `autoCompactWindow`: it can only lower the window. Compaction is skipped through the `session.compact` hook instead.

   `SHADOW_PLACEHOLDER_PROMPT`: `You are a display placeholder for an external NativePeers worker. Reply exactly: "NativePeers mirror unavailable — run /np doctor." Do nothing else.` This is defense in depth (X10 showed it holds).
7. **Register tools:**
   - `mirror`, deferred, described `Internal: renders a NativePeers worker's activity. Calls outside a NativePeers shadow are refused.`
   - `catalog`, deferred, read-only: providers, models, efforts, profiles, quota.
8. **Register `/np` commands** (§7.9.6), with `immediate: true` for stop, interrupt, open and pane.
9. **Hydrate lineage** (`GET /v1/lineage`). Start a lineage long-poll loop (a detached loop of `$.http.fetch` calls) that updates `$.state` lineage. This loop **never** spawns shadows (§7.4).
10. **If the session was resumed**, read `$.store['bindings:<session id>']` and show re-attach offers in the band (§7.11).

`session.end`:
- If `e.reason !== 'clear'`: `POST /v1/shutdown` (fire and forget). The process stream ends with the module, which kills the bridge.
- If `e.reason === 'clear'`: keep the bridge, its leases and the lineage (§7.11).

### 7.3 Model-facing surface

**`agent.offer`:** offer `np:*` to the model only while `bridge.state` is `ready`.

**Header grammar.** An optional header leads the Agent `prompt`:

````text
```np
{"model":"gpt-6.1-sol","effort":"high","profile":"analysis","workspace":"inherit-read-only",
 "title":"Codebase review","fallbacks":[{"provider":"antigravity","model":"gemini-4-argon-high"}]}
```
<self-contained brief>
````

- The header is strict JSON, at most 4 KiB.
- Allowed keys: `model`, `effort`, `profile`, `workspace` (string mode or object), `title`, `fallbacks`, `quota_policy` and `requirements`.
- Unknown keys or invalid values make the `agent.spawn` hook deny, with a one-line reason naming `mcp__np__catalog`.
- The header is stripped before the brief is shown or sent.

**`mcp__np__catalog`** returns compact JSON from the bridge's cached `/v1/catalog`:
- each provider's exact model slugs with efforts;
- profiles allowed for this caller;
- quota summary.

**`prompt.compose`.** Add one `scope:'session'` section of at most 12 lines, only when the bridge is ready. It says:
- NativePeers agent types exist and what they are for;
- the header format;
- call the catalog once before choosing exact models;
- briefs must be self-contained, because workers receive only the brief (a T3 lesson);
- prefer `run_in_background` for fan-out; end the turn and let notifications arrive; do not poll;
- follow-ups go through `SendMessage` to the agent;
- results carry NativePeers references for `npeer result` and `npeer artifact-read`.

### 7.4 Spawn paths

**Path A: native Agent call.** `on('agent.spawn', hook)` handles `e.subagentType` starting with `np:`. It applies only when `!e.resume && !e.isTeammate && !e.workflow`.

Deny cases:
- `e.parentAgentId` is set and `allowFromClaudeSubagents` is false;
- a workflow agent, with the reason `np agents are not available in workflows (v1)`.

Then:
1. Parse the header. Build the worker spec: provider from the type; `title` from the header or `e.description`; the brief as `task`; `cwd` from `e.cwd` or the session's.
2. Preflight with `POST /v1/spawn {check_only:true}`. On refusal, return `{ deny: '<CODE>: <message>' }`: the model sees an Agent tool error.
3. Call `next({ ...e, prompt: <clean brief>, description: <label>, model: 'haiku' })`. The label is `"<Model label> <effort> · <title>"`, for example `GPT-6.1 Sol high · Codebase review`. If the result is a deny, return it.
4. `POST /v1/spawn {client_request_id:'np:'+session+':'+agentId, claude_agent_id: agentId, worker, mirror}`. **Do not wait for the worker to start.** Record a `shadow` family member `{phase:'BINDING', …}`.
   - A failure here is recorded as `spawnError`, and the shadow's first step reports it (§7.5).
5. Return the `next` result.

**Path B: adoption.** These are separate observer registrations, never the mirror `tool.call` registration:
- `on('tool.call', { tool: 'Bash' }, observer)`, active when the command matches `\bnpeer\s+(spawn|resume-worker)\b` without `--check`;
- the same for `mcp__nativepeers__worker_spawn` and `mcp__nativepeers__worker_resume`.

The observer:
1. Awaits `next(e)`.
2. Asks `GET /v1/lineage` for groups and workers of this parent that were created or resumed since the call started and are not mirrored.
3. For each one, calls `$.agent.spawn({ subagentType:'np:'+provider, prompt:<adoption brief>, description:<label> })`, then `POST /v1/mirror/attach {display_only:true}`. Delivery of these initial tasks stays with NativePeers (§6.4).
4. Returns the original result **unchanged**.

The adoption brief is `NativePeers worker "<title>" (<provider> <model> <effort>), group <id>.\n\nTask:\n<task text>`.

Setting `adoption:"ask"` shows an `[Attach]` band row instead.

**Path C: person-initiated attach.**
- Triggers: `[Attach]` and `[Re-attach]` band buttons, `/np attach <n|all>` and `/np reattach`.
- These are `ui.press` and `command.run` hooks, and they spawn exactly as in Path B.
- **M0 gate G5** decides whether these origins keep `turn.step` coverage. If they do not, Path C routes through a mod tool call, which runs the spawn inside a `tool.call` hook: the proven X1/X2 origin.

### 7.5 Mirror engine (`turn.step`)

**Registration**

```ts
on('turn.step', async function* stepHook($, e, next) { ... }).catch(async function* stepFault($, e, next) { ... })
```

**Algorithm, for a shadow `agentId`**
1. **Pass-through.** If `e.agentId` is undefined or not a known shadow, return `yield* next(e)`. Main and Claude subagents are untouched.
2. **Bind.** Loop on `GET /v1/bindings/:agentId?wait_ms=25000` until bound or failed. On failure, yield the failure text (for example `NativePeers refused this worker: QUOTA_EXHAUSTED …`) and stop `end_turn`.
3. **Forward pending inputs**, oldest first (§7.7). The brief that created the task is marked consumed, never forwarded. For each forward `disposition`, yield a dim one-line text such as `↳ queued for the worker (after its current turn)`, unless the step has nothing else to show yet.
4. **Answer the pending tool.** If the previous step ended with mirror `tool_use`s, the engine has already called the mirror tool for them (§7.6). Continue.
5. **Stream.** Loop on `GET /v1/workers/:id/activity?after=<cursor>&wait_ms=25000&max=256`.
   - **Before yielding** a batch's chunks, commit `cursor` and `inflight={from,to}` to `$.state`. Ordering is commit, then yield, then clear `inflight` at the next commit.
   - Map events with the pure planner (`mirror.ts`):

| Activity | Chunks | Step end? |
|---|---|---|
| `message.delta` | `text` in the current text block | no |
| `message.completed` (no prior deltas) | one `text` with the whole message | no |
| `reasoning.delta` (`showReasoning:live`) | `thinking` (shown live, not recorded: X6) | no |
| `tool.started` (live) | for each `tool.started` in the batch: `tool` (`mcp__np__mirror`, a fresh `toolu_…` id) and `input` `{title,kind,detail?,ref}`: the title first, `detail` only when it adds to the title, and `ref = #<seq>` of the `tool.started` event, unique within the worker it mirrors; a fallback move clears the pending refs (a surface may show the input) | yes: `stop{tool_use}` |
| `tool.started` + `tool.completed` already in the batch, or catch-up | the same, up to 8 parallel blocks per step; the tool hook answers immediately | yes |
| `files.changed` without a tool event | a mirror `edit` block | yes |
| `usage` | accumulate; report on the next stop chunk as `{input_tokens:0, output_tokens:Δ, cache_*:0, model:<observed>}` (display only: X7) | no |
| `input.queued` / `input.accepted` | dim `text` marker, for example `↳ follow-up accepted` | no |
| `fallback.started` | `text` marker, for example `↳ provider fallback: claude → antigravity gemini-4-argon-high (QUOTA_EXHAUSTED)` | no |
| `gap` | dim `text`, for example `[N activity events unavailable: <reason>]` | no |
| `task.terminal` | `POST /v1/mirror/surfacing`, then the final answer `text` (§7.5.1), then `stop{end_turn}`; in a handback run, `SubagentHandback` carrying the report, then `stop{tool_use}` (§7.5.2) | yes |

6. **Silence.** If the worker is quiet for 60 s, optionally yield one `thinking` heartbeat (`codex: still working (3m12s)`). Nothing is recorded.
7. **Generations within one run.** A shadow run ends only when its worker has no task in progress and no accepted-but-unstarted input.
   - A `task.terminal` followed by a pending follow-up becomes a dim marker (`↳ generation 2: your follow-up`), and mirroring continues in the same run.
   - The run's final answer is the last generation's, listing every generation's references.
8. **Return** a complete `TurnStepResult` on every path: `turnId`, `index`, `answer`, `toolUses`, `stopReason` and `usage`, matching what was yielded. A bare `return` or `undefined` makes the hook absent, which is a real model call (X10).

**Fail-closed `.catch`** (mandatory):
- yields `\n[NativePeers mirror fault: <kind>. The worker is unaffected; /np reattach or /np doctor.]` and `stop{end_turn}`, then **returns a complete `TurnStepResult`**;
- posts `/v1/mirror/fault` and raises one toast;
- marks the shadow `FAULTED`.

It never calls `next`. Proven by X10.

**Fallthrough detector.** On `turn.complete` for a shadow, any of the following means a real model request happened:
- `usage.cache_creation_input_tokens > 0`;
- `usage.input_tokens > 0`;
- the answer contains the placeholder sentinel.

When detected:
1. Mark `fallthrough`.
2. Toast.
3. Set `agent.offer` to false for `np:*` until `/np doctor` passes.
4. Record `mirror.fallthrough` through the bridge.

**Budget discipline:**
- No plain-promise awaits in hooks (X11).
- Every `$.process.run` passes `timeoutMs`.
- CPU work per batch is bounded (at most 512 events, at most 256 KiB of text).
- Pure planning keeps hook time to milliseconds.

#### 7.5.1 Final answer (what the parent model reads in the notification)

```text
<worker final message — inline up to 12 KiB, then "[… truncated; the full text: npeer result <group>]">

— NativePeers · Codex · GPT-6.1 Sol · high effort · read-only · completed in 2m41s
Changed 3 files (+41 −7) in .worktrees/<id>
Details: npeer result <group>
```

People read this text too (the desktop app shows a handed-back report as the run's last message, X19), so it uses names and labels and one handle: the group, from which `npeer result` names the worker, task, native session and artifacts. When another model answered than the one asked for, the line says `GPT-6.1 Sol (asked for …)`.

**Statuses:**
- `failed`, `canceled` and `timed_out` replace `completed` and add `Error: <CODE> <message>`.
- `interrupted` adds `Interrupted: the text above is a partial result.`
- **Mirror-owned generation:** the mod posts `/v1/mirror/surfacing` before yielding the final text, and `/v1/mirror/ack` on `turn.complete`. The last line reads `Details: npeer result <group>`.
- **Native-owned generation:** it posts neither, and the last line reads `Delivered by NativePeers · npeer result <group>`, or `Kept in NativePeers: npeer result <group>` for a wait-mode route.

#### 7.5.2 Handback runs (auto mode)

In auto mode only a `SubagentHandback` call reaches the parent (X15). For such a run:
- **Detection.** The engine's note row (door `note`, `origin.kind:'engine'`, naming `SubagentHandback(`) or its enforcement row marks the shadow `handback`. A note that lands before the shadow's record is held by the module until the first step.
- **The report** is the §7.5.1 text with the worker's final text always included. It is the call's `message`, kept on the shadow until the run completes.
- **The transcript**: the surface shows the report as the run's last message (X19), so the step writes no closing text of its own.
- **The call** is left to auto mode's classifier (X16, §7.6). A refused call returns as another step, answered after a one-second pause. After three refused tries the run ends on `end_turn` with `[NativePeers could not hand this report to the main conversation …; npeer result <group>]`, and the engine ends the run: its enforcement is bounded (`run_handback.py --refuse`).
- **A follow-up** accepted before the handback starts another generation, whose report replaces the kept one. A refused follow-up starts none, so the kept report still ends the run, after the refusal note.
- **A step after the answer** with no new input is the engine asking for the report again. It hands the kept report back, or ends at once; it never waits on activity from a worker with nothing left to run.
- Failures that end a run before any activity (spawn refused, binding failed) end the same way, with their text as the report.

### 7.6 Mirror tool contract

`tool.call` matcher `{ tool: 'mcp__np__mirror' }`, a dedicated registration with `.catch(() => ({ deny: 'NativePeers mirror unavailable' }))`.

**Validation.** The call must satisfy all of:
- `e.agentId` is a shadow;
- `e.ref` is in that shadow's `pendingRefs`;
- the input's `kind`, `title` and `detail` match the recorded `tool.started`.

Otherwise return `{ deny }`. **It never executes anything, and never calls `next`.**

**Wait** for the matching `tool.completed`: `GET …/activity?after=<start seq>&item=<item>&wait_ms=25000`, looped. A task terminal without completion becomes `interrupted`.

**Result:**

```text
[codex exec] exit 0 · 1m30s
<output head … tail, ≤ mirrorOutputBytes>
[full output: npeer artifact-read <id>]   (when stored)
```

Failures are reported in the text. The renderer colors them from mod state rather than relying on `isError`: a hook cannot set `isError`, and `deny` would read as a refusal.

**`tool.check`:**
- **Allow** `mcp__np__mirror` only for shadow agentIds with a pending ref.
- **Leave** `SubagentHandback` to the engine (`next`) for a shadow holding its report: only auto mode's classifier may allow it (X16). Deny it to a shadow holding none.
- **Deny** every other tool inside shadow loops.
- **Deny** `mcp__np__mirror` everywhere else.

X3 suggests the call is answered before the permission layer runs. Keep the hook anyway (M0 gate G6).

### 7.7 Input forwarding

**Capture.** `on('session.append', hook)`:
1. `const r = await next(e)`.
2. Act only when `e.agentId` is a shadow, `e.message.type === 'user'`, the content has text blocks and no `tool_result`, and the door is `prompt` or `delivery`. **Never `queue`**: a queue record is not a conversation row, and the same message also arrives as a row (D; spike 1 shows both), so capturing it would forward one input twice. Exact doors and origins come from M0 gate G3.
3. Record `{ uuid: r.uuid, text, origin: e.origin.kind, at }` in the shadow's `inputs`.
4. Return `r`.

**Raw text.** Strip the engine framing using a versioned list of known prefixes, for example `The coordinator sent a message while you were working:\n` (X9). Where the raw text is available earlier, prefer it and correlate FIFO per agent:
- `session.send` (model or plugin sends to the agent);
- `session.receive` / `prompt.submit` (person-typed while viewing).

**Origin mapping:**
- `coordinator` or model `session.send` → `parent-model`;
- person (`composer`/view; exact kind from G3) → `human-claude-view`;
- anything else → refused, with a dim note.

**Forward.** In the next `turn.step` of that shadow: `POST /v1/workers/:id/input {client_request_id:'np-in:'+uuid, text, origin, mode}`, where `mode` comes from `followupMode` and provider capability. Mark the input forwarded and keep the disposition.

**Exactly once.** Each row uuid is forwarded at most once (bridge idempotency). The planner never re-forwards a row with `forwarded:true`, even after a hot reload, because `$.state` persists.

### 7.8 Interrupt, kill and stop

`turn.complete` for a shadow with `isAborted` (`reason === 'aborted'`). D: the reason set cannot distinguish an Esc from a kill or an engine-side abort.
- If `$.agent.list()` shows the shadow `killed` (stopped from the tasks list, or `TaskStop`), `POST /v1/workers/:id/cancel`.
- Otherwise, `POST /v1/workers/:id/interrupt` only when all three hold:
  - the band's last render showed this shadow in view (`e.props.view.agentId`);
  - no mod reload or shutdown is in progress;
  - gate G4 confirmed that Esc produces exactly this pattern.

  The worker turn then ends `interrupted`, and the shadow shows `↳ interrupted`.
- Otherwise nothing reaches the worker, and the next step resumes from the cursor.
- The `[Interrupt]` button is always the unambiguous path.

`[Stop all]` and `/np stop all` (person-initiated, `immediate`) `POST /v1/groups/:id/cancel` for every active group of this parent; this cascades.

Main-view Esc does nothing to workers.

### 7.9 User interface

All trees use only `Box`, `Text`, `Button`, `Code`, `Markdown`, and `Input` for the degraded viewer (mobile has no `Input`). Size to `bodyColumns`. Test on `terminal` and `desktop` with the test kit. No brand logos: providers are short neutral tags (`cx`, `cl`, `ag`, `gk`) in theme colors in the band, and names (Codex, Claude, Antigravity, Grok) in the pane.

1. **Tasks list.** Native; nothing drawn. The mod controls the type name and `description`. M0 gate G1 records what else the row shows. If it shows the latest tool title, the mirror `title` is written for that (`pytest -q`, `src/foo.py +12 −3`). If a per-agent `Spinner` instance feeds it, render `{ message: '<tag>: <latest>' }`.
2. **`ToolUse` and `ToolResult` for `mcp__np__mirror`:**
   - **Compact form:** `⏺ <kind>  <title>` and `⎿ <one-line summary> · <duration>`.
   - **Expanded form** (`isExpanded`): input detail plus output in `Code` (`format:'diff'` for edits).
   - Errors in the error color.
   - Running state shows `⎿ running…`.
3. **`UserMessage` in shadow views.** Label provenance: `From main agent` (coordinator), `You` (person) and `Task from main · via NativePeers` (adoption brief). `UserMessage` props carry no agent id or view (D), so label by `origin.kind`: coordinator rows occur only in subagent transcripts. Gates G1 and G3 confirm the kinds. The row folds to one line unless `isExpanded`, keeping T3's "Show full message" behavior through ctrl+o.
4. **`AbovePrompt` band** (respect `hasSurvey`, `maxRows` and collapse):
   - **Drawn** on the terminal (and everywhere under `band: always`). On the desktop app under `band: auto` the hook passes, and the pane carries the summary.
   - **Main view:** `⟐ np · N delegates · r running · d done · f failed [Stop all] [Pane]`, then up to 4 rows: `<tag> <model> <effort> · <title> · <elapsed> · <status> [Open]`. `[Open]` calls `$.ui.view({agentId})`, and is not offered on the desktop app (X17). Hotkeys are `1` to `4`.
   - **Shadow view** (`e.props.view.agentId` is a shadow): `↳ Subagent of main · <provider> · <model observed> · <effort> · <profile> · <elapsed> [Interrupt] [Stop] [Resume cmd] [Pane]`. `[Resume cmd]` uses `$.ui.copy` on the terminal. On remote surfaces copy has no path, so it shows the command in the pane instead.
   - **Offers:** `[Attach]` and `[Re-attach]` rows (§7.4 Path C).
   - **Degraded mode** (no shadows): rows open the pane viewer instead.
5. **Pane `np-lineage`**, titled `NativePeers` (`/np pane`, docked in fullscreen at 110 columns or more, inline otherwise; on the desktop app it opens unfocused beside the conversation when a delegate starts, unless it is already open):
   - **Workers:** the lineage tree, including descendants, each a button with its title and a dim `<Provider> · <Status> · <elapsed>` line. On the desktop app it also shows the band's summary, with `[Stop all]` while delegates run. Pressing a worker shows its conversation.
   - **Conversation** of one worker, on every surface: `[‹ Workers]`, its title and status. Then its details in words: model and effort (and the requested model when another answered), access (`Read-only` or `Can edit its own workspace`), output tokens and latest activity.
   - **Rows:** the conversation from its activity log, as `Markdown` and `Code`: the task from main, the worker's messages under its name, tool steps (`▸ <title> · exit 0 · 2s` with a short output), follow-ups (`You`, or `Task from main` for the parent's), and how each generation ended. While shown, it follows the log (activation follows a conversation still shown after a reload); the newest 80 rows are kept in `$.state`, keyed by generation as well as item, since providers reuse item ids across generations.
   - **Message field:** an `Input` (`Message <Provider>…`; mobile has none) sends a follow-up through `POST /v1/workers/:id/input` (origin `human-claude-view`, mode `queue`). A live worker takes it after its current turn and a released one resumes with it, and its answer appears in the conversation. Its result is delivered natively: where the parent has no inbox (the desktop app), it stays in NativePeers and in this conversation. The message is held as a draft until the bridge answers. A definite refusal, or a bridge that is not running, returns it to the field. An uncertain answer (the request left, the reply did not arrive) offers `[Retry]`, which sends it again under the same request id; the bridge admits it once. Drafts are per worker; a worker takes one message at a time while its last one is unresolved, and a draft a reload left `sending` becomes uncertain.
   - **Buttons:** Interrupt and Stop (a running worker), Attach (a running worker with no live shadow here, as in the band), Patch preview (`delta-preview`), Copy IDs. Ids (worker, group, task, native, generation) are never shown: `[Copy IDs]` copies them. The resume command stays with the band's shadow view and `/np resume-cmd`; while a worker is open for follow-ups it has none, and the toast says to message it from the pane.
6. **Commands.**

   | Command | Does |
   |---|---|
   | `/np` | Status table. |
   | `/np pane` | Open the Lineage pane. |
   | `/np open <n>` | View that worker (person-initiated); where the main view cannot switch to it (the desktop app, or an agent Claude Code no longer lists), the pane shows its conversation. |
   | `/np stop [<n>\|all]` | Stop one worker, or all. |
   | `/np attach <n\|all>` / `/np reattach` | Attach unmirrored workers (Path C). |
   | `/np doctor` | Readiness in sentences: `Ready.` or what is not ready, the Claude Code version and where it comes from (the desktop app keeps and updates its own copy), the bridge, providers by name, the fallthrough flag and the npeer path. |
   | `/np resume-cmd <n>` | Copy the exact manual resume command. |

7. **Status, toasts and notifications:**
   - `$.ui.status('<r> running · <d> done · <f> failed · <s> stopped')`, zero counts left out, while the session has delegates; refreshed on every lineage change.
   - Toasts are sentences without a prefix: the engine heads them with the plugin's name (X17).
   - A toast on each completion or failure.
   - Optional `$.ui.notify` (config `notifyOnCompletion`, default off) for completions of tasks that ran 2 minutes or more. It sends at once through the person's channel, and is skipped where it answers `no-surface` (desktop and SDK hosts).

### 7.10 State contract and persistence

```ts
// types/index.d.ts (sketch)
export type NpProvider = 'codex' | 'claude' | 'antigravity' | 'grok'
export type NpBridge = { state: 'off'|'starting'|'ready'|'degraded'|'down'; version?: string; peer?: string; reason?: string; since: number }
export type NpWorker = { workerId: string; groupId: string; taskId?: string; generation: number; provider: NpProvider;
  modelRequested?: string; modelObserved?: string; effort?: string; profile?: string; title: string;
  status: 'queued'|'starting'|'running'|'waiting'|'completed'|'failed'|'canceled'|'interrupted'|'timed_out';
  startedAt?: number; endedAt?: number; outputTokens?: number; agentId?: string; parentWorkerId?: string; latest?: string; mirrorOwner?: 'this'|'other'|null }
export type NpShadow = { agentId: string; workerId?: string; spawnError?: string;
  phase: 'SPAWNING'|'BINDING'|'STREAMING'|'TOOL_PENDING'|'COMPLETING'|'COMPLETED'|'FAULTED'|'DETACHED';
  cursor: number; inflight?: { from: number; to: number };
  pendingRefs: Record<string, { item: string; kind: string; startSeq: number }>;
  inputs: Array<{ uuid: string; text: string; origin: string; forwarded: boolean; disposition?: string }>;
  briefConsumed: boolean; fallthrough?: boolean; lastFault?: string; handback?: boolean; report?: string; handbacks?: number }
// The conversation the pane shows for one worker (§7.9), newest 80 rows.
export type NpRow = { kind: 'task'|'you'|'text'|'tool'|'note'; text: string; item?: string; status?: string; detail?: string }
export type NpTranscript = { workerId: string; cursor: number; rows: NpRow[] }
// A worker's message typed in the pane until the bridge answers (§7.9).
export type NpDraft = { workerId: string; text: string; requestId: string; state: 'sending'|'refused'|'uncertain'; reason?: string }
declare module 'claude-code' {
  interface PluginState {
    np: { bridge: NpBridge; lineage: Shaped<NpWorker[]>; shadow: StateFamily<Shaped<NpShadow>>;
          ui: { bandCollapsed: boolean; paneOpen: boolean; selected?: string; viewing?: string; sent?: number; drafts?: Record<string, NpDraft> };
          transcript: Shaped<NpTranscript> }
  }
}
```

- Use `shape` tags (`'np-lineage-1'`, `'np-shadow-1'`) so a reload with a changed shape reinitializes rather than misreads.
- `$.store.set('bindings:<session id>', …)` holds `{agentId → workerId, cursor}` for resume, read back with `$.store.get`. The 4 MiB limit covers the whole store across sessions, so entries stay compact and are pruned to 200 per session and to 30 days.

### 7.11 Reload, restart, resume and clear

**Hot reload** (development, or `/reload-plugins`):
- `$.state` survives; module variables do not.
- The bridge is killed with the module and restarted by `session.start`. Leases survive their 30 s TTL.
- An in-flight step's fate is M0 gate G9. If it restarts, the planner resumes from `cursor`.
- A non-empty `inflight` yields one `[mirror resumed after reload; up to N events may be missing]` marker rather than risk duplicates. This is at-most-once display; the full transcript remains in the native history.

**Parent exit:** NativePeers' existing policy cancels active owned work unless `survive_parent` is set. Leases expire. Completed shadows remain in the session transcript.

**`claude --resume`:**
- Completed shadows and notifications remain wherever the engine restores them (G10).
- Running `survive_parent` workers produce `[Re-attach]` offers.
- Re-attached shadows are new agents that backfill from `seq 0`, with batched catch-up.

**`/clear`:**
- `session.end{reason:'clear'}` fires and no new start follows. The process continues under a **new session id** (D).
- Keep the bridge, the leases, the lineage and the band for workers started before the clear. They stay bound to the old session's NativePeers peer.
- New spawns after the clear need the new session's binding. Start a second bridge for the new id once NativePeers' `SessionStart` hook (source `clear`) has registered it.
- Keys (`client_request_id`, `bindings:<session id>`) always use the session id current at spawn time.
- Workers are never canceled by `/clear`. Gate G10 records what happens to shadows.

---

## 8. State machines

### 8.1 Shadow lifecycle (mod, per `agentId`)

```text
SPAWNING ──next(e) ok──▶ BINDING ──binding──▶ STREAMING ⇄ TOOL_PENDING
   │                        │ failed                │ task.terminal
   │ deny                   ▼                       ▼
   ▼                     COMPLETING ◀─────────── COMPLETING ──turn.complete──▶ COMPLETED (ack sent)
 (no shadow)          (failure text)                                               │ new user row
                                                                                   ▼
                                                                    STREAMING (new generation; input forwarded)
any ──.catch──▶ FAULTED ──/np reattach──▶ (new shadow, backfill)
any ──lease lost / session end──▶ DETACHED
```

**Rules**
- **`TOOL_PENDING`** means the step ended with mirror tool uses and the tool hook is awaiting `tool.completed`.
- **An interrupt** (G4) keeps the shadow in `STREAMING` until `task.terminal{interrupted}`.
- **A kill** moves the shadow to `COMPLETING` once `task.terminal{canceled}` arrives.

### 8.2 Input lifecycle (per captured row)

```text
CAPTURED (session.append) ──next shadow step──▶ FORWARDING ──bridge──▶ QUEUED | STEERED | RESUMED | REFUSED
                                                    │ transport error
                                                    ▼
                                         retry same client_request_id (idempotent) ≤ 3, then REFUSED{transport}
```

### 8.3 Delivery route

The delivery route state machine is defined in §6.4.

**Invariants**
- **One owner per worker:** a worker has at most one mirror owner at a time.
- **One delivery per generation:** each task generation has one delivery owner fixed at creation (§6.4). It is delivered by exactly one route: the native notification acked by the mirror, a NativePeers push, or a deliberate retain.
- **Uncertainty blocks push:** `MIRROR_SURFACING` with an unknown ack never auto-pushes.

---

## 9. Failure modes and edge cases

| # | Situation | Required behavior |
|---|---|---|
| 1 | Preflight refuses (quota, model, profile ceiling, plan mode) | Deny the Agent call with `CODE: message` and the catalog hint. No shadow is created and no worker starts. |
| 2 | Spawn fails after the shadow exists | The first step yields the failure text and ends the turn. The parent gets a native notification. NativePeers records it. |
| 3 | Provider failure mid-task with configured fallback | `fallback.started` marker. The same shadow continues mirroring the replacement attempt, whose native identity is shown in the band and final answer. |
| 4 | Huge tool output or huge message | Truncate per §6.1/§7.6, with artifact references. The display never stalls the provider pipe; overflow becomes `gap`. |
| 5 | Follow-ups typed rapidly | Each row is forwarded once, in order. NativePeers queues them. Each produces `input.queued`/`input.accepted` markers. |
| 6 | Follow-up to a released worker that another runtime has since opened | `OWNERSHIP_CONFLICT`. The shadow shows it. No second writer. |
| 7 | Bridge crash | Steps see fetch failures. The mod restarts the bridge (backoff 0.5, 1, 2, 4, 8 s; waits are `$.process.run(['sleep', …])`). After 5 failures it ends the step with fail-closed text. Leases survive TTL. `/np reattach` recovers. |
| 8 | Mod hot reload mid-step | §7.11: resume from the cursor, with an `inflight` marker. Never re-forward inputs, never duplicate the final answer (the route state machine holds). |
| 9 | Parent session exits | NativePeers' parent-exit policy applies. Leases expire. Push or retain fallback applies (§6.4). |
| 10 | Two sessions try to mirror one worker | The second gets `MIRROR_OWNED` and a read-only pane view. |
| 11 | Model calls `mcp__np__mirror` directly | Deny. The tool description says it is internal. |
| 12 | Shadow model fallthrough (mod fault without the catch path, engine change) | The placeholder answers with the sentinel. The detector disables `np:*` offers, toasts and records. A harness test pins zero API usage. |
| 13 | Nested delegation by a worker | Descendants appear read-only in the band and pane tree. No grandchild shadows in v1. Stop all cascades through NativePeers. |
| 14 | Parent in plan mode | The NativePeers profile ceiling refuses workspace profiles. Analysis workers still run. |
| 15 | Worker silent for a long time | Thinking heartbeats (live only). The step stays held (X5; M0 gate G8 for 30 min). |
| 16 | Activity log missing (legacy worker or rotated beyond retention) | A backfill reader if available, otherwise `gap` plus native `resume-command` guidance. |
| 17 | Desktop host without `$.process` | The mod stays in band-only degraded mode with a doctor explanation (gate G7). |
| 18 | NativePeers runtime auto-renew while the bridge runs | The bridge keeps the leases on its pinned executables. New spawns use the current approval as today. A version mismatch with the mod protocol → `degraded`. |
| 19 | A Claude worker (owned) loads the mod | Worker-mode guard: inert. |
| 20 | `/rewind` or a fork of the parent conversation | Shadows are keyed by `agentId`, and NativePeers state is unaffected. Forked sessions do not inherit leases. |
| 21 | `/clear` in the parent | The session id changes (D). Existing workers stay bound to the old peer and keep mirroring; new spawns rebind (§7.11). |
| 22 | An adopted (Path B/C) shadow finishes its first run | The engine does not notify the parent (X8). NativePeers delivers as requested, and the shadow's final row says so. |

---

## 10. Security and authority

- **No new trust root.** The bridge binds with the existing interactive ancestry checks. Mutations carry the bound parent's authority and the existing profile ceilings. Provenance labels origin only.
- **Bridge confinement:**
  - the socket directory is mode `0700` and the socket `0600`, with a short path that never derives from titles;
  - a per-launch bearer token is held only by the mod and never written to disk or logs;
  - the bridge is killed with the mod, and also exits on owner death, on heartbeat loss, or when idle.
- **Same-user scope.** Same-user malicious processes remain out of scope, as in `IMPLEMENTATION.md` §6.5.
- **The mirror tool cannot act.** It only replays recorded events for its own shadow, and every other call is denied. Shadows have no other tools. Nested `agent.spawn` from shadows is denied.
- **Model exposure is unchanged.** Shadows never call a model (X2), so worker text reaches the parent model only through the final answer, which has the same exposure as today's NativePeers results. Activity logs are redacted and owner-only.
- **Recursion guard.** Worker-mode detection keeps the mod inert inside NativePeers-owned Claude workers.
- **No credentials, token borrowing or global setting changes.** The installer is merge-only, with an uninstall manifest. A permission rule for `mcp__np__mirror` is added only if M0 gate G6 proves it is needed, and it is scoped to that one tool.

---

## 11. Performance budgets (P; measured in M6)

- **Row visible:** a shadow row appears at most 1.5 s after the `Agent` call returns. The worker start is asynchronous.
- **Display latency:** worker event to shadow transcript is at most 750 ms at p95 and 2 s at p99.
- **Hook time:** hook own-time per step is at most 50 ms at p95 and never more than 2 s. Hooks make no plain-promise waits (X11).
- **Bridge cost:**
  - idle CPU about 0;
  - RSS at most 80 MB;
  - one connection per active long-poll;
  - the lineage poll is at most 1 per session.
- **Recorder overhead:**
  - at most 2% of controller CPU;
  - never blocks reading provider stdout (separate task and bounded queue; overflow becomes `gap`).
- **Mirrored text cap:** 8 MiB per shadow (soft cap). Past it, the shadow switches to summary mode (titles only) and says so; the full history stays in the pane and native resume.

---

## 12. Testing strategy

**1. NativePeers unit tests** (pytest, existing style)
- **Normalizers:** each provider normalizer against recorded fixtures (golden activity output).
- **Recorder:** sequence and generation monotonicity, fsync points, torn lines, rotation, redaction and bounds.
- **Bridge:**
  - auth token;
  - ancestry binding, including refusal outside ancestry;
  - every endpoint;
  - long-poll timing;
  - idempotency;
  - shutdown on owner death, heartbeat loss and idle (not stdin EOF).
- **Lease and route state machine:** including races between expiry, surfacing and ack, and lease takeover refusal.
- **Input dispositions:** running, grace, released, conflict and steer-unsupported.
- **Interrupt and cancel:** interrupt versus cancel.
- **Desktop owner recognition.**
- **Schema:** `schemas/activity.v1.json` validation.

**2. Mod unit tests** (`claude plugin test integrations/claude/mod/np`)
- **Pure logic:** the planner (golden batch to chunk plan), header parser, labels and input capture with dedupe.
- **Guards:** mirror denials and `tool.check`.
- **Fail-closed:** the `.catch` generator path.
- **Renderers:** `ToolUse`, `UserMessage`, band and pane, each on `terminal` **and** `desktop`.
- **State:** `shape` migration.

**3. Headless zero-API integration harness** (`tests/integration/claude_mod/`, CI-able, no model input)
- **Setup:** `env -i HOME=<tmp> NP_MOD_HEADLESS=1 ANTHROPIC_BASE_URL=http://127.0.0.1:<tripwire port> claude -p --plugin-dir integrations/claude/mod/np --plugin-dir tests/integration/claude_mod/driver --output-format stream-json --verbose`.
- **Fake bridge:** a fixture that replays recorded activity over the real bridge protocol.
- **Driver mod:** a test-only mod that answers **main-loop** steps with deterministic `Agent(np:*)` calls, `SendMessage` follow-ups and waits (Appendix A pattern).
- **Assertions:**
  - task events in the stream;
  - shadow transcript rows (tool_use/tool_result pairs);
  - final answers;
  - one forward per follow-up row;
  - fault injection (bridge kill, malformed events, budget overrun) yields fail-closed text;
  - and in **every** run: `duration_api_ms == 0`, `total_cost_usd == 0`, `modelUsage == {}`, **zero tripwire hits**, and no API error messages.
- **Tripwire, not missing credentials.** A run without credentials can still bill (X13). On the Mac, Keychain OAuth is found regardless of `HOME`. So every harness run points `ANTHROPIC_BASE_URL` at a local tripwire stub (`tests/integration/claude_mod/tripwire.py`) that answers HTTP 400, counts requests and never logs headers. Gate G0 proves on the Mac that the tripwire catches a deliberate fallthrough before any harness run counts as free.

**4. Live acceptance on the Mac**
- Covers: the CLI (normal and fullscreen layouts), the desktop app, all four providers, fan-out of four, follow-ups, interrupt, stop, Stop all with cascade, adoption, re-attach, and fault injection.
- Recorded as L evidence under the kickoff's budget.

---

## 13. Milestones and implementation order

### M0: Target-Mac feasibility gates

M0 uses minimal or zero model input. It proves the tripwire (G0), reproduces X1–X14 locally with the harness pattern, then resolves the gates below. Write `evidence/claude-lineage-m0.json`, with a decision per gate, and record `compat/claude-mod.json` (minimum engine version).

| Gate | Establish | Fallback if it fails |
|---|---|---|
| **G0** | Tripwire: with `ANTHROPIC_BASE_URL` at the local stub, a deliberate fallthrough (a fault without `.catch`) hits the stub and bills nothing | No harness run counts as free; every live-budget rule applies to harness runs. |
| **G1** | Tasks-list rendering of plugin-spawned shadows (type, description, activity text, tokens), plus ↓/Enter navigation into the view | Band and pane become the primary list. Shadows are kept for notifications and resume. |
| **G2** | `$.ui.view({agentId})` from a band press. Enter while viewing reaches that shadow. | Pane viewer with an `Input` field that posts to `/input`. |
| **G3** | The input event chain for: person-typed (running and completed), parent `SendMessage`, and the doors, origins and framing involved | Capture from `session.append` only, with prefix stripping. |
| **G4** | Esc, kill and `TaskStop` signals for a shadow (`isAborted`, `reason`, `AgentStatus`); a held step aborts within `lingerMs` | Explicit `[Interrupt]`/`[Stop]` buttons only. Esc is not mapped. |
| **G5** | Whether `turn.step` sees shadows spawned from `ui.press`, `command.run`, a timer and `session.start` | Route Path C through a mod tool call (the proven `tool.call` origin). |
| **G6** | Mirror tools in interactive `default`, `acceptEdits`, `plan` and `auto` modes: no prompts | Set `AgentSpec.permissionMode` explicitly; never inherit the parent's (in plan mode a hook can only tighten). Either `dontAsk` plus a scoped `permissions.allow` rule for `mcp__np__mirror` through the installer, or `bypassPermissions`, which is safe here because the shadow's only tool is the replay-only mirror. |
| **G7** | Desktop: engine path and hash, messaging-socket presence, `$.process` and `$.http(socketPath)`, desktop rendering of shadows and the band, messaging a shadow, `$.ui.copy` and `$.ui.notify` behavior | Desktop degraded mode (band only), documented. |
| **G8** | A 20-minute mirror tool call; a 30-minute silent step; 600 steps in one shadow (the `maxTurns` value); 6 concurrent shadows; memory | Complete-then-emit for long tools, catch-up batching, step caps with summary mode. |
| **G9** | Hot reload during a held step and during a pending mirror tool | Cursor plus `inflight` marker (already designed). Document the observed behavior. |
| **G10** | `--resume` and `/clear` behavior for shadows; `session.send` to an old shadow after resume; the new session id after `/clear` and NativePeers' re-registration | Re-attach and rebind flows (already designed). |
| **G11** | Token display with reported usage; `/cost` unchanged; compaction never triggered for shadows | Report `usage:null`. |
| **G12** | `claude --version`, types header, `claude plugin validate`, `tsc -p` | Must pass. No fallback. |
| **G13** | Parent notification per origin: Agent-tool spawn (first run), `$.agent.spawn` (first run), resume through `SendMessage`/`session.send`, resume by the person typing, a follow-up queued while running | Any origin that does not notify gets delivery owner `native` (§6.4). |

### M1: Activity log (NativePeers)

- The `activity/` recorder, Codex and Claude normalizers, `activity_index`, and `npeer activity`.
- Fixtures and unit tests.
- No behavior change for existing commands. The full suite must still pass.

### M2: Bridge, leases and inputs (NativePeers)

- `npeer bridge` (binding, token, API), mirror leases and route states, input, interrupt and cancel.
- Spawn `mirror` field, CLI equivalents, `doctor` reporting, and tests including races.

### M3: Mod core and the thin vertical slice

- Agent types, Path A, the mirror engine with `.catch`, the mirror tool and guards, compaction skip, final answer and ack.
- The zero-API headless harness passes.
- **Live slice:** one Codex worker from a CLI parent, watched end to end.

### M4: Interaction and UI

- Input capture and forwarding.
- Interrupt, kill and Stop all.
- Provenance labels, `ToolUse` renderer, band, `/np` commands, status and toasts.

### M5: Breadth

- Grok and Antigravity normalizers.
- Path B adoption and Path C attach and re-attach.
- Backfill readers (Codex and Claude first).
- Pane with lineage tree and degraded viewer.

### M6: Desktop parity, install and acceptance

- Desktop owner recognition and desktop verification.
- `npeer install --claude-mod` and uninstall.
- Skill and conventions updates; README and docs (`docs/CLAUDE_LINEAGE_MOD.md` stays normative; add `docs/LINEAGE_USAGE.md`).
- Performance measurements.
- Full live acceptance; PR merged.

---

## 14. Acceptance criteria

Each criterion is PASS, FAIL, BLOCKED or NOT_RUN, with evidence.

- **AC1 (fan-out):** in a CLI session, one prompt asking for delegates in Codex (GPT-6.1 Sol high), Grok Build (Grok 4.7), Antigravity (Gemini 4 Argon high) and Claude (Sonnet 5.5) produces four `np:*` rows in the native tasks list. Each is labeled with model and effort, and they run concurrently (overlapping NativePeers task intervals, distinct native IDs).
- **AC2 (live transcript):** opening any row shows a live transcript:
  - the brief, with provenance;
  - streaming assistant text;
  - every external tool call as a row with input and bounded output;
  - file edits with diffs;
  - the final answer with NativePeers references.

  Where the provider exposes reasoning summaries, they show while live and are absent after.
- **AC3 (zero Claude usage):** no Claude model usage is attributable to shadows. The harness asserts `duration_api_ms==0`, `total_cost_usd==0` and `modelUsage=={}`. Live: the session cost and `modelUsage` show only the parent's own requests.
- **AC4 (follow-up to running):** a message typed while viewing a running worker is forwarded exactly once (one journal input with origin `human-claude-view` and the row-uuid request ID). The worker's response appears in the same view.
- **AC5 (follow-up to completed):** a message to a completed worker resumes the same native conversation (same native ID), streams the new turn, and the parent receives a second native notification.
- **AC6 (stop semantics):**
  - Esc in a worker view interrupts only that worker's current turn;
  - stopping the agent cancels only its task;
  - `[Stop all]` cancels every active group of this parent and its descendants.

  No unrelated process is affected.
- **AC7 (exactly once):** while the mod is healthy, each task generation's completion reaches the parent exactly once, through its recorded owner (§6.4). There is no NativePeers push for mirror-owned generations, and no native notification is claimed for native-owned ones. When the mod or bridge is killed mid-task, fallback follows §6.4 exactly, including `NOTIFICATION_UNKNOWN` without a push when surfacing was uncertain.
- **AC8 (fault injection):** throws, budget overruns, bridge death and malformed activity never produce a Claude model request for a shadow. The worker keeps running, and `/np reattach` restores mirroring.
- **AC9 (desktop):** in the desktop app's local Code session, shadows appear in its subagent UI, can be opened and messaged, and the band and pane render. Alternatively, G7's documented degraded mode holds and is reported as such.
- **AC10 (hot reload):** reloading the mod during active mirroring resumes from the persisted cursor. There are no duplicate follow-ups and no duplicate final delivery; display gaps are marked, never silent.
- **AC11 (resume):** after `claude --resume`, previous workers are listed, completed shadows remain viewable (per G10), and `survive_parent` workers can be re-attached and backfilled.
- **AC12 (adoption):** `npeer spawn` and MCP `worker_spawn` issued by the parent are adopted into shadows within 2 s of the tool result. Their initial completions are delivered exactly once, by NativePeers as requested, and the shadow says so.
- **AC13 (worker mode):** inside NativePeers-owned Claude workers the mod is inert (no agent types, tools or band).
- **AC14 (hygiene):**
  - the existing NativePeers suite passes;
  - new unit, mod and harness suites pass;
  - `claude plugin validate` is clean;
  - `tsc -p` is clean;
  - the installer is idempotent, with an uninstall dry-run;
  - commits carry no agent attribution trailers;
  - the PR is merged to `main`.

---

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Early-access API churn ("may change between releases without notice") | Minimum version gate. Types read from the engine. `claude plugin validate` and the zero-API harness run on each Claude update, added to `runtime-check --provider claude` as a separate "mod compatibility" verdict. Band-only degraded mode. |
| Engine changes the semantics of hook-answered steps (billing, validation) | The harness's zero-API assertions catch it immediately. The fallthrough detector does the same live. |
| Fallthrough cost or behavior | Mandatory `.catch`, complete `TurnStepResult` returns on every path, the placeholder agent definition (Haiku, no tools beyond the mirror, sentinel), the tripwire in every harness run, and detector plus disable. |
| Duplicate or missed delivery | Lease, surfacing and ack, the `NOTIFICATION_UNKNOWN` discipline, and race tests. |
| Long-session bloat or performance | Bounds, catch-up batching, summary mode. Native history stays authoritative. |
| The tasks list does not show the activity text we want | G1 records what it shows. The band and the `Spinner` hook compensate. |
| Desktop gaps | G7 with documented degraded mode. NativePeers owner recognition behind runtime approval. |
| Provider stream drift | Normalizers pinned to runtime approvals and fixtures. Unknown events become `gap` with counts, never silent. |
| Orphaned bridge processes | Killed with the mod; exits on owner death, heartbeat loss and idle. `doctor` lists bridges. Covered by tests. |
| Complexity creep (T3 lesson: a 13.7k-line orchestrator) | The mod stays thin (pure planner plus wiring). NativePeers owns semantics. Each milestone ends green. |

---

## 16. T3 Code reference notes

**Source.** `github.com/pingdotgg/t3code` at `028ed851d552eda86d8c28a758de3d8157b80d56` (main, 2026-10-30). This is the "Orchestration V2" design, with docs in `docs/orchestration-v2/orchestrator-mcp-server.md` and `thread-lineage-and-context-transfer.md`. Label S, from a research agent's reading; re-verify any detail you rely on. **Copy no code.** Check the license before reusing anything.

**What maps to this design**

| T3 Code | NativePeers Lineage |
|---|---|
| One injected MCP server `t3-code` with `delegate_task` (`packages/contracts/src/orchestratorMcp.ts`), validated against the parent's provider/instance | `np:*` agent types through the native Agent tool, plus the `mcp__np__catalog` tool and the `np` header |
| `orchestrator_capabilities` ("call once per thread before the first delegate") | `mcp__np__catalog` and the prompt section |
| Child as a first-class thread with lineage `{parentThreadId, relationshipToParent, rootThreadId}` and a task row on the parent | NativePeers group/worker/task (system of record) and a shadow agent (`claude_agent_id` binding) |
| Async default; the tool result says "end your turn, completion wakes you"; results inlined up to 16 KB; steered into a live turn or queued as a new turn | Native background-agent notifications (X14, X9): the engine delivers into the running turn or wakes the idle session. Final answer inline up to 12 KiB, with references. |
| Reading a result acknowledges delivery | Mirror ack and route `MIRROR_DELIVERED` |
| Human follow-up to a finished child records a new task on the parent and wakes it | Native resume by message (X9), a new NativePeers task generation, and a second notification |
| "Waiting on N subagents … Stop" (`ThreadActivityBanners.tsx`), with Stop cascading through `stopDelegatedTasks` | Band and `[Stop all]`, using NativePeers' cascading group cancel |
| Lineage sidebar (`ThreadRelationshipsControl.tsx`), "Subagent of" divider, "Sent by another agent" label (since 2026-10-22 a folded row plus "Open thread") | Native tasks list, band header, and `UserMessage` provenance rows folded to one line |
| Capability flags (steering supported by Codex, Claude and Muse; ACP cannot steer) | Bridge `capabilities.steer` per provider; queue fallback |
| Privilege ceiling (children never broader than the parent); scoped, revocable per-session tokens | NativePeers caller profiles; bridge binding and token |
| Event log, outbox, idempotent finalization, startup recovery scan | Activity log, route states, idempotent request IDs, re-attach |

**T3 pitfalls not to inherit**
- unlimited fan-out and depth (NativePeers keeps its caps);
- a shared checkout as the default for concurrent writers (NativePeers uses read-only analysis or isolated worktrees);
- child approvals that never reach the parent (NativePeers workers run fixed profiles and never ask);
- monolithic orchestration code;
- ACP harnesses that cannot steer (queue explicitly and say so).

**Not needed: reverse engineering.** T3 Code is open source, and Claude Code exposes the needed seams through its mod API. The REA toolkit (Ghidra) is **not** to be used to patch either product. Read-only diagnosis of Claude Code behavior is permitted only if an M0 gate is ambiguous and the engine's own debug log (`claude --debug`) cannot settle it.

---

## Appendix A: Reproduced spike (X), condensed

This is a minimal, verified pattern on Claude Code 2.1.314. The production mod must split it into the files listed in §7.1, add guards and replace the fake activity with the bridge.

```ts
// hooks/register.ts — plugin "npspike"
import type { Register } from 'claude-code'
let shadowId: string | undefined
const tid = () => 'toolu_01' + Math.random().toString(36).slice(2, 12) + Math.random().toString(36).slice(2, 12)

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const r = await next(e)
    await $.agent.register({ name: 'mirror', description: 'Mirror of an external worker',
      prompt: 'You are a display placeholder. Reply exactly: MIRROR-FALLTHROUGH',
      tools: ['mcp__npspike__mirror_exec'], model: 'haiku', omitClaudeMd: true })
    await $.tool.register({ name: 'mirror_exec', description: 'internal', isDeferred: false,
      inputSchema: { type: 'object', properties: { command: { type: 'string' } } } })
    // (test-only) spawn_shadow / wait_shadow tools registered the same way
    return r
  })

  // Spawn from a tool.call hook (X1): the shadow's steps skip THIS hook only (re-entry rule).
  on('tool.call', { tool: 'mcp__npspike__spawn_shadow' }, async ($, e) => {
    const r: any = await $.agent.spawn({ prompt: 'BRIEF…', description: 'codex gpt-6.1-sol review',
      subagentType: 'npspike:mirror' })        // → { model: 'claude-haiku-5-5', agentId }
    shadowId = r.agentId
    return { result: 'spawned ' + r.agentId }
  })

  // The mirror tool never executes anything; it replays a recorded result (X3).
  on('tool.call', { tool: 'mcp__npspike__mirror_exec' }, async () =>
    ({ result: 'exit_code: 0\nstdout: hello from the external worker' }))

  // Answer the shadow's steps alone (X2); pass every other loop through.
  on('turn.step', async function* ($, e, next) {
    if (!e.agentId || e.agentId !== shadowId) return yield* next(e)
    if (e.index === 0) {
      yield { kind: 'text', index: 0, text: 'Mirroring external worker: running echo hello' }
      yield { kind: 'tool', index: 1, id: tid(), name: 'mcp__npspike__mirror_exec' }
      yield { kind: 'input', index: 1, json: JSON.stringify({ command: 'echo hello' }) }
      yield { kind: 'stop', stopReason: 'tool_use', usage: null }
      return { turnId: e.turnId, index: e.index, answer: '', stopReason: 'tool_use', usage: null,
               toolUses: [{ name: 'mcp__npspike__mirror_exec', input: { command: 'echo hello' } }] }
    }
    yield { kind: 'text', index: 0, text: 'External worker finished: hello' }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: 'External worker finished: hello',
             toolUses: [], stopReason: 'end_turn', usage: null }
  }).catch(async function* ($, e, next) {   // MANDATORY fail-closed path (X10)
    const text = `\n[NativePeers mirror fault: ${next.error?.kind ?? 'unknown'}. Worker unaffected.]`
    yield { kind: 'text', index: 0, text }
    yield { kind: 'stop', stopReason: 'end_turn', usage: null }
    return { turnId: e.turnId, index: e.index, answer: text, toolUses: [], stopReason: 'end_turn', usage: null }
  })
}
// Follow-up to a completed shadow (X9):
//   await $.session.send({ to: { agentId: shadowId }, text: 'FOLLOWUP: …' })  // → { isDelivered: true }
// Waits that outlive 10 s must be $ calls (X11): $.http.fetch(..., { socketPath }) or $.process.run([...], { timeoutMs }).
// Every turn.step path, and the .catch handler, returns a complete TurnStepResult; never a bare return (X10).
```

**Headless run:**

```sh
env -i HOME="$TMP/home" PATH="$PATH" ANTHROPIC_BASE_URL=http://127.0.0.1:18080 claude -p go --plugin-dir ./npspike --output-format stream-json --verbose
# with a tripwire stub listening on 127.0.0.1:18080 (X13): HTTP 400 on every request, a hit counter, no header logging
```

A test-only `turn.step` branch answered the main loop with tool calls to `spawn_shadow` and `wait_shadow`. The result record showed `duration_api_ms: 0` and `total_cost_usd: 0`.

---

## Appendix B: `activity.v1` examples

```json
{"schema":"nativepeers.activity.v1","seq":41,"at":"2026-10-30T17:12:01.120Z","worker_id":"W","group_id":"G","task_id":"T","generation":1,"native_turn":"turn_9","item":"item_17","kind":"tool.started","data":{"kind":"exec","title":"pytest -q","input":{"command":"pytest -q","cwd":"/Users/danny/Documents/Tools/LocalFlow"}},"source":{"provider":"codex","event":"item/started"}}
{"schema":"nativepeers.activity.v1","seq":44,"at":"2026-10-30T17:13:31.402Z","worker_id":"W","group_id":"G","task_id":"T","generation":1,"native_turn":"turn_9","item":"item_17","kind":"tool.completed","data":{"status":"ok","exit_code":0,"duration_ms":90282,"output":{"text":"42 passed in 90.1s","truncated":false,"bytes":19}},"source":{"provider":"codex","event":"item/completed"}}
{"schema":"nativepeers.activity.v1","seq":57,"at":"2026-10-30T17:14:02.010Z","worker_id":"W2","group_id":"G2","task_id":"T2","generation":1,"native_turn":null,"item":"toolu_x","kind":"files.changed","data":{"paths":["src/pipeline.py"],"stats":{"added":12,"removed":3},"diff":{"text":"@@ -10,3 +10,12 @@ …","truncated":false}},"source":{"provider":"claude","event":"assistant/tool_use:Edit"}}
{"schema":"nativepeers.activity.v1","seq":63,"at":"2026-10-30T17:15:40.000Z","worker_id":"W","group_id":"G","task_id":"T","generation":1,"native_turn":"turn_9","item":null,"kind":"task.terminal","data":{"status":"completed","final_text":"Three easy wins: …","result_artifact_id":"A"},"source":{"provider":"codex","event":"turn/completed"}}
```

---

## Appendix C: Key sequences

**C1. Path A fan-out (one worker shown)**

```text
parent model → Agent(np:codex, "```np {...}```brief", run_in_background)
  mod agent.spawn: parse → POST /v1/spawn{check} ok → next(e) → agentId A
                   → POST /v1/spawn{client_request_id np:S:A, mirror} (async)
  engine: task_started(A) → tasks list row
  shadow step 0 (turn.step, A): GET /v1/bindings/A … bound(W) → GET activity(after 0, wait)
     message.delta… → text; tool.started(exec) → tool_use mcp__np__mirror{ref W:1:item} → stop
  engine → mirror tool.call: GET activity(item, wait) → tool.completed → {result}
  shadow step 1: … task.terminal → POST surfacing → final text → stop(end_turn)
  engine: task_notification → parent's turn (or wake); mod turn.complete(A) → POST ack
```

**C2. Person follow-up to completed worker**

```text
person views A, types "also check the tests"  → engine resumes A (agent.spawn resume) → user row (uuid U)
  mod session.append: capture U → step 0: POST /input{np-in:U, origin human-claude-view} → resumed (gen 2)
  stream gen 2 … task.terminal → final → second notification to parent
```

**C3. Path B adoption**

```text
parent model → Bash("npeer spawn --spec … --json")
  mod Bash observer: await next(e) → GET lineage (new, unmirrored) → $.agent.spawn(np:grok, adoption brief) → B
                     → POST /mirror/attach{W, B} (display-only; initial delivery stays with NativePeers) → return original Bash result
  shadow B backfills from seq 0 (batched), then streams live
```

**C4. Fault**

```text
shadow step throws / overruns → .catch yields fault text → turn ends → parent notified "mirror fault"
  worker unaffected (lease held by bridge) → person: /np reattach → new shadow backfills → normal mirroring
```
