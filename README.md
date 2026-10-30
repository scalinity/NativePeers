# NativePeers

NativePeers connects eligible native Claude Code CLI, managed Codex TUI, Antigravity CLI and Grok Build conversations on this Mac. It routes by exact native title or stable native identity, and launches separate native worker conversations of all four providers. Python performs routing and supervision; it does not add a reasoning model. Each delegation has one finite supervisor that exits after its work and follow-up grace.

The implementation lives at `/Users/danny/Documents/Tools/NativePeers`. The installed command is `/Users/danny/.local/bin/npeer`; private state defaults to `/Users/danny/.local/state/nativepeers`. New interactive native sessions register through the installed namespaced lifecycle hooks. Codex runs its startup hooks on the first native turn; a named empty TUI is preserved in native discovery and becomes a NativePeers peer when that hook runs. Existing sessions that started before installation need their next normal native launch/resume to register. No active production conversation is restarted or adopted.

The latest reliability corrections are described in [the ce4a4ac review resolutions](docs/REVIEW_CE4A4AC.md); [current verification](docs/VERIFICATION.md) separates offline checks from operation-specific native acceptance. Commands include `spawn --check`, `delta-preview`, and `cleanup --plan`; MCP supports `peer_ack` and `task_groups`, and exact message refresh supports older windows. Current native acceptance remains scoped to its recorded fingerprints.

For routine native patch updates, use the [bounded runtime-check workflow](docs/RUNTIME_UPDATES.md); worker/inbox and quota approval remain separate.

See [daily commands](docs/USAGE.md), [runtime and permissions](docs/RUNTIME.md), [installation and rollback](docs/INSTALLATION.md), and the durable [progress checkpoint](PROGRESS.md). The canonical [implementation contract](docs/IMPLEMENTATION.md) remains unchanged. [Acceptance results](evidence/acceptance.json) distinguish real native runs from unit, mock and passive checks. An unrun check is not a pass.

Launch conversations normally:

```sh
claude --name "Pipeline Review"
codex --name "LocalFlow Development"
# Existing interactive shell shortcuts remain available:
codex -n LocalFlow Development
codex -r LocalFlow Development
```

Inside a registered native conversation, ask it to use the `nativepeers` skill, or have its native shell tool call:

```sh
npeer whoami --json
npeer list --json
npeer resolve --to "codex:LocalFlow Development" --json
npeer send --to "codex:LocalFlow Development" --message "Review the fixture and reply with one finding." --request-reply --client-request-id review-1 --json
npeer reply MESSAGE_ID --message "The largest fixture value is gamma=13." --client-request-id review-reply-1 --json
```

An ordinary human terminal is an unbound local origin. Discovery and setup work there; mutating peer/delegation commands require a verified native caller. Missing binding never creates human authority. Duplicate titles return candidates; qualify by provider, configured home label or returned stable handle. No recipient directory, PID, socket or UUID is needed for ordinary title addressing.

A Claude parent can launch two exact Sol/xhigh workers:

```sh
npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/claude-to-two-codex.json --json
```

A Codex parent can launch two Claude workers with independently selected Opus and Sonnet models:

```sh
npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/codex-to-two-claude.json --json
```

Give each new batch a new `client_request_id`; reuse the exact same ID and content only to recover the same operation. Use `npeer wait GROUP_ID --timeout-ms 30000 --on-timeout push --json` for a bounded current-tool wait, or let a push completion reach the original parent. Full immutable results remain available through `npeer result GROUP_ID --json` and `npeer artifact-read ARTIFACT_ID --offset 0 --limit 262144 --json`. Workers are independently persisted native conversations with application parent links. These are the namespaced `worker_spawn`/`npeer spawn` operations, not the vendor's built-in Agent/subagent tool or private agent-navigation UI. Native tools remain intact.

For development, use `.venv/bin/python -m pytest -q`. Dependencies are pinned in `uv.lock`; no daemon, login service, Herdr, T3, terminal injection or GUI is required for product delivery. Managed Codex uses a queue-only guard to consume retained input before turn startup. The [notification record](docs/NOTIFICATIONS.md) separates exact installed-runtime and standalone-TUI qualification from source checks.

Each worker accepts a `model` field. The MCP spawn tool advertises that field directly; explicit models also survive follow-ups and owned resume. See [model selection and Gemini integration](docs/ANTIGRAVITY.md). From Claude Code or Codex, `npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/two-gemini-workers.json --json` requests two independent `gemini-4-argon-high` workers. Two High workers were live-tested concurrently from a native Claude parent. Antigravity idle wakeup, IDE ingress and automatic native startup naming are outside the verified CLI mode.

NativePeers also implements changes: tailor [the three-provider implementation example](examples/implementation-workers.json) using `permission_profile:"workspace"` and `workspace.mode:"isolated-worktree"`. Claude, Codex and Gemini have each created and tested code in concurrent native workers, with results returned to the original Claude parent. Worktrees remain inside the source project, and results include reviewable patches for new files. [Current checks and remaining boundaries](docs/VERIFICATION.md). For non-Git projects, [explicit isolated copies](docs/IMPLEMENTATION_WORKERS.md) accept only selected nonsecret files and return the same patch views; their provider acceptance is recorded separately. Retained groups (`groups --mine`), artifact export, paged unread inboxes and request-ID-based resume support handoff and recovery without new reasoning turns.

The [current review corrections and capabilities](docs/REVIEW_2551947.md) record every item from the independent follow-up review and the current verification boundaries. The [earlier Astra review](docs/ASTRA_REVIEW.md) retains its paired-review history and original scope. The [installed Claude conventions](docs/CLAUDE_CONVENTIONS.md) explain when and how to delegate implementation, review and integration.

A [copy-ready GPT-6 Pro review prompt](docs/GPT6_PRO_REVIEW_PROMPT.txt) pins the complete code/review snapshot for an independent capability and gap review.

Explicit Claude worker fallback to an exact Gemini selection is documented in [Provider errors and fallback](docs/PROVIDER_FALLBACK.md), with a ready-to-tailor [spawn example](examples/claude-gemini-fallback.json). Native chat notices show readable results and exact references; structured records remain available through the CLI/MCP. Current inference acceptance remains scoped to its recorded source/runtime; the cumulative test ceilings were removed and usage history is retained.

Native [usage intelligence](docs/USAGE_INTELLIGENCE.md) adds `npeer usage`, bound `usage_status`, multi-pool quota assessment, shared-pool planning, explicit model-preserving quota policy and separately attributed task estimates. Collectors never submit inference or change account/billing settings.

[Grok Build integration](docs/GROK.md) covers exact native identities, hook inboxes, fixed worker profiles and persisted task usage. Account quota stays unknown; verification is scoped to the recorded runtime and checks.
