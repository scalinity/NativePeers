# Provider errors and explicit fallback

A Claude worker can authorize an ordered list of exact Gemini alternatives using `fallbacks`. This permission applies only to an already-authorized delegated task. It does not authorize delegation, widen tools or permissions, change accounts, or make provider policy optional. A Codex-authored change can use Gemini as its alternative independent reviewer; another Codex worker is not a cross-vendor replacement.

```json
{
  "client_request_id": "review-with-gemini-fallback-1",
  "completion_mode": "wait",
  "workers": [
    {
      "provider": "claude",
      "model": "sonnet",
      "title": "Independent implementation review",
      "task": "Review the current implementation. Report concrete defects, file locations and relevant checks. Do not edit files or start other agents.",
      "permission_profile": "analysis",
      "workspace": {"mode": "inherit-read-only"},
      "fallbacks": [
        {"provider": "antigravity", "model": "gemini-4-argon-high"}
      ]
    }
  ],
  "limits": {
    "max_concurrent_workers": 1,
    "max_depth": 1,
    "wall_time_seconds": 300,
    "max_task_turns_per_worker": 2,
    "followup_grace_seconds": 0
  }
}
```

Save this as an absolute-path JSON file. From the verified parent, run `npeer whoami --json`, then `npeer spawn --check --spec /absolute/review.json --json`. The plan shows the selected primary, explicit alternatives, pinned native homes and deterministic readiness failures. Its native catalog helpers submit no model input. Then use `npeer spawn --spec /absolute/review.json --json` and a bounded `npeer wait GROUP_ID --timeout-ms 30000 --on-timeout retain --json`. The bound MCP `worker_spawn` tool accepts the identical `spec`. Existing MCP processes load their schema at startup; use the CLI immediately or refresh the facade through its normal native lifecycle.

`gemini-4-argon-high` was present in the signed-in native `agy models` catalog during implementation. This establishes catalog presence, not inference entitlement or future availability. A missing exact slug or unavailable catalog is reported on that alternative, never replaced silently. A working primary can still run with an unavailable alternative. Up to three distinct Antigravity selections may be listed in order for a Claude primary. Unavailable candidates are recorded and skipped. Once input to a candidate is submitted, an ambiguous or failed execution stops the chain; an unproved failure is never an invitation to replay it.

## When an automatic handoff is allowed

Only a confirmed `QUOTA_EXHAUSTED` or `AUTH_REQUIRED` synthetic native error can authorize a clean handoff. The current Claude stream must contain an explicit API-error diagnostic, a terminal result and zero reported input/output usage, with no real assistant output or tool activity. Synthetic `<synthetic>` error messages are diagnostics, not observed inference models. A genuine unexpected model still produces `MODEL_UNAVAILABLE` and is not retried through fallback.

The input may already have been submitted to the CLI: `may_have_received:true` accurately records that admission. Separately, `error.execution` records the terminal/no-execution observations. Missing terminal output, timeout, tool effects, partial output, positive/unknown usage or uncertain execution prevents automatic fallback. The original failure stays inspectable. No configured fallback preserves exact-model behavior and returns the actual failure.

Provider-supplied reset timestamps are converted to US Eastern with EDT/EST. If only a local reset hour and Eastern zone are supplied, the message says that no date was supplied rather than inventing a date or daylight-saving offset. No reset time is guessed. Bounded native diagnostics stay in the local result record.

For editing, use `permission_profile:"workspace"` and the normal `isolated-worktree` or explicit-file `isolated-copy` workspace. The initial failed attempt must have an unchanged materialized input and an empty worker delta. The bounded byte/mode check includes ignored files and refuses symlinks, special files and inputs above 10 MiB/10,000 files. An unavailable check blocks fallback and retains the workspace. The replacement receives its own owned directory and exactly the failed attempt's frozen input, even if the parent has continued editing. Changes or uncertainty never cause replay. Patches remain explicit integration work; no merge occurs automatically.

## Ownership, limits and results

The failed writers exit before a replacement reserves capacity. Each selected replacement has its own worker ID, binding, native conversation, provider/model attribution and retained history. The logical task ID, original phase, request ID and completion ownership remain the same. A pinned follow-up to the old peer is not redirected to the new provider. Queued old-peer follow-ups are settled without submission when that worker releases for replacement.

The original task deadline, group lifetime, depth and concurrency limits apply throughout. Each failed attempt consumes an original task-turn slot; fallback needs a remaining slot and does not reset the allowance. Native per-run model/tool-round caps also apply. Candidates are tried at most once per logical operation. Confirmed no-execution quota failures suppress future submissions to that provider and canonical home within this group only; already-submitted siblings remain independent. There is no global provider-health service or background polling.

`status`, `wait` and `result` expose `attempts`, each with provider, requested/observed model, native ID, workspace and immutable result artifact. A final fallback outcome also carries `fallback.primary_failure`, `reason`, `choices` and `selected`. While releasing the failed attempt, `FALLBACK_PENDING` means the logical task is still unfinished. Completed siblings are preserved. Exact spawn retries return that same group and task, including an uncertain existing operation, rather than submitting input again.

## Readable native chat input

Peer and completion input uses readable text instead of a JSON dump. A completion shows the worker title, outcome, requested and observed models, useful result or failure, fallback reason, native history and result artifact. Long summaries are explicitly shortened; full immutable results remain available through `npeer result GROUP_ID --json` and artifact pages. References include the exact message/exchange/reply IDs and the envelope digest. Authorized senders and recipients can read the full routing envelope with `npeer status MESSAGE_ID --json`. JSON CLI/MCP responses and journal envelopes retain their structured form.

Receipt reconciliation accepts the exact readable frame or the legacy structured envelope. An ID or body fragment alone is insufficient. Presentation does not turn queue admission into ingestion, change delivery policy, or add a second writer.

## Verification scope

The retained incident histories confirmed synthetic `authentication_failed` / native login errors that were incorrectly masked as model mismatch. They did not independently establish quota exhaustion. Fake streams separately cover quota, authentication, real mismatches, missing terminal output, partial execution and editing boundaries. Real supervisor/controller processes with fake providers exercise separate histories, native-shaped hooks, clean editing fallback, sibling preservation and wait-versus-push claims.

No new provider inference was submitted for this feature: the durable allowance is 148/148 inputs with no reservations or outstanding requests. The signed-in catalog query was metadata only. New Gemini fallback inference and the changed native message presentation remain **NOT_RUN** against native models because that allowance is exhausted. Historical native successes retain their own source/runtime scope. See `evidence/provider-fallback.json` and `docs/VERIFICATION.md` for current checks.

## Targeted rollback

Keep native histories, attempts, artifacts and the journal. Settle or cancel only owned unfinished groups before reverting the feature commit, because an older running controller cannot take over a pending handoff. The new tables are additive and can remain; do not delete them to force an old schema shape. The installed command links to this checkout, so a reviewed Git revert changes future invocations without replacing native provider binaries. Do not reset the checkout or any provider history.

Private recoverable copies of the three prior owned skills, owned Claude instruction block and integration manifests are under `backups/provider-fallback-20261022/`. Restore only a matching owned block/file after comparing its current value, preserving later edits and every outside Claude instruction byte. The ordinary owned installers handle full NativePeers install/uninstall; do not restore an entire global configuration or roll back CodexMCP to remove this feature. No native auth, model/account setting, shell function, daemon policy or managed package changed for this work.
