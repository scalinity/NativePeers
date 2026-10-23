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
| Page older inbox entries | `npeer inbox --unread --page --limit 20 --json`; continue with `--cursor CURSOR` |
| Retained delegations for this parent | `npeer groups --mine --limit 20 --json`; continue with `--cursor CURSOR` |
| Export a granted artifact to a new file | `npeer artifact-export ARTIFACT_ID --output /absolute/new.patch --json` |
| Preview a worker-only delta | `npeer delta-preview WORKER_ID --task-id TASK_ID --json` |
| Plan retained workspace cleanup | `npeer cleanup --plan --limit 20 --json`; narrow with repeated `--worker-id WORKER_ID` |
| Explicit processing report | `npeer ack MESSAGE_ID --json` |
| Message/task/group status | `npeer status HANDLE --json` |
| Check a batch without reserving or launching workers | `npeer spawn --check --spec /absolute/path/batch.json --json` |
| Launch independent workers | `npeer spawn --spec /absolute/path/batch.json --json` |
| Current-tool wait, then push fallback | `npeer wait GROUP_ID --timeout-ms 30000 --on-timeout push --json` |
| Retain after wait timeout | `npeer wait TASK_ID --timeout-ms 1000 --on-timeout retain --json` |
| Immutable results without inference | `npeer result GROUP_ID --json` |
| Cancel owned active task/group | `npeer cancel HANDLE --reason "No longer needed" --json` |
| Resume an owned released worker | `npeer resume-worker WORKER_ID --task-file /absolute/path/followup.txt --completion-mode wait --client-request-id followup-1 --json` |
| Print native manual resume command | `npeer resume-command WORKER_ID --json` |
| Preview exact released native histories for archival | `npeer history-cleanup --plan --manifest /absolute/private700-directory/preview.json --json` |
| Archive only the rechecked preview selection | `npeer history-cleanup --apply --manifest /absolute/private700-directory/preview.json --json` |
| Reverse archival from that exact manifest | `npeer history-cleanup --unarchive --manifest /absolute/private700-directory/preview.json --json` |
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

The optional private state `policy.json` follows [the example](../examples/policy.json). It sets concurrency, depth, task/group duration, pending-message and hop limits and maps absolute native-home paths to short labels. A task request may narrow these limits. Changes are local operator configuration, not authority granted by an inbound message. Historical verification counts remain in `evidence/live-budget.json`; cumulative input and active-time ceilings are disabled. Each operation still has explicit deadlines and concurrency limits. Historical counts do not block new authorized tests.

## Per-worker models and Gemini

Every `workers[]` entry accepts `model`, including Claude. Choose `"model":"opus"`, `"model":"sonnet"`, or an exact Claude model ID. Omit it to resolve the current configured Claude default once at spawn. Codex accepts an installed catalog ID and `reasoning_effort`; `gpt-6.1-sol` with `xhigh` remains the usual example, not a hard-coded restriction. Explicit blank models fail before provider input. Results retain `model_requested` and `model_observed`; aliases are resolved by the native provider, then the observed underlying model is pinned for later turns and owned resume. Claude launch selections retain an explicitly requested `[1m]` context modifier; that modifier is not claimed as independently provider-observed. Manual resume commands also include the model and effort.

For Gemini, use `"provider":"antigravity","model":"gemini-4-argon-high"`. This selects native Gemini 4 Argon (High), including its model tier. Do not add an inconsistent effort. `agy models` reads the signed-in native catalog without inference. The same spawn specification works from a registered Claude, Codex or Antigravity parent. Copy `examples/two-gemini-workers.json`, change its task/title/request ID, then use the normal `spawn`, bounded `wait`, `result`, `cancel` and owned resume commands. Concurrent Gemini High workers and original-Claude return were observed in the historical model/Antigravity extension at its recorded source/runtime fingerprints (see the “Earlier model/Antigravity extension record” in [VERIFICATION.md](VERIFICATION.md)). The latest native batch at source map `a917299b…` used Sol/Flash; fresh Claude inference was blocked by the reported usage limit. Later source changes require affected native reacceptance.

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

Workers may append a fenced `nativepeers-result` JSON object with `findings` and `tests_run` during their existing task. Findings use `severity` (`critical`, `warning`, `info`) and `summary`, optionally `file` and `line`. Checks use `command`, `outcome` (`passed`, `failed`, `not_run`) and optional `details`. Valid entries appear in the result with `source:"worker-reported"` and `independently_verified:false`; malformed or missing structure leaves the complete provider final text available with a limitation. No formatting-only model turn is launched. Parent integration checks establish independent verification.

Editing results retain two patch views. `changes.patch_artifact` reconstructs the final worker tree from baseline HEAD, including inherited dirty input. `changes.worker_delta_artifact` contains only the worker's changes against its frozen materialized input; `worker_delta_empty:true` identifies a successful no-op. `worker_delta_base` and `baseline.input_tree` identify that input Git tree. Selected untracked inputs carry content hashes and modes in `baseline.untracked_inputs`. Both captures have byte/time limits and keep secret/symlink exclusions. Inspect the retained worktree when a capture is unavailable. The input tree is a local Git object; later Git garbage collection may make a new delta unavailable, while previously stored artifacts remain immutable. Neither view merges changes automatically.

## Import selected context

```sh
npeer artifact-add --file /absolute/path/design-brief.txt --max-bytes 1048576 --json
```

The bound CLI imports one regular file and returns its immutable `artifact_id`, digest and size. The default/hard ceiling is 10 MiB; `--max-bytes` narrows it. Add the returned handle to selected workers' `context_refs`; other workers do not gain access. The command rejects directories, symlinks, private provider state, known credential paths and native transcripts. Select an appropriate nonsecret brief deliberately: the importer does not infer secrets from prose. Workers can import only from their registered cwd. No recursive import or new MCP file-reading tool is exposed. Source changes after a successful import do not change the artifact.

A completion notice deferred before native admission retains the same delivery ID and retries within the supervisor's finite group lifetime. Short idle grace does not abandon that known unsubmitted notice. Definite refusals retain the result; accepted or uncertain submissions are not replayed. If the lifetime ends first, the notice is retained for result reads. Verification-budget retries reuse one reservation for that delivery.


## Retained work, inbox pages and local export

`npeer groups --mine --limit 20 --json` discovers this verified native parent's retained groups after context compaction or a restart. Each entry includes group state, worker IDs/titles, task/result counts and result availability. Use its `next_cursor` to continue. The stable page excludes groups added after its first page. Listing does not claim notifications or scan provider transcripts.

`npeer inbox --unread --page --limit 20 --json` returns `messages` and `next_cursor`; pass that cursor with the same unread selection. Plain `inbox` retains its list response. MCP `peer_inbox` accepts `unread`, `paged`, `cursor` and `limit` with the same behavior. Unread selection precedes limiting, equal timestamps have a stable message-ID tie-break, and pages exclude new inserts. Interactive message bodies still require native ingestion evidence.

`npeer artifact-export ARTIFACT_ID --output /absolute/new.patch --json` exports binary or text artifacts through the existing reader grant and bounded pages, verifies the complete digest, and publishes a complete file exclusively. An existing file or symlink is never overwritten. Interrupted exports remove their temporary output. The response includes `worker_delta_base` when recorded for a worker-only patch; legacy artifacts without that metadata still export, and their base remains in the retained result. Output parent directories must already exist; their canonical path is returned. Writing workers may export only within their owned cwd; analysis workers cannot use export to gain a write tool. No patch is applied automatically.

Give every intended `resume-worker` operation a fresh `--client-request-id`. Reuse that exact ID, worker, task text and completion choice if the response disappears. Exact retries return the durable original task and its current state, including uncertain or completed work. They never replay its model input. Changed content with an old ID fails with `IDEMPOTENCY_CONFLICT`. Omission generates a new operation ID and is unsuitable when a caller must survive losing the first response.

A group wait claims every still-unsubmitted completion phase included in its result. Phases added during a live wait are included. Initial task-specific waits preserve the separate batch notification; immutable `result` reads do not claim anything. Disconnect, cancellation and timeout before output release the current waiter's claims. After output may have occurred, ownership remains uncertain to suppress duplicate model wakeups. If an established wait connection disappears after terminal commit, the read-only operation returns the retained result; mutation calls are never retried by this fallback.

For a non-Git project, use `permission_profile:"workspace"` with `workspace:{"mode":"isolated-copy","file_allowlist":["main.py","tests/test_main.py"]}`. Select only the focused nonsecret regular files needed by the task. Inputs are bounded to 256 files and 10 MiB across the batch, frozen once with digests/modes, then copied under `<source>/.worktrees/WORKER_ID`. The source directory is never initialized as a Git repository or modified by the worker. Each owned copy has a local baseline for reconstruction and worker-only patch capture. This mode has offline/fake-provider coverage; see [implementation workers](IMPLEMENTATION_WORKERS.md) and the current native verification scope before claiming provider acceptance.

Doctor includes selected-home owned-integration checks as well as executable compatibility. `PRESENT` means a definition matches the owned installation; it does not establish entitlement or effective native precedence. Missing, modified and known-disabled definitions are reported. Antigravity startup refuses missing/restricted/conflicting NativePeers MCP definitions before model admission. A workspace conflict remains unresolved availability; a global `agy mcp list` is not proof that a native turn uses the same effective definition. Doctor performs no repair or inference. Install/uninstall pin Codex's helper to the same default home they edit, even when the shell selects an alternate `CODEX_HOME`.

Ambient worker binding belongs to the exact current generation's recorded native process. Launching another native CLI from a worker does not inherit that worker's identity or grants. Ordinary child shells and serialized turns remain supported. Use an explicit owned delegation for a separate NativePeers worker instead of copying bootstrap bindings.


## Batch checks and bounded artifact results

`spawn --check` shares spawn validation and model/default selection, returns selected provider homes, immutable executable paths, profiles, input manifests and capacity, and creates no group, task, reservation or workspace. It reports any non-inference catalog helpers separately: Codex `model/list` and Antigravity `models`. A plan does not prove entitlement or freeze capacity, runtime tools or files for a later launch. An explicit model/profile or unsafe selection failure remains a structured error; executable/readiness failures appear in the plan's `failures` with `ready:false`.

`result GROUP_ID --include-artifacts` and MCP `task_result` with `include_artifacts:true` include at most 16 authorized first pages, each at most 16 KiB (256 KiB total decoded bytes). Each page carries a digest and `next_offset` for `artifact-read`; additional handles remain in the result and `artifact_handles_omitted` reports the count beyond this batch. Ordinary result reads stay compact. These reads never claim a completion notification.

The bound MCP surface also exposes `peer_ack` with `message_id` and `task_groups` with `limit`/`cursor`. Acknowledgment is the recipient's model-reported processing claim, not native ingestion proof. Retained group discovery remains parent-scoped and never claims completion.

## Preview changes and retained workspace cleanup

`delta-preview WORKER_ID [--task-id TASK_ID]` reads that exact owned worker generation's immutable worker-only delta. It reports its frozen base, affected paths, current input comparison and a bounded `git apply --check` result without changing the source or index. The full reconstruction patch is never substituted. `applicable:true` requires both matching relevant inputs and a clean application check; `source_changed` can coexist with `check_applicable:true`. Neither proves semantic correctness. Inspect and apply changes explicitly.

`cleanup --plan` lists up to 20 released owned writing workspaces (maximum selection 100); repeated `--worker-id` selects exact workers. It inspects sizes, retained results, ownership and current content under a shared bounded deadline. It returns a normal Git worktree removal procedure or an exact-path selected-copy procedure only for unchanged, verified candidates. Active/resumed generations, unknown ownership, unique/unavailable changes and special or unaccounted content are refused. It deletes nothing and preserves native histories and artifacts. Rerun immediately before any separately authorized removal; never add `--force` or remove unrelated/tool-owned worktrees.

## Older delivery evidence

The default `status MESSAGE_ID --refresh` still scans the most recent 1 MiB. Its `reconciliation` reports half-open byte ranges and a `next_cursor`. To inspect an older window explicitly:

```sh
npeer status MESSAGE_ID --refresh --scan-cursor CURSOR --scan-max-bytes 1048576 --json
```

MCP `task_status` accepts `refresh:true`, `scan_cursor` and `scan_max_bytes`. Every continuation requires the same authorized sender/recipient and exact message. The total read cap, including identity guards, is 4 MiB per call. Cursors retain the original EOF and validate path/native/message identity plus file guards; appends are supported, detected replacement/truncation/edits invalidate the cursor. Bounded guards cannot certify all unsampled bytes during concurrent growth. Only complete exact native input envelopes advance receipts; missing history or a window without a match remains inconclusive. No new send or attempt is created.

## Explicit Claude-to-Gemini fallback

Set a Claude worker's `fallbacks` to `[{"provider":"antigravity","model":"gemini-4-argon-high"}]` when that exact alternative is authorized for the existing task. Tailor `examples/claude-gemini-fallback.json`; the normal `spawn --check`, CLI spawn and bound MCP `worker_spawn` share this schema. Only a confirmed clean quota/auth failure may advance. Original budgets, frozen inputs and one logical completion remain in force; partial or uncertain work is retained without replay. Read [Provider errors and explicit fallback](PROVIDER_FALLBACK.md) for a complete example, attempt attribution and the native verification boundary.

Native chat input now shows readable outcomes and references. Full structured envelopes stay in the journal and are available to authorized senders/recipients through `status MESSAGE_ID --json`; full results use `result GROUP_ID --json`. Queued/held/unknown receipts retain their existing meaning.


## Bound MCP checks, resume and phase progress

A bound parent with delegation authority can use `worker_spawn({"spec":BATCH,"check":true})`. The plan shares CLI validation, makes no reservation or workspace, and reports native metadata helpers. Git inspection leaves index metadata unchanged and never executes external filters. If a custom clean filter is needed to normalize the input, the check reports `WORKSPACE_SNAPSHOT_REQUIRED`; built-in line-ending conversion remains supported.

Continue an exact released owned worker with `worker_resume({"worker_id":"WORKER_ID","task":"FOCUSED FOLLOW-UP","completion_mode":"wait","client_request_id":"NEW_OPERATION_ID"})`. The request ID is required through MCP. Reuse identical arguments and the original ID after a lost response to retrieve that task, including a terminal failure; an unknown startup is never replayed. Wrong-parent and inactive calls are refused. New tool declarations appear when the MCP facade next starts normally.

`groups --mine`, MCP `task_groups`, and detailed status expose `progress.active_tasks`, `queued_tasks`, `initial_batch_complete`, `latest_completion_phase`, `deadline` and `last_native_activity`. Existing group-state semantics describe the initial batch; use the additive counts for follow-ups/resumes. Only the current worker generation contributes native activity, and activity is not evidence of success.

## Retained runtime approvals and never-launched cleanup

Normal worker readiness records its verified executable fingerprint in the owned lease. An explicit compatibility update retains the previous approval only for those already-verified exact leases. New work uses the current managed executable. Old packages must still exist with identical bytes/path; unrelated leases and unknown hashes do not gain approval. Legacy leases without a recorded fingerprint require explicit revalidation. Retained approval is scoped historical integration evidence, not a claim of indefinite native compatibility.

Revoke a retained approval explicitly with `npeer runtime-revoke --provider codex --sha256 EXACT_RETAINED_SHA256 --json`. This is a local maintenance command unavailable to owned workers; it does not change the current approval, activate a package or modify provider configuration. A no-op verification refresh preserves revocation.

`cleanup --plan` also considers a failed initial workspace with a durable exact-generation controller-never-created record and no native execution evidence. It checks frozen content and Git ownership again, retains histories/artifacts and deletes nothing. Missing manifests, unknown startup, resumed generations, unique changes and inherited dirty Git inputs remain refused. Inherited dirty inputs require reconciliation before ordinary worktree removal; no force-removal command is generated.

Post-admission controller handoff failures retain `DELIVERY_UNKNOWN`, original task/message IDs and `may_have_received:true`. Use exact retries and receipt observation, never a fresh copy. Codex teardown has bounded process and output-reader waits; `CLEANUP_UNCERTAIN` means a descendant may still hold output pipes. No dead leader’s process group is signaled.

Usage intelligence and its exact commands, native sources, installation, freshness, quota overrides, recommendation constraints and forecast limitations are documented in [USAGE_INTELLIGENCE.md](USAGE_INTELLIGENCE.md). `install --usage` composes only owned status-line capture; it preserves existing display commands and does not update worker/inbox runtime approval.

`doctor --json` includes `operation_readiness`, with selected home/configuration layout and separate quota, worker, inbox and MCP states. An approved usage collector does not approve inference or establish a live bound MCP client. Worker rows in status/groups include `logical_task_slots` (`limit`, `used`, `remaining`, `generation`), separate from native internal rounds. Isolated-copy preflight returns `GIT_IDENTITY_REQUIRED` before creating a copy when Git lacks author/committer identity. Integration updates preserve independently verified runtime receipts. See [review corrections](REVIEW_FF8C8C2.md).


## Explain selection and release

Use `npeer status GROUP_ID --explain-selection --json` (MCP `task_status` with `explain_selection:true`) to inspect canonical home/layout, requested and observed models, effort, current cached pool measurements, local demand and release closure. This inspection does not refresh usage or reserve quota. Use `npeer resume-command WORKER_ID --explain --json` for an ownership-only resume prerequisite report; live/unknown captured births and missing pipe proof retain ownership. A normal resume still verifies external native ownership and exact history.

Isolated input preparation is limited to 20 seconds across capture, revalidation and materialization. Git dirty/selected input bytes are bounded at 10 MiB and explicit Git selections at 1024 files; selected non-Git copies retain their 256-file/10 MiB bounds. Plans show selected bytes/files and dirty patch size. Oversized inputs must be narrowed before dispatch. Retained unique workspaces are never swept automatically.
