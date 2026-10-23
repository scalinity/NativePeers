# NativePeers progress

Live usage-source implementation and installation are complete at product commit `58b6a4b627cec652c785d787996c79d5ef776122`. Final offline verification: **460 tests / 556 subtests PASS**; external manager/naming fixtures: **23 PASS**. Actual native CLI quota reads pass for Claude, Codex and Antigravity with zero model inputs.

- [x] Terminal provider errors, source/auth separation and synthetic stream precedence.
- [x] Active acquisition generations/TTL/coalescing, clocks, exact-scope refusal reconciliation and quota units.
- [x] Native Antigravity usage/credits and exact-build freshness proof.
- [x] Authorized native Claude usage-only PTY, normal config layout, modern terminal parsing, exact no-history/zero-token checks.
- [x] Provider/home admission refresh, shared deadlines, sparse pool preservation and shutdown/readiness race corrections.
- [x] CLI/MCP schemas, owned skills/global instructions, source/runtime publication and protected-environment checks.

Known verification scope: real stdio MCP probe is CALLER_NOT_READY; offline protocol/caller parity passes. Full native inference/messaging/delegation matrix is not reaccepted by quota reads. No model input or budget reset occurred. Worker-history visibility and the separate Pro review remain independent.

Details: docs/LIVE_USAGE_SOURCES.md and evidence/live-usage-sources.json. Private baseline/captures: evidence/private/live-usage/. Backups: backups/live-usage/. No worktree or native worker team was created; regenerable large binary indexes were removed.
