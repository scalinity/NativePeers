# NativePeers Lineage: using it in Claude Code

NativePeers workers of every provider (Codex, Claude, Antigravity, Grok) appear in an interactive Claude Code session as ordinary background subagents. Their rows sit in the tasks list below the prompt, their live transcripts open in the normal agent view, and their results arrive as normal background-agent notifications. NativePeers still owns the workers; the `np` mod mirrors them. The contract is [CLAUDE_LINEAGE_MOD.md](CLAUDE_LINEAGE_MOD.md).

## Install

From macOS Terminal:

```sh
npeer install --claude-mod --dry-run --json   # shows the claude plugin commands it would run
npeer install --claude-mod --json             # runs them; a repeat run performs zero operations
```

The installer registers this checkout's `integrations/claude/mod` folder as the local marketplace `nativepeers-local` and installs `np` at user scope, so it loads in Terminal sessions and in the desktop app's local Code sessions. Claude Code keeps its own copy of an installed plugin: after `git pull`, run `npeer install --claude-mod --json` again, which refreshes the copy when it differs from the checkout, then start a new session or run `/reload-plugins`. Remove it with `npeer uninstall --claude-mod --json`.

In the desktop app, a local Code session starts its engine with the first message you send, so the `⟐ np` band appears after it. NativePeers approves the desktop's bundled engine on first use, once its Anthropic code signature and version check out on a private copy. A desktop session has no inbox, so it is a mirror-only parent: `np:*` workers mirror and notify as in Terminal, and the first results of workers it starts with `npeer spawn` stay with NativePeers (`npeer result`).

## Delegating

Ask in an ordinary `claude` session, for example:

> Spin up delegates for GPT-6.1 Sol high in Codex, Grok 4.7 in Grok Build, Gemini 4 Argon in Antigravity and Sonnet 5.5 in Claude. Tell them all to explore the codebase and find easy wins.

The model calls `mcp__np__catalog` for exact model ids, then issues `Agent` calls with `subagent_type` `np:codex`, `np:grok`, `np:antigravity` and `np:claude`. Each prompt may start with a header:

````text
```np
{"model":"gpt-6.1-sol","effort":"high","profile":"analysis","title":"Codebase review"}
```
Review the codebase and list easy wins. ...
````

Header keys: `model`, `effort`, `profile` (`analysis` or `workspace`), `workspace`, `title`, `fallbacks`, `quota_policy`, `requirements`. Anything else is refused before a worker starts. Without a header the worker runs the provider's configured default with the read-only `analysis` profile. The worker sees only the brief, never the conversation.

Rows read like `◯ np:codex  GPT-6.1 Sol high · Codebase review   1m 12s · ↓ 18.4k tokens`.

## Watching and talking to a worker

| To | Do |
|---|---|
| Open a worker's transcript | ↓ to reach the tasks list, ↓ to the row, Enter; or press `[Open]` in the band; or `/np open N` |
| Send a follow-up | Type while viewing it. A running worker gets it after its current turn (or steered, with `followupMode: steer-if-supported`, where the provider supports steering); a finished worker resumes the same native conversation while it is still listed |
| Interrupt the current turn only | `[Interrupt]` in the band, or `/np interrupt N` |
| Stop that worker's task | `x` on its tasks-list row, Esc while viewing it, `[Stop]`, or `/np stop N` |
| Stop everything this session started | `[Stop all]` or `/np stop all` (descendants too) |
| Return to the main conversation | ↓ to the list, select `main`, Enter |

Esc in a worker's view stops that worker's task: Claude Code reports Esc there exactly as it reports `x`, so the mod cannot treat it as a gentler interrupt. Use `[Interrupt]` when the worker should keep its conversation and only end the current turn. Esc in the main view never touches workers.

The parent model can follow up with `SendMessage` to the agent. Workers started with `npeer spawn` or `worker_spawn` are adopted into rows automatically; their first results still arrive through NativePeers' own notice (the row's final line says so).

## Commands

`/np` status · `/np pane` lineage pane · `/np open N` · `/np stop N|all` · `/np interrupt N` · `/np attach N|all` · `/np reattach` · `/np doctor` · `/np resume-cmd N`.

## What it costs

A mirrored worker spends its own provider's usage only. Shadows never call a Claude model: each step is answered by the mod from the worker's activity, and shadows run on a model id no API serves, so a fault, or Claude Code's own background-agent summaries, cannot reach inference. If a request for a shadow is ever detected, the `np:*` types switch off until `/np doctor` passes. In auto permission mode, Claude Code's own safety check reviews each result as it enters the main conversation; that review is the main session's usage, as for any background agent.

## Limits

- Only interactive sessions activate the mod; `claude -p`, SDK runs and NativePeers' own Claude workers leave it inert.
- Claude Code drops a finished agent from the tasks list about 30 s after it ends, and its view can no longer be opened. To follow up later, ask the parent (it uses `SendMessage`), or use `/np resume-cmd N`; `/np pane` still shows the worker.
- After `claude --resume`, finished workers stay in the transcript but not in the tasks list; running workers can be re-attached from the band or with `/np reattach`.
- After `/clear`, workers started before it keep mirroring and still take follow-ups while they run or wait in their grace period, but one that has already been released cannot be resumed from the cleared session; use `/np resume-cmd N` for its exact resume command.
- A session keeps its workers' mirror leases while it stays open, so another session can view those workers in its pane but cannot mirror them.
- Pressing ← moves the conversation into Claude Code's background-sessions manager. That host is outside the verified design.
- Grandchildren of a worker appear read-only in the pane, not as their own rows.
