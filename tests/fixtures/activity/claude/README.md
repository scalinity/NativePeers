# Claude stream-json fixtures

Recorded from the installed Claude Code 2.1.314
(sha256 21d6e7b9a6f07b4cfcb7df13760b3b72aa25e084e13a5354413382987ba832ed) with
the zero-API harness: `tests/integration/claude_mod/harness.py`, the script
driver `tests/integration/claude_mod/driver` and the script
`tests/integration/claude_mod/scripts/claude_fixture.json`, under
`--permission-mode dontAsk --allowedTools Bash,Read,Write,Edit,TodoWrite`.

The driver answers the model steps, so no model request was made (tripwire hits
0, total_cost_usd 0). The engine itself executed every tool call, so each
`tool_result` and `tool_use_result` is the CLI's own output: Bash stdout and a
failing exit, Write and Edit with `structuredPatch`, Read, a missing file, and
TodoWrite refused because `-p` disables it.

`2.1.314-stream-partial.jsonl` adds `--include-partial-messages`; hook-answered
steps emit no partial `stream_event` deltas, so it matches the plain recording.
