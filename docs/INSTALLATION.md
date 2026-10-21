# Installation and rollback

Installation and uninstall pin each hook merge to the configuration hash captured before reading it. A concurrent edit causes `OWNERSHIP_CONFLICT`; reconcile the newer file before retrying. Reinstall updates an installed skill only when it still matches the recorded installed hash or already matches the desired content. Local skill edits are preserved and reported as conflicts.

The installed project is `/Users/danny/Documents/Tools/NativePeers`. Its shell entrypoints select the pinned project virtual environment and module path. There is no permanent service or provider executable replacement.

```sh
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers install --dry-run --json
/Users/danny/Documents/Tools/NativePeers/.venv/bin/python -m nativepeers install --json
npeer doctor --json
```

Installation uses an own lock plus the existing CodexMCP manager's supported update lock. It compares files before replacement, merges only its owned hook handlers and uses the installed native version-checked API for owned Codex hook trust. A second install is a no-op when those owned pieces match. A command collision preserves the existing command and reports module invocation as the alternative. Unknown skill ownership is a conflict, not permission to overwrite.

| Path | Owned change |
|---|---|
| `~/.local/bin/npeer` | Link to this project's `bin/npeer` |
| `~/.claude/settings.json` | NativePeers SessionStart/SessionEnd command handlers |
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
