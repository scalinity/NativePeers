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

`max_task_turns_per_worker` limits logical task inputs in each controller generation and also supplies Grok's `--max-turns` ceiling for native model/tool rounds within each input. The logical-input and native-round counters are reported separately. The example's value of four is intended for its short fixture tasks.

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

## Progress and verification

Implementation is isolated on `feat/grok-provider` from `ad09cdd`. Main remains the live runtime until the required checks hold. Current native acceptance and review outcomes are recorded in `evidence/grok-20261026.json`; the durable fixture allowance is `evidence/live-budget-grok-20261026.json` (24 top-level turns, four concurrent workers, 30 minutes active live testing).

The unchanged-main baseline passed 644 tests and 656 subtests with four Linux-only skips in 353.39 seconds. The scoped source suite passed 712 tests and 728 subtests with four skips in 339.13 seconds, excluding three publication-pointer checks. Follow-up path, process-exit, Unicode-context and global-control regressions pass separately; the complete final-source suite runs after verified feature publication.

Native fixture acceptance passed exact interactive identity/title resolution, explicit refusal in an untrusted folder, Claude↔Grok reply correlation, Codex→Grok, native-history reconciliation and visible inbox, two concurrent analysis workers, workspace patch artifacts, exact-UUID resume with native turn 2 usage, and owned cancellation with captured descendant/output closure. The initial shell-inventory mismatch is retained as a failed canary; the fresh corrected canary passed. Imported MCP catalogs and startup children remain visible on this runtime; they receive no tool grant, and owned cleanup captured their process births.

Two independent GPT-6.1 Sol/xhigh reviewers and a native Claude Opus reviewer completed. All original findings have fixes or an explicit documented disposition. The Opus follow-up approved with suggestions; its long-path warning and process-exit, Unicode-bound and global-control suggestions have targeted fixes. Verbatim reports and failed attempts remain in the project's ignored verification directory. Host installation, the long-path installed-worker smoke and final teardown are recorded separately in the current verification record.

The installed user guide under `/Users/danny/.grok/docs/user-guide/` is the version-specific contract: hooks (10), headless mode (14), agent mode (15), sessions (17), sandbox (18), permissions (22), and config fields (26). Test fixtures and native records determine the measured scope; a version string or receipt alone does not establish live acceptance.
