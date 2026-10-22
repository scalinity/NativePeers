# Usage intelligence and quota-aware delegation

NativePeers reads supported native metadata and ordinary status-line telemetry. It never submits inference to collect quota, opens another writer for a conversation, redeems credits, changes billing or polls in the background.

```sh
npeer usage
npeer usage --json
npeer usage --provider codex --refresh
npeer usage --provider claude --model opus --details
npeer usage --provider antigravity --model gemini-3.8-flash-medium --json
npeer usage --provider codex --home-label WORK --json
```

The ordinary command reads the cache. `--refresh` performs supported metadata work and coalesces concurrent requests. Codex can fetch account quota independently. Claude and Antigravity retain status-line quota from normal native operation; refreshing does not automate their interactive `/usage` panels. Antigravity can independently refresh its signed-in model catalog, which is separate from quota freshness.

## Native sources and boundaries

| Provider | Native source | Reported fields and important limits |
|---|---|---|
| Managed Codex | Private stdio `account/read` without token refresh, `account/rateLimits/read`, `model/list`; owned `account/rateLimits/updated` events | All returned buckets; primary/secondary used percentages, actual window durations/resets, credit balances, individual spend controls, reached flags, ordinary included-usage permission and reset-credit count. Bucket names do not establish complete model membership. API-key billing has no invented subscription allowance. |
| Claude Code | Documented `statusLine` JSON `rate_limits` | Five-hour and seven-day usage/reset fields, gateway spend fields when present, and additional percentage-shaped pools actually emitted. Additional pool membership is unknown unless explicitly established. No independent account quota fetch is claimed. |
| Antigravity CLI | Documented `statusLine` JSON `quota`; native `agy models` | Model/bucket remaining fractions and reset fields. Only exact catalog model/display-name matches establish model membership automatically; shared family names are not guessed. Credits and hidden overlapping windows absent from telemetry remain unknown. |

Primary contracts: [Codex app-server](https://learn.chatgpt.com/docs/app-server), [Claude status line](https://code.claude.com/docs/en/statusline), [Antigravity status line](https://www.antigravity.google/docs/cli/statusline/), [Antigravity model-quota panel](https://www.antigravity.google/docs/cli/commands/usage/).

The native catalog establishes advertised choices and supported effort/modalities where supplied. It does not establish inference entitlement. `ordinaryUsageAllowed` describes included usage, not an invented model-to-bucket map. Credit balances and earned reset credits are displayed separately; neither is silently spent or used to claim a depleted model is available.

Every model includes applicable pool IDs, state, coverage and reasons. Every pool retains native units, field provenance, precision when known, observation age and reset information. A percentage derived from a native fraction is marked as derived. Unknown absolute allowances stay unknown. No token-to-subscription-percentage conversion is made.

All known applicable limiting pools matter. `EXHAUSTED` wins over a healthy overlapping window. Healthy but unmapped pools cannot certify a particular model. `AVAILABLE` means the observed applicable constraints permit it, with the stated coverage; it is not an entitlement guarantee.

`UNKNOWN` means insufficient evidence. `STALE` means an old or reset-crossing observation. An elapsed reset timestamp does not refill anything locally. Authentication and runtime/schema failures remain separate from exhaustion. A failed refresh retains old data with its age and error; it does not disable peer messaging or immutable results.

## Status-line capture and installation

```sh
npeer install --usage --dry-run --json
npeer install --usage --json
npeer doctor --json
npeer uninstall --usage --dry-run --json
```

The usage installer composes a namespaced helper with the existing Claude/Antigravity status-line command. It forwards the original full stdin, stdout/stderr, working directory and exit status. With no custom Antigravity line, it stacks beneath the built-in display without replacing it. Disabled or unsupported definitions are preserved and reported. Normal hooks, models, permissions, provider homes, Codex configuration and native authentication are unchanged.

The helper validates native ancestry, the selected home, native UUID and the installed runtime signature. It filters out emails, account details, working directories and transcript paths, then attempts a short local journal write. It performs no network calls and does not register peers. Identical cached payloads do not continually renew quota freshness. An unavailable/busy journal does not break the original status line.

Owned definitions and interrupted operations are recoverable in the private state manifest. A second install is a no-op. Uninstall restores only a matching owned status-line definition; later unrelated settings and modified status lines are preserved. Native configuration may reload the command normally; no production conversation is restarted.

## Planning and dispatch

```sh
npeer usage --refresh --json
npeer spawn --check --spec /absolute/batch.json --json
npeer spawn --spec /absolute/batch.json --json
```

Preflight reads cached quota without writing observations, reserving workers or materializing workspaces. Its `usage` section shows relevant pools, freshness, exact model/effort, proposed/active shared-pool demand and estimates. Quota and explicitly configured pool-ceiling blockers make `ready:false`. Configured fallbacks remain visible separately; a blocked primary is not reported as available because a fallback exists.

Actual dispatch refreshes supported Codex quota when needed, reuses the existing Antigravity catalog, and checks applicable observations before task input. Unknown or stale data does not block a model. Fresh, proven applicable exhaustion yields a retained `QUOTA_EXHAUSTED` task disposition without submitting input. Other siblings can run. The default per-worker policy is `respect-known-limits`.

For an intentional attempt of the exact model despite a quota warning:

```json
{"provider":"codex","model":"gpt-6.1-sol","reasoning_effort":"xhigh","title":"Exact review","task":"Review the selected patch.","quota_policy":"warn"}
```

This does not change the model, native authentication or permissions. It is not an instruction to replay uncertain input. Request retries still return the original operation before evaluating mutable quota or titles again.

Explicit Claude-to-Gemini fallbacks use the existing clean-attempt mechanism. A pre-execution quota block may advance only to configured candidates within original time/turn/depth/concurrency limits. Admitted or uncertain tasks are never blindly replayed. Failed attempts, native identities and final outcomes remain separately attributable. See [provider fallback](PROVIDER_FALLBACK.md).

Local shared-pool demand is advisory by default. An explicit `pool_concurrency` entry imposes a local admission ceiling for known mappings across spawn, resume, follow-up and fallback. Idle grace without pending work does not occupy demand. External applications/devices can consume the same quota, so this never reserves provider capacity. Different homes are kept distinct unless their shared account scope is established; unknown cross-home sharing remains a limitation.

## Recommendations and preferences

```sh
npeer usage --recommend --requirements /absolute/task-requirements.json --preference quality-first --json
```

Requirements can name `task_type`, `complexity`, `quality`, `context_tokens`, `required_tools`, `modalities`, `reasoning_effort`, `independent_from_vendor` and an exact `model` for `exact-model-only`. Each worker can carry the same optional `requirements` object.

The available preferences are `quality-first`, `balanced`, `quota-preserving`, `fast` and `exact-model-only`. Hard quality, context, tools, effort and independent-vendor requirements come first. Only observed native catalog/selection/model candidates qualify. Quality/efficiency priorities require explicit local metadata with a source; NativePeers does not invent benchmark rankings or treat same-provider models as interchangeable. If evidence is insufficient, it explains that no recommendation is supported. Recommendations never rewrite a task or replace configured defaults.

An optional `usage` section in the existing private `policy.json` controls freshness, thresholds, model preferences, explicit pool membership and local ceilings. See [the preference example](../examples/usage-policy.json). Its quality labels are illustrative local preferences, not provider measurements; tailor them deliberately and retain their source. Pool membership supplied by configuration is labeled `explicit-local-policy`, not provider-reported. No configuration is required for ordinary usage readouts.

Default freshness is 120 seconds; warning thresholds are 20% remaining, 5% critical and resets within 15 minutes. Per-provider freshness and thresholds are configurable. Warnings appear in requested reads/plans, never unsolicited model-waking turns. Automatic model selection is not implemented or enabled.

## Local consumption and qualified forecasts

`local_usage` reports only NativePeers-owned attempts. Measurements include provider/model/effort, available token counters, duration, native turn IDs/counts, terminal state and quota errors. Repeated terminal handling cannot double-count an attempt. Costs, where reported, are separate native metrics and are not estimates of subscription dollars. Older unmeasured tasks are not fabricated into a complete account ledger.

Task estimates require five comparable successful measured attempts, matched on provider/home/model/requested effort/profile/requirements. They report sample count, median and spread. Account-pace runway requires three compatible observations spanning a minute, monotonic usage within one reset window and enough change relative to reported precision. It explicitly includes activity outside NativePeers and assumes that pace continues. Missing evidence, resets, clock inconsistency and unknown precision produce `UNKNOWN` estimates. No extra model turns are spent on calibration.

Planned remaining quota stays unknown without a validated quota-per-task relationship. Token estimates and shared-pool contention are still useful without pretending tokens equal allowance percentages.

## Bound MCP and optional imports

`usage_status` provides the same provider/model/home selection, optional refresh and requirements/preferences. It is available to bound analysis workers and parents without a delegation grant. Inactive callers are refused. The read tool offers no import, configuration editing, account mutation, arbitrary credential path or model dispatch operation. Existing facades advertise new tools after their next normal startup.

Where native quota is inaccessible, explicitly import a selected nonsecret snapshot:

```sh
npeer usage-import --file /absolute/quota-snapshot.json --json
```

The versioned [import schema](../schemas/usage-import.v1.json) accepts provider, original observation time, optional home label, model IDs and bounded pool values. Imports are clearly user-supplied, never override native snapshots and cannot alone block dispatch. Owned workers cannot import claims. The public normalized result is described in [usage.v1.json](../schemas/usage.v1.json).

Current automated/native source checks and limitations are recorded in `evidence/usage-intelligence.json`. Runtime status-line emission, authenticated metadata reads, fake-provider tests and real inference acceptance are separate verification classes. This feature needs no inference for collection or calibration.
