# Grok Build

NativePeers supports Grok Build as a fourth native CLI provider. It uses the existing Grok sign-in and selected `GROK_HOME`; it never reads or copies `auth.json`, selects an API key, changes the default model, or changes global compatibility settings.

The measured executable is Grok **1.0.70 (f50904eaee6c), stable**, at `/Users/danny/.grok/downloads/grok-1.0.70-macos-aarch64`, SHA-256 `5c8a688251bf61e35370b828b6a353b0a20fcc3f64000de8a91d426039f29cec`. Owned launches hash and execute the resolved versioned target, pass `--no-auto-update`, and set `GROK_DISABLE_AUTOUPDATER=1`. A different binary requires separate qualification and returns `CAPABILITY_MISMATCH`.

## Identity and messages

Grok-native hooks supply the exact `sessionId`, process owner and incarnation. Registration checks the nearest native process boundary, so a Grok child cannot borrow an enclosing Claude identity or an inherited Codex thread ID. Titles come only from native Grok `sessions/*/UUID/summary.json` records; compatibility imports are excluded. Use `npeer resolve --to "grok:Exact Native Title" --json`.

Folder trust controls hook loading. On the measured runtime, the untrusted feature worktree lists three plugin hooks, while the approved fixture lists eleven hooks including Claude-compatible handlers. The inherited NativePeers Claude handler refuses Grok payloads. NativePeers does not grant folder trust during installation.

Inbound messages wait in the NativePeers journal for Grok's next native tool invocation. The owned `PreToolUse` hook returns model-facing `additionalContext`. Grok 1.0.70 discards allowing `UserPromptSubmit.additionalContext`, so that event supplies no delivery claim. Hook output remains `SUBMITTED_UNCONFIRMED`; reconciliation requires the exact message in that UUID's native user record. An idle CLI cannot be awakened through this route. Native session records are read, never rewritten.

## Workers and usage

Tailor `examples/two-grok-workers.json` and use the ordinary `spawn --check`, `spawn`, bounded `wait`, `result`, owned cancellation and exact-UUID resume commands. A new worker UUID is reserved before launch. Serialized follow-ups and owned resume retain that UUID, selected home, requested model and observed native selection. Patch artifacts use the same isolated-worktree or isolated-copy machinery as the other providers.

The native headless `streaming-json` transport supplies terminal completion, session and request IDs, errors, round counts and per-prompt usage. The owned summary supplies the selected model. The measured catalog advertises `grok-4.7`, `grok-4.7-build-fast`, `grok-4.6` and `grok-4.5`, with `grok-4.7` as default. Its engine usage can name `grok-4.7-build`; that engine attribution remains separate from the selected `grok-4.7` model. Explicit reasoning effort is refused because this catalog advertises no per-model effort menu.

`limits.max_task_turns_per_worker` limits logical task inputs in each controller generation (default 8). `limits.max_native_turns_per_task` independently supplies Grok's native `--max-turns` cap for model rounds within each input (default 128). The logical-input and native-round counters are reported separately. Native `max_turn_requests` or `max_turns` exhaustion reports `BUDGET_EXHAUSTED` with the cap's field and effective value. Claude uses the same separate native-round setting; Codex and Antigravity do not use it.

Task wall time defaults to 1800 seconds when omitted. Task-wall, logical-input, native-round and group-lifetime ceilings are unset (`null`) unless configured by the operator. Explicit positive request limits may exceed the omitted defaults. An explicit group ceiling shorter than the task request is rejected before launch; without a group ceiling, the group adds no earlier cutoff. [Daily commands](USAGE.md) lists every default, ceiling and policy key. `spawn --check` and `doctor` expose their effective values and sources. The example's logical-input value of four is intended for its short fixture tasks and does not reduce the native-round cap.

Analysis exposes fixed file-read tools. Workspace workers add the native shell and file-edit tools in an owned isolated directory. Both disable recursive agents and web tools, deny MCP tool calls, and require fresh native hook-execution records and controller-verified kernel permissions for the exact launched process. An advertised tool catalog alone is not a grant. No bound worker MCP facade is granted in this integration.

The owned `nativepeers-analysis` and `nativepeers-workspace` sandbox profiles extend Grok's `read-only` and `workspace` profiles. They grant no write access to the NativePeers journal or control files. Owned hooks open the existing journal read-only to verify their binding, runtime and process ancestry. The controller records identity and lifecycle state outside the worker sandbox, and independently inspects the exact Grok PID's kernel permissions. A nonempty deny path makes native custom-profile startup fail closed, and the hook checks inherited macOS kernel permissions before granting a tool. The journal must be outside a workspace worker's writable directory. Child-process network blocking is unavailable on macOS; tool grants and the filesystem sandbox have separate scope.

Owned-turn token accounting reads `grok usage UUID`. Resumed input requires the next persisted turn after the pre-launch baseline; missing or stale records stay explicitly unknown. Session totals are not counted again as new task usage. Account quota remains **UNKNOWN** because no documented native account-allowance source has been qualified.

## Installation

```sh
npeer install --grok --dry-run --json
npeer install --grok --json
npeer uninstall --grok --dry-run --json
npeer uninstall --grok --json
```

The installer owns `GROK_HOME/hooks/nativepeers.json` and only the two named profile entries in `GROK_HOME/sandbox.toml`. It uses compare-and-write checks and recoverable backups; uninstall preserves unrelated TOML bytes and later comments. Conflicting owned edits are retained for reconciliation. Initial worker/inbox approval checks the exact executable against the qualification record and never replaces another provider's approval. Equivalent fixture-local hooks and profiles support disposable qualification before host installation.

Grok Desktop, remote or cloud sessions, `cursor-worker`, leader-hosted sessions and fallback routing to or from Grok are outside this integration. Model evaluations and allocation/default changes are separate work.

## Verification and close-out

The implementation is integrated into local and remote `main`. The owned Grok hooks, analysis/workspace profiles and four-provider shared-instruction block are installed from the main checkout. Reinstall performs zero operations, and uninstall planning names only the owned hook file and profile entries. Current provider runtime approvals, including the independently updated Codex runtime, remain byte-identical through this finalization.

The complete worktree suite passed **721 tests and 740 subtests**, with 4 Linux-only skips, in **364.96 seconds**. The complete main suite passed **721 tests and 740 subtests**, with 4 Linux-only skips, in **273.96 seconds**. Main includes the final exact-PID readiness refinement for an existing catalog cleanup fixture; production deadlines and provider behavior are unchanged by that test correction. Source and publication checks are recorded in `evidence/grok-20261026.json`.

Native fixture acceptance covers exact trusted identity/title resolution, explicit untrusted refusal, Claude↔Grok replies and native-history reconciliation, Codex→Grok, concurrent analysis workers, workspace patches, exact-UUID resume, owned-turn usage and cancellation with descendant/output closure. Installed global-control protection and a nested Unicode-path worker also passed. Two GPT-6.1 Sol/xhigh reviewers and independent native Claude Opus review and follow-up completed; findings and later fixture corrections have recorded dispositions. Verbatim reports remain under the project's ignored `evidence/scratch/grok-20261026/` directory.

The original qualification record remains **PARTIAL**. The corrected historical live-input count is **28 against 24**: 24 model/task inputs plus four local-shell inputs initially excluded despite native turn records. The ceiling was not raised. Recorded active testing is 572.10 of 1,800 seconds, with two concurrent Grok workers. No additional model input is submitted during finalization. A complete initial native-history/authentication hash inventory was not captured. Grok→Codex live acceptance is not run; Antigravity live messaging remains blocked by its preexisting runtime mismatch.

The configuration-preservation hold is cleared by explicit finalization authorization. The possible ultra-mode explanation for Codex config drift is not independently established; the original comparisons remain recorded. Finalization preserves the current Codex config, existing Grok trust, other provider settings/hooks, all runtime approval bytes and every byte outside the owned shared-instruction markers.

Both disposable fixture repositories and sixteen created Grok histories are removed. Three Codex histories are archived; four exact Claude histories remain because a verified UUID-scoped delete path is unavailable. The merged feature worktree and local/remote branch are removed. Verified worker archives, fixture bundles, raw reviews and the worktree's CodeGraph index remain in named project recovery folders. The unrelated `fix/review-2885497` branch is unchanged.

The installed user guide under `/Users/danny/.grok/docs/user-guide/` is the version-specific contract: hooks (10), headless mode (14), agent mode (15), sessions (17), sandbox (18), permissions (22), and config fields (26). Test fixtures and native records determine the measured scope; a version string or receipt alone does not establish live acceptance.
