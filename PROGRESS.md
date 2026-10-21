# NativePeers implementation checkpoint

M1 caller/ingress/helper/return probes passed in four disposable native TUIs (two Claude, ordinary and explicit standalone Codex). Each original UUID received its helper completion. The native queue is selected; no Rust patch. [Observations](evidence/m1-native-observations.json), [feasibility](evidence/feasibility.json), [decision](docs/native-queue-decision.md). These are seam probes, not final product acceptance.

M1 bootstrap: official MCP SDK inactive handshake/refusal/list-change tests pass; installed Codex starts a native thread with only the scoped unbound facade. Native binding activation and Claude structured startup remain to validate in the early worker slice.

M2 code now includes stable identity, native caller binding, SQLite journal, exact-title resolver, bounded artifacts, send/reply correlation/idempotency, native transport adapters, hooks, scoped MCP, launch profiles and worktree baselines. Unit/contract checks: 11 passed. Existing manager/naming regressions: 23 passed. No product installation or protected configuration writes. Canonical documents and REVIEW.md are present and hashed in [checkpoint](evidence/checkpoint.json). Native-generated trust/cache/history updates are retained. Existing manager changes remain untouched.

Live ledger: 9 submitted turns; 0 unsettled/reserved; 231.84 active seconds; 16-turn delegation reserve retained. Four owned probe TUIs are idle and their exact process identities are checkpointed. All final LIVE-01–18 remain NOT_RUN.

Next: finish minimal worker controllers and finite group supervision, then prioritize actual parent-tool LIVE-07/LIVE-08. Resume from [checkpoint](evidence/checkpoint.json) and [budget](evidence/live-budget.json); do not rerun accepted probes or reset usage.
