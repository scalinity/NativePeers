---
name: nativepeers
description: Discover and message registered native coding-agent CLI sessions, reply to requests, or delegate bounded independent native work.
---

Use the native shell and `npeer`. Workers are real native conversations with their own tools, histories and identities; keep vendor-native tools available. Read [daily commands](/Users/danny/Documents/Tools/NativePeers/docs/USAGE.md) for less common operations and provider-specific details.

1. At a new human task, run `npeer whoami --json`, require its verified native UUID, owner, incarnation and grants, then `npeer notifications open --json`. `CALLER_UNBOUND` requires a normal native launch/resume, never borrowed identity. A worker reports its task normally and does not open/close interactive notifications.
2. Before quota-sensitive native work, run `npeer usage --refresh --json` and inspect operation readiness with `npeer doctor --json`. Worker/inbox, quota and bound MCP readiness are separate. Preserve unknowns and exact requested models; never change homes, permissions or approvals to force admission.
3. Follow the bounded review or peer coordination recipe below. Keep context selected and small; independent review uses a different vendor. Do the implementation in the selected authoring session unless delegation was authorized.
4. Collect one task with `npeer result TASK_ID --compact --json`. Use full `result`, `artifact-read --text` and their offsets only for a concrete gap. Findings/checks remain worker-reported until independently verified. A failed initial task stays failed even when a later phase completes.
5. When all required work/replies are handled, immediately before the useful final response run `npeer notifications close --json`, confirm `ok:true` and `data.state:"QUIET"`, then write that response last. Do not close while returning only to wait. Quiet late frames stay retained; read them deliberately with `npeer inbox --retained --json`, without an extra human-facing acknowledgment. Actual visible-tail qualification depends on the selected native runtime; configured hooks alone do not prove it.

## Bounded review

Declare the parent review/remediation loop before delegation. For an authorized implementation review, load `/address` after consolidation and account for every Critical, Warning and Suggestion, including duplicates and pending items, using the shared contract. Review-only ends with its report. Before delegating a review, read [the shared review contract](/Users/danny/Documents/Tools/NativePeers/docs/NATIVE_REVIEW.md). Include it in the packet with the changed paths and essential source:

```sh
npeer context-prepare --project /absolute/repo --base BASE_COMMIT --path docs/NATIVE_REVIEW.md --path src/file.py --path tests/test_file.py --json
```

Add `--untracked new_file.py --path new_file.py` for explicit new inputs. The immutable packet includes base/current selected source, real diff, dirty/staged identity and omissions. Large selections fail with their measured size: narrow the paths or explicitly choose `--max-bytes`. Binary content is tagged base64. No diff, textconv, pager or custom filter runs.

Tailor `/Users/danny/Documents/Tools/NativePeers/examples/bounded-review.json`: use a fresh request ID, the exact authorized provider/model and returned `context_refs`. Keep `analysis` / `inherit-read-only`; its Claude tools are Read/Glob/Grep and the scoped facade, without Bash. The packet is readable with `artifact_read`, `text:true` and `limit:16000`; follow `next_offset` to fit native tool-output limits.

```sh
npeer spawn --check --compact --spec /absolute/review.json --json
npeer spawn --spec /absolute/review.json --json
npeer wait TASK_ID --timeout-ms 30000 --on-timeout retain --json
npeer result TASK_ID --compact --json
```

Preflight shows native owner, selections/home, planned tools, exact context, limits/sources, blockers and the next action. It submits no model input or reservation; native helpers and effective runtime tools remain distinct. Recheck only after inputs change. Logical task inputs, native model/tool rounds, wall deadlines and the 1–30000 ms per-tool wait are separate. Never raise global ceilings for a review. Use `--on-timeout push` only with a ready parent route. Retain accepted/uncertain attempts; a timeout is not permission to replay.

## Peer coordination

Discover with `npeer list --json`; resolve exact titles with `npeer resolve --to "codex:TITLE" --json`, then reuse the stable handle. Duplicate titles require an explicit candidate, not cwd/newest/focus guessing.

For overlapping source/runtime work, prepare selected context with optional `--runtime codex`. Attach the returned artifact to an authorized message:

```sh
npeer send --to STABLE_HANDLE --message "Own these selected paths; compare the frozen baseline before integration." --artifact ARTIFACT_ID --request-reply --client-request-id NEW_ID --json
npeer context-check ARTIFACT_ID --project /absolute/recipient/repo --json
```

`context-check` reports changed selected paths, heads and expected/current runtime fingerprints without mutations. Frozen observations carry their original time. Queue `ACCEPTED_QUEUED` is admission; native ingestion, `ack` (model-reported processing), a reply, integration and installed qualification are separate evidence. Unknown delivery never triggers automatic replay. Reply to the immediate received message ID with `npeer reply MESSAGE_ID --message TEXT --client-request-id NEW_ID --json`; add `--request-reply` only when another answer is needed. Controller completions have no peer reply address.

## Other work and recovery

Use `permission_profile:"workspace"` with `isolated-worktree` for authorized Git edits; non-Git `isolated-copy` requires explicit files. Each worker owns distinct files/responsibilities. Inspect worker-only deltas with `npeer delta-preview WORKER_ID --json`, then explicitly integrate and check; no automatic merge occurs. Preserve unrelated dirty input and unique work.

Use `groups --mine` for owned retained group IDs, `resume-worker` for exact released histories and `resume-command` for its recorded native environment. Reuse the original request ID after a lost response. Do not invent ambient-home resumes or launch a second writer. `cleanup --plan` removes nothing; preserve changes before separately authorized retirement. Full commands and provider/model/profile/fallback constraints: `/Users/danny/Documents/Tools/NativePeers/docs/USAGE.md`, `docs/RUNTIME.md`, `docs/USAGE_INTELLIGENCE.md` and `docs/PROVIDER_FALLBACK.md`.
