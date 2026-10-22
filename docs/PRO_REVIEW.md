# Independent Pro review corrections

The seven confirmed findings from the independent review of `b5c51f9`, both portability issues and five scoped improvements are addressed. A further controller-path defect was found while integrating the recipient-lock fix: definitely unsubmitted completion notices also needed safe retry and reservation reuse. All 15 items have separate verified commits. [Machine-readable results](../evidence/pro-review.json).

| Item | Resolution | Commit |
| --- | --- | --- |
| G01 | Retry definite pre-admission lock contention | `1864bd2` |
| G01F | Retry definitely unsubmitted controller completions with one reservation | `7de9889` |
| G02 | Settle accepted successors on lifetime shutdown | `57261f3` |
| G03 | Claim terminal wait completion ownership | `dc0584e` |
| G04 | Classify delivery errors consistently for MCP and CLI | `73a7400` |
| G05 | Freeze selected untracked baseline inputs | `08c6fdf` |
| G06 | Pin provider homes throughout worker lifetime | `f75c224` |
| G07 | Filter unread messages before limiting | `968757b` |
| T01 | Portable Gemini shell fixture | `5b95850` |
| T02 | Portable bounded queue-helper fixtures | `ba35e25` |
| S01 | Expose exact message reconciliation | `d39e545` |
| S02 | Retain worker-only change delta | `2096db2` |
| S03 | Retain optional structured worker reports | `1bf7b92` |
| S04 | Diagnose compatibility in doctor | `f15bbfd` |
| S05 | Import bounded selected context artifact | `ee26abf` |

The final offline gate passed **165 tests and 149 subtests** on Python 3.14.8. Existing isolated CodexMCP manager/naming regressions passed **23 tests**. Linux execution was not performed locally; the two reviewed fixture dependencies on an installed Mac executable/interpreter were removed.

Final-source native checks used managed Codex **0.163.0**, `gpt-6.1-sol` with requested `xhigh`, and official Antigravity **1.4.5**, `gemini-3.8-flash-medium`. Both original parent conversations received separately attributed results from distinct overlapping native workers. The Codex parent also verified generated code and worker-only deltas, resumed the same Gemini UUID, and received a separately ingested native queue completion in that original Codex UUID. The provider does not independently echo Codex inference effort.

Claude **2.1.308** was checked passively; fresh Claude inference is **BLOCKED** by the reported usage limit. Earlier Claude and full-matrix native observations retain their original source/runtime scope. A clean all-provider unattended reliability verdict is not claimed.

The fixture loader initially omitted `__file__`; retained results were reused after correcting it. One untrusted Antigravity fixture launch was exited without changing trust and conservatively counted with zero observed inference. A final Codex tool invocation selected the wrong test journal because a custom environment variable was not forwarded; the same parent continued with the directory explicit and ordinary caller checks intact. No identity or token was supplied.

Protected configuration and auth-file hashes are compared at the task boundary. Native Codex added one disposable project-trust entry; cleanup removes only that exact owned entry under the manager lock and requires reconstruction of every original byte. All owned native processes and exact queues are checked at close-out. Tiny dirty fixture worktrees are retained for exact native history resume, with checksum-verified private backups.

Ordinary use gains `status MESSAGE_ID --refresh`, `artifact-add --file`, passive compatibility diagnostics, per-worker home pinning, optional worker-reported findings/checks and a worker-only patch delta alongside the reconstruction patch. [Commands and boundaries](USAGE.md). No Codex inbox patch, new service, provider replacement or authentication change was needed.
