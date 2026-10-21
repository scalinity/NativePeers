# NativePeers — Review and Revision Report

**Review date:** 2026-10-21 (UTC)  
**Revised contract:** `IMPLEMENTATION.md` revision 1.1  
**Revised execution prompt:** `KICKOFF_PROMPT.md` revision 1.1

## Verdict

**Approve the revised documents for gated implementation. Do not interpret this as runtime acceptance.**

The native-first architecture is retained: native conversation identity and history, a symmetric routing layer, native provider worker backends, finite delegation supervisors, and a conditional managed Codex inbox patch. Neither Herdr nor T3 becomes a dependency. Both cross-provider two-worker delegation directions remain mandatory.

The review addressed the earlier critique and additional internal contract gaps. All changes are integrated into the complete canonical files; no separate addendum or manual patch is needed. The original 26 specification sections, 11 kickoff sections, six implementation milestones, and 18 LIVE acceptance scenario IDs remain. Milestone sequencing now favors early executable integration and a thin end-to-end delegation path rather than postponing important failures until the end.

This report explains the changes. It does not independently add requirements beyond the two canonical documents.

## Basis and scope

The requested basis was the full uploaded `IMPLEMENTATION(3).md` and `KICKOFF_PROMPT(3).md`, plus the preceding review critique. Both attached files were reviewed in full. Their original design, terminology, feature scope and level of detail were preserved except where the changes below explicitly resolve a conflict or missing execution contract.

| Input | SHA-256 |
|---|---|
| `IMPLEMENTATION(3).md` | `778f6dcf07bcde2f031051a6eb22d4cf41abe3aecc01d3bd4cd2bca63cd60a16` |
| `KICKOFF_PROMPT(3).md` | `1ed50c27cdcf6c920ca66aaacf8d1bf95bd73e3920afa270c0372cb14f3fe336` |

I also selectively rechecked four official references, identified below. This was not a fresh audit of every repository in the inherited source register. No user's Mac, local CodexMCP checkout, provider authentication, native conversations, running service, or model execution was accessed or tested in this review. Only the uploaded documents and generated files in the working container were processed.

“Resolved” below means **resolved in the specification and kickoff contract**, not fixed and proven in a running implementation. Severities describe risk to successful implementation; no runtime bug is asserted in software that has not yet been built.

## Findings and integrated revisions

| ID | Priority | Source-derived concern | Revision 1.1 resolution | Main locations |
|---|---|---|---|---|
| R01 | Medium | “Native,” “implementation ready,” and model/source evidence could be read more broadly than the actual contract. The public tool name also appeared as both `spawn_worker` and `worker_spawn`. | Defined native-backed tool/skill integration rather than vendor-native subagent UI; standardized `worker_spawn`; distinguished design approval from runtime PASS; separated carried research from fresh checks and requested model identity from proven entitlement. | Spec §§1–2, 11, 17, 26; kickoff §§1, 4, 11 |
| R02 | Medium | Terminal independence could be mistaken for guaranteed private Desktop/T3 or multi-machine ingress. Installed hooks are also not proof that unchanged receiver policy permits unattended messages. | Preserved required core CLI support, explicitly conditional external hosts, and the existing local-only v1 scope. Required installation and everyday-policy capability to be reported separately. | Spec §§3, 8, 18; kickoff §§1, 5, 9 |
| R03 | High | A one-shot run had no sufficiently concrete continuation/checkpoint contract. Compaction or restart could cause lost evidence, repeated work, or a reset usage allowance. | Added `PROGRESS.md`, a machine-readable checkpoint, retained protected baselines, source/runtime fingerprints, unsettled native requests, remaining allowance, and an exact next step. Defined safe continuation and selective evidence invalidation. | Spec §§5, 19–22, 24–25; kickoff §§3, 8–11 |
| R04 | High | Caller identity, detached-helper survival and original-parent return were described but not all proven at the earliest gate. Requiring a finished route before implementing its already-authorized fallback could create a circular dependency. | Added five executable M1 gates: identity, ingress, supervisor survival, original-parent return, and worker bootstrap. Permitted tiny scoped probes and a narrow tested M1B fallback before the full product, while preventing a paper fallback from being counted as a PASS. | Spec §§8–10, 20–21; kickoff §5 |
| R05 | High | Native turn-process lifetime, logical worker ownership, initial native-ID availability and controller-origin completion authority were not fully separated. Inherited hooks could duplicate registration or unregister a serialized Claude worker between turns. | Added two-phase inactive-to-bound bootstrap, nonblocking hook/MCP startup, controller-owned routing across turns, explicit group-controller actors, and launch-local environment boundaries. Parent liveness is tied to its real owner/lease, not a transient shell or finished worker turn. | Spec §§6, 7, 10–12; kickoff §§4, 6–8 |
| R06 | High | The original text labeled an invocation with no session binding as operator origin. Missing model environment metadata must not itself confer human authority. | Changed it to non-privileged unbound local origin, with no invented native reply address, approval or elevated control. Kept the explicit limit that same-UID unrestricted processes are not isolated from each other. | Spec §§6–7, 22; kickoff §6 |
| R07 | Medium | A 64-KiB wire limit and 256-KiB result pages lacked separate transport bounds. Several example/interface values and error labels were inconsistent. | Distinguished peer frames from larger bounded local result responses, clarified pagination, used syntactically valid UUID/timestamp examples, added missing named error outcomes and aligned tool/signature spelling. Examples remain illustrations, not execution evidence. | Spec §§7, 12, 16–17, 20, 22 |
| R08 | High | A waiter could claim completion and then time out or disconnect; the contract did not say when push becomes eligible again. Result-output uncertainty also needs different handling from a clean pre-output timeout. | Added bounded waiter leases, explicit push/retain fallback, scoped completion generation, uncertain-output reconciliation, idempotent result reads and race tests. A timed-out wait cannot own future delivery forever, and uncertain output cannot trigger an automatic duplicate. | Spec §§7, 12, 17, 20, 22; kickoff §§7–9 |
| R09 | High | Held, unknown and expired states were too easy to treat as terminal. Local timeout after a native queue accepts input does not prove that input can never execute. | Separated pending hold, reconcilable uncertainty, pre-submission expiration and post-admission wait expiration. Required honest native-expiry/cancellation capability reporting and exact-item native reconciliation. | Spec §§7–9, 13, 16, 20; kickoff §§6, 9 |
| R10 | High | Multi-hop reply addressing needed an immediate-message rule, and resolving a mutable title before checking a retry's idempotency record could change its target. The send pseudocode also omitted the previously required interprocess admission lock. | Replies verify the caller is the recorded recipient and target the referenced message author; explicit continuation preserves exchange identity. Retries consult the existing request before new name resolution. Pseudocode now includes the admission lock and uncertainty checks. | Spec §§7, 17, 20, 22; kickoff §6 |
| R11 | Medium | Catalog preflight is not entitlement proof; one first request can fail after a sibling starts. Readiness/capacity and concurrency evidence needed more precise boundaries. | Defined partial launch failure under `collect-all`, inactive identity readiness, safe idle-grace reclamation and capacity refusal. Required overlapping native task intervals and real results without claiming visibility into vendor GPU scheduling. | Spec §§10–11, 20–22; kickoff §§7, 9 |
| R12 | High | The live allowance could be exhausted by probes or edge cases before demonstrating the core two-worker use case. Parent wakeups and child/reply turns could be omitted from accounting. | Kept the original 48-turn / eight-worker / 60-minute ceilings, added a durable 10/16/16/6 planning allocation, reserved core delegation early, counted induced receiving turns, and defined active-test time and restart behavior. Required exact evidence sharing rather than weaker tests. | Spec §§13, 20–21; kickoff §9 |
| R13 | Medium | Strict file-hash preservation can conflict with legitimate credential refresh, concurrent user edits or the managed updater. Restoring an old baseline blindly would be dangerous. | Separated installer mutations from legitimate runtime changes, required reconciliation and compare-and-apply, prohibited credential rollback to force hash equality, and kept private evidence out of source commits. | Spec §19; kickoff §§3, 8 |

## Earlier critique: disposition

The prior critique requested earlier caller-binding/supervisor/return probes, clarity about native-backed versus vendor-native tools, explicit host support boundaries, durable execution checkpoints, and protection of the live-test budget for core delegation. Those points are now normative in both files, rather than appended as a disconnected instruction block.

The architecture was not rewritten merely because it is ambitious. No central broker, new UI, remote service, orchestration model, extra provider account, or unconditional Codex patch was added. The additional actor/state/lease rules clarify responsibilities the original design already needed.

## Selective outside-source verification

These are outside checks, distinct from findings derived from the uploaded documents.

1. **Codex `rust-v0.163.0` queue command:** the pinned source contains the explicit no-daemon/server checks, generates a client message ID and submits `thread/queue/add`. This supports separating sender command validation from recipient execution. It does not establish the behavior of the user's managed binary.  
   https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/tui/src/session_queue_commands.rs

2. **Codex `rust-v0.163.0` queue service:** the pinned source contains the external-change watcher and ten-second interval. This supports testing the native route before patching; it is not universal evidence of live delivery under every process topology.  
   https://raw.githubusercontent.com/openai/codex/rust-v0.163.0/codex-rs/ext/queue/src/service.rs

3. **Claude hooks reference:** consulted for the hook/environment and background-process integration surface. The revised design still requires executable parent-binding and tool-runner-survival tests on the installed version.  
   https://code.claude.com/docs/en/hooks

4. **Claude headless reference:** consulted for structured noninteractive execution, native continuation and process-exit/background-work considerations. The document does not turn these public primitives into a claim that every local authentication/profile combination has passed.  
   https://code.claude.com/docs/en/headless

All other inherited references remain in the specification for implementation-time validation; this review does not silently promote them to fresh verification.

## Validation performed on the reproduced files

The accompanying `VALIDATION.json` records the exact static checks and file hashes. These checks cover document structure and fence closure, parseable JSON examples and selected example invariants, required gates/acceptance IDs, synchronized limits and terminology, source-register references, protected-environment requirements, and the presence of the integrated fixes in both files where applicable.

This is **document validation**, not unit testing or acceptance testing of NativePeers. NativePeers has not been implemented or run by this review. No provider usage was consumed, no commands were run on the user's Mac, and no user configuration was modified.

## Remaining implementation gates

The actual managed Codex queue route, Claude inbox wire/policy, caller identity exported by each installed harness, supervised process survival, scoped worker tools, exact requested model/effort entitlement and original-parent result delivery remain local implementation-time questions. Revision 1.1 moves them to the earliest useful executable gates instead of pretending the documents prove them.

The 48-turn allowance is preserved, not guaranteed sufficient for every failure/retry pattern. A valid run can support multiple assertions, but the implementing agent must not weaken acceptance, substitute a cheaper model or silently increase the allowance. If a hard live boundary is reached, it must retain completed implementation and evidence, finish independent non-model work, and report the exact remaining gate.

## Using the revised pair

Place `IMPLEMENTATION.md` and `KICKOFF_PROMPT.md` together in the dedicated NativePeers project directory. Keep `REVIEW.md` alongside them as rationale if useful. Start the existing managed Codex from that project directory and paste the complete revised kickoff. No earlier addendum is required.

The intended outcome remains unchanged: Claude and Codex as independently running, native-backed peers, each able to spawn real workers from the other provider and receive their results in the original conversation.
