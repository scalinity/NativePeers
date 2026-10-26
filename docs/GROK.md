# Grok Build

The `feat/grok-provider` candidate supports Grok Build as a fourth native CLI provider. Local `main` remains at `ad09cdd` while preservation and live-budget failures hold integration. The candidate uses the existing Grok sign-in and selected `GROK_HOME`; it never reads or copies `auth.json`, selects an API key, changes the default model, or changes global compatibility settings.

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

The verdict is **PARTIAL**. Implementation is isolated on `feat/grok-provider` from `ad09cdd`. Current native acceptance and review outcomes are recorded in `evidence/grok-20261026.json`. The durable fixture ledger, `evidence/live-budget-grok-20261026.json`, records a ceiling of 24 top-level native inputs, four concurrent owned workers and 30 minutes of active testing. Its corrected conservative count is **28 inputs: 24 model/task inputs and four local-shell inputs**. The shell inputs produced native user-message and completed-turn records with no model usage; excluding them initially was an accounting error. The ceiling was not raised. Recorded active intervals total 572.10 seconds, peak concurrent Grok workers is two, and no further native input is submitted by this run.

The unchanged-main baseline passed 644 tests and 656 subtests with four Linux-only skips in 353.39 seconds. The final complete worktree suite, including publication checks, passed **721 tests and 740 subtests**, with four Linux-only skips, in **277.55 seconds** using `.venv/bin/python -m pytest -q`. The published source checkpoint is `ddf64dc18b0fb151543450766aefe2d2e0b69cf7`; subsequent close-out commits contain documentation and verification metadata. That checkpoint adds the required `FAIL` outcome to the verification-index test contract. An earlier full pass took 364.26 seconds. The required suite after integration on main is **NOT RUN** because integration is held.

Native fixture acceptance passed exact interactive identity/title resolution, explicit refusal in an untrusted folder, Claude↔Grok reply correlation, Codex→Grok, native-history reconciliation and visible inbox, two concurrent analysis workers, workspace patch artifacts, exact-UUID resume with native turn 2 usage, and owned cancellation with captured descendant/output closure. The initial shell-inventory mismatch is retained as a failed canary; the fresh corrected canary passed. Imported MCP catalogs and startup children remain visible on this runtime; they receive no tool grant, and owned cleanup captured their process births.

Two independent GPT-6.1 Sol/xhigh reviewers and a native Claude Opus reviewer completed. Every original critical finding, warning and suggestion is addressed. The Opus follow-up approved with suggestions; its long-path warning and process-exit, Unicode-bound and global-control suggestions also have fixes and regression coverage. Verbatim reports, dispositions and failed attempts remain under `evidence/scratch/grok-20261026/` in the main project.

Host installation passed the guarded exact-runtime approval, zero-operation second install and owned-only uninstall plan. Installed workspace workers passed global-control protection and the approved nested Unicode-path check. Since main remains held, the owned Grok integration was then uninstalled idempotently, global shared instructions were restored to their exact initial bytes, and the two approved fixture trust grants were removed with an exact initial-file hash match. The qualified Grok runtime approval remains retained; hooks and profiles are not activated for daily use.

Preservation comparison confirms unchanged Claude settings, Grok configuration, Codex hooks, original-provider approval values, other-provider capability entries and `fix/review-2885497`. The Codex configuration changed from its initial hash through an intermediate hash to a different current hash. Its writer and changed keys remain unresolved after native-log, tool-call and backup investigation; `features.daemon_auto_start` remains false. The auxiliary Claude native metadata file also changed. After exact fixture-trust restoration was verified, a new NativePeers root-folder trust entry appeared with a decision time of **4:56:33 AM EDT on October 26, 2026**. A new native root-session summary is also present; neither belongs to a recorded owned worker or disposable session, and both are retained without attributing the writer. The two approved fixture grants remain absent. Strict byte preservation therefore fails, and current files are retained rather than replaced with guesses. A complete native-history and authentication before-and-after hash inventory is **NOT RUN**: this run did not capture an initial inventory for those files. The recorded 28 protected-path snapshots do not establish that broader comparison.

Both disposable fixture repositories are removed after verified Git bundles, worker archives and NativePeers-state copies. All owned Grok process births are closed, no Grok leader remains, and sixteen created Grok histories were deleted through the native command. Three disposable Codex histories are archived. Four exact Claude histories are retained because a verified UUID-scoped delete path is unavailable; a broad project purge could affect unrelated histories. The unmerged feature worktree and local/remote branch remain for recovery and later integration. The protected `fix/review-2885497` branch is unchanged.

The installed user guide under `/Users/danny/.grok/docs/user-guide/` is the version-specific contract: hooks (10), headless mode (14), agent mode (15), sessions (17), sandbox (18), permissions (22), and config fields (26). Test fixtures and native records determine the measured scope; a version string or receipt alone does not establish live acceptance.
