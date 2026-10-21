# NativePeers — Native Messaging and Cross-Harness Delegation

**Implementation specification · revision 1.1 · research baseline: 2026-10-20 · reviewed: 2026-10-21 (UTC)**

**Status:** implementation-ready design, not an implementation or a claim of local verification.

**Companion:** `KICKOFF_PROMPT.md` revision 1.1 is the autonomous implementation instruction. This document is the normative technical contract. `REVIEW.md` records this revision's findings; it is explanatory, not an additional set of requirements. The two revision-1.1 files supersede revision 1.0 and the earlier proposed addendum.

**Review verdict:** ready for gated implementation. This is document/design acceptance, not a runtime PASS. No local provider, CodexMCP, or live-model test was performed by this document review. New execution defaults and protocol clarifications below are proposed engineering decisions, not newly discovered vendor guarantees.

**Working project name:** NativePeers. **Proposed command:** `npeer`; check for a collision before installation. `python -m nativepeers` is the non-conflicting fallback, not a replacement for `claude` or `codex`.

---

## 1. Executive decision

Build a small, symmetric interoperability layer around the **actual Claude Code and managed Codex runtimes**. Keep their native conversation IDs, histories, authentication, terminal interfaces, model settings, and existing user launch commands. Do not make T3 Code, Herdr, Agent Messenger, tmux, a GUI, or a permanently running central broker a dependency.

Separate two problems that must not be conflated:

1. **Addressing an existing live conversation.** Find its native identity, prove which runtime currently owns it, and deliver through that owner's supported input path. Never resume it in another process merely to send a message.
2. **Creating an owned worker.** Launch a new native conversation through the appropriate provider interface, supervise its process, and retain the exclusive right to resume or cancel that worker. Its parent may be either provider.

The recommended architecture is **a Python core, provider adapters, native lifecycle hooks, small agent skills, a narrowly scoped MCP facade for owned workers, and finite-lifetime delegation supervisors**. The core uses local files and its own SQLite journal; it does not write either provider's databases or rollouts. An optional Rust patch adds an in-process Codex inbox **only if a defined live compatibility gate proves the native queue inadequate**. The fallback is fully specified here so the implementer can continue without returning for an architectural choice.

For worker execution, use **Codex's app-server over private stdio** and **Claude's installed CLI in structured headless mode**, initially one owned provider process per worker. Claude workers use serialized native CLI turns and exact-ID resume after the previous process has exited. This deliberately avoids making subscription authentication depend on a new SDK product or reimplementing its private control protocol. Persistent Claude streaming can be enabled only as a validated adapter optimization, not as a prerequisite or a silent billing change.

All four delegation directions use the same application-level worker contract: Claude→Codex, Codex→Claude, Claude→Claude, and Codex→Codex. These are **independent native worker sessions**, not falsely advertised vendor-native subagents. Existing vendor-native subagent tools remain available and unmodified.

“Native-first” means native-backed conversations and transports, not adding GPT workers to Claude's built-in `Agent` picker or Claude workers to Codex's internal subagent tree. Parents invoke the namespaced `worker_spawn` tool or `npeer spawn` through a small skill; the original `claude` and `codex` launch commands do not change.

“One sustained run” is an execution goal, not an assumption of an immortal agent process. The implementing agent must checkpoint code, evidence, remaining test allowance, and the next action so it can continue after context compaction, disconnection, or a usage boundary without restarting the design or replaying uncertain model work. Section 21 defines the checkpoint contract.

### The decisive correction to the earlier conversation

A blanket statement that native Codex queues cannot reach standalone sessions is not justified. The `rust-v0.163.0` queue extension contains an external-change watcher that checks persisted queue revisions and wakes **loaded** threads. The command separately rejects some explicit `--no-daemon` combinations, and unloaded threads are not automatically resumed. These are three different facts. Therefore, test the actual managed build and process topology before adding a listener. [S03–S06]

### Definition of the intended experience

The user launches ordinary sessions as before:

```sh
claude -n LocalFlow
codex -n Code Review
```

After integration, the parent can be told:

> Ask Code Review to check the change and send its findings back.

Or:

> Spawn two GPT-6.1 Sol workers at xhigh. Have one audit the transcription pipeline and the other independently evaluate the architecture. Run both concurrently, then compare their findings.

Neither instruction requires the user to supply a directory for a recipient, a PID, a native UUID, or a terminal-pane address. A spawned worker still needs a work location; that defaults to the verified parent's working directory, subject to the worktree rules below.

---

## 2. Evidence baseline and limits

### 2.1 Evidence labels

Every implementation report and compatibility record must distinguish:

- **U — User-reported:** the supplied installation/naming reports; not independently re-executed here.
- **D — Documented:** supported by a current official documentation page.
- **S — Source-inspected:** supported by a named public source revision or tag; not necessarily the installed patched binary.
- **P — Proposed:** an engineering decision in this specification.
- **L — Locally verified:** established by the implementing agent on the target Mac with an evidence file.

Never relabel U, D, or S as L. This design environment did not inspect `/Users/danny`, run the user's CLIs, inspect local credentials, or execute model turns.

### 2.2 Protected local baseline — U, verify before modifying

| Component | Supplied baseline | Required treatment |
|---|---|---|
| Managed Codex | `rust-v0.163.0-8f85dd9c6933`, CLI 0.163.0 | Retain its manager and existing patch set |
| Manager | `/Users/danny/Documents/Tools/CodexMCP` | Inspect in place; do not repurpose as the new project |
| Managed executable | `~/.local/share/codex-mcp-manager/current/bin/codex` | Resolve and record the actual immutable release for each owned worker |
| Naming | `-n`, `--name`, unquoted multiword names, empty-session discovery, resume | Completed functionality; regression-test, do not reimplement |
| Dynamic MCP | live enable/disable/re-enable | Completed functionality; preserve binary and config behavior |
| Ordinary Codex permissions | existing shell wrapper adds approval/sandbox bypass | Preserve exactly; do not assume GUI or child launches inherit it |
| Daemon policy | `[features] daemon_auto_start = false` | Preserve; distinguish this from explicit `--no-daemon` |
| Claude | last reported 2.1.305, `~/.local/bin/claude` | Recheck actual path/version/auth status |
| Native storage | existing `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, or their defaults | Do not relocate, import, truncate, or edit native state directly |
| Herdr/T3 | installed and working; one round trip reported in each environment | Leave operational; they are neither dependencies nor proof of native-only messaging |

Historical verification logs may be read if still present. Missing temporary logs do not invalidate the new capability tests, and historical PIDs or sockets must never be reused as current endpoints.

### 2.3 Public research baseline and this review’s verification scope

| Finding | Evidence | Design consequence |
|---|---|---|
| Claude exposes cross-session discovery/messaging and a per-session local inbox; scripts/hooks can obtain the socket address. Incoming messages can be accepted, held, or refused. | D [S01] | Use the native inbox with honest receipt semantics and receiver policy intact |
| Claude's complete third-party socket framing and registry format are not a stable public specification. | D/S [S01, S19] | Isolate the wire codec, bind it to verified versions, and test it; never spoof permission class |
| Codex app-server offers native thread/turn APIs, model discovery, notifications, and private stdio transport. | D [S02] | Use it for workers owned by NativePeers |
| Codex 0.163.0 has an external queue-change watcher for loaded threads. | S, pinned tag [S03, S04] | Native queue is the first live-session candidate, not a presumed failure |
| `codex queue` uses `thread/queue/add`; explicit no-daemon handling and server selection are separate from destination liveness. | S, pinned tag [S05] | Probe the target process topology and sender-side effects separately |
| Queueing an unloaded thread does not imply resuming it. | Maintainer statement [S06] | Offline delivery never secretly spawns/resumes an arbitrary peer |
| Claude's `-p` mode has structured results, native session IDs, and exact-ID resume. | D [S07, S08] | A supervisor can own an autonomous Claude worker without a terminal |
| The Claude Python SDK has stateful clients, interrupts, custom CLI paths, and session APIs. | D [S09] | Useful reference/optional optimization, not required to borrow a live session or change billing |
| Custom Claude channels are an opt-in preview; the development-channel flag is not a headless fallback. | D [S10] | Do not claim an MCP server alone wakes arbitrary Claude sessions |
| Claude SessionStart hooks can publish session-local environment bindings. | D [S11] | Bind CLI calls to the real sender without rewriting the user's shell wrapper |
| Vendor-native subagents have provider-owned lifecycle and addressing semantics. | D/S [S12, S13] | Do not equate them with independent cross-provider processes |

This revision rechecked the version-pinned queue sources [S03, S05] and the official hooks/headless references [S11, S07]. The other source labels are preserved from the supplied research baseline, not represented as fresh checks by this review. `REVIEW.md` separates file-derived findings, newly checked sources, and proposed fixes.

Public docs and `main` source can advance beyond the user's build. Store the exact installed schema/help/source fingerprints in `evidence/capabilities.json` before emitting provider requests. A public source check does not establish availability in the user's custom binary.

### 2.4 Model evidence

The user's required test configuration is the exact requested model identifier `gpt-6.1-sol` and reasoning effort `xhigh`. Treat that as a requirement to validate, not as an entitlement claim. Discover the installed provider's model/effort capabilities and confirm them in a bounded real turn. A catalog entry is not proof that the user's current account can execute the request. Never silently substitute another model or reduce `xhigh`; record provider-reported values separately from requested values. [S02, S20]

---

## 3. Goals, non-goals, and acceptance boundary

### Required in this implementation

- Exact native-title discovery and provider-qualified addressing without recipient directories.
- Both directions of cross-provider messaging plus both same-provider directions.
- Correct multiple-round replies, concurrent request correlation, and explicit transport states.
- Busy-recipient queueing, idle delivery, stale endpoint rejection, and safe resume rebinding.
- Parent-initiated parallel creation of two workers of the other provider.
- Follow-up messages, sibling exchange when granted, attributed results, cancellation, and native history retention.
- A parent completion notification that reaches the **same original conversation**, or an explicit retained/pending result if that parent is unavailable or holds messages.
- Native Codex naming/MCP/launch behavior preserved.
- No runtime dependency on a terminal host or central orchestration UI.
- One sustained implementation with gated tests, installation, documentation, and targeted rollback.

### Not required and not to be silently added

- A fleet UI, cloud broker, scheduler, new model API gateway, new account system, or universal conversation import.
- Automatic worktree merging, force pushes, automatic permission approvals, or unlimited recursive spawning.
- Copying parent reasoning/transcripts into every child.
- Guaranteed ingress into an externally owned Desktop runtime that exposes no supported route.
- Production multi-machine delivery. Provide the adapter boundary and an explicit unsupported status; do not install SSH services/tunnels in v1.
- Literal exactly-once model execution across a crash between two independent systems.

**Local v1 support contract:** eligible, registered Claude interactive sessions; the managed Codex standalone TUI after the native-queue gate or patch; owned headless workers; and explicitly reachable app-server sessions whose ownership can be established. Unsupported host modes do not invalidate the core release, but unsupported core CLI messaging or either cross-provider spawn direction does.

Terminal independence means transport does not manipulate terminal panes. It does **not** mean every unrelated app's private backend becomes externally addressable. Core CLI support cannot be downgraded to an optional host mode to obtain a PASS. Conversely, missing private Desktop/T3 ingress or cross-machine support must not trigger an unauthorized redesign or block otherwise complete core CLI work.

There are two installation outcomes to report separately: **integration installed** and **the requested receiver policy permits unattended delivery**. A receiver deliberately holding/refusing peer messages is not a transport failure to bypass. Disposable-session policy tests must not be presented as proof of unattended delivery under the user's unchanged everyday policy.

---

## 4. Selected architecture and rejected alternatives

### 4.1 Components

| Component | Responsibility | Lifetime |
|---|---|---|
| `nativepeers` Python package | validation, identity, discovery, routing, journal, permissions, CLI | invoked on demand |
| `npeer` CLI | human/agent commands and JSON interfaces | one command, except an explicit wait |
| provider hooks | register exact session identity, update lifecycle, expose sender binding | short native hook invocations |
| agent skill | teaches discovery, send/reply, delegation, and completion handling | provider-loaded skill, no polling agent |
| owned-worker MCP facade | narrowly bound tools for workers whose tool profile excludes arbitrary shell execution | provider-owned stdio subprocess |
| delegation supervisor | owns one batch, workers, deadlines, result aggregation and parent notification | finite, only while a delegation exists |
| provider worker adapter | one native writer per worker, event translation | worker lifetime |
| optional Codex inbox patch | native live-TUI ingress when existing queue fails the gate | inside the existing Codex process |
| own SQLite journal/artifact store | correlation, receipts, tasks, recovery evidence | files, not a server |

Use Python 3.12+ with `asyncio`, `sqlite3`, `subprocess`, `socket`, `dataclasses`, and `tomllib`. Use the official Python MCP SDK only for the small facade; pin its version and transitive dependencies. Do not make an LLM framework a dependency. Development dependencies may include pytest, pytest-asyncio, Hypothesis, Ruff, mypy, and a JSON Schema validator. Reuse the managed Codex Rust dependency stack for any patch.

### 4.2 Data flow

```text
existing Claude CLI                         existing managed Codex TUI
  native UUID/title                           native UUID/title
  SessionStart binding                        native hook binding
          |                                            |
          +---- npeer CLI / small native skill --------+
                              |
                 resolve title -> exact PeerKey
                              |
                own journal + transport selection
                     /                    \
       Claude native inbox          native Codex queue
       (receiver controls)          OR conditional inbox patch
                     \                    /
               explicit reply -> original PeerKey
```

```text
Claude OR Codex parent
    |
    +-- spawn_many(specs, one idempotency key)
           |
           +-- finite delegation supervisor -- own result/outbox journal
                   |
                   +-- worker A: managed Codex app-server + native thread
                   +-- worker B: managed Codex app-server + native thread
                   OR
                   +-- worker A: native Claude CLI + native session
                   +-- worker B: native Claude CLI + native session

provider events -> per-worker result -> batch result -> original parent inbox
```

There is no model acting as a mandatory supervisor. The supervisor is ordinary local code. The parent model decides tasks and interprets results.

### 4.3 Existing projects reviewed

| Reference | Useful mechanism | Why it is not the default runtime dependency |
|---|---|---|
| `cxpeer` | Codex hooks, correlated result forwarding, native-Claude peer presentation | Writes undocumented Claude peer registry structures; spawning uses tmux; its documented sandbox fallback polls files. Do not inherit its advertised guarantees without tests. [S14] |
| `session-peer` | native inbox/queue adapters, exact destination handling, honest submission/ACK distinction | Valuable reference and possible MIT-licensed utility reuse; does not establish this user's no-daemon, parent-wake and parallel-worker contract. [S15] |
| `inter-agent-bus` | lease IDs, bounded offline state, delivery serialization | Central broker and PTY adapters are a different execution model; some native adapters require external modules. [S16] |
| `agent-intercom-claude` | owned headless worker and resume lifecycle | Shared broker and adapter ecosystem are not necessary here; native-wire behavior remains version-sensitive. [S17] |
| Herdr Agent Messenger | sender envelopes and useful peer workflow | Depends on live Herdr panes and temporary identities; does not meet independence. [S18] |

These were reviewed as public descriptions/source references, not installed or tested here. Do not copy unlicensed code. Check the exact revision's license before reuse; conceptual comparison is not permission to redistribute. Keep attribution for any licensed code actually incorporated.

### 4.4 Alternatives rejected

- **T3 or Herdr as message bus:** violates the independence requirement.
- **A new permanent broker:** unnecessary for same-machine discovery/direct delivery; supervisor processes have bounded task ownership instead.
- **Raw terminal injection:** risks draft corruption, uncertain Enter delivery, and terminal coupling.
- **A new `--resume` writer on an active peer:** violates native ownership and can corrupt or split the intended interaction.
- **MCP notifications alone:** not a universal native wake mechanism.
- **Writing provider SQLite/JSONL directly:** unsupported and unnecessary.
- **Registering fake Claude sessions:** not needed for symmetric tools and creates avoidable undocumented authority/identity coupling.
- **Rust patch first:** source evidence requires a native-queue experiment first.
- **Vendor-specific subagents as the cross-provider abstraction:** cannot establish that one provider's native subagent tool launches the other's harness. Uniform independent workers avoid this false equivalence.

---

## 5. Project layout and installation ownership

Create the project in a new directory, preferably:

```text
~/Documents/Tools/NativePeers/
  IMPLEMENTATION.md
  KICKOFF_PROMPT.md
  REVIEW.md                  # supplied design review; explanatory only
  PROGRESS.md                # durable implementation handoff, kept concise
  pyproject.toml
  uv.lock
  src/nativepeers/
    cli.py                 # argv/JSON contracts only
    models.py              # typed data and validation
    registry.py            # stable identity, incarnation, resolution
    caller.py              # provider-specific caller binding
    journal.py             # own SQLite migrations and transactions
    routing.py             # capability-based dispatch; no provider heuristics
    protocol.py            # canonical envelopes and errors
    receipts.py            # evidence-backed transition reduction
    authority.py           # local profiles, delegated capability ceilings
    artifacts.py           # bounded artifact storage/readback
    worktrees.py           # explicit baseline and isolated write roots
    supervisor.py          # delegation group lifecycle
    worker_process.py      # owned-process watchdog and cleanup
    aggregation.py         # deterministic result assembly, no model calls
    mcp_server.py           # same core functions; bound worker identity
    hooks.py               # fast register/update/unregister handlers
    install.py             # merge-only configuration and uninstall manifest
    adapters/
      base.py
      claude_discovery.py
      claude_inbox.py
      claude_worker.py
      codex_discovery.py
      codex_queue.py
      codex_app_server.py
      codex_tui_inbox.py    # client only; patch conditional
  schemas/
    peer.schema.json
    message.schema.json
    spawn.schema.json
    result.schema.json
    receipt.schema.json
  integrations/
    claude/skills/nativepeers/SKILL.md
    codex/skills/nativepeers/SKILL.md
    hooks/
  patches/codex/            # candidate patch + manifest, not another manager
  tests/{unit,contract,integration,live,fixtures,probes}/
  examples/
  docs/{usage,architecture,security,compatibility,uninstall}.md
  evidence/
    checkpoint.json         # code/evidence fingerprints and next exact action
    feasibility.json        # M1 probe outcomes, not full-feature acceptance
    live-budget.json        # durable reservations and usage across restarts
```

Local user state:

```text
~/.config/nativepeers/config.toml
~/.local/state/nativepeers/
  journal.sqlite3          # only our state; WAL and SHM owned by us
  peers/                   # atomic, credential-free descriptors
  bindings/                # private session bindings; never included in reports
  workers/                 # owned worker process manifests
  artifacts/               # results and approved context, not provider transcripts
  install-manifest.json
  compat/                  # fingerprints and tested adapter contracts
```

Honor an explicit `NPEER_HOME` for isolated tests. Keep runtime Unix sockets under a validated short private path such as `/tmp/npeer-<uid>/`; check parent ownership, symlinks, modes, and platform path-length limits. Never derive a socket pathname from untrusted titles.

Install only a `npeer` entry point and namespaced integration files. Do not replace `codex`, `claude`, `msg`, `t3`, or `herdr`. Do not rewrite `.zshrc`. When an existing `npeer` belongs to another package, use the module entry point and record the command alias chosen without overwriting the collision.

The final user-scope install adds short hooks and a skill; an MCP facade is optional for ordinary interactive sessions and mandatory only for worker profiles that need bound tool access without arbitrary shell commands. M1 uses explicitly scoped test hooks/skills and scratch state, not a premature global installation. A global MCP entry is not allowed to guess its session by working directory. Unsupported identity binding means it must expose no mutating operation. Pure listing/diagnostics can remain available without inventing a sender.

---

## 6. Identity, registration, and native-title resolution

### 6.1 Stable identity versus a live process

Use the following internal identity:

```python
@dataclass(frozen=True)
class PeerKey:
    host_id: str           # locally generated installation UUID
    provider: str          # "claude" or "codex"
    home_id: str           # digest of canonical provider home; no credentials
    native_session_id: str # exact provider ID, not a title or PID
```

A live registration adds an `incarnation_id` (random UUID), PID, OS process-start identity, canonical executable path, tested transport, title, working directory, owner kind, capabilities, and lifecycle state. A restarted/resumed native conversation keeps its PeerKey but receives a new incarnation. A fork gets a new native ID and therefore a new PeerKey.

Distinguish the **logical conversation owner** from an individual provider-turn process. For a turn-at-a-time Claude worker, the controller owns the stable worker route while successive native CLI processes have distinct process identities. A native SessionEnd for one finished turn must not unregister that controller-owned conversation or revoke its follow-up route. For a standalone interactive peer, the native session itself remains the owner.

A stable public handle is `np1_` followed by the lowercase SHA-256 hex digest of the canonical PeerKey JSON (UTF-8, sorted keys, compact separators). Store the reverse mapping in the journal. It is an identifier, not a secret or an authorization token; resolve it through the registered record before use.

Provider home matters: identical names or IDs in distinct configuration homes are not assumed interchangeable. Host qualification is reserved for future adapters; v1 accepts only the local host.

### 6.2 Descriptor example

```json
{
  "schema_version": 1,
  "peer_key": {
    "host_id": "018f7a21-0000-4000-8000-000000000001",
    "provider": "codex",
    "home_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "native_session_id": "018f7a21-0000-4000-8000-000000000002"
  },
  "incarnation_id": "018f7a21-0000-4000-8000-000000000003",
  "title": "Code Review",
  "title_source": "native-explicit",
  "owner_kind": "interactive-standalone",
  "pid": 12345,
  "process_start": "os-process-start-identity",
  "cwd": "/example/project",
  "provider_version": "locally-detected",
  "transport": {"kind": "codex-native-queue", "profile_id": "verified-local"},
  "capabilities": {
    "send": true,
    "receive": true,
    "idle_wake": "verified",
    "busy_delivery": "next-turn",
    "cancel_owned_work": false
  },
  "lifecycle": "idle",
  "last_evidence_at": "2026-10-20T23:00:00-04:00"
}
```

Do not put auth tokens or exported provider messaging tokens in this descriptor.

### 6.3 Discovery order

1. Read NativePeers' registered descriptors and validate their owner process identity.
2. Query documented native discovery where available, including `claude agents --json` and reachable app-server listings.
3. Join by exact native ID, not cwd, title resemblance, or mtime.
4. Obtain the current title from supported provider metadata APIs first. A versioned read-only metadata reader is acceptable when no current public title API exists. The managed naming implementation's actual metadata source must be inspected rather than assumed.
5. Treat transcript/header fallbacks as display-only unless they are proven to represent the authoritative native name. Do not route by an LLM-generated guess of the task title.
6. Show saved/offline conversations separately. A transcript file's presence or recent mtime is not proof of a live receiving process.

Use targeted native metadata reads; do not scan every full transcript on each command. Cache by provider identity/revision and invalidate through lifecycle hooks or observed metadata changes.

### 6.4 Address syntax

- `Code Review`: exact title, only if unique across eligible local peers.
- `codex:Code Review`: exact title within a provider.
- `claude:LocalFlow`: equivalent Claude selector.
- Opaque stable handle returned by `resolve`: exact PeerKey, useful to the agent after initial selection.
- `provider@home-label:title`: disambiguates explicitly configured multiple homes.

Titles use Unicode NFC normalization but remain case-sensitive for routing. Case-insensitive/fuzzy search may suggest candidates; it never authorizes a send. A rename updates the alias, not identity. No automatic stale-title redirects.

A send resolves once, records the PeerKey, then revalidates the live incarnation immediately before transport submission. If a name now points elsewhere, do not silently retarget. If the same native session has legitimately resumed, return `TARGET_REBOUND` and retry the **resolution step**, not an uncertain transport submission.

`AMBIGUOUS_TARGET` includes provider, title, short handle, state, home label, and cwd as optional explanatory metadata. The human need not type a cwd; they select a candidate. Never choose the newest conversation automatically.

### 6.5 Caller binding

A model-supplied `from`, PID, name, or `CODEX_THREAD_ID` string alone is not authentication.

- Claude SessionStart registers the hook's native session ID and documented inbox endpoint. It appends only NativePeers environment assignments to `CLAUDE_ENV_FILE`, preserving other hooks. [S11]
- Codex lifecycle hooks register native thread ID and host process evidence. Shell calls use the installed build's native thread metadata plus an owned binding verified against process ancestry/current incarnation. Verify the actual exported variables in M1; do not assume them from another version.
- Owned workers receive a private bootstrap binding created by their supervisor. It becomes active only after the provider's emitted native ID matches the reserved worker identity.
- The optional MCP facade is launched with a fixed binding by the worker owner. Its tools cannot choose an arbitrary caller session.
- An invocation without a session binding is **unbound local origin**, not authenticated human/operator authority. It may perform passive discovery and, if the configured external-sender policy permits, submit a clearly labeled external notice without inventing a native reply address. It cannot create an approval, raise a worker ceiling, cancel arbitrary sessions, or obtain operator-only control merely because binding variables are missing. A model subprocess that loses its binding must not fall back to human identity.

A stale/missing binding produces `CALLER_UNBOUND` for session-origin and delegated control operations; it must not choose a session by cwd or terminal focus. Tokens and process checks prevent accidental cross-session attribution; they are not a security boundary against a malicious process already running as the same unrestricted OS user.

### 6.6 Owned-worker bootstrap and controller identity

Worker bootstrap is two-phase: `RESERVED_UNBOUND` then `BOUND`. The supervisor can launch the provider and its MCP facade using a private bootstrap lease before the provider has emitted its native ID. The facade must complete its protocol handshake without waiting on a hook or native ID that the same startup is about to produce. Before binding, it exposes no mutating tools; an early attempted mutation returns `CALLER_NOT_READY` promptly.

Once the native initialization event or verified native hook supplies the exact ID, the supervisor binds the lease and activates the permitted tools. When a UUID was preselected, require a match. Otherwise bind once to the actual emitted ID; never substitute a transcript found by cwd. A failed/mismatched bind cancels only this owned startup.

Inherited NativePeers hooks must recognize a controller-owned worker through its verified bootstrap lease. They report native lifecycle observations to that controller; they must not replace its authoritative transport with the ordinary Claude socket/Codex queue, create a duplicate peer, or unregister the worker between serialized turns. Hook reporting remains bounded and nonblocking even while binding is pending. Apply the same rule to the worker's own MCP subprocess.

A delegation supervisor also needs a real sending identity. Use a separate `controller` actor keyed by `group_id` and supervisor generation, bound to its owned-process lease. It may publish completion/failure for its group to its recorded parent and operate its own workers only. It is not a synthetic Claude/Codex session and must not borrow the parent's or a worker's token to impersonate that session. Individual results retain the producing worker's native identity as attribution metadata.

---

## 7. Message protocol and API contract

### 7.1 Envelope

All application messages are UTF-8 JSON, schema version 1. NativePeers controls the envelope; message bodies remain data, never shell code or permission grants.

```json
{
  "schema_version": 1,
  "message_id": "018f7a21-0000-4000-8000-000000000030",
  "exchange_id": "018f7a21-0000-4000-8000-000000000031",
  "kind": "request",
  "from": {"kind": "peer", "peer_key": "np1_1111111111111111111111111111111111111111111111111111111111111111", "incarnation_id": "018f7a21-0000-4000-8000-000000000032"},
  "to": {"peer_key": "np1_2222222222222222222222222222222222222222222222222222222222222222"},
  "reply_to": {"peer_key": "np1_1111111111111111111111111111111111111111111111111111111111111111"},
  "in_reply_to": null,
  "created_at": "2026-10-20T23:00:00-04:00",
  "expires_at": "2026-10-20T23:30:00-04:00",
  "delivery_mode": "queue",
  "expect_reply": true,
  "hop_count": 0,
  "max_hops": 8,
  "origin": "peer-agent",
  "body": {"text": "Please review the committed fixture."},
  "artifact_refs": [],
  "task_id": null
}
```

Required fields: version, message ID, kind, sender identity, recipient identity, creation time, body. Requests additionally require an exchange ID and reply route when `expect_reply=true`. Replies require `in_reply_to` and route to the author of that specific received message, using its immutable journal record—not a body-supplied address. The caller must be the recorded recipient of the referenced message. This handles a first reply and subsequent back-and-forth replies without always redirecting every hop to the exchange's first author.

`from` is a discriminated actor: a bound native `peer`, a leased group `controller`, or `external-local`. `peer` has a PeerKey handle/incarnation; `controller` has group ID and generation; `external-local` has an explicitly non-privileged source label. These namespaces cannot collide. `origin` describes provenance, not permission: `peer-agent`, `group-controller`, or `external-local`. Controller completions are not requests and carry `reply_to=null`; use the returned worker handles for follow-ups. Validate actor capability separately from envelope shape.

Kinds: `notify`, `request`, `reply`, `completion`, `failure`. Transport receipts are separate records and must not trigger model turns. `cancel`, `spawn`, and `approve` are authenticated control operations, not message kinds that a text envelope can authorize.

Limits: 64 KiB serialized peer-delivery/request frame; 12 KiB UTF-8 inline message body; 16 artifact references; 256 KiB decoded artifact/result page per read; 10 MiB artifact default maximum. Bounded **local control/result responses** may be up to 2 MiB serialized to accommodate JSON escaping/base64 and metadata; they are not 64-KiB peer messages. Paginate large results rather than attempting to send a 256-KiB read through a 64-KiB delivery frame. Use the stricter native-provider limit whenever applicable. Reject oversize content or store an explicitly approved artifact and send its bounded summary. Do not silently truncate a message while reporting full delivery.

### 7.2 Journal schema

NativePeers owns one local SQLite database. Enable foreign keys, WAL, a bounded busy timeout, and durable commits for accepted work. Never hold a transaction while waiting for a provider or running a model.

Minimum tables:

```text
peer_instances(peer_key, incarnation_id PRIMARY KEY, pid, process_start,
               title, owner_kind, descriptor_json, status, last_seen)
send_requests(caller_key, client_request_id, payload_sha256, message_id,
              PRIMARY KEY(caller_key, client_request_id))
messages(message_id PRIMARY KEY, exchange_id, from_actor_key, to_key,
         in_reply_to, task_id, payload_json, payload_sha256,
         expires_at, created_at)
attempts(attempt_id PRIMARY KEY, message_id, target_incarnation,
         transport, external_request_id, external_submission_id,
         state, started_at, ended_at, error_json)
receipts(receipt_id PRIMARY KEY, message_id, attempt_id, stage,
         evidence_kind, source_native_id, source_turn_id, details_json, at)
spawn_requests(caller_key, client_request_id, spec_sha256, group_id,
               PRIMARY KEY(caller_key, client_request_id))
groups(group_id PRIMARY KEY, parent_key, policy_json, state, owner_manifest)
completion_routes(group_id, completion_generation, parent_key, mode, state,
                  waiter_id, lease_deadline, delivery_id, claimed_generation,
                  PRIMARY KEY(group_id, completion_generation, parent_key))
workers(worker_id PRIMARY KEY, group_id, peer_key, provider, native_id,
        role_title, model_requested, model_observed, effort_requested,
        cwd, worktree_id, state, generation, lease_json)
tasks(task_id PRIMARY KEY, worker_id, request_message_id,
      native_turn_id, state, result_artifact_id, terminal_reason)
events(sequence INTEGER PRIMARY KEY, subject_id, kind, payload_json, at)
artifacts(artifact_id PRIMARY KEY, sha256, byte_size, media_type,
          relative_path, owner_key, allowed_readers_json)
leases(lease_id PRIMARY KEY, owner_key, scope_json, expires_at, revoked_at)
```

Store private secrets outside ordinary report/query surfaces. Add migrations with schema versions and a test for rollback-compatible reads. The event sequence is local journal order, not a claim of global causal order between independent providers.

### 7.3 Delivery and evidence state machine

```text
CREATED -> OUTBOX_COMMITTED -> SUBMITTING
                                  |
                +-----------------+----------------------+---------------+
                |                 |                      |               |
       ACCEPTED_QUEUED   SUBMITTED_UNCONFIRMED            HELD          REJECTED
                |                 |                      |          (attempt ends)
                +----- INGESTED (matching evidence) <----+
                                  |
                    MODEL_REPORTED_PROCESSED (optional)
                                  |
                       REPLY_RECORDED -> REPLY_SUBMITTED
```

`HELD` is pending, not a successful delivery or an irreversible rejection. `DELIVERY_UNKNOWN` is an unresolved observation that can later be reconciled; it is not permission to resend. `REJECTED` is final only for the attempt that the provider demonstrably refused. Late native evidence and an explicit reply may advance a previously unknown record without creating another attempt.

Use `EXPIRED_BEFORE_SUBMISSION` and `CANCELED_BEFORE_SUBMISSION` only when no native admission occurred. Use `WAIT_EXPIRED` when the caller's deadline elapsed after admission or uncertainty. `TARGET_GONE` describes liveness and does not itself prove that an already submitted message was never consumed. A reply is itself a message with its own record; exchange success is a derived relation, not a transport-success boolean.

- A socket write proves bytes were written, not model ingestion.
- A Codex queue submission ID proves native acceptance, not processing.
- A matching hook/native transcript event proves ingestion into that conversation, not semantic comprehension.
- An explicit `ack` by the receiving model is labeled **model-reported**, not a verified read of every word. A correlated reply is sufficient response evidence even without a separate ACK.
- An exact correlated reply observed at the original sender establishes the complete round trip.

Receipts contain native session/turn IDs where available. Parse only documented/version-pinned event fields. Do not mark a response complete from a substring in a terminal screenshot. After handing a message to a native queue with no enforced expiry API, NativePeers cannot promise it will never run after its local deadline. Report `native_expiry_enforced=false`, stop waiting as requested, and use a supported exact-item cancellation only when authorized and its outcome is known. Do not mutate the native queue database or label an uncertain cancellation as definitive.

### 7.4 Idempotency and uncertain outcomes

The local journal deduplicates `(caller, client_request_id)` and message IDs. **Check an existing idempotency record before resolving a mutable title again.** Hash the canonical original caller request independently of its current title resolution. A matching retry returns the original pinned target and recorded result; changed content under the same ID is `IDEMPOTENCY_CONFLICT`. A rename or newly reused title must not retarget a retry.

For a new request, resolve once, commit its pinned PeerKey and attempt, then acquire the recipient admission lock before final revalidation/submission. Concurrent processes presenting the same request ID must converge on one journal record. Supply a stable external idempotency ID only through an API whose actual server-side deduplication semantics have been established; a field named `client_id` alone is not that evidence.

The vendor call and journal commit are not one transaction. A crash after transport acceptance but before local receipt can leave an uncertain outcome. **Do not promise exactly-once model execution.** In that window, set `DELIVERY_UNKNOWN`, reconcile through native queue/event evidence, and never resend automatically unless the transport contract proves the retry is idempotent or proves nothing was submitted.

If invoking `codex queue` gives no control over its generated client ID, invoke it once per attempt; an uncertain exit is not a reason to run it again. Prefer the same supported queue API through a verified endpoint when a caller-controlled idempotency field is available.

Per-recipient sends serialize across **all local CLI/facade/supervisor processes**, not just tasks in one Python event loop. Use an OS advisory lock keyed by the digest of the recipient PeerKey, with a bounded acquisition timeout. Hold that lock across the single transport admission attempt, but never hold a SQLite transaction across transport I/O. Process death releases the OS lock; the durable attempt still determines whether recovery is uncertain. Revalidate incarnation after acquiring the lock.

Different recipients proceed concurrently. The local attempt sequence orders admissions performed by NativePeers; a provider's own human inputs and other clients are outside that ordering. Ordering after provider admission is reported according to that provider's tested behavior; never imply that busy-tool steering is ordered like next-turn queueing.

### 7.5 Reply discipline and loop control

`npeer reply <message-id>` accepts a received peer request or reply, verifies that the bound caller is its intended recipient, and targets the referenced message's author. Preserve the root `exchange_id`, set `in_reply_to` to the immediate preceding message ID, and increment the recorded hop count. Never reconstruct return addresses from rendered message text.

A reply does not request another reply by default. `reply --request-reply` explicitly continues the exchange; its new message becomes the reference for the next response. An unsolicited response to a request-free reply is not generated automatically. Cap automatic hops at eight and pending requests per peer at 32. Transport ACKs and receipt updates never wake models. A deliberate semantic peer reply or group-completion message can start a turn through the verified native route and must be counted in the live-test budget.

Do not automatically forward every final response from an interactive session: only an explicit reply or a result tied to a NativePeers-owned task may be forwarded. This prevents private human turns from leaking to another session and prevents response loops.

---

## 8. Native peer transport adapters

### 8.1 Claude interactive receiver

Use the verified session's native inbox address, discovered through documented environment/hook data or current native discovery. The codec is version-gated because the full wire format is not a stable public API. [S01, S19]

The candidate minimal message shape can include `msgV`, `msg_id`, `type: user`, `session_id`, `priority`, and `message.role/content`, but **those are adapter-level hypotheses until validated against the installed version**. Store fixtures and the exact tested contract. Do not copy a third-party claim of stable compatibility into the acceptance report.

Mandatory invariants:

- Verify receiver UUID, OS user, endpoint type, parent-directory ownership, and current incarnation. Reject symlinked/unexpected endpoints.
- Do not read another session's auth-key file or reuse its exported messaging token.
- Do not claim to be the receiver's own child to bypass inbound policy.
- Do not forge `from-mode`, permission class, human consent, or a native-Claude identity for Codex.
- Preserve `crossSessionInbound` accept/hold/refuse behavior. If held/refused, report it or retain the uncertainty when no receipt is available; never turn it into a global accept setting.
- Keep the NativePeers reply route in the structured text envelope and use `npeer reply`; do not require Claude's native `SendMessage` to accept a synthetic Codex socket.
- Incoming slash commands are text, not instructions to execute a CLI control command automatically.

A receiving skill or one-line hook context tells Claude to treat `origin=peer-agent` as another agent's request and use the correlation-aware reply operation. It must not override the user's task scope or approve protected actions.

If the installed native socket contract cannot be validated, keep discovery/outbound working and mark the required inbound gate blocked. A custom channel may be an explicitly approved interactive fallback, but cannot silently change ordinary launch commands or be presented as a supported headless mechanism. [S10]

Run this as an M1 executable probe before building a full inbox implementation. Record both the transport behavior and the applicable native inbound policy. A test-only `accept` setting can characterize the wire, but a separate unchanged-policy observation is required to describe everyday unattended use honestly. Do not resolve a policy hold by borrowing a token or claiming to be the receiver's child.

### 8.2 Codex live receiver — native queue gate

M1 must inspect `rust-v0.163.0`-equivalent queue code and the actual managed executable, then run a finite test against **new sacrificial sessions**, not production conversations. Build only the smallest reusable probe needed to exercise the transport; it need not depend on the completed journal, installer or worker scheduler.

A supported native queue writer can be different from the process currently executing the conversation when the provider itself persists the submission and the loaded owner demonstrably consumes it. This is not permission to start a second conversation writer, resume that peer elsewhere, or write the native database directly. Establish the distinction with the actual runtime topology.

Test the following separately:

| Target | Sender route | What must be established |
|---|---|---|
| ordinary named CLI, current daemon configuration | ordinary native queue/API | correct original UUID ingests and processes; no unexpected shared-daemon startup |
| explicitly standalone `--no-daemon` target | ordinary queue/API without adding that flag to sender | do not confuse receiver topology with CLI option validation |
| loaded app-server thread | its owning reachable endpoint | normal queued delivery and turn attribution |
| saved but unloaded thread | native queue | distinguish retained queue from actual wake |
| Desktop-owned thread | exposed supported route, if any | no second writer; otherwise capability-limited |

Instrument processes/sockets before and after each test. A queue call may use an ephemeral provider process, but it must not secretly change ordinary Codex shared-daemon policy or load a second writer. Provider-owned queue persistence through its API is allowed; writing its database ourselves is not.

The **M1 route gate** passes when required ordinary/standalone topologies demonstrate exact-owner consumption, a minimal return to the original parent, and no unexpected shared-daemon or permission/config changes. Characterize busy-next-turn behavior early when possible; its full product-level busy/draft/correlation suite remains mandatory in M3 and LIVE-10. A route-probe pass is not full messaging acceptance.

Allow a measured queue latency budget of 30 seconds for ingestion in controlled conditions; distinguish accepted input, observed ingestion, and model runtime. This accommodates the source-observed ten-second watcher rather than inventing millisecond delivery. [S03] If the probe misses the budget, inspect endpoint selection, pauses and native policy before declaring the transport inadequate.

If the native route passes its required cases, **do not build the Rust listener**. If a required route reproducibly fails, record the selected fallback and implement only the narrow section-9 bridge needed to close that probe before proceeding with dependent work. A recorded fallback choice is not proof that it works: the fallback must pass the same minimal owner/return probe. This staged M1 fallback is permitted; it avoids a circular demand to prove a missing transport without implementing its already-authorized seam.

If a connection reaches the wrong runtime, fix routing first; a wrong endpoint is not evidence that the underlying native queue is impossible. A later reproducible M3 failure can reopen only this transport gate, invalidate affected evidence, and select the same authorized fallback without reopening the whole architecture.

### 8.3 Owned worker receiver

Messages to an owned worker go to its controller, which already owns the provider's input stream. Default delivery is queue-after-current-turn. The controller exposes `queued`, `working`, `blocked`, and task receipts without sampling a model for status.

Do not send both through a native inbox and its controller. That would create duplicate turns and ambiguous correlation. Controller-owned peers advertise one authoritative transport. Inherited hooks and direct-socket discovery must not override that route during initialization or between Claude turn processes; see section 6.6.

Treat a peer follow-up as a separately correlated queued input with its own native turn/task mapping. It cannot overwrite an earlier completed task result. Never hold an active worker tool call indefinitely while waiting for a message whose only native delivery point is that worker's next turn. Use bounded local waits only for journal events that can occur while the current turn remains active; otherwise return control and process the queued input in a later turn.

### 8.4 Reachable external app-server receiver

Allow only an endpoint explicitly discovered/configured and verified to own the target. Use native queue/turn methods according to its schema and permissions. Never start a second app-server and `thread/resume` an externally owned live thread.

A `thread/list` result of `notLoaded` in one server is not a global proof that no other runtime owns the conversation. Filesystem liveness and native ownership must be reconciled before any owned resume.

---

## 9. Conditional Codex native-inbox patch

### 9.1 Scope

Patch only when section 8.2 proves it necessary. Add a narrow local ingress integrated into the managed TUI's existing native input lifecycle. Do not create another global daemon, replace its renderer, or expose arbitrary RPC/control/approval execution.

Candidate source seams to locate in the exact managed source:

- TUI startup/current-thread selection and shutdown.
- Native input queue and paused-submission conditions.
- Existing queued-turn dispatch/`start_turn_if_idle` or equivalent.
- Session switch/fork/resume events.
- Current task/approval/composer state.

Paths from upstream are navigational hints, not guaranteed patch targets. Confirm symbol ownership before editing.

### 9.2 Activation and protocol

Opt in through a small project-owned activation descriptor read by the new patch, not by changing existing daemon or YOLO settings. This descriptor points to validated NativePeers runtime/state roots and policy version. The installer owns and can remove it. Existing Codex invocation syntax is unchanged.

Bind a private Unix socket inside the same process. Advertise native UUID, runtime generation, and supported protocol. Changing the current TUI conversation invalidates the old binding before exposing the new one. Bindings used by tool subprocesses must be tested independently of their short-lived shell PIDs; publish only the narrowly required native identity/lifecycle metadata.

Methods are deliberately small:

- `hello`: version, current native ID, incarnation, capabilities.
- `deliver`: bounded peer envelope, expected native ID/incarnation, stable idempotency ID.
- `receipt`: look up the patch's admission result by message ID.
- `subscribe`: optional bounded lifecycle notifications for already-authorized clients.

No shell execution, permission approval, model changes, transcript mutation, or general `eval` endpoint.

### 9.3 Event-loop behavior

The socket task validates and posts to an internal bounded channel. Only the existing TUI/runtime owner admits the input. It must atomically check current session identity and queue policy at admission time.

```text
validate transport + binding + expected identity
  -> reserve/deduplicate message ID in patch-owned admission record
  -> post PeerInput event into current owner event loop
  -> owner checks approval/modal/interruption/composer state
  -> native next-turn queue admission OR explicit held/rejected result
  -> record native submission/turn mapping when observable
  -> send receipt
```

Do not synthesize keypresses. Preserve a user's partially typed draft. Never auto-dismiss dialogs, press Enter on an approval, or restart an interrupted turn. If a human draft/modal prevents safe automatic submission, retain in the owned pending queue and expose `held_for_user`. Provide a release/cancel operation scoped to that exact held item and current native owner, with the receiving session's configured authorization; ordinary peer text cannot release an approval. Do not implement release by a guessed keystroke. This is controlled message admission, not a general approval RPC.

Busy delivery defaults to a new turn after the current one. Explicit steer support is optional and must use the exact active turn identity; unsupported steer returns `UNSUPPORTED_DELIVERY_MODE` rather than silently restarting.

Persist only patch-owned admission metadata under NativePeers state. Reuse native routines for conversation input persistence. Crash reconciliation distinguishes recorded-but-not-submitted, admitted, and uncertain. Uncertain records are never automatically replayed.

### 9.4 Integration with CodexMCP

Create a separate patch file, tests, compatibility manifest, and update verification in the existing manager's supported mechanism. Do not rewrite the naming/MCP patches or activate a manager rollback that removes completed work. Build a candidate package, run existing naming/manager tests and focused Rust tests, then activate atomically for **new sessions only**. Keep the previous known-good package for targeted rollback. Existing running processes continue with their old binary.

Unsupported future source revisions must leave the last-good package active and explain why patch application failed. Do not auto-drop the inbox patch while claiming messaging still works.

---
## 10. Worker spawning: ownership first

### 10.1 What a worker is

A NativePeers worker is a **new independent native conversation with an application-level parent link**. Its provider supplies inference, tools, session persistence, and resume semantics. NativePeers supplies launch intent, ownership, limits, correlation, and result delivery. The worker's transcript remains in the normal provider home.

Do not create an application-owned child by adopting an arbitrary live conversation. `spawn` creates a new conversation; `resume-worker` operates only on a worker already owned by this installation, after confirming the previous owner released it. `send` never implies adoption.

Same-provider delegation uses this same worker abstraction for consistent behavior. Native Claude/Codex subagent tools remain a separate optional provider feature; do not replace or override them. [S12, S13]

### 10.2 Spawn specification

```json
{
  "schema_version": 1,
  "client_request_id": "018f7a21-0000-4000-8000-000000000050",
  "completion_mode": "push",
  "failure_policy": "collect-all",
  "workers": [
    {
      "provider": "codex",
      "title": "LocalFlow Pipeline Audit",
      "model": "gpt-6.1-sol",
      "reasoning_effort": "xhigh",
      "task": "Audit the supplied transcription fixture. Do not edit files.",
      "workspace": {"mode": "inherit-read-only"},
      "permission_profile": "analysis",
      "context_refs": [],
      "result_format": "nativepeers.result.v1"
    },
    {
      "provider": "codex",
      "title": "LocalFlow Architecture Review",
      "model": "gpt-6.1-sol",
      "reasoning_effort": "xhigh",
      "task": "Independently evaluate the supplied architecture. Do not edit files.",
      "workspace": {"mode": "inherit-read-only"},
      "permission_profile": "analysis",
      "context_refs": [],
      "result_format": "nativepeers.result.v1"
    }
  ],
  "limits": {
    "max_concurrent_workers": 2,
    "max_depth": 1,
    "wall_time_seconds": 1800,
    "max_task_turns_per_worker": 8
  },
  "allow_sibling_messages": false
}
```

The service derives parent identity and maximum authority from the bound caller. The request may narrow limits, not raise the installation's trusted ceiling. Model parameters cannot declare the caller to be the human operator.

A task's `cwd` is inherited from the verified parent unless an explicit approved workspace is supplied. Human recipient addressing never requires cwd; worker execution legitimately has a working location.

### 10.3 Atomic launch intent, concurrent execution

1. Validate every worker specification, advertised model/effort capabilities, worktree baseline, resource ceiling and parent completion route before launching any model turn. Account entitlement can remain runtime-unknown until inference; record that uncertainty rather than claiming catalog preflight proves entitlement.
2. In one short journal transaction reserve the group, worker IDs, native-session intentions, and `(caller, client_request_id)`.
3. Start one finite supervisor for the group, with an exclusive group lease.
4. Create both worker controllers concurrently. Each verifies its executable, launch context and binding bootstrap. Establish native IDs as soon as the provider can emit them; for Claude's turn-at-a-time backend that may be after first input, as section 10.5 specifies.
5. Wait for a **process/protocol readiness barrier**, not for worker A to finish before starting B.
6. Submit the independent tasks concurrently and record native turn/request identities.
7. Translate provider events into status and final result records.
8. Assemble an aggregate when all requested workers reach a terminal task outcome, retaining successful results even if another worker fails.
9. Deliver one group completion notification or satisfy one registered waiter. Retain results when delivery is held, unknown, or the parent is offline.

`spawn_many` is the internal batch operation behind `npeer spawn` and the public `worker_spawn` tool. It is idempotent by caller request ID and specification digest. Reusing an ID with different content returns `IDEMPOTENCY_CONFLICT`; it must not create another group. Check that record before resolving new mutable defaults or creating additional worktrees.

Model selection preflight is not an all-or-nothing inference transaction. If one worker's first real request fails after its sibling has started, apply `collect-all`: retain the sibling result and report the precise partial failure without relaunching the group or substituting a model.

### 10.4 Supervisor lifetime

The supervisor is **not** a machine-wide daemon or an AI supervisor. It exists only while a group has active tasks, explicit subscriptions, or a bounded follow-up grace period. No LaunchAgent/service is installed for it.

Default lifecycle policy:

- Work is allowed to continue while the parent model is idle; idle is not process death.
- If an interactive parent's verified native owner exits, cancel owned active work unless `survive_parent=true` was explicitly granted for the group. A model turn ending, a shell tool returning, or a TUI client detaching from a still-live owning backend is not parent death.
- If an owned worker is authorized to delegate, its logical parent for child-lifetime purposes is its verified owning controller/lease, not the temporary Claude `-p` process for one turn. This prevents a normal turn boundary from canceling a legitimate nested group.
- A resume with the same PeerKey can reclaim pending results, but not silently broaden an old lease.
- After task completion, retain controller readiness for a configurable ten-minute follow-up grace period, then release provider ownership while keeping native history. For serialized Claude turns, controller readiness does not imply a live provider process. Idle grace is reclaimable under capacity pressure only after verifying there is no active task or accepted follow-up; it must not block useful new work for ten minutes.
- Explicitly detached groups still have wall-time and concurrency limits and terminate when finished; they are not permanent services.

A completed worker may be resumed later through `resume-worker`, preserving its exact native ID and permissions. Never resume using `--last` or the newest transcript.

### 10.5 Process bootstrap and control endpoint

The brief-lived `npeer spawn` command must not be the lifetime owner of long-running workers. After journaling intent, start the group supervisor with a fresh OS session/process group, `close_fds=True`, private log files, and no inherited terminal streams. Pass only the explicit bootstrap and readiness descriptors needed for startup. Use the verified native owner or owned-worker controller lease from the caller binding—not the short-lived shell-tool PID—as the parent-liveness reference. Record which kind of owner is authoritative and the evidence tying it to the parent native ID. A provider tool runner may clean up subprocesses on tool exit: verify the detached supervisor survives that cleanup in the controlled integration test. If the runtime forbids such a launch, report the capability failure rather than changing the user's global sandbox or daemon setting.

The supervisor acquires the exclusive group lock, binds a short private group-control socket, writes its PID/start/generation manifest atomically, and sends a bounded readiness response before `spawn` returns. If the readiness timeout expires, inspect the reserved group and manifest instead of launching a second supervisor. A failed caller connection must not create duplicate workers.

The group socket provides only `hello`, `status`, `enqueue`, `wait`, `cancel`, and `result` operations scoped to that group and the caller's grant. Use the same bounded JSON framing, exact identities and schema checks as the common core; it is not a general shell RPC endpoint. Waiters subscribe through this socket, so model turns are not burned polling. Read-only status can use the journal when the owner has exited. Runtime endpoints and process manifests are removed only after owner verification; durable task/result records remain.

Each worker controller is launched in its own owned process group with a supervisor lease pipe. The controller drains native output continuously and exits its provider cleanly if the lease closes. The parent session does not share that process group, so cancellation cannot accidentally terminate the original CLI. Verify every descriptor's inheritance explicitly; a sibling must not keep another worker's lease alive accidentally. Document the difference between a finite background task process and a persistent machine-wide daemon.

M1 must test supervisor survival from **both actual provider tool contexts** with a harmless non-model child before building the complete scheduler. A shell script run directly by the human is not evidence that a provider's tool runner will preserve the same child. Model-idle, tool-return, provider-exit and controller-exit are separate probe events.

For Claude's turn-at-a-time backend, the readiness barrier means controller/executable/bootstrap validation is complete, not that native identity binding has already completed. Native session initialization can arrive only after initial input; do not claim that creating a controller proves a zero-turn native conversation exists. Start both providers' first tasks concurrently after preflight, then bind/verify emitted IDs before accepting peer delivery to those workers. A pending MCP handshake or inherited hook must not wait for an event it prevents the provider from emitting.

### 10.6 Capacity and child-launch context

Reserve the group and its capacity atomically. A reservation is associated with an owner lease and remains distinct from a ready or running worker. Reclaim stale reservations only after owner reconciliation. When there is insufficient capacity, return `CAPACITY_UNAVAILABLE` promptly or leave an explicitly requested queued job; do not silently hold a parent model's tool call forever. Default v1 admission rejects rather than queueing indefinitely. Reclaim verified idle-grace workers before rejecting new useful work. If recursion is explicitly enabled, reject a child launch that would deadlock on slots held by its waiting ancestors.

Construct a minimal launch-local context instead of blindly cloning all transient parent environment variables. Preserve the intended provider home, authenticated account route, required PATH/project configuration and explicitly selected model. Do not leak the parent's messaging/binding token, tool-call identity, `CLAUDE_ENV_FILE` path or another worker's lease into a new worker. Inject the new worker's bootstrap separately. Detect documented provider nested-session guards in M1; use supported launch mechanisms rather than unsetting arbitrary safety flags until a launch succeeds. Record a nonsecret allow/drop policy; never dump the whole environment. Environment isolation does not authorize changing global auth, MCP registrations, YOLO or daemon policy.

---

## 11. Provider-specific worker adapters

### 11.1 Codex worker — private app-server

Launch the resolved, managed executable directly with its supported app-server stdio invocation. Use an immutable resolved package path for that worker's entire lifetime so an hourly manager update cannot switch half a conversation's executable. New workers can use a newer validated current package.

Do not launch the zsh function, inherit its YOLO flag by accident, enable the shared daemon, or use T3's provider process. An app-server child owned by a delegation is distinct from the ordinary CLI shared-daemon policy.

Protocol sequence, using the **installed generated schema**:

```text
spawn private stdio app-server
  -> initialize (client identity; experimental capabilities only if needed)
  -> initialized
  -> inspect auth status/model catalog/capabilities
  -> thread/start (non-ephemeral, explicit cwd/model/permissions)
  -> obtain native thread ID
  -> thread/name/set through the supported native API, if available
  -> activate the already bootstrapped, scoped NativePeers tool binding
  -> turn/start for task A
  -> consume notifications, approvals, errors and turn completion
  -> serialize subsequent follow-ups on the same thread
  -> unload/close ownership before terminal resume elsewhere
```

Exact JSON field names must come from the installed protocol schema. For example, CLI approval names and app-server enum spellings may differ; do not paste CLI strings into an RPC object without validation. [S02]

When worker-specific MCP configuration must be provided before `thread/start`, start its facade in `RESERVED_UNBOUND` mode. After the emitted thread ID is verified, activate it through the owning controller; do not require a second session or broad global MCP registration to close this bootstrap cycle.

A single reader owns stdout and dispatches responses by RPC ID and events by native thread/turn/item ID. Keep reading while approval callbacks or tool handling waits, so the process cannot deadlock on a full output pipe. Capture stderr separately and redact secrets.

Completion requires a matching terminal turn event and extracted final assistant result, not process exit alone or the first text delta. A worker can emit several assistant items and tool calls before its final outcome. Interrupted/failed/limited turns yield structured partial results, never false success.

Only this controller may `thread/resume` an owned worker after release. An external ownership conflict returns `OWNERSHIP_CONFLICT`; never remove writer locks.

### 11.2 Claude worker — native structured CLI turns

Default to the actual installed Claude executable and its existing authentication route. Do not use `--bare`, silently add an API key, export an OAuth token, select a bundled replacement CLI, or change subscription/API billing to make the adapter start. Official headless and CLI references provide the structured-output and exact-ID resume primitives. [S07, S08]

Reserve a native UUID when the installed CLI supports `--session-id`. Use native startup naming where supported. Pass the task through stdin, not through a shell-evaluated command string. Candidate argv shape, subject to installed-help validation:

```text
<installed-claude> -p
  --output-format stream-json --verbose
  --session-id <new-native-uuid>
  --name <worker-title>
  --model <resolved-model>
  <validated per-worker permission/settings/tool arguments>
```

For follow-ups, omit new-session creation arguments and use `--resume <exact-native-id>`. Only start the resumed process **after** the previous owned process has exited and released its writer. Validate the emitted `session_id` against the expected ID. A mismatch is a hard error, not a new worker accepted silently.

The supervisor remains alive between turns, so an idle worker is wakeable without a permanent provider process. It queues messages received while a task is running and begins the next native turn after the current process releases ownership. The default does not promise mid-generation steering.

Parse newline-delimited structured events incrementally with bounded buffers. A `result` with an error subtype is a failed/limited task even when a process exits successfully. A partial `assistant` event is not task completion. Drain final stdout/stderr before recording exit. The CLI may wait for background work; do not start another writer simply because a `result` was seen. [S07]

Graceful cancellation sends the documented/validated native process interrupt to the owned process group, waits for an interrupted result or exit, then escalates only within that owned group if necessary. Never run a global `pkill claude`.

If `--name` cannot name this headless mode, use a supported provider naming API for the **owned, released** session. The public SDK's session-renaming function is an acceptable optional helper after validating compatibility; manual JSONL edits are not. Record when the name became persistent. Zero-turn named-Claude discovery is not invented as a requirement that the provider does not support. [S09]

#### Persistent streaming optimization

The public SDK's stateful client is a viable optimization for lower startup overhead, but it is not required for v1. It has additional authentication, dependency and transport considerations. Do not switch the selected backend automatically or inherit undocumented SDK internals. A future or explicitly validated persistent backend must satisfy the same native-ID, single-reader, serialized-turn, permission, billing, result and cancellation tests. [S09, S21]

### 11.3 Worker tools and sibling messaging

Workers receive a small NativePeers tool surface bound to their own bootstrap lease. For an analysis-only worker this avoids granting an arbitrary shell just to send a reply.

The per-worker MCP server exposes only granted operations. Typical analysis worker tools are `peer_list`, `peer_send`, `peer_reply`, `task_status`, and `artifact_read`. Parent-controlled spawning is absent unless the worker's depth and authority lease explicitly grant it. No argument can request a different sender.

Sibling access is denied by default except replies to a received request. When the parent asks workers to compare findings, grant exactly the group siblings and record that permission. They exchange their findings as peers; the parent still receives separately attributed results.

### 11.4 Provider-native subagents

Research and report available native subagent APIs, but do not route the mandatory cross-provider features through a guessed `spawn_agent` model override. Claude's Agent definitions and Codex's native multi-agent mechanisms are provider-specific; current main also distinguishes messaging that wakes a turn from messaging that does not. [S12, S13]

The base release exposes `worker_spawn` (or `npeer spawn`), not an assertion that the new process is a vendor-native subagent. Natural-language instructions can use “agent” or “worker”; documentation must preserve the distinction. Optional observation of vendor-native children is not permission for NativePeers to take over their ownership.

---

## 12. Worker/task state machines and parent return path

### 12.1 Separate process, conversation, and task state

One field cannot represent all three.

```text
Worker process:
RESERVED -> STARTING -> READY -> EXITING -> EXITED
                          |        ^
                          v        |
                        FAILED ----+

Task:
QUEUED -> RUNNING -> WAITING/APPROVAL_BLOCKED -> RUNNING
              |                  |
              +-> COMPLETED      +-> TIMED_OUT/CANCELED/FAILED
              +-> INTERRUPTED/PARTIAL

Native conversation:
NEW -> PERSISTED -> LIVE -> RELEASED_RESUMABLE
                         -> OWNERSHIP_UNKNOWN
```

A successful task does not mean the provider process has exited. An exited worker does not mean its history disappeared. A completed process without a terminal task result is not a successful task.

Use generation numbers on task attempts and worker incarnations. Late events from an older process cannot complete a new task generation.

### 12.2 Parent notification without polling an LLM

Provider readers and local event subscriptions update the journal without generating model requests. Parent status queries read this state. Do not ask a model “are you done?” repeatedly.

Two modes remain:

- **Wait:** a bounded `wait(group_id, timeout_ms)` returns data through the parent's current tool call; it must not require the parent's next provider turn to unblock that same call.
- **Push:** after the parent returns to its work or idle, the leased controller submits one group completion through the verified native return route.

Keep **result availability**, **notification ownership**, and **observed provider delivery** separate. Key each automatic notification by `(group_id, completion_generation, parent_peer_key)`, with a stable delivery ID. A follow-up task gets a distinct completion generation/result record; it must not overwrite an earlier result or silently resend the old group's completion.

Use an atomic notification-claim record with a bounded waiter lease:

```text
UNCLAIMED -> WAIT_LEASED -> WAIT_RESULT_COMMITTED
     |           |
     |           +-- timeout/disconnect before result output -> UNCLAIMED
     v
PUSH_RESERVED -> PUSH_SUBMITTING -> PUSH_ACCEPTED / NOTIFICATION_UNKNOWN
```

A live waiter can claim an unsubmitted notification. On timeout or disconnect **before any result response could have been sent**, release that claim. Follow the group's chosen fallback: original `completion_mode=push` re-arms push; original `completion_mode=wait` retains the ready result without waking a model. `wait --on-timeout push|retain` can choose this explicitly within caller authority; return the selected disposition. A timed-out tool is not a permanent owner of future notification delivery.

If result output may have reached the caller but its receipt is unknown, retain `NOTIFICATION_UNKNOWN`; do not auto-push a second turn. Likewise, an accepted or uncertain push cannot be replaced with another push simply because no model ACK was observed. Later `wait`/`result` calls can read the same immutable results and current notification status without creating another model turn. This is idempotent result retrieval, not a duplicate delivery attempt.

Use supervisor-generation checks when updating claim records. A crashed supervisor or expired waiter cannot suppress result delivery forever, but lease expiry alone cannot prove an uncertain native send failed. Reconcile before retrying. Test timeout-before-ready, disconnect-after-output, supervisor restart and waiter-versus-push races.

This prevents a parent tool from waiting for its own next-turn reply, and prevents a timeout from stranding all subsequent notifications. It does not promise exactly-once model observation across a process crash.

If parent delivery is held/refused/offline, persist the exact pending/uncertain outcome and retain results. Native policies remain authoritative. Do not rerun workers, invent a native reply socket or create a T3 proxy thread to hide a missing return path. Controller-origin completions use the scoped identity in section 6.6; they do not impersonate a finished worker.

### 12.3 Completion summary

Send a compact summary containing group ID, completion generation, worker titles, actual native IDs as machine metadata, status, result-artifact handles and optional high-level findings. Do not dump entire worker transcripts. Deterministic aggregation packages results; the original parent model performs the requested intellectual comparison.

The initial batch aggregate covers its explicitly assigned task IDs. A later peer follow-up is separately correlated and cannot change a terminal initial result in place. Do not declare the original collaboration task successful while its task-required sibling exchange is still pending; record the task as waiting/partial or schedule a named follow-up phase. Test sibling collaboration as genuine message processing with separately attributable outcomes, not merely two independent reports.

Example result record:

```json
{
  "schema_version": 1,
  "group_id": "018f7a21-0000-4000-8000-000000000070",
  "task_id": "018f7a21-0000-4000-8000-000000000071",
  "worker": {"provider": "codex", "native_session_id": "018f7a21-0000-4000-8000-000000000072", "title": "Pipeline Audit"},
  "model_requested": "gpt-6.1-sol",
  "model_observed": null,
  "reasoning_effort_requested": "xhigh",
  "reasoning_effort_observed": null,
  "status": "completed",
  "summary": "Illustrative fixture result; no files changed.",
  "findings": [],
  "artifacts": [],
  "changes": {"worktree": null, "commit": null, "patch_artifact": null},
  "verification": {"tests_run": [], "limitations": ["Illustrative schema example; no provider was executed."]},
  "native_turn_ids": ["provider-turn-id"],
  "usage": {"input_tokens": null, "output_tokens": null, "cost_usd": null},
  "started_at": "2026-10-20T23:00:00-04:00",
  "completed_at": "2026-10-20T23:00:00-04:00"
}
```

The controller wraps and validates provider output. If a model fails to emit requested structured findings, preserve its complete final text as an artifact and mark structured extraction unavailable. Do not invent findings or spend an extra model turn solely to beautify the result.

---

## 13. Model selection, context, and usage budgets

### 13.1 Selection is explicit and auditable

For Codex, query the installed model catalog and supported reasoning efforts. For Claude, use supported installed CLI/provider configuration and any available non-inference metadata; do not assume the Python SDK's initialization info is a complete account entitlement catalog.

Resolve a user-selected default once at group creation, record the exact model choice, and keep it consistent through the group's turns unless the user explicitly changes it. When an exact requested model or effort is rejected, return `MODEL_UNAVAILABLE` or `EFFORT_UNSUPPORTED`. No silent downgrade, provider substitution, or switch from subscription to API billing.

For `gpt-6.1-sol`/`xhigh`, distinguish:

1. exact identifier requested by the user;
2. advertised in the local catalog;
3. request accepted;
4. real response reports the expected model and configuration.

If the provider does not echo a reasoning-effort field, report the validated submitted configuration plus `observed=unverified`, not a fabricated observed value.

### 13.2 Context isolation

Workers start with a focused task brief, approved artifact references, required repository instructions, and a short NativePeers usage contract. Do not clone all of the parent's messages, reasoning, MCP tool results, secrets, or prompt cache.

A follow-up to the same worker resumes its own native conversation. A new independent review gets a new UUID and fresh context. Code artifacts should reference an exact commit, patch digest, or baseline manifest when reproducibility matters.

The parent receives compact completion summaries and can read selected artifacts. The user may configure richer results; do not make a large prompt-injection payload the only way to retrieve full findings.

### 13.3 Default configurable limits — P, not vendor limits

| Setting | Default | Purpose |
|---|---:|---|
| concurrent workers per group | 4 | ample parallelism without accidental spawning explosion |
| concurrent owned workers per user | 8 | shared local cap enforced transactionally |
| delegation depth | 1 | workers do not recursively spawn by default |
| task wall time | 30 minutes | explicit per-task extension permitted within a user ceiling |
| group wall time | 60 minutes | bounds detached work |
| pending messages per peer | 32 | backpressure |
| peer automatic exchange hops | 8 | prevents reply loops |
| peer request expiry | 30 minutes | no indefinite abandoned requests |
| follow-up ready grace | 10 minutes | keeps a completed worker reusable, then releases runtime |
| local wait call | at most 30 seconds | fits tool deadlines; parent may elect push instead |
| graceful interrupt interval | 15 seconds | collect native terminal result |
| termination grace | 5 seconds | then terminate only verified owned processes |

Limits are editable local project policy, not enterprise governance. Raising them is allowed through the operator configuration, not through a worker's untrusted message.

Track provider-reported usage and costs when exposed. A pre-dispatch budget gate is enforceable; an exact hard dollar ceiling is not guaranteed when the provider reports usage only after a request. Mark unavailable subscription-dollar estimates as unavailable. Do not invent a conversion from tokens to subscription allowance.

### 13.4 Live verification allowance

The kickoff authorizes a bounded suite of harmless real model turns: at most **48 top-level submitted test turns**, **eight concurrent owned workers**, and **60 minutes of active live testing**. These are one durable allowance across M1 probes, later live checks, compactions and implementation restarts—not a new allowance per phase or process.

Count initial parent prompts, worker tasks, peer-triggered receiving turns, explicit replies that start a receiving turn, parent completion wakeups, resumes with new input and retries. Reserve before issuing a request; an accepted/uncertain request keeps its reservation. Do not count only the human's kickoff prompts while excluding the child/return turns they create. Provider-internal model/tool round trips are additional: record them and apply supported per-run controls, without representing 48 top-level turns as a precise inference-token or dollar cap.

Allocate the initial planning envelope as follows. These suballocations may be shifted in the durable plan without exceeding the fixed total; the first two-worker delegation pair must not be starved by lower-priority edge-case retries.

| Execution tranche | Planning turns | Priority |
|---|---:|---|
| M1 identity, native ingress, finite-supervisor survival and minimal parent-return probes | 10 | establish the integration seams |
| First real Claude→two Sol/xhigh and Codex→two Claude delegation/return tests | 16 | reserve before running optional live diagnostics |
| Remaining mandatory messaging, lifecycle, same-provider and regression assertions | 16 | combine compatible assertions using exact shared evidence |
| Diagnostic retry reserve | 6 | each retry needs a new concrete hypothesis |
| **Total ceiling** | **48** | no automatic increase |

This is a budget plan, not a claim all scenarios necessarily fit. Short fixtures can share a valid run across several assertions, but cannot replace required live semantics with mocks. If observed turn costs exceed the plan, finish non-model implementation/tests, report remaining acceptance precisely, and require an explicit allowance extension for further paid submissions.

Measure active-test time as the union of live-test intervals from first native submission until their admitted activity settles or is canceled. Concurrent workers do not multiply elapsed wall time. Compilation/offline unit tests are excluded; queue, approval and provider waiting during a live test are not artificially paused out. Persist reservations, observed turns, elapsed intervals and outstanding native requests in `evidence/live-budget.json`; do not reset them after context compaction or an agent restart.

These are verification ceilings, not normal product limits. Use short fixture tasks and one response per intended message. No real repositories need be edited for the live smoke suite. The requested Sol/xhigh test must use that configuration; a faster substitute cannot satisfy it. At the ceiling, stop new submissions, allow already admitted bounded work to settle or cancel only owned test work, and keep collecting evidence without launching extra model prompts. Reconcile any test input left in a native queue so it cannot be forgotten and unexpectedly execute on a later resume; cancellation must use a supported exact-item API, never direct database edits.

---

## 14. Workspace isolation and artifact handling

### 14.1 Read-only analysis

For audit/review tasks, inherit the parent's verified project path as a read target. Apply a provider-specific analysis profile that exposes only appropriate read tools plus the bounded NativePeers tools. `allowed_tools` is not by itself a restriction on all other Claude tools; use the provider's actual tool-set restriction and deny/sandbox mechanisms. [S09, S22]

Do not claim filesystem isolation merely because the task text says “do not edit.” Record the enforcement level: native sandbox, restricted tool set, or advisory. An unknown level cannot satisfy a hard read-only acceptance test.

Compile the worker profile against the **effective** provider tool inventory, including inherited MCP/plugin tools. An analysis profile that denies `Bash` but leaves a filesystem-writing MCP tool available is not read-only. Apply launch-local tool restrictions and, where necessary, a worker-local explicit MCP/tool selection without editing or disabling the parent's global registrations. Preserve required repository instructions and native authentication. If the installed provider cannot enforce the requested profile, reject that worker profile or mark the test blocked; do not claim a prompt instruction is equivalent to enforcement.

Headless startup can execute inherited project hooks before a model tool decision. Treat the caller's approved project and installed user integrations as trusted execution context, record that boundary, and inspect newly introduced worktree/project settings before first launch. Do not describe restricted model tools as isolation from malicious same-user startup scripts. This task does not authorize importing arbitrary untrusted project hooks or broadening native trust settings.

### 14.2 Parallel modifying workers

Use a separate Git worktree and branch per writing worker. Before creating it, capture repository root, HEAD, branch, staged/unstaged state, and untracked-file inventory. Never stash, reset or clean the user's working tree.

Default baseline is the committed HEAD. If the task concerns uncommitted changes, capture an immutable patch/baseline manifest and reproduce it in the isolated worktrees without modifying the original checkout. Include selected untracked files only through an explicit allowlist; do not copy `.env`, tokens, private keys, caches or ignored directories automatically.

If changes cannot be reproduced safely, return `WORKSPACE_SNAPSHOT_REQUIRED` with the precise conflict. Do not silently analyze a different code revision.

Workers return patches/commit references and test evidence. No automatic merge, force push or conflict resolution in the user's checkout. Worktree isolation prevents overlapping source edits, but shared Git metadata and filesystem permissions still require the native policy; a worktree is not a security sandbox.

For a non-Git directory, analysis can proceed read-only. Concurrent writing requires explicit isolated copies or user-approved disjoint roots; never assume disjoint file ownership from model text alone.

### 14.3 Artifacts

Use opaque artifact handles, hashes, bounded reads and per-task reader grants. The artifact service returns only the registered file, not arbitrary paths supplied by a peer. A message may mention a path as text; following it remains subject to the receiving provider's own permissions.

Keep NativePeers results separate from native transcripts. Do not ingest all histories into a new memory database. Retain worktrees/results until explicit cleanup; automatic cleanup may remove only clean, owned temporary test resources after evidence is recorded.

---

## 15. Permission and trust model

### 15.1 What is and is not protected

This is a single-user local development tool. Private directories/sockets, caller binding, native ownership and scoped tools protect against accidental misrouting, stale processes, command injection and unintended authority transfer. They do not isolate two fully privileged malicious processes already running as the same OS user.

Preserve the user's ordinary Codex YOLO mode. Do not represent a provenance label as an OS sandbox. A peer message can influence a powerful model; user-enabled peer communication is a capability, not proof that every action requested by a peer is authorized.

### 15.2 Delegated profiles

Define three operator-configurable profiles:

- **analysis:** read-only task tools and bounded messaging/results; no arbitrary shell or filesystem mutation by default.
- **workspace:** edits and test execution within the assigned isolated worktree under the provider's validated sandbox/tool policy; no unrelated path write or permission approval.
- **trusted:** inherits broader authority only from an explicit operator grant and a verified parent ceiling; never inferred from an inbound peer request.

The user's direct instruction to implement/test a task can select the configured workspace profile. Do not turn routine use into repeated institutional approval steps. But a model cannot widen a granted root, turn on trusted mode, or approve a pending permission prompt by sending a message.

When parent permission mapping is unknown, permit only the proven narrower analysis profile or return `PERMISSION_CAPABILITY_UNKNOWN`. Preserve unknown as unknown rather than inventing a total ordering of vendor modes.

### 15.3 Approvals and native policy

For owned workers, ungranted tool requests become `APPROVAL_BLOCKED` or a precise provider denial. The controller never auto-answers a request with broader permissions. A human operator can resolve an owned approval through a narrow local command or native interface when supported; peer tools do not expose that action.

Claude's allow/deny rules, sandbox, and inbound controls are independent. Do not globally set `crossSessionInbound=accept`, enable all Unix sockets, bypass managed policy, or read another session's messaging token. [S01, S22]

Test-only disposable Claude sessions may be launched with an explicitly documented inbound policy suitable for a harmless smoke test. Record that setting; never imply the test proves automatic acceptance under every production policy.

### 15.4 Process and transport controls

- Create private state directories with mode 0700 and files/sockets with 0600 where applicable.
- Reject symlink substitutions and wrong-owner endpoints; validate native UUID and runtime generation on each send.
- Use argv arrays and stdin/JSON files, never `shell=True` or interpolated peer messages.
- Bound inputs, connection time, queued bytes, and concurrent requests.
- Do not log tokens, environment dumps, account secrets, or entire unrelated transcripts.
- Bind MCP tools to a fixed caller; do not expose arbitrary `from`, `run_command`, or approval tools.
- Use separate operator and model-facing control surfaces. Same-UID bypass remains outside the threat model, explicitly.

---

## 16. Failure handling, cancellation, and recovery

### 16.1 Failure taxonomy

At minimum implement:

```text
CALLER_UNBOUND, AMBIGUOUS_TARGET, TARGET_OFFLINE, TARGET_REBOUND,
TARGET_GONE, UNSUPPORTED_HOST, UNSUPPORTED_TRANSPORT,
UNSUPPORTED_DELIVERY_MODE, CAPABILITY_MISMATCH, PROVIDER_POLICY_HELD,
PROVIDER_POLICY_REFUSED, DELIVERY_UNKNOWN, PAYLOAD_TOO_LARGE,
IDEMPOTENCY_CONFLICT, OWNERSHIP_CONFLICT, AUTH_REQUIRED,
MODEL_UNAVAILABLE, EFFORT_UNSUPPORTED, RATE_LIMITED, QUOTA_EXHAUSTED,
APPROVAL_BLOCKED, WORKSPACE_SNAPSHOT_REQUIRED, BUDGET_EXHAUSTED,
PROTOCOL_ERROR, WORKER_CRASHED, SUPERVISOR_LOST, USER_CANCELED,
CALLER_NOT_READY, PERMISSION_CAPABILITY_UNKNOWN, OWNERSHIP_UNKNOWN,
CAPACITY_UNAVAILABLE, REMOTE_HOST_UNSUPPORTED, WAIT_EXPIRED,
EXPIRED_BEFORE_SUBMISSION, CANCELED_BEFORE_SUBMISSION, NOTIFICATION_UNKNOWN
```

Errors include a safe explanation, whether a provider may have received input, and the allowed next action. Do not flatten `AUTH_REQUIRED`, stale endpoint and quota failure into a generic retry loop.

### 16.2 Cancellation

Only the owning parent, explicitly delegated controller, or operator can cancel owned work. A peer cannot cancel an arbitrary live conversation. Canceling a group stops queued tasks, requests native interruption of active tasks, records partial outputs, then cleans only owned processes.

Codex uses `turn/interrupt` with exact thread and turn IDs when available. Claude's default native CLI adapter uses a validated interrupt signal to its owned process group. After grace periods, terminate only a process whose PID/start identity/ownership manifest still matches. PID reuse must fail closed.

Cancellation is not a guarantee that a filesystem effect already performed is undone. Preserve changed worktrees and report them. Do not auto-revert source files or erase evidence.

### 16.3 Crash cleanup

Each group supervisor owns explicit process manifests and child groups. Keep a lease pipe between supervisor and per-worker owner; unexpected EOF causes the worker owner to interrupt its provider and exit. Close unrelated inherited pipe descriptors so another child cannot keep the lease falsely alive.

On macOS, process groups are useful cleanup boundaries but not an adversarial containment guarantee. Track and verify descendants created by owned wrappers; a process that deliberately detaches can require operator review. Never claim a cgroup-like guarantee that macOS does not provide.

`npeer doctor` detects stale descriptors and owned orphan candidates read-only by default. `npeer recover --group <id>` reconciles journal/native state; it must not blindly kill or restart anything. Restarting an owned worker requires proof of exclusive ownership release. If that proof is missing, mark `OWNERSHIP_UNKNOWN` and retain evidence.

### 16.4 Retry policy

- Retry read-only discovery/status with bounded backoff.
- Retry a transport before any bytes/request could have been admitted.
- Retry an idempotent native request only with the same validated idempotency key.
- Do not retry a task/model turn after an uncertain result by generating a new request ID.
- Never rotate accounts, models, permissions or provider homes to bypass a rate limit or auth failure.

A known rate limit can pause an owned task within its deadline and configured retry budget; an unknown submission stays unknown.

### 16.5 Offline and restart behavior

An ordinary send to an unavailable session returns `TARGET_OFFLINE`. An explicit deferred-send option may retain a request addressed to the exact PeerKey; it does not start the session. On a verified resume, the recipient can read its retained inbox or trigger an explicit safe drain. No background global poller is necessary.

Reply routes bind to the stable PeerKey, not the old call-sign/PID. A same-ID resume can receive retained results; a different session with the same title cannot. An operator may reclaim a finished owned worker for interactive use only after the supervisor releases it. `resume-command` prints the exact native resume command; it does not launch a second writer.

---

## 17. Native-feeling interfaces

### 17.1 Human/agent CLI

All commands support `--json`. JSON output has `schema_version`, `ok`, `data`, `error`, and a request ID. Diagnostics go to stderr. Exit codes: 0 successful operation, 2 invalid usage, 3 target/ambiguity, 4 permission/auth, 5 unsupported capability, 6 uncertain delivery, 7 provider/task failure, 8 deadline/budget.

```text
npeer whoami
npeer list [--provider claude|codex] [--include-offline] [--json]
npeer resolve --to "codex:Code Review" [--json]
npeer send --to "codex:Code Review" --message "..." [--request-reply] [--client-request-id UUID]
npeer send --to "claude:LocalFlow" --message-file /path/to/message.txt
npeer reply <message-id> --message-file /path/to/reply.txt [--request-reply] [--client-request-id UUID]
npeer inbox [--unread] [--limit N]
npeer ack <message-id>
npeer status <message-or-task-or-group-id>
npeer spawn --spec /path/to/spawn.json
npeer wait <task-or-group-id> --timeout-ms 30000 [--on-timeout push|retain]
npeer result <task-or-group-id> [--include-artifacts]
npeer cancel <task-or-group-id> --reason "..."
npeer resume-worker <worker-id> --task-file /path/to/followup.txt
npeer resume-command <worker-id>
npeer recover --group <group-id> [--dry-run]
npeer doctor [--json]
npeer doctor --live --budget-file /path/to/approved-test-budget.json
npeer install --dry-run
npeer install
npeer uninstall --dry-run
```

Shell examples are product interface contracts, not claims these commands are already installed. Names with spaces use normal shell quoting in `npeer`; the existing `codex -n` unquoted-name parsing stays untouched. Model-facing tool inputs avoid shell quoting entirely.

`send` default is a notice; `--request-reply` creates a correlated request. `ack` marks only the bound receiver's explicit processing report. `reply --request-reply` continues an existing exchange; without it, no further reply is requested. Reply-to-reply targets the immediate message author, not always the exchange's first sender.

When `--client-request-id` is omitted, durably allocate it before transport submission and include it in every final response/diagnostic. Do not claim a one-response JSON CLI can always return it to a caller killed before output; for crash-safe caller retry, supply the ID up front or reconcile through the journal. The local deduplication record does not by itself make an uncertain native submission safe to replay.

`spawn --spec` accepts one or many workers. Separate helper aliases such as `spawn-many` are unnecessary; the API supports a batch natively. `resume-worker` returns a conflict if that native conversation has been opened by an external process.

### 17.2 Minimal tool surface

Expose the following through a bound MCP facade when appropriate:

| Tool | Required arguments | Result |
|---|---|---|
| `peer_list` | optional provider/search | bounded peers and capabilities |
| `peer_send` | target, text/artifacts, expect_reply, client_request_id | message ID and evidence-backed receipt |
| `peer_reply` | message_id, text/artifacts, client_request_id, optional expect_reply | correlated reply receipt |
| `peer_inbox` | optional cursor/limit | pending messages/results |
| `worker_spawn` | validated spawn spec | group/worker handles and startup status |
| `task_status` | task/group handle | local state, no inference |
| `task_wait` | handle, timeout_ms ≤ 30000, optional on_timeout | terminal results or timeout with notification disposition |
| `task_result` | handle, optional artifact handles | structured results |
| `task_cancel` | owned handle, reason | cancellation progress |
| `artifact_read` | artifact handle, offset, limit | bounded authorized bytes/text |

A read-only peer profile can omit `worker_spawn` and `task_cancel`. A worker's facade gets a narrower tool set than an interactive parent by default. MCP connectivity alone does not establish delivery into a recipient model; it is an outbound interface to the same routing core.

### 17.3 Skills and normal language

The skill is short: what tools exist, identity and duplicate-name rules, queue versus wait, how to request/answer a reply, and when not to spawn. Keep detailed reference material in lazily loaded files rather than repeating a large protocol manual in every prompt.

Teach both providers:

- Discover/resolve only when needed; reuse an exact stable handle for the exchange.
- Prefer explicit `reply` for incoming requests.
- Return after dispatching asynchronous work; do not busy-loop for status.
- Use `wait` only when the next step depends on the result and the bounded tool wait is useful.
- Preserve user task scope and peer provenance.
- Ask for clarification only when a target is genuinely ambiguous or an authority/model requirement cannot be satisfied.

The original parent, not an extra coordinator model, synthesizes multiple worker results. The system must demonstrate this with an actual parent-model call to `worker_spawn` or `npeer spawn`, not only a human-written script launching child processes.

Do not promise that these workers appear in the built-in Claude `Agent` UI, native `SendMessage` recipient list, or Codex's internal child-agent navigation. The skill/tool integration is a separate namespaced surface backed by real native sessions. Its required user experience is natural-language delegation plus native history/resume, not an undocumented vendor-UI modification.

---

## 18. Compatibility matrix

This table defines the **target support contract**, not an assertion of tests already passed. M1 and final evidence replace each provisional status with locally verified results.

Legend: **R** required for v1; **C** conditional on an exposed native route/permissions; **O** owned workers only; **N** unsupported in v1.

| Host/session mode | Discovery | Outbound | Inbound / idle wake | Busy handling | Replies | Spawn | Resume/control |
|---|---|---|---|---|---|---|---|
| Claude interactive CLI, integrated | R | R | R through verified inbox; native policy can hold/refuse | native boundary or queued | R | R | native user resume; never seize ownership |
| Claude owned headless worker | R | R | R through controller and native exact-ID turn | next-turn queue | R | C by depth grant | O |
| Managed Codex standalone TUI | R | R | R via passing native queue or conditional patch | native next-turn queue | R | R | native user resume |
| Codex owned app-server worker | R | R | R via owning stdio controller | queue; exact steer only if supported | R | C by depth grant | O |
| External reachable Codex app-server | C | C | C through verified owning endpoint | native route | C | C with caller binding | no ownership takeover |
| Codex Desktop private backend | metadata C | C if binding exists | C only with a proven supported route, else N | C | C | C outbound only | no private stdio interception or second writer |
| T3-owned thread | metadata C | C if installed tools expose binding | C only with native owning route; T3 not required | C | C | C | no T3 DB editing/import |
| Herdr-hosted native CLI | R as its underlying CLI | R | same as underlying CLI | same | R | R | Herdr remains optional host |
| Different local directories | R | R | R | R | R | R; cwd needed only for new work | exact identity, not cwd matching |
| Different configured local homes | R | R | C per native endpoint/policy | C | C | C explicit home | home-qualified IDs |
| Another machine | N in base release | N | N | N | N | N | explicit future transport extension |

For externally hosted sessions, discovery may be read-only metadata while actual messaging is unsupported. Do not advertise a peer as receive-capable merely because it is visible in history.

### Cross-machine extension boundary

Keep `host_id` and a `TransportAdapter` interface so a later SSH-local execution adapter can run discovery/delivery on the remote owner machine. Do not forward a native Claude socket or replay its credentials across machines by assumption. The v1 CLI returns `REMOTE_HOST_UNSUPPORTED` and does not silently use T3 Connect, Remote Control, or a public relay.

---

## 19. Installation, updates, and rollback

### 19.1 Merge-only installation

Inspect current config with semantic parsers and preserve comments/format where practical. Record changed keys, owned blocks, file modes and hashes. For secrets, record only whether present and an integrity hash when needed; do not copy tokens into project evidence.

Install user-scope namespaced hooks and skills after verifying the actual discovery directories of the installed CLIs. Never infer Codex's skill path from an older version. Do not add duplicate hooks or overwrite another plugin's config.

Capture one targeted rollback bundle under the new project's private `backups/`, mode 0700. Modified copies use 0600. Backups should support a three-way/owned-block rollback, not whole-file restoration over later user edits.

The installer must be idempotent and provide a dry-run change manifest. A second install should change nothing except an explicitly newer owned payload.

### 19.2 Protected baseline

Before and after installation, record semantic state and relevant hashes for:

- `.zshrc` Codex function and launcher links;
- CodexMCP manager configuration, update job, patch list, naming helper and tests;
- native naming and interactive-MCP patches;
- active managed package/executable;
- Codex/Claude MCP entries, enabled/disabled states, hooks and plugins;
- authentication integrity without secret disclosure;
- `features.daemon_auto_start=false` and existing permission settings;
- Herdr/T3/Ghostty configuration relevant to coexistence.

An additional validated inbox patch is the only permitted native Codex change by this task. If not selected, NativePeers must not change the managed binary. If selected, compare preserved patches/config semantically and rerun the user's regressions against the new candidate package.

Baseline checks must distinguish this installer’s writes from legitimate concurrent provider activity. For example, a normal credential refresh is not proof that the installer changed the user's account. Preserve provider/account/home and auth mechanism; record unexpected differences without logging secrets or restoring old credentials. A concurrent manager update or user edit invalidates the affected compare-and-apply baseline: reread, reconcile and use the manager's supported lock before activation. Never restore an entire earlier config or roll back a refreshed credential just to make a hash match. Do not commit private backups, binding files, live transcripts or raw credential inventories into the project repository.

### 19.3 Update behavior

Record compatibility by native executable hash/version, source baseline, adapter codec revision and protocol schema digest. An unknown version triggers passive capability probes and a visible compatibility status; no unattended model test or automatic permission change.

Do not freeze all harmless provider updates indefinitely, but disable only the unvalidated operation rather than pretending it works. Existing workflows continue without NativePeers when its adapter is unavailable.

Never let a new dependency install an alternate Codex executable. Do not modify Herdr/T3's configured provider paths or run their global provider update buttons. Use the CodexMCP manager for any native patch activation, preserving its rollback/update mechanism.

### 19.4 Uninstall and recovery

`uninstall --dry-run` lists only owned hooks, skill paths, entry point, activation descriptor and facade registrations. Remove those owned blocks surgically. Keep messages/results by default and print an explicit, separately authorized purge option for NativePeers' own state only.

If a Codex inbox patch was installed, use the manager's supported patch-set rebuild/activation path to remove just that patch while retaining the naming/MCP patches. Do not invoke a historic blanket rollback that could undo intervening user work.

Never delete `.codex`, `.claude`, provider queues, native histories, T3 data, or unrelated worktrees. Do not use broad `rm -rf`, `git clean`, Homebrew `--zap`, or forceful home-directory cleanup as repair steps.

---
## 20. Test strategy and machine-verifiable acceptance

### 20.1 Evidence is part of the implementation

Use these stable requirement IDs in evidence so the implementation can aggregate a verdict without inventing a completion percentage:

| Requirement ID | Acceptance scope | Primary live checks |
|---|---|---|
| `ID-01` | exact native-title/UUID binding, home/provider qualification, deterministic ambiguity | LIVE-12, LIVE-13 |
| `MSG-01` | all four native peer-message directions, busy and idle processing | LIVE-01–04, LIVE-10–11 |
| `REPLY-01` | correct original sender, successive and out-of-order correlated replies | LIVE-05–06, LIVE-18 |
| `SPAWN-01` | Claude parent launches two actual Sol/xhigh workers and receives results | LIVE-07 |
| `SPAWN-02` | Codex parent launches two actual Claude workers and receives results | LIVE-08 |
| `SPAWN-03` | same-provider delegation and controlled sibling communication | LIVE-09, LIVE-15 |
| `LIFE-01` | owned cancellation, stale-runtime handling, safe native release/resume | LIVE-13–14 and failure-injection contracts |
| `INDEP-01` | no Herdr/T3/terminal-host runtime dependency | LIVE-16 |
| `PRESERVE-01` | existing naming, dynamic MCP, launch defaults and native histories intact | LIVE-17 and baseline/rollback checks |
| `SAFE-01` | permission ceilings, no competing writers, no uncertain duplicate replay | permission/ownership/property tests plus scoped live checks |

A requirement passes only when its applicable component assertions pass; `BLOCKED` is not averaged into a successful percentage. Each check writes one record to `evidence/acceptance.json`. M1 feasibility probes use separate `GATE-*` IDs and evidence in `evidence/feasibility.json`; they do not automatically satisfy a finished-feature LIVE check. Evidence may cover several explicit assertions when it genuinely exercised them under the same runtime/config/code fingerprints. After an affected implementation changes, invalidate or rerun that evidence rather than carrying forward a stale PASS.

Record at least:

```json
{
  "test_id": "LIVE-01",
  "requirement_ids": ["MSG-01", "REPLY-01"],
  "status": "PASS",
  "evidence_class": "live",
  "provider_versions": {"claude": "detected", "codex": "detected"},
  "native_session_ids": ["018f7a21-0000-4000-8000-000000000091", "018f7a21-0000-4000-8000-000000000092"],
  "native_turn_ids": ["turn-a", "turn-b"],
  "message_ids": ["request-uuid", "reply-uuid"],
  "assertions": ["original sender received exact correlated reply"],
  "artifacts": ["evidence/live/cross-provider-roundtrip.json"],
  "limitations": [],
  "timestamp": "2026-10-20T23:00:00-04:00"
}
```

Allowed statuses: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`. Evidence classes: `unit`, `mock-contract`, `installed-passive`, `live`. Do not label a mocked test as a live pass. Evidence copies must redact secrets while retaining IDs locally; publish only sanitized excerpts.

### 20.2 Unit and property tests

Implement deterministic tests for:

- Exact title resolution, Unicode normalization, duplicate names across providers/homes, and rename/fork behavior.
- Two sessions in one cwd and one session changing cwd; cwd must never select identity.
- PID reuse, stale incarnation, endpoint replacement, symlink rejection, home changes and missing permissions.
- All protocol validations, maximum sizes, unknown versions, malformed Unicode/JSON and control characters.
- Reply routing that ignores body-supplied sender/reply-to fields, checks caller-as-recorded-recipient, and supports reply-to-reply without redirecting every hop to the root sender.
- Retries after a title rename/reuse must return the original request/target record before attempting new resolution; same ID/different request must fail.
- Unbound model subprocesses never acquire human/operator authority; controller completions carry their own scoped actor rather than an invented native session.
- Message-ID and spawn-request idempotency, including same ID/different content rejection.
- Transaction interruption at every journal/send boundary; uncertain sends must not be blindly replayed.
- Concurrent requests returning in reverse order; correlation remains exact.
- Exchange hop limits, duplicate ACKs, notifications that must not wake models, and no automatic forwarding of human turns.
- Task generation guards; stale worker completion cannot complete a resumed attempt.
- RESERVED_UNBOUND MCP handshakes and inherited hooks cannot deadlock native initialization, steal a controller route, or unregister the logical worker at a Claude turn boundary.
- Message expiry before native admission versus wait expiry after admission; uncertain and held states remain reconcilable.
- Result pagination respects the distinct peer-frame and local control-response bounds.
- Global concurrency reservation under multiple simultaneous parents.
- Profile ceilings, sibling grants, worker depth limits and blocked approval behavior.
- Worktree baseline capture without stash/reset/clean; non-Git and dirty-tree failures.
- Artifact size/path/read-authorization checks and secrets redaction.
- Installer merge/uninstall ownership and preservation of unrelated edits after installation.

Use property tests for the message/worker state reducers and randomized arrival order. Assert invariants rather than only example snapshots.

### 20.3 Fake-provider contract tests

A fake Codex app-server and fake Claude stream process must cover:

- Out-of-order RPC responses and interleaved notifications.
- Partial lines, split UTF-8, large messages, stderr output and malformed JSON.
- Multiple assistant messages before the terminal result.
- Auth failure, invalid model, unsupported effort, quota/rate limit, permission denial and provider crash.
- A worker result followed by delayed process exit.
- Lost terminal event, disconnect after submission, cancellation during tool work and parent disappearance.
- A supervisor crash with open lease pipes; only its own workers are cleaned up.
- Two worker launch handshakes overlapping before either task completes.
- A blocked worker while its sibling completes normally.
- Parent wait versus push race, timeout-before-result, disconnected waiter, uncertain tool-result output and supervisor recovery; at most one automatic completion submission unless an earlier one is proven not admitted.
- A logical worker parent persists across successive native turn processes; normal turn exit must not look like cancellation of its authorized child group.
- Capacity reservations and idle-grace eviction; reject an ancestor/child slot deadlock instead of waiting indefinitely.

These tests establish our logic, not compatibility with a live vendor runtime.

### 20.4 Installed passive checks

Without model turns:

- Resolve native binaries and managed package hashes.
- Parse installed CLI help and generate the native app-server schema in a scratch directory.
- Probe supported auth-status/model-catalog surfaces without exposing secrets.
- Validate hook payload schemas from real observed native events where available.
- Verify entry points/skill registrations and config semantic preservation.
- Verify no shared-daemon policy change or unrelated service installation.
- Run existing naming/launcher/manager regressions that do not consume model usage.
- Test installer dry-run, second-install no-op and three-way uninstall against isolated config fixtures.

Do not run broad command names such as `doctor` merely assuming they are read-only; inspect their documented behavior first. Do not call third-party live self-tests on the production environment.

### 20.5 Required live scenarios

Use new clearly named disposable sessions and an isolated fixture repository. Never inject a smoke test into the user's real LocalFlow/Code Review sessions. The word “disposable” does not authorize deleting native histories afterward; retain them unless explicitly approved for archive/removal.

| ID | Scenario | Pass evidence |
|---|---|---|
| LIVE-01 | Claude → Codex → original Claude | receiving native transcript/event, exact correlated return at original UUID |
| LIVE-02 | Codex → Claude → original Codex | independently initiated reciprocal exchange |
| LIVE-03 | Codex → Codex round trip | two distinct native IDs, no cwd-based ambiguity |
| LIVE-04 | Claude → Claude round trip | native policy respected, exact return route |
| LIVE-05 | request plus three successive replies | explicit continuation requests; each hop targets its immediate author, retains the exchange and uses the correct original native conversation |
| LIVE-06 | two outstanding requests, reversed replies | each reply binds to the correct request and sender |
| LIVE-07 | Claude parent asks for two Sol/xhigh workers | actual parent tool call; two real managed Codex processes/threads; requested model; overlap of native working intervals; two attributed results back at same Claude UUID |
| LIVE-08 | Codex parent asks for two Claude workers | actual parent tool call; two native Claude session IDs; concurrent task intervals; two attributed results returned |
| LIVE-09 | same-provider spawning both ways | application-level worker creation and result return for Claude→Claude and Codex→Codex |
| LIVE-10 | busy receiver | message retained/queued, ongoing work not silently interrupted, later processed once |
| LIVE-11 | idle receiver | existing conversation wakes/consumes through its native path without human relay |
| LIVE-12 | duplicate names / stale recipient | deterministic refusal with candidates; no message reaches an arbitrary target |
| LIVE-13 | restart/resume | same native UUID, new incarnation, old endpoint refused, subsequent reply reaches resumed conversation |
| LIVE-14 | cancel owned worker | interrupted/partial result, owned processes end within configured grace; unrelated agents unaffected |
| LIVE-15 | sibling exchange | explicit sibling grant, genuine findings exchange, separate results remain attributable |
| LIVE-16 | T3/Herdr independence | no invocation, socket access or runtime dependency; run with both absent/stopped in the test environment |
| LIVE-17 | preserved CodexMCP features | naming/empty-session/resume regressions and a scoped live MCP toggle cycle where safely testable |
| LIVE-18 | parent return modes | push reaches idle parent; bounded wait returns through current tool; wait timeout re-arms the selected fallback; no duplicate completion submission |

For LIVE-16 do not stop an unrelated active Herdr server or T3 session. Prefer an isolated test environment in which neither is started or reachable. Record the exact isolation. If the literal host-stopped variant cannot be performed safely, mark that variant `BLOCKED`; do not relabel a source grep or a mock as its proof. The capability matrix must state the narrower evidence actually obtained.

For a small worker test, generate two random fixture nonces and distinct simple analysis tasks. Require each result to include its assigned nonce and finding. Capture two distinct native IDs, independent provider submissions/events and overlapping in-flight native task intervals, with both submitted before either terminal completion. Two supervisor task objects or artificially delayed cleanup are not evidence of parallel provider work. This proves concurrent client-side work, **not simultaneous GPU computation inside a vendor**; the implementation must not claim visibility into that scheduler. Parent prompts must instruct use of NativePeers; a parent answering both tasks itself does not pass.

Run the requested Sol/xhigh verification exactly. An unavailable entitlement is a real blocker for that test, not a reason to claim success using a different model. Other implementation and tests can finish; report the release's live acceptance as incomplete until the required provider test is possible.

### Live execution order and isolation

After M1’s smallest route probes and fast deterministic contracts, prioritize one thin end-to-end slice of LIVE-07 and LIVE-08 as soon as each provider's worker adapter and return path can execute safely. This is early vertical integration, not permission to skip ownership, permission or protocol tests. Then complete the remaining mandatory cases. Full M3 edge-case acceptance need not be finished before discovering that a required child/parent return path is broken.

Reserve the section-13.4 delegation tranche before spending on optional host investigation or repeated edge-case live retries. Reuse a valid fixture run for multiple compatible assertions, record the exact mappings, and never change requested models to make the allowance look sufficient. M1 prototype evidence must be rerun against the real integrated components when their behavior has changed.

A test harness may allocate a PTY to start and observe a sacrificial native TUI, or simulate a human typing a draft solely to test noninterference. That is a test fixture, not the product’s delivery path. Every tested peer request/return must still arrive by the chosen native adapter; synthetic keystrokes cannot be used to turn a failed native-delivery test green. Do not require Ghostty, Herdr or tmux to launch test fixtures. Record isolated native homes/config/policy and any differences from the ordinary environment; a clean isolated config alone is not proof that the unchanged production launch works.

### 20.6 Adversarial reliability checks

Use mocks for destructive failure injection and only harmless live equivalents:

- kill a fake or test-owned worker between provider acceptance and local commit;
- lose a reply after creation but before original-parent delivery;
- swap a test socket path or reuse a title with a different UUID;
- resume a test session while an old descriptor is cached;
- let an approval block one worker without stalling all others;
- alter a requested model name to an unavailable identifier;
- submit a duplicate spawn ID;
- send an envelope containing quotes/newlines/shell metacharacters;
- create a human draft in a test TUI while a peer message arrives;
- change the managed current symlink while an owned worker is active: that worker keeps its resolved executable.

No live test may validate robustness by modifying real secrets, stopping an unrelated daemon, or creating competing writers for an actual user conversation.

---

## 21. Implementation milestones, feasibility gates, and continuation

These are internal gates for one sustained implementation, not six user approval cycles. Implement small vertical slices early, retain the architecture, and avoid a circular prerequisite that requires the finished product before a transport can be probed.

```text
M1A passive baseline + minimal executable integration probes
  -> M1B narrow selected fallback only where a required probe fails
  -> M1 seams established: caller / owned process / original-parent return
       -> M2 identity + journal + reusable contracts
            -> M3 minimal native messaging and receipts
            -> M4 minimal safe worker adapters
                 -> M5 early LIVE-07 + LIVE-08 delegation/result slices
                      -> finish M3/M4/M5 reliability and required cases
                           -> M6 installation, regression, docs, acceptance
```

Independent pure-code/test work can proceed while a bounded route is blocked. Do not mark an unproven integration supported or build a large scheduler around a guessed return path. A later route regression reopens only its affected gate/evidence, not the whole project.

### M1 — Establish executable integration seams

**M1A: passive inventory and tiny probes.** Inspect installed binaries, CLI help, generated schemas, native hooks, relevant managed-source seams, scoped policy and running ownership. Record the protected state and external baseline. Build only reusable diagnostic probes under `tests/probes/`; temporary hook/skill scaffolding is allowed in isolated test sessions. No final global installation or full worker scheduler is necessary here.

| Gate | Required observation | Evidence and exit rule |
|---|---|---|
| `GATE-ID` | A tool call in each real Claude/Codex parent resolves its exact native ID, owner kind and live incarnation, independently of cwd and transient shell PID | same-cwd and child-shell checks; unbound/forged fields rejected; no human-identity fallback |
| `GATE-INGRESS` | Native Claude inbox and Codex native queue reach the intended loaded owner, with relevant policy and daemon effects recorded | exact IDs and admitted/processed distinction; test ordinary config separately from explicit standalone target |
| `GATE-SUPERVISOR` | A finite helper launched from each actual provider tool context survives tool return, retains no terminal FDs, and cleans a harmless owned child on lease loss | no worker model needed; verify native-parent liveness versus shell/turn completion |
| `GATE-RETURN` | A tagged completion from that scoped helper reaches the original Claude parent and the original Codex parent through native paths | exact original UUID plus matching event/response; an outbound send from a human shell alone is insufficient |
| `GATE-BOOTSTRAP` | Owned provider startup, required tool profile and inactive-to-bound MCP/identity sequence have a concrete executable contract | no circular wait for native ID; no duplicate inherited-hook registration; minimal provider startup only |

A prototype probe result establishes the seam, not the complete runtime feature. Keep artifacts redacted and count **all** M1 live turns in the single test allowance. Use the smallest turns necessary; pure helper survival/cancellation checks need no extra model calls after their initiating tool event.

**M1B: selected fallback.** If an existing Codex route reproducibly fails, implement the narrowly scoped section-9 inbox seam and its focused tests through the existing CodexMCP mechanism, then rerun the failed owner/return probe. A fallback selected on paper remains `BLOCKED` until executable. A Claude policy refusal or invalid unverified frame is not permission to impersonate a session, globally accept messages or patch an unrelated component.

**Deliver:** `evidence/baseline.json`, `protected-files.json`, `capabilities.json`, `feasibility.json`, schema/help fingerprints, `live-budget.json`, and a short native-queue decision record. Gate entries contain source/config/binary fingerprints, observations, capability state, selected route, and remaining limits.

**M1 exit:** caller attribution, finite-helper survival and original-parent return have demonstrated contracts for each required provider, either native or through the authorized tested fallback. Missing credentials/policy/necessary source remain explicit bounded blockers. Continue independent safe work; do not claim a required gate passed from documentation, a mock, or a matching file hash.

### M2 — Identity, discovery, schemas, journal

Implement stable PeerKeys/handles, incarnation and logical-owner distinction, caller/controller binding, exact resolver, local state models, SQLite migrations, artifacts and JSON CLI. Promote working probes into reusable contract tests rather than building a second throwaway implementation.

**Gate:** duplicate/stale/rename/home/process-identity tests pass; no cwd routing; no direct provider-state writes; example payloads validate; idempotency resolves an existing request before a mutable alias.

### M3 — Native messaging and replies

Implement the verified Claude/Codex routes, receipts, explicit reply chains, bounded inbox/status operations, interprocess admission locking and small skills. Honor held/uncertain outcomes and exact target revalidation. Complete the conditional patch only if selected by evidence.

**Early gate:** one native cross-provider request/return path per direction is integrated and usable by the worker slice. **Final gate:** all four directions, continuation replies, reversed replies, busy/draft/idle behavior, unknown outcomes and expiry rules pass. Do not delay the first real worker-return experiment until every unrelated edge case is polished.

### M4 — Owned worker controllers

Implement launch/bootstrap, model selection, exact emitted identities, scoped tools, serialized native resume, process leases, baseline-aware worktrees and cancellation. Default to one owned provider process per worker/turn as specified.

**Gate:** two native submissions can overlap; provider events identify distinct sessions; no second writer or unbounded orphan; invalid profiles/models fail honestly; inherited hooks never replace controller ownership. Passing preflight does not guarantee entitlement before the first real request.

### M5 — Delegation and original-parent results

Implement batch idempotency, parent/child authority, limits, structured results, sibling grants and the waiter-lease/push state machine. Exercise LIVE-07 and LIVE-08 as early integrated slices, then finish same-provider and lifecycle cases.

**Gate:** an actual Claude parent launches two requested Sol/xhigh workers and receives their attributed results; an actual Codex parent launches two Claude workers and receives theirs. Prove overlapping in-flight native task intervals, not vendor-side GPU scheduling. Waiting, timeouts, follow-ups and resumed logical worker ownership do not duplicate notifications or strand results.

### M6 — Harden, install, document and accept

Finish unit/property/mock-provider tests and all budget-permitted required live checks; prepare update handling, semantic preservation diff, idempotent installer/uninstaller and daily-use examples. Final user-scope installation follows demonstrated integration seams, not merely compilation.

**Gate:** the required feature matrix is satisfied by current evidence, rollback fixtures preserve later unrelated edits, and no required failure is hidden. A missing required live check remains `BLOCKED`/`NOT_RUN`; an optional private host mode is reported separately. Only actual complete core acceptance earns an overall `PASS`.

### 21.1 Durable implementation checkpoints

Create `PROGRESS.md` and `evidence/checkpoint.json` before the first mutation. Update atomically after each gate/milestone, before and after managed patch activation, after every live batch, and before a planned context handoff. Keep the Markdown short and point to evidence rather than pasting entire transcripts.

Checkpoint contents must include:

- specification/kickoff revision and SHA-256; project directory and current branch/commit;
- completed implementation, applicable gate states and exact evidence paths/fingerprints;
- uncommitted owned changes, touched configuration keys, targeted backup/rollback locations;
- original protected baseline and separately recorded authorized deltas—never replace the baseline with the desired new state;
- current native transport selection, tested binary/schema/config fingerprints and assumptions still unverified;
- active owned groups, native IDs, process-start identities, outstanding/uncertain submissions and safe cleanup/recovery state;
- consumed/reserved live turns, active-test time, remaining allowance and preserved delegation reserve;
- concrete blockers, the next exact executable step, and tests invalidated by a subsequent change.

Use small project-scoped commits where appropriate; stage only owned nonsecret source/docs/fixtures. Never use a blanket add/commit that includes credentials, private baselines, backups, raw native conversations or a worker's working tree.

### 21.2 Continuation procedure

After context compaction, tool interruption, agent restart or a usage boundary: read both canonical files, `PROGRESS.md`, checkpoint and current acceptance/budget ledgers; inspect the existing worktree before making edits; reconcile owned live processes and uncertain native submissions; revalidate only capabilities whose dependency fingerprints changed; then continue at the recorded next step.

A restart does not grant a new test allowance, consent to re-send unknown messages, permission to reset configs, or a reason to redesign completed milestones. Do not duplicate a group whose request ID already exists. If an update or concurrent user edit changed a protected dependency, pause only the affected operation and reconcile it. Honest continuation is part of the one-shot execution strategy, not a requirement for the agent to remain alive indefinitely.

## 22. Concrete module contracts and pseudocode

The following signatures are design contracts. Adapter method parameters are translated into installed native schemas rather than copied literally into vendor RPCs.

```python
class DiscoveryAdapter(Protocol):
    async def discover(self, context: DiscoveryContext) -> list[PeerDescriptor]: ...
    async def verify(self, peer: PeerDescriptor) -> VerifiedPeer: ...

class MessageAdapter(Protocol):
    async def submit(self, peer: VerifiedPeer, message: Message) -> SubmissionReceipt: ...
    async def reconcile(self, attempt: Attempt) -> list[Receipt]: ...

class WorkerAdapter(Protocol):
    async def prepare(self, spec: WorkerSpec, lease: WorkerLease) -> PreparedWorker: ...
    async def start_task(self, task: Task) -> NativeTaskRef: ...
    async def events(self) -> AsyncIterator[WorkerEvent]: ...
    async def interrupt(self, task: NativeTaskRef) -> CancellationReceipt: ...
    async def close(self, preserve_history: bool = True) -> CloseReceipt: ...

class NativePeersCore:
    async def list_peers(self, caller: Caller, query: PeerQuery) -> PeerPage: ...
    async def send(self, caller: Caller, request: SendRequest) -> SendReceipt: ...
    async def reply(self, caller: Caller, request: ReplyRequest) -> SendReceipt: ...
    async def spawn(self, caller: Caller, request: SpawnRequest) -> GroupReceipt: ...
    async def wait(self, caller: Caller, target: Handle, timeout_ms: int, *, on_timeout: str | None = None) -> WaitResult: ...
    async def cancel(self, caller: Caller, target: Handle, reason: str) -> CancelReceipt: ...
```

### 22.1 Send algorithm

```text
caller := verify_bound_caller_or_nonprivileged_external_origin()
request_key := canonical_original_request(caller, client_request_id)
if request_key exists:
    compare payload digest; return recorded pinned-target outcome or conflict
else:
    target := resolve_exact_selector(request.target)
    authorize caller -> target, operation, payload size and expiry
    transaction: reserve idempotency key, canonical envelope, pinned PeerKey, attempt
acquire bounded OS admission lock for pinned PeerKey
    reconcile existing attempt; never replay an uncertain/accepted request blindly
    verified_target := validate current owner/incarnation/endpoint/capabilities
    if identity changed: record re-resolution-required before native submission
    transaction: mark attempt SUBMITTING
    receipt := selected adapter.submit(verified_target, message)
    transaction: append exact evidence-backed receipt
release admission lock
return stable IDs, exact target and current receipt/uncertainty
```

Hold no SQLite transaction during native I/O. A local lock serializes only NativePeers admissions, not unrelated human/vendor inputs. An unexpected native response sets `PROTOCOL_ERROR` and records whether admission may have occurred. Generated IDs are persisted before dispatch but cannot be guaranteed visible to a crashed caller; callers requiring replay protection supply their ID initially.

### 22.2 Spawn algorithm

```text
caller := verify_bound_caller()
spec := validate_advertised_models_workspaces_and_authority(request)
if prior spawn id exists: return same group or payload conflict
transaction:
    obtain unique (caller, client_request_id)
    reserve global concurrency slots
    write group/worker intents and lease ceilings
supervisor := ensure_single_owned_supervisor(group_id)
await concurrently prepare worker controllers and inactive bindings
if preflight fails before any task: close owned startups, release reservations, report
else:
    await concurrently submit each validated task
    bind emitted native identities; enable scoped tools without a startup cycle
    stream provider events into task reducers
    persist each terminal result before making it visible
    collect-all aggregate with explicit partial failures
    deliver through leased completion consumer; release timed-out waiter safely
    release providers after grace and retain native histories/results
```

Model API calls must not occur inside SQLite transactions. Group cancellation is an explicit state transition observed by every task, not an exception that discards already completed results.

### 22.3 Recovery algorithm

```text
read owned manifests and unresolved attempts
verify pid + process-start + executable + native session identity
reconcile known native submission/turn IDs through supported read APIs
never infer "not sent" merely from a missing process
recover only owned released workers; never resume arbitrary peers
retain uncertain attempts/results; surface a precise recovery action
remove only stale NativePeers descriptors whose ownership is conclusively gone
```

### 22.4 Hook performance contract

Register/update/unregister hooks must not make model calls, install packages, scan all transcripts, or wait for another agent. Target steady-state completion under 100 ms on local test fixtures and enforce a small native-hook timeout. A failed informational registration hook should not break the provider's ordinary session, but must make NativePeers' registration unavailable rather than manufacture identity.

A hook that drains deferred input is separate from fast registration and only executes when explicitly enabled. No permanent poll loop is added to every model session merely to provide title lookup.

---

## 23. Risks, assumptions, and mitigations

| Risk | Evidence or uncertainty | Required mitigation |
|---|---|---|
| Claude wire changes | full external frame contract is version-sensitive | isolated codec, fixtures, installed-version probe, fail closed for that operation |
| Native Codex queue routing differs from assumptions | source supports external queue changes, local topology untested here | finite live gate; conditional native inbox, not a premature rewrite |
| A queued message never becomes a turn | accepted queue is not wake proof | preserve distinct states; exact ingestion/result evidence |
| Multiple native writers | externally owned session may be live elsewhere | never resume a peer for delivery; owned-worker-only resume |
| Parent is idle/unreachable/holds input | receiver policy/liveness varies | verified push or bounded wait; retain results, no fake success |
| Sender identity lost through a shell/MCP boundary | analogous metadata problem reported for Messenger | session binding plus native IDs; reject unbound sends rather than cwd guessing |
| Unrestricted same-UID agents can misuse authority | ordinary Codex is intentionally YOLO | truthful peer provenance, explicit delegated ceilings, no fake security guarantee |
| Native updates break a custom patch | managed Codex changes over time | separate patch, schema fingerprints, manager compatibility test, last-good package |
| Background supervisors leak children | async process/pipe ownership | lease pipes, exact process manifests, bounded cancellation and orphan reporting |
| SDK/CLI authentication changes billing | different launch paths can select different auth | native CLI default, record auth route without secrets, no silent API/subscription switch |
| Worktrees omit the actual dirty change | branch HEAD differs from parent's working tree | baseline manifest and explicit patch/untracked-file selection |
| A provider rejects requested model/effort | catalog is not entitlement proof | explicit model/effort error and bounded real verification |
| Result correlation relies on arbitrary final text | final message may be partial/multiple | native task/turn IDs plus owned result wrapper; explicit replies for free peers |
| Exactly-once claim hides crash windows | native store and our journal are independent | uncertainty state, stable IDs, no blind replay |
| “No daemon” is misread as “no process” | delegation needs live ownership while work runs | finite scoped supervisor, no permanent shared service |
| Full-screen/GUI backend lacks ingress | host control surface not always public | capability matrix; never claim universal host coverage |

An uncertainty is not a reason to omit an essential feature without investigation. It is a reason to select the documented compatibility gate and report the evidence correctly.

Additional review-driven risks addressed in revision 1.1: late feasibility surprises (M1 executable seams); mistaken logical-worker death on a turn boundary (controller ownership); MCP/native-ID startup cycles (two-phase binding); timed-out waiters permanently suppressing push (bounded leases); alias reuse on an idempotent retry (lookup before resolution); unsupported post-admission expiry guarantees (separate wait expiry); and lost progress/test allowance after compaction (durable checkpoints). These are design/contract fixes, not claims that the corresponding software has already passed tests.

---

## 24. Final definition of done

The implementation is complete when all of the following are true:

1. Ordinary eligible Claude and managed Codex CLI sessions discover/message each other by native title, with explicit ambiguity handling and no recipient cwd requirement. M1 caller/owner/return contracts are supported by executable evidence, not merely an intended fallback.
2. Independent initiations and multiple replies work in all four provider directions.
3. A Claude parent actually launches two requested GPT-6.1 Sol/xhigh workers concurrently and receives their separately attributable results in the original conversation.
4. A Codex parent actually launches two Claude workers and receives their results; same-provider delegation also works.
5. Workers can receive follow-ups, exchange sibling messages when granted, be canceled, release native ownership, and resume their own retained histories.
6. Busy/idle, duplicate-name, stale-instance, uncertain-delivery, crash and restart cases are tested without arbitrary retargeting or blind resends.
7. Core functionality does not depend on Herdr, T3, tmux, terminal keystrokes, a new cloud service, or a permanent global broker.
8. Existing managed naming, dynamic MCP, default launch semantics, authentication, native history and update mechanism remain intact.
9. Installation and uninstall are namespaced, reversible and documented; any conditional patch has focused regression/update tests. Implementation checkpoints and the single live-budget ledger support safe continuation without replaying completed or uncertain work.
10. The compatibility report states precisely which host modes were verified and which are unsupported/blocked; no mocked or source-only result is reported as live.

A blocked exact-model test or a missing required standalone return path prevents an unqualified `PASS`. Finish and document independent components, but do not silently redefine done as “message accepted” or “workers spawned.”

---

## 25. Deliverables the coding agent must leave behind

- A working local project and installed user-scope integrations.
- `README.md` with immediate native messaging/delegation examples.
- `docs/compatibility.md` and `evidence/capabilities.json` with local evidence.
- Protocol/CLI schemas and fixtures matching the implementation.
- Focused unit/property/contract/integration tests and bounded live receipts.
- `evidence/acceptance.json`, `evidence/feasibility.json`, protected-state semantic diff and test summary.
- `PROGRESS.md`, `evidence/checkpoint.json` and the persisted `evidence/live-budget.json` with continuation instructions and unsettled native attempts.
- Targeted installation manifest, uninstall dry run, rollback instructions.
- Separate conditional Codex patch and manager integration only if it was needed.
- A final report with supported commands, model/effort observations, remaining limitations, and honest overall verdict.

The implementation agent should not output another architecture proposal and stop. It should execute through these deliverables while respecting the limited stop conditions in the kickoff prompt.

---

## 26. Source register and research notes

The supplied revision-1.0 design contains this source register. Revision 1.1 preserves it and selectively rechecked [S03], [S05], [S07] and [S11] against their official pages/source; the remaining entries were not independently re-audited in this review. These sources are not proof that the target Mac exposes every documented capability. URLs are retained so the implementing agent can validate the installed version; current `main` can differ from the pinned baseline. The review made no local provider calls and no paid model calls.

New checkpoint, actor, bootstrap, receipt, waiter and test-ordering requirements are proposed design fixes grounded in the attached contracts and the preceding critique. They do not assert new native vendor capabilities.

**[S01] Anthropic — Cross-session messaging.** Native discovery, local socket exposure, incoming-message controls and recipient behavior. https://code.claude.com/docs/en/cross-session-messaging

**[S02] OpenAI — Codex app-server.** Native threads/turns, initialization, model catalog, permissions, stdio transport and events. Current documentation redirects to ChatGPT Learn. https://developers.openai.com/codex/app-server

**[S03] OpenAI — `rust-v0.163.0`, queue service source.** `watch_external_messages`, ten-second revision check, loaded-thread dispatch and queue lifecycle. https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/ext/queue/src/service.rs

**[S04] OpenAI — PR #39034, external queued-message dispatch.** Merged upstream implementation and test intent. https://github.com/openai/codex/pull/39034

**[S05] OpenAI — `rust-v0.163.0`, TUI queue command source.** Native queue request, name lookup, generated message ID and no-daemon/server checks. https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/tui/src/session_queue_commands.rs

**[S06] OpenAI — issue #44491, maintainer clarification.** Queueing an unloaded thread is intentionally not equivalent to resuming it. https://github.com/openai/codex/issues/44491

**[S07] Anthropic — Programmatic/headless Claude Code.** Structured result streams, native continuation/resume and noninteractive behavior. https://code.claude.com/docs/en/headless

**[S08] Anthropic — Claude CLI reference.** Installed commands and flags must be checked against this reference and local help. https://code.claude.com/docs/en/cli-reference

**[S09] Anthropic — Python Agent SDK reference.** Stateful clients, custom CLI paths, session metadata/naming, tool configuration and interrupts. https://code.claude.com/docs/en/agent-sdk/python

**[S10] Anthropic — Channels reference.** Preview channel contract, opt-in startup and development-channel restrictions. https://code.claude.com/docs/en/channels-reference

**[S11] Anthropic — Hooks reference.** Native lifecycle events, hook input and `CLAUDE_ENV_FILE`. https://code.claude.com/docs/en/hooks

**[S12] Anthropic — SDK subagents.** Provider-native Agent definitions and lifecycle, distinct from independent CLI workers. https://code.claude.com/docs/en/agent-sdk/subagents

**[S13] OpenAI — Native multi-agent tool specifications and local board.** Current-main references, not automatically the managed build's contract. https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/multi_agents_spec.rs ; https://github.com/openai/codex/blob/main/codex-rs/ext/agent-message-board/README.md

**[S14] cxpeer project README.** Third-party mechanism comparison; its own caveats cover undocumented registry/wire and tmux spawning. https://github.com/HivaMohammadzadeh1/cxpeer

**[S15] session-peer project README.** Third-party native-inbox/queue reference and explicit submission-versus-ACK semantics. https://github.com/abruption/session-peer

**[S16] inter-agent-bus project README.** Third-party broker/PTY architecture and documented external-module limitations. https://github.com/ppikkuaho/inter-agent-bus

**[S17] Agent Intercom Claude project README.** Third-party broker, native/MCP adapters and owned headless-resume pattern. https://github.com/dataforxyz/agent-intercom-claude

**[S18] Herdr Agent Messenger README/protocol.** Third-party pane-bound transport; comparison only, not a dependency. https://github.com/aashishd/herdr-agent-messenger ; https://github.com/aashishd/herdr-agent-messenger/blob/main/PROTOCOL.md

**[S19] Claude socket transport implementation.** Third-party frame/reference material; not an official stable protocol. Revalidate before using its shapes. https://github.com/PeterSR/claude-code-socket-transport

**[S20] OpenAI — App-server model catalog and entitlement distinction.** Account/provider-specific access must not be inferred solely from a catalog. https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server

**[S21] Anthropic — SDK streaming input and quickstart.** Persistent versus one-shot behavior and SDK authentication considerations. https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode ; https://code.claude.com/docs/en/agent-sdk/quickstart

**[S22] Anthropic — SDK permissions.** Tool allow/deny behavior and callback evaluation; an allowlist for auto-approval is not the entire tool restriction. https://code.claude.com/docs/en/agent-sdk/permissions

**[S23] Anthropic — Native session persistence.** Sessions preserve conversations, not filesystem snapshots. https://code.claude.com/docs/en/agent-sdk/sessions

**[U01] Supplied master task and conversation reports.** The attached `Pasted text(20261021-021653).txt`, native naming completion output and Herdr/T3 integration report are user-provided requirements/state descriptions. They were not independently re-executed here.

### Closing decision

Implement native-first, not host-first. Reuse the existing native queue where the installed runtime proves it works, add the smallest managed inbox patch only when required, and give cross-provider workers explicit ownership and a reliable path back to their original parent. Keep the sophisticated behavior in a compact, testable integration layer rather than another terminal or orchestration platform.
