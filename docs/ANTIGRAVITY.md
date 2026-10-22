# Antigravity and worker models

The official Antigravity CLI is installed at `/Users/danny/.local/bin/agy`, version 1.4.5. It uses the normal Google sign-in completed in the native CLI. NativePeers does not extract credentials or switch to an API key. The exact signed-in catalog includes `gemini-4-argon-low`, `gemini-4-argon-medium` and `gemini-4-argon-high`. High is used in the Gemini example.

All three worker providers accept an optional per-worker `model`. Claude aliases such as `opus` and `sonnet`, or full native IDs, are passed to `claude --model`; Codex validates exact model and effort against the installed app-server catalog. Omission snapshots the configured provider default at spawn. The requested and observed values are retained separately, and later turns/resumes pin the observed model. An exact full-ID substitution fails rather than silently passing as the requested model. Native inference establishes availability; a catalog entry alone does not establish entitlement.

From Claude Code or Codex, tailor `examples/two-gemini-workers.json` and run:

```sh
npeer whoami --json
npeer spawn --spec /Users/danny/Documents/Tools/NativePeers/examples/two-gemini-workers.json --json
npeer wait GROUP_ID --timeout-ms 30000 --on-timeout retain --json
npeer result GROUP_ID --json
```

Use fresh batch request IDs, separate titles and focused tasks. `completion_mode:"wait"` is useful when returning to an Antigravity parent, because its native hook cannot wake an idle CLI. Worker histories are independent native conversations, with NativePeers parent links; these are not Antigravity's built-in subagents or private navigation UI. Two independent High workers were live-tested concurrently from the original Claude parent, with attributed native completion return.

## Shell shortcuts

`~/.zshrc` sources the owned `integrations/zsh/gemini.zsh` block. Open a new shell or source that file. `gemini` invokes `agy --dangerously-skip-permissions` using the existing Google account. This is the requested interactive permission bypass; NativePeers worker launches use their own scoped profiles instead.

| Command | Behavior |
|---|---|
| `gemini` | Open native Antigravity with permission bypass |
| `gemini --model gemini-4-argon-high` | Select the native High model |
| `gemini -c` or `gemini --continue` | Native continue behavior |
| `gemini -r UUID` | Resume that exact native history |
| `gemini -r "Exact Native Title"` | Resolve the exact title in the native summary database, then resume its UUID; duplicates fail explicitly |
| `gemini -r` | Open native CLI and show the instruction to enter `/resume` for its picker |
| `gemini -n "Title"` or `gemini --name Title Words` | Explain the native startup-naming limitation and exit without inference; enter `/rename Title Words` in a running native TUI |

Antigravity 1.4.5 has no startup name flag. A local interactive `/rename Title` works and was verified against the same native UUID. Passing slash text through `--prompt-interactive` sends it to the model instead; the wrapper never uses that workaround. Automatic naming and automatic picker opening are therefore unavailable through these shell flags. No typing injection, native database edits or private backend takeover is used. There is no invented `-ca` equivalent.

## Native routes and scope

Interactive Antigravity hooks verify its actual CLI process ancestry and native UUID. The global NativePeers MCP facade starts inactive and binds after native registration; ordinary shell calls use the same core. Native titles come from exact read-only summary queries. Incoming messages remain `SUBMITTED_UNCONFIRMED` until independently reconciled; the hook offers them once as explicitly attributed ephemeral peer/controller content on the next normal native invocation. It cannot wake idle Antigravity. No live peer exchange or idle delivery is claimed for this new provider.

Gemini workers use structured native CLI turns, exact owned UUID resume, finite controllers and normal Google authentication. Analysis exposes native file-read tools; workspace workers expose editing and sandboxed commands in isolated worktrees. Native PreToolUse hooks also deny tools outside the fixed profile, using the documented matcher wrapper and explicit allow/deny result. Inherited MCP/plugin and recursive native-agent tools are not treated as grants. Claude, Codex and Gemini implementation workers were live-tested concurrently in separate project-local worktrees, with actual new code, passing checks, retained patches and original-parent return. Gemini worker MCP/sibling communication and automated cancellation/resume still need dedicated live acceptance; their local controller contracts have offline coverage.

The integration covers the standalone native CLI. Antigravity IDE/App backend ownership and remote hosts are outside this verified mode. Existing Claude/Codex native tools, shell behavior, authentication, histories, Codex daemon policy and managed package remain preserved.

## Owned installation and rollback

```sh
npeer install --antigravity --dry-run --json
npeer install --antigravity --json
npeer uninstall --antigravity --dry-run --json
npeer uninstall --antigravity --json
```

Only the `nativepeers` block in `~/.gemini/config/hooks.json`, its entry in `mcp_config.json`, the two `nativepeers-*.md` profiles and the NativePeers skill are owned. Reinstallation is idempotent; uninstall preserves later unrelated edits. Private rollback snapshots are centralized in project `backups/`. Remove only `NATIVEPEERS_GEMINI_START` through `NATIVEPEERS_GEMINI_END` in `~/.zshrc` to remove the shortcut. The official `agy` executable, its Google sign-in and native histories are retained by NativePeers uninstall.

Native contracts: [headless CLI](https://www.antigravity.google/docs/cli/headless/), [hooks](https://www.antigravity.google/docs/hooks/), [custom agent profiles](https://www.antigravity.google/docs/subagents/). Installed binary behavior and private native transcripts determine the actual verification result; see [verification](VERIFICATION.md).
