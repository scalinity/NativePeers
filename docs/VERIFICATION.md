# NativePeers verification — 2026-10-21

NativePeers is implemented and installed. **Overall acceptance is BLOCKED**: four required live variants remain after the authorized 48-turn ceiling, and affected live checks require new evidence against the review fixes. Recorded successful native runs remain evidence of their original code/runtime fingerprints; they are not a current full acceptance claim.

The project is `/Users/danny/Documents/Tools/NativePeers`; the command is `/Users/danny/.local/bin/npeer`. Daily state is `/Users/danny/.local/state/nativepeers`. The disposable acceptance journal is separate at `evidence/private/live-state`. The review-fix code commit is `6256250`; the preceding implementation code was `5ff9f02377e8c2897368e759696b112482c24297`. [Source and dependency fingerprints](../evidence/fingerprints.json), [checkpoint](../evidence/checkpoint.json).

The paired read-only review at `151d4e5` found three critical issues and seven warnings. All ten are fixed in separate commits with regressions that reproduced the previous behavior. Critical fixes preserve concurrent configuration edits, keep task waits from consuming batch notifications, and scope cancellation to the exact task. Other fixes preserve edited skills, pin a shared writing baseline, bound UTF-8 completion bodies, read terminal waits without a supervisor, settle exact budget batches, bound queue-helper cleanup and return definite CLI send failures accurately. Opus 4.7 was unavailable, so both specialist reviewers used the inherited session model. [Original review](../evidence/reviews/2026-10-21.md), [fix ledger](../evidence/review-fixes.json), [current offline/passive verification](../evidence/reviews/fix-verification.json).

The implementation includes the Python SQLite routing journal, exact provider/home/native-ID identity and incarnations, verified native callers, exact-title resolution, sender provenance, idempotent/correlated send and immediate-author replies, native queue/inbox adapters, bounded artifacts and result reads, inactive-to-bound scoped MCP, concurrent independent native workers, group supervisors/leases, structured attributed results, sibling grants, bounded waits/push fallback, cancellation, exact owned-history resume, crash reconciliation, isolated writing worktrees and merge-only installation/uninstall. Controllers do not reason or impersonate workers. Worker processes are separate native conversations, not vendor-native subagents or private agent-navigation entries.

## Runtime observations

| Provider | Observed executable/version | Model and authentication |
|---|---|---|
| Managed Codex | `/Users/danny/.local/share/codex-mcp-manager/releases/rust-v0.163.0-8f85dd9c6933-1792479166735424000/bin/codex`, `codex-cli 0.163.0` | `gpt-6.1-sol` executed; `xhigh` advertised and submitted without substitution. Backend effort echo is unverified. Native ChatGPT sign-in retained. |
| Claude | `/Users/danny/.local/share/claude/versions/2.1.306`, `2.1.306` | Configured `sonnet` resolved to observed `claude-sonnet-5-5`. Native `claude.ai`, first-party Max subscription route retained. |

The managed current link remains `/Users/danny/.local/share/codex-mcp-manager/current/bin/codex`; the existing user launcher remains `/Users/danny/.local/bin/codex`. Neither was replaced. Both authentication file integrity checks match their original private baseline; no credentials were copied or extracted. [Authentication evidence](../evidence/auth-preservation.json).

The native Codex queue passed ordinary configured and explicit standalone recipient owner/return probes. Sender commands did not add `--no-daemon`, and `features.daemon_auto_start=false` remains unchanged. **No Rust inbox patch, compilation or managed package activation was required or performed.** Unloaded-target behavior was not separately live-tested and is not inferred from loaded-owner results. [Queue decision](native-queue-decision.md), [M1 evidence](../evidence/m1-native-observations.json).

Claude's native policy remains unchanged globally. Test parents used explicitly scoped `dontAsk` or existing Codex full-access behavior. One old Claude fixture refused a later spawn beyond its original task scope; that consumed turn remains in the ledger. A fresh disposable parent received the intended bounded scope. A real worker test also exposed the Claude first-tool-snapshot issue and Codex MCP approval default; those were fixed with inactive advertised Claude tools and a NativePeers-only worker-local Codex approval setting, then actual sibling calls succeeded after exact-history resumes. Later validation, failure-labeling, writer-retirement, topology and recovery fixes were verified offline; no post-ceiling provider turns were submitted.

## Verification classes

| Class | Result | Scope |
|---|---|---|
| Unit/property | **PASS — 45 tests** | Identity, routing, artifacts/grants, schemas, environment, shared worktree baseline, installer concurrent/local edit preservation, scoped cancellation, budget batches, queue-helper cleanup and CLI error mapping |
| Mock provider/SDK contracts | **PASS — 15 tests** | Official SDK bootstrap/refusal, fake native workers/concurrency, lost terminal events, waiter leases/disconnect/uncertain output, partial-task notification ownership, byte-bounded pushes, retained terminal waits and stale generations |
| Existing manager/naming regressions | **PASS — 23 tests** | Existing launcher, naming shortcuts and manager/scheduler preservation; simulated candidate failure/activation use temporary fixtures |
| Installed native MCP/PTY regression suite | **PASS** | Existing CodexMCP `smoke.py` against immutable package, isolated homes and local fake model: runtime disable/re-enable, persistence/resume, plugin/owner overlays, declarations and TUI at 80/60/40 columns |
| Installed passive | **PASS within recorded scope** | CLI/schema/catalog fingerprints, no-op second install, auth/config preservation, empty native session names and ended owned owners. Empty Codex TUIs defer SessionStart registration to their first turn. |
| Vendor inference/live | **Qualified results below** | 48 submitted inputs, including failed/diagnostic attempts; no further submissions authorized |

The current Python command `.venv/bin/python -m pytest -q` passed **60 tests and 16 subtests** in 4.30 seconds. The preceding 42-test record and manager/native-package regressions remain historical; unchanged manager/package checks were not repeated during the review fixes. Current installed help and installation dry-run pass with no pending operations. Authentication integrity, provider binary hashes, daemon configuration and the protected manager worktree match their recorded state. No production config files changed during these fixes. [Current verification](../evidence/reviews/fix-verification.json), [earlier regression record](../evidence/regressions.json).

## Recorded live runs before review fixes

The table below preserves the original run outcomes. In [current acceptance](../evidence/acceptance.json), affected successful checks are now **NOT_RUN** against the review-fix implementation, with their previous outcome retained in `recorded_status`. The four previously blocked variants remain **BLOCKED**. LIVE-17 retains its qualified preservation result because the protected native manager and binaries are unchanged. No live check was rerun, and the exhausted ledger was not reset.

| Check | Status | Actual evidence or remaining assertion |
|---|---|---|
| LIVE-01 | **PASS** | Claude request to original Codex, correlated return to the original Claude UUID |
| LIVE-02 | **PASS** | Independently initiated Codex exchange with Claude and exact return |
| LIVE-03 | **PASS** | Distinct native Codex sibling IDs exchanged a finding through their controller/native turns |
| LIVE-04 | **PASS** | Distinct native Claude sibling IDs exchanged a finding through serialized exact-ID native turns |
| LIVE-05 | **PASS** | Request plus three successive replies; immediate authors, exchange and final no-reply intent preserved |
| LIVE-06 | **PASS** | Two outstanding requests answered in reverse order with distinct correct exchanges |
| LIVE-07 | **PASS, recorded run fingerprints** | Actual Claude parent tool launched two Sol/xhigh workers; native working intervals overlap; attributed results arrived at its original UUID |
| LIVE-08 | **PASS, recorded run fingerprints** | Actual Codex parent tool launched two Claude workers; distinct UUIDs/concurrent native intervals; attributed results arrived at its original UUID |
| LIVE-09 | **BLOCKED** | Both same-provider pairs really ran concurrently. Claude received its same-provider results. Codex's initial bounded waits expired; its same-provider results are retained, but an original-parent result read was not observed before the ceiling. |
| LIVE-10 | **BLOCKED** | Busy Claude messages were natively queued/attached and processed. The human-draft noninterference variant remains NOT_RUN. |
| LIVE-11 | **PASS** | Idle original parents consumed native completion pushes |
| LIVE-12 | **BLOCKED** | Duplicate/stale contracts pass and empty native naming is preserved. Duplicate *registered live* target assertions remain NOT_RUN; zero-input Codex TUIs are not registered until native startup hooks run. |
| LIVE-13 | **PASS** | Four owned histories retained exact native UUIDs across new incarnations; subsequent sibling replies reached resumed owners |
| LIVE-14 | **PASS within observed scope** | Native-started owned Codex follow-up canceled and released. Claude owned cancellation initially reported provider failure; final labeling correction is unit-tested, not live-repeated. |
| LIVE-15 | **PASS** | Explicitly granted sibling findings exchanged on both providers; initial results remain separate |
| LIVE-16 | **BLOCKED** | Worker-local launch/MCP/plugin isolation and direct native transport observed. Strict absence/unreachability of Herdr/T3 was not demonstrated; unrelated active services were preserved. |
| LIVE-17 | **PASS within regression scope** | Existing native manager/naming/launcher/MCP regressions, semantic configuration/auth checks and native history retention |
| LIVE-18 | **PASS within observed scope** | Original-parent push, actual current-tool wait outputs, timeout rearming push, and no duplicate generation completion admission |

[Machine-readable acceptance](../evidence/acceptance.json) and [native event/identity evidence](../evidence/final-live.json) retain source dependencies and qualifications. Original run evidence is not silently rehashed into proof of every later code path.

## Original-parent and concurrency evidence

The first Claude parent UUID was `e1c55334-58c7-4926-9c31-63e98df094d8`. Its actual tool spawned group `30e9f254-e399-4a91-8038-62db9d39b191`, with distinct Codex worker UUIDs `01a15cf8-8a32-7991-8909-63d8adcaae82` and `01a15cf8-8a47-7462-a609-dd7f959751ed`. Native task starts overlapped before either native terminal event. The same Claude UUID ingested the controller completion and reported each assigned nonce and finding.

The first successful Codex parent UUID was `01a15cf9-7e4e-7ba3-b3a7-74e6ea304143`. Its actual tool spawned group `15fded49-7da6-4831-bc11-bd7b8a9dda8b`, with distinct Claude UUIDs `51138894-fab3-4a18-834d-f3c0072b318e` and `a425360c-afb1-4ba6-8ab6-07df404edb36`. Both native tasks were in flight concurrently; the original Codex UUID received and attributed both results. This is client-side native work overlap, not a claim about vendor GPU scheduling. [Detailed first runs](../evidence/early-delegation.json).

Later four-worker groups also contain genuine concurrent cross-provider and same-provider pairs. Actual resumes kept four worker UUIDs, generated new incarnations and used the corrected scoped MCP profiles for real sibling calls. The separate initial and follow-up task/result records are retained. [Final groups and resume identities](../evidence/final-live.json).

## Preservation, cleanup and continuation

Final protected comparison preserved the shell function/YOLO shortcuts, managed current package and patches, updater/LaunchAgent, configured model/effort/features, MCP definitions and enabled/default-valued states, existing hooks and authentication. Authorized changes are only the owned lifecycle handlers, their native trust entries, two owned skills and `npeer` entrypoint. `.claude.json` has legitimate native cache/trust updates and was retained. No provider histories or original worktrees were edited/reset/cleaned. [Installation](../evidence/installation.json), [targeted rollback](INSTALLATION.md).

All 48 submitted inputs are settled: **0 reserved, 0 unresolved native attempts**. Active live testing was the union of admitted intervals: **756.276 seconds (12 minutes 36.276 seconds)**, including native waiting and excluding offline work. The eight-worker ceiling remained enforced. Provider internal observations/caps are recorded separately; no subscription-dollar estimate is invented. [Ledger](../evidence/live-budget.json).

All four finite group supervisors and 12 owned worker controllers/native writers exited. Seven registered disposable interactive owners and the separately reconciled failed unbound fixture owner were ended by exact owned-process cleanup. The zero-input naming fixture owners also ended. Native histories/results remain available. Supported exact `thread/queue/list` checks found no residual queued items; nothing required deletion. No unrelated process or service was signaled. [Queue settlement](../evidence/queue-settlement.json), [cleanup](../evidence/cleanup.json).

Additional vendor-input acceptance requires an explicit increase to the exhausted live allowance. The smallest retained-result continuation is to resume the original Codex fixture under its existing isolated state, then ask its actual native tool to read group `18d29e2b-c2a6-4ae0-8d44-eeb1cf548833` and report the initial Codex worker results. The exact launch command, previously blocked variants and affected checks needing fresh evidence are in [checkpoint](../evidence/checkpoint.json). Do not reset the ledger or replay uncertain inputs. The command remains installed with the current fixes; ordinary native inbound/tool policy can still hold, refuse or prompt. The implementation and review fixes are published to the public [scalinity/NativePeers repository](https://github.com/scalinity/NativePeers); `main` tracks `origin/main`.

Canonical SHA-256 values remain:

```text
IMPLEMENTATION.md  615eb40eafbc5d875373c2b173bce77f450d31444543b27d56d7b6ea9cb8160e
KICKOFF_PROMPT.md  18de5e0d3230bba5bab4a380108c3a8cd420d4ddc281936e4104d89138841141
REVIEW.md          625e8ec453a673e1c616bc359cdba269741e721f685031389ddc65586d2778e1
```
