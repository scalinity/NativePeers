# Daily commands

Use these commands from a registered native conversation's shell tool. Both providers share the same routing core and JSON envelope. `--json` may follow the subcommand. Success has `ok:true`; errors have stable `error.code` and `may_have_received`. Exit 6 means delivery uncertainty: keep the request ID and reconcile rather than sending a fresh copy.

| Need | Command |
|---|---|
| Verify this native caller | `npeer whoami --json` |
| Discover live eligible peers | `npeer list --json` |
| Include retained offline registrations | `npeer list --include-offline --json` |
| Resolve exact title | `npeer resolve --to "claude:Pipeline Review" --json` |
| Send request | `npeer send --to "claude:Pipeline Review" --message "Review fixture.txt and reply." --request-reply --client-request-id fixture-review-1 --json` |
| Send notice | `npeer send --to "codex:LocalFlow Development" --message "The fixture is ready." --client-request-id fixture-ready-1 --json` |
| Reply to immediate author | `npeer reply MESSAGE_ID --message "gamma=13" --client-request-id fixture-reply-1 --json` |
| Ask for another reply | `npeer reply MESSAGE_ID --message "Please check the sum too." --request-reply --client-request-id fixture-followup-1 --json` |
| Bounded inbox | `npeer inbox --unread --limit 20 --json` |
| Explicit processing report | `npeer ack MESSAGE_ID --json` |
| Message/task/group status | `npeer status HANDLE --json` |
| Launch independent workers | `npeer spawn --spec /absolute/path/batch.json --json` |
| Current-tool wait, then push fallback | `npeer wait GROUP_ID --timeout-ms 30000 --on-timeout push --json` |
| Retain after wait timeout | `npeer wait TASK_ID --timeout-ms 1000 --on-timeout retain --json` |
| Immutable results without inference | `npeer result GROUP_ID --json` |
| Cancel owned active task/group | `npeer cancel HANDLE --reason "No longer needed" --json` |
| Resume an owned released worker | `npeer resume-worker WORKER_ID --task-file /absolute/path/followup.txt --completion-mode wait --json` |
| Print native manual resume command | `npeer resume-command WORKER_ID --json` |
| Reconcile exact native completion evidence | `npeer recover --group GROUP_ID --json` |
| Inspect recovery without reading native events | `npeer recover --group GROUP_ID --dry-run --json` |
| Passive health check | `npeer doctor --json` |

Titles are exact NFC aliases; native provider/home/UUID identity is stable. `codex@work:Review` uses a configured home label. The same title on two eligible sessions produces `AMBIGUOUS_TARGET` with candidates. A resumed UUID gets a new live incarnation; queued attempts remain tied to the original incarnation and are not silently retargeted. Retries consult the caller/request-ID journal before resolving a changed title.

The incoming native message contains `message_id`, `exchange_id`, `in_reply_to`, `from` and `expect_reply`. Reply with the immediate received message ID. Each continuation targets its immediate author and keeps its exchange. A reply with `expect_reply=false` ends that requested continuation. Controller completions carry their own group-scoped provenance and have no peer reply address.

`ACCEPTED_QUEUED` means native admission, not model processing. `SUBMITTED_UNCONFIRMED` and `DELIVERY_UNKNOWN` remain uncertain; a timeout does not prove expiry or nonexecution. `HELD` respects the receiver's native policy. Interactive inbox bodies are withheld until native ingestion/ack/reply evidence exists, so local inbox reads cannot bypass a held native message. `ack` is explicitly a model report. Exact native-history observations can advance ingestion without replaying a send. No exactly-once model-execution guarantee is made across external crash windows.

Peer frames are at most 64 KiB, inline text 12 KiB, local control responses 2 MiB, artifact pages 256 KiB and registered artifacts 10 MiB. Artifact pages carry a digest, byte count, base64 data and `next_offset`. Artifact handles grant access only to the registered immutable file; arbitrary file paths are not artifact handles. Spawn `context_refs` are explicit registered handles already readable by the parent, then granted to the new worker. Whole histories are not copied.

Use [the Claude-to-Codex example](../examples/claude-to-two-codex.json) or [the Codex-to-Claude example](../examples/codex-to-two-claude.json) as a batch template. The Sol example requests `gpt-6.1-sol` and `xhigh` without a fallback. Installed catalog checks precede dispatch; entitlement remains a real-inference question. Requested and provider-reported models are stored separately. Effort observation stays unverified when the backend does not independently echo it.

Set `allow_sibling_messages:true` only for tasks that request collaboration. Completed initial task records remain immutable; peer follow-ups get separate tasks and completion generations. Follow-up message tasks are retained for result reads instead of submitting another aggregate by default. Worker follow-ups serialize through their existing controller; a Claude headless writer exits before the exact owned history is resumed.

For writing tasks use `permission_profile:"workspace"` and `workspace:{"mode":"isolated-worktree"}`. Each worker gets a separate branch/worktree and an exact baseline. Dirty tracked changes are captured into that worktree, selected untracked files require `untracked_allowlist`, and secrets are excluded. NativePeers does not stash, reset, clean or automatically merge the source checkout. Results retain the owned worktree path for inspection.

The optional private state `policy.json` follows [the example](../examples/policy.json). It sets concurrency, depth, task/group duration, pending-message and hop limits and maps absolute native-home paths to short labels. A task request may narrow these limits. Changes are local operator configuration, not authority granted by an inbound message. During this implementation's verification, the separate durable 48-turn/8-worker/60-minute ceiling takes precedence over product policy.
