# Installation and rollback

Installation and uninstall pin each hook merge to the configuration hash captured before reading it. A concurrent edit causes `OWNERSHIP_CONFLICT`; reconcile the newer file before retrying. Reinstall updates an installed skill only when it still matches the recorded installed hash or already matches the desired content. Local skill edits are preserved and reported as conflicts.

The installed project is `/Users/danny/Documents/Tools/NativePeers`. Its shell entrypoints select the pinned project virtual environment and module path. There is no permanent service or provider executable replacement.

The activation lock follows the installed `~/.local/bin/codex-mcp` manager link, so a project rename does not select a stale manager directory. Qualification and managed activation share that supported lock; runtime receipt changes also take the NativePeers runtime lock and compare the selected provider's previous approval.

```sh
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers install --dry-run --json
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers install --json
npeer doctor --json
```

Installation uses an own lock plus the existing CodexMCP manager's supported update lock. It compares files before replacement, merges only its owned hook handlers and uses the installed native version-checked API for owned Codex hook trust. A second install is a no-op when those owned pieces match. A command collision preserves the existing command and reports module invocation as the alternative. Unknown skill ownership is a conflict, not permission to overwrite.

| Path | Owned change |
|---|---|
| `~/.local/bin/npeer` | Link to this project's `bin/npeer` |
| `~/.claude/settings.json` | NativePeers SessionStart/SessionEnd handlers and a Bash PreToolUse capability refresh |
| `~/.codex/hooks.json` | NativePeers SessionStart/SessionEnd command handlers |
| `~/.codex/config.toml` | Native trust hashes for those exact owned handlers |
| `~/.claude/skills/nativepeers/SKILL.md` | Claude CLI integration instructions |
| `~/.agents/skills/nativepeers/SKILL.md` | Codex integration instructions |
| `~/.local/state/nativepeers/` | Own private SQLite journal, bindings, compatibility receipts, results and install manifest |
| Project `backups/` | Private targeted owned-block rollback snapshots; no provider credentials |

The original protected inventory and semantic baseline are retained privately in the project's ignored evidence files. Authorized deltas and semantic preservation are summarized in [installation evidence](../evidence/installation.json). Provider-native caches/trust/history and legitimate credential refreshes are retained, not restored to an old hash.

Targeted uninstall removes only matching owned handlers, their matching trust hashes, unchanged owned skills and this exact command link. Later unrelated edits remain. Modified owned files are preserved and reported as conflicts. State, native histories, results and worktrees remain available.

```sh
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers uninstall --dry-run --json
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers uninstall --json
```

Do not restore whole provider configuration or authentication files from a historical baseline. To undo an individual hook entry manually, match only `/Users/danny/Documents/Tools/NativePeers/bin/npeer-hook --provider claude` or `--provider codex` in the corresponding lifecycle hook, then remove only its exact owned trust state if still matching the install manifest. NativePeers never changes the `codex` shell function, global daemon feature, model/account settings, existing MCP states, native histories, managed patch set, Herdr/T3/Ghostty configuration or hourly update job.

The live acceptance fixture uses a separate ignored `evidence/private/live-state` journal and disposable native sessions. That state contains test history pointers and private process manifests; it is not the daily-use default journal. Do not copy it into the installation or replay its native inputs. `evidence/live-budget.json` is the one implementation verification allowance across restarts.


Antigravity is installed separately with `npeer install --antigravity --json`; use `npeer uninstall --antigravity --json` for owned-block removal. Its native CLI lives at `~/.local/bin/agy`, and configuration under `~/.gemini/config/` contains the namespaced hook/MCP entry, two scoped profiles and skill. The shell source block `NATIVEPEERS_GEMINI_START` through `NATIVEPEERS_GEMINI_END` in `~/.zshrc` owns only the `gemini` shortcut. It preserves the prior file bytes and existing Codex function. Remove only that block to undo the shortcut; NativePeers uninstall retains the official CLI, Google sign-in, native histories and results. [Detailed commands and limitations](ANTIGRAVITY.md).

The model guidance in `~/.claude/CLAUDE.md` now documents optional per-worker models for all providers. Its namespaced NativePeers block was updated without replacing unrelated conventions. Targeted private snapshots are in project `backups/model-antigravity-conventions/` and `backups/model-antigravity-shell/`; do not restore whole historical configuration over later unrelated edits.

Antigravity installation validates the current executable against its scoped entry in `evidence/capabilities.json` before writing configuration. A native CLI auto-update requires reviewing the changed runtime and recording the affected checks before admission. Reinstall does not silently bless a changed binary. Worktree creation owns only an appended `/.worktrees/` line in each source repository's local Git exclude, with a targeted snapshot under project `backups/worktree-excludes/`; remove only that exact line after reviewing/removing the owned worktrees.

## Global usage conventions

The owned NativePeers section in `~/.claude/CLAUDE.md` mirrors `docs/CLAUDE_CONVENTIONS.md`. This correction pass changed only the text between `NATIVEPEERS_START` and `NATIVEPEERS_END`; every outside byte is preserved. Its recoverable original block is `backups/astra4-20261022/CLAUDE-owned-block.md`. To undo only this text update, compare the current owned block against the checked-in conventions, replace only those markers and their contents with that private backup, and preserve subsequent unrelated edits. This manual text convention is separate from the hook/skill installer and native policy.

Review 0274647 conventions are installed in the three owned skills and the NativePeers block of `~/.claude/CLAUDE.md`. Recoverable prior owned text is under `backups/review-0274647/`; outside instruction bytes were preserved. Both second installs performed zero operations. A raw Codex configuration difference appeared during the install window without an owned hook-trust delta; its attribution remains unresolved, its contents were retained, and further Codex configuration writes stopped. Current model/effort and daemon settings remain Sol/xhigh and daemon_auto_start=false. Do not restore a whole configuration to force an old hash match.

Usage intelligence and its exact commands, native sources, installation, freshness, quota overrides, recommendation constraints and forecast limitations are documented in [USAGE_INTELLIGENCE.md](USAGE_INTELLIGENCE.md). `install --usage` composes only owned status-line capture; it preserves existing display commands and does not update worker/inbox runtime approval.

## Claude executable maintenance

After an update, run `npeer doctor --json` and inspect the separate worker, inbox and quota rows. Follow [the bounded renewal probe](../tests/probes/claude_runtime_verify.py) for worker/inbox qualification. Use a new private run directory for each attempt, beneath this project's ignored `evidence/scratch`; never replay an existing launch to repair uncertain delivery. Review the installed version, help, exact fingerprint and the [official changelog](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md) before selecting the reviewed SHA-256.

```sh
cd /Users/danny/Documents/Tools/NativePeers
qualification_run=/Users/danny/Documents/Tools/NativePeers/evidence/scratch/claude-renewal-UNIQUE
.venv/bin/python tests/probes/claude_runtime_verify.py inspect --run "$qualification_run"
# Review inspect.json and the executable change; substitute its exact SHA-256.
.venv/bin/python tests/probes/claude_runtime_verify.py prepare --run "$qualification_run" --reviewed-sha256 EXACT_REVIEWED_SHA256
.venv/bin/python tests/probes/claude_runtime_verify.py launch --run "$qualification_run"
```

Launch in a newly owned terminal/PTY. It creates a disposable Claude parent using the normal selected native home/authentication, scoped `dontAsk`, restricted tools and disabled child plugins/MCP registrations. Global native hooks register the fixture in separate NativePeers state. Its actual native shell spawns one analysis worker, then arms a one-millisecond wait with push fallback. The exact original parent must receive the native completion and run the receipt helper itself. No production conversation is restarted, resumed or used for test input. If trust, login, native policy or quota blocks it, retain that result and stop the affected qualification; do not change policy or approve the binary.

When the parent has reported the actual worker marker and the receipt helper has returned, close only that owned fixture normally. Collect the proof and explicitly approve:

```sh
.venv/bin/python tests/probes/claude_runtime_verify.py collect --run "$qualification_run"
.venv/bin/python tests/probes/claude_runtime_verify.py approve --run "$qualification_run"
npeer doctor --json
```

Collection requires a completed, independently identified native worker, its effective analysis tool inventory and exact original-parent `INGESTED` evidence. The approval step refuses changed product source, a changed selected executable, a concurrently changed prior approval or a revoked target. It rehashes the selected Claude link immediately before the guarded write while holding the installed manager lock. Only Claude's worker/inbox approval changes; quota receipts and all other providers remain intact. New workers verify the current approved runtime normally; retained exact leases keep their existing approval rules. Inspect doctor again after writing because the native updater can independently change the executable. Do not copy a fingerprint into a receipt or interpret a quota-read receipt as this qualification.

The probe retains `inspect.json`, native launch/parent/wait records, actual completion receipt and `qualification.json`. Keep private native captures in the ignored run directory; publish sanitized native IDs, runtime/source fingerprints, commands, exit codes and verification limits in the current evidence record. Run the focused renewal regressions with `.venv/bin/python -m pytest -q tests/unit/test_claude_runtime_verify.py tests/unit/test_completion_delivery.py`. The probe is explicit maintenance, never an unattended inference test triggered by an installer or update.

To roll back a product correction, use a normal Git revert of its named commit after preserving later work. A failed runtime qualification leaves the prior receipt unchanged. To withdraw a successful renewal, use the same guarded `update_verified` transition under the supported locks and only select a runtime with valid retained qualification; never restore an entire historical receipt over other providers' later approvals. Leaving a changed runtime unapproved is a safe, visible state, with retained results still readable.
