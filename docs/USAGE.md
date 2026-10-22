# Daily commands

Use these commands from a registered native conversation's shell tool. All providers share the same routing core and JSON envelope. `--json` may follow the subcommand. Success has `ok:true`; errors have stable `error.code` and `may_have_received`. Exit 6 means delivery uncertainty: keep the request ID and reconcile rather than sending a fresh copy.

A definite send failure returns `ok:false` and the documented nonzero exit code. Its `data` retains the message and client-request IDs for inspection. Held, queued and ingested receipts remain distinct; a held receipt does not establish model processing. An outbox record without confirmed admission also returns exit 6.

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

Waiting for one task in a multi-task batch leaves the batch notification unchanged. Waiting for already-terminal work reads its retained results even after the finite supervisor exits. Canceling a task affects that task; a canceled queued frame cannot start later, and canceling a terminal task leaves later follow-ups alone. Cancel the group handle to stop the whole owned group. A scoped running-turn cancellation closes its writer before any pending task continues in the same owned native history.

For writing tasks use `permission_profile:"workspace"` and `workspace:{"mode":"isolated-worktree"}`. Each worker gets a separate branch/worktree and an exact baseline. Dirty tracked changes are captured into that worktree, selected untracked files require `untracked_allowlist`, and secrets are excluded. NativePeers does not stash, reset, clean or automatically merge the source checkout. Results retain the owned worktree path for inspection.

The optional private state `policy.json` follows [the example](../examples/policy.json). It sets concurrency, depth, task/group duration, pending-message and hop limits and maps absolute native-home paths to short labels. A task request may narrow these limits. Changes are local operator configuration, not authority granted by an inbound message. Verification uses its own durable allowance: the original 48 turns plus an explicitly authorized 16-slot review retest, with eight concurrent workers and 60 active minutes unchanged. These verification limits do not restrict ordinary NativePeers use.

## Per-worker models and Gemini

Every `workers[]` entry accepts `model`, including Claude. Choose `"model":"opus"`, `"model":"sonnet"`, or an exact Claude model ID. Omit it to resolve the current configured Claude default once at spawn. Codex accepts an installed catalog ID and `reasoning_effort`; `gpt-6.1-sol` with `xhigh` remains the usual example, not a hard-coded restriction. Explicit blank models fail before provider input. Results retain `model_requested` and `model_observed`; aliases are resolved by the native provider, then the observed model is pinned for later turns and owned resume. Manual resume commands also include the model and effort.

For Gemini, use `"provider":"antigravity","model":"gemini-4-argon-high"`. This selects native Gemini 4 Argon (High), including its model tier. Do not add an inconsistent effort. `agy models` reads the signed-in native catalog without inference. The same spawn specification works from a registered Claude, Codex or Antigravity parent. Copy `examples/two-gemini-workers.json`, change its task/title/request ID, then use the normal `spawn`, bounded `wait`, `result`, `cancel` and owned resume commands. Concurrent Gemini High workers and original-Claude return passed the current native checks.

[Antigravity setup, shell shortcuts and native limitations](ANTIGRAVITY.md) cover the new provider. `npeer list --provider antigravity --json` and `npeer resolve --to "antigravity:Exact Native Title" --json` use native interactive titles. Owned Gemini workers retain their NativePeers role titles and separate native UUIDs; the role is not claimed as a native CLI history rename.


## Implementation workers

NativePeers supports editing workers as well as reviewers. Tailor `examples/implementation-workers.json` with focused tasks and a fresh request ID. It selects separate Claude, Codex and Gemini workers with `permission_profile:"workspace"` and `workspace.mode:"isolated-worktree"`.

```sh
npeer whoami --json
npeer spawn --spec /absolute/path/implementation-workers.json --json
npeer wait GROUP_ID --timeout-ms 30000 --on-timeout retain --json
npeer result GROUP_ID --json
```

Each worker edits its own `<source repository>/.worktrees/WORKER_ID` branch from an exact baseline. The local Git exclude owns only the `/.worktrees/` line; existing content is preserved. Dirty tracked changes are copied only after the baseline checks, and untracked source files require an explicit allowlist. New worker-created files are included in result patches; common secret paths and symlinks are excluded from automatic patch capture. Oversized diffs remain in the retained worktree with an explicit artifact-limit message. No source reset, stash, cleanup or automatic merge occurs.

Claude startup omits its permission mode. The namespaced Bash pre-tool hook refreshes the exact parent's capability before `npeer whoami` or `spawn`; it preserves the UUID and incarnation. Supported ordinary/editing modes expose the workspace profile, while plan mode remains analysis-only. If an older session has not loaded the new hook, use its next normal launch/resume rather than inventing a binding or changing global permissions. A missing grant in one conversation does not mean the entire installation only supports reviewers.

Inspect `changes.worktree`, `changes.patch_artifact`, changed/new file lists and each worker's actual checks before explicitly merging anything. Owned resume returns to the exact worker directory and preserves its observed model. NativePeers leaves vendor-native tools available; it can now perform scoped implementation in its independent native sessions.

## Refresh an uncertain message

Run `npeer status MESSAGE_ID --refresh --json` to observe the exact recipient history without resubmitting input. The bound sender or recipient may refresh; an unrelated peer cannot. The MCP equivalent is `task_status` with `{"handle":"MESSAGE_ID","refresh":true}`. A matching persisted native input advances ingestion evidence. Missing, unreadable or unmatched history remains unconfirmed; absence never proves non-delivery. Refresh creates no new message or submission attempt. Reuse the original request ID only for a definitely unsubmitted `ADMISSION_DEFERRED` retry; never replay an uncertain send with a fresh ID.

`npeer doctor --json` passively compares each installed provider executable with its recorded compatibility fingerprint. A mismatch includes expected/current hashes, affected operations and the verification scope to renew. Doctor never approves an update, changes a receipt or launches inference. A matching executable alone does not renew source-dependent live acceptance.

Worker leases pin the selected provider home at spawn. Claude uses `CLAUDE_CONFIG_DIR` or `~/.claude`; Codex uses `CODEX_HOME` or `~/.codex`. Defaults, plugin/MCP restrictions, native launch and identity all use that same canonical directory. Automated resume restores it explicitly, and `resume-command` returns `native_env` plus a shell command with the home assignment. Changing the parent's ambient environment does not retarget an existing worker. Legacy leases recover their home from their registered identity. Antigravity retains its supported native home; no alternate-home flag is invented. Alternate Claude/Codex home behavior is covered by offline fixtures; native entitlement and authentication still belong to the chosen home.
