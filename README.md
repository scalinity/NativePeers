# NativePeers

NativePeers connects eligible native Claude Code CLI and managed Codex TUI conversations on this Mac. It routes by exact native title or stable native identity, and launches separate native worker conversations of either provider. Python performs routing and supervision; it does not add a reasoning model. Each delegation has one finite supervisor that exits after its work and follow-up grace.

The implementation lives at `/Users/danny/Documents/Tools/NativePeers`. The installed command is `/Users/danny/.local/bin/npeer`; private state defaults to `/Users/danny/.local/state/nativepeers`. New interactive native sessions register through the installed namespaced lifecycle hooks. Existing sessions that started before installation need their next normal native launch/resume to register. No active production conversation is restarted or adopted.

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

A Codex parent can launch two Claude workers using its existing configured model:

```sh
npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/codex-to-two-claude.json --json
```

Give each new batch a new `client_request_id`; reuse the exact same ID and content only to recover the same operation. Use `npeer wait GROUP_ID --timeout-ms 30000 --on-timeout push --json` for a bounded current-tool wait, or let a push completion reach the original parent. Full immutable results remain available through `npeer result GROUP_ID --json` and `npeer artifact-read ARTIFACT_ID --offset 0 --limit 262144 --json`. Workers are independently persisted native conversations with application parent links. These are the namespaced `worker_spawn`/`npeer spawn` operations, not the vendor's built-in Agent/subagent tool or private agent-navigation UI. Native tools remain intact.

For development, use `.venv/bin/python -m pytest -q`. Dependencies are pinned in `uv.lock`; no daemon, login service, Herdr, T3, terminal injection or GUI is required for product delivery. The managed Codex native queue passed the owner/return probes, so no Rust inbox patch or package activation was performed.
