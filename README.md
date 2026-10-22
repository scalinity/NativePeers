# NativePeers

NativePeers connects eligible native Claude Code CLI, managed Codex TUI and Antigravity CLI conversations on this Mac. It routes by exact native title or stable native identity, and launches separate native worker conversations of all three providers. Python performs routing and supervision; it does not add a reasoning model. Each delegation has one finite supervisor that exits after its work and follow-up grace.

The implementation lives at `/Users/danny/Documents/Tools/NativePeers`. The installed command is `/Users/danny/.local/bin/npeer`; private state defaults to `/Users/danny/.local/state/nativepeers`. New interactive native sessions register through the installed namespaced lifecycle hooks. Codex runs its startup hooks on the first native turn; a named empty TUI is preserved in native discovery and becomes a NativePeers peer when that hook runs. Existing sessions that started before installation need their next normal native launch/resume to register. No active production conversation is restarted or adopted.

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

For development, use `.venv/bin/python -m pytest -q`. Dependencies are pinned in `uv.lock`; no daemon, login service, Herdr, T3, terminal injection or GUI is required for product delivery. The managed Codex native queue passed the owner/return probes, so no Rust inbox patch or package activation was performed.

Each worker accepts a `model` field. The MCP spawn tool advertises that field directly; explicit models also survive follow-ups and owned resume. See [model selection and Gemini integration](docs/ANTIGRAVITY.md). From Claude Code or Codex, `npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/two-gemini-workers.json --json` requests two independent `gemini-4-argon-high` workers. Two High workers were live-tested concurrently from a native Claude parent. Antigravity idle wakeup, IDE ingress and automatic native startup naming are outside the verified CLI mode.

NativePeers also implements changes: tailor [the three-provider implementation example](examples/implementation-workers.json) using `permission_profile:"workspace"` and `workspace.mode:"isolated-worktree"`. Claude, Codex and Gemini have each created and tested code in concurrent native workers, with results returned to the original Claude parent. Worktrees remain inside the source project, and results include reviewable patches for new files. [Current checks and remaining boundaries](docs/VERIFICATION.md).

The [Astra review and fixes](docs/ASTRA_REVIEW.md) records the paired review, all 12 resolutions and current verification boundaries. The [installed Claude conventions](docs/CLAUDE_CONVENTIONS.md) explain when and how to delegate implementation, review and integration.
