# Native CLI updates

A different executable hash means **untested**, not a demonstrated protocol failure. NativePeers keeps exact executable approval for admission and immutable leases, with a bounded renewal for supported vendor patch updates that runs automatically on first use and on demand.

```sh
npeer doctor --json
npeer runtime-check --provider claude --json
npeer runtime-check --provider claude --apply --json
npeer runtime-check --provider antigravity --apply --json
npeer runtime-check --provider grok --apply --json
```

Inspection leaves receipts unchanged and uses one private temporary executable copy, removed on completion. `--apply` checks and records selected worker/inbox admission in one command; no hash copying, reinstall, model input, usage refresh or unrelated-session restart is required. It runs only fixed `codesign`, `--version` and `--help` commands with closed stdin. Native metadata runs under the macOS no-network, no-file-write guard. Each command has a five-second deadline and metadata is limited to 256 KiB. No prompt, slash command, login, trust or permission question is answered.

## Automatic renewal on first use

A vendor patch update needs no command. The first admission check that finds the selected Claude, Antigravity or Grok executable different from its approval (a send to a native peer, a worker or batch admission, a worker's first verification at spawn or resume, the parent-return readiness in `whoami`) runs the same bounded check as `--apply` once and continues. A person runs `runtime-check` only to inspect, or after a refusal.

- **Same envelope, same refusals.** Signature, supported release series, strictly newer patch, required options and adapter markers are checked on one private snapshot, exactly as below. A refusal (`INCOMPATIBLE`, `PROVENANCE_UNVERIFIED`, `QUALIFICATION_REQUIRED`) is recorded for those exact bytes and is not retried; the original `CAPABILITY_MISMATCH` and its detail are returned. Revoked and retired runtimes never renew.
- **Only the selected runtime.** An explicit executable path other than the provider's selected one, a verification against a worker's already pinned runtime and `verify(..., renew=False)` stay strict. Managed Codex never renews this way.
- **Transient failures back off.** A check that could not complete (`CHECK_UNAVAILABLE`, `UPDATE_RACED`) is not repeated for the same bytes for 60 seconds (`compat/auto-renew.json`); the refusal is returned meanwhile.
- **One renewal at a time.** `locks/auto-renew.lock` serializes concurrent callers (30 second wait); a caller that waited sees the renewal and proceeds.
- **Passive views stay passive.** `doctor` and `runtime-check` without `--apply` write nothing. Quota and status-line receipts are never touched.
- **Cost.** One bounded check (a copy of the executable, `codesign`, `--version`, `--help`, a marker scan), on the first use after an update only.

`compatibility.AUTO_RENEW` is a code constant, switched off by the offline tests that assert strict verification.

## Why approvals fall behind

| Provider | Update and selection | Renewal boundary |
|---|---|---|
| Claude | Native updater changes the launcher symlink to a versioned executable. The worker lease records the resolved target. | Vendor-signed forward patches in 2.1, starting with qualified 2.1.310. |
| Antigravity | Native update replaces `agy`, including its bytes at the same path. An old lease cannot execute replacement bytes as its old runtime. | Google-signed forward patches in 1.4, starting with qualified 1.4.5. |
| Grok | Native launcher selects a versioned download. Owned turns pin the resolved target and preserve the existing child-only auto-update controls. | xAI-signed forward patches in 1.0, starting with qualified 1.0.70. |
| Managed Codex | The existing manager activates an immutable package containing local queue/history patches. | Manager qualification of the affected queue, hook and worker-history seams remains required; vendor version/help cannot establish local patch compatibility. |

`compat/runtime.json` stores exact executable approvals. A selected update whose bytes differ from that receipt fails `compatibility.verify` until the bounded check or affected-seam qualification establishes its approval. Reinstall preserves existing approvals and cannot repair that difference. Status-line approval in `compat/usage-runtime.json` and quota transport approval in `compat/usage-sources.json` are separate; neither certifies workers or inbox delivery.

## Bounded support checks

The CLI options and embedded spellings are compatibility canaries, not semantic contract proofs. Routine renewal requires an existing qualified approval, the same supported major/minor release series, a strictly newer patch version with no conflicting version triples, an intact Apple code signature with the expected vendor team and executable identifier, required CLI options, and the adapter's named protocol/permission markers in the signed executable. The check makes one bounded-size private copy of the selected bytes, verifies its digest, and runs signature and metadata checks against that same snapshot. It also hashes the mapped bytes used by the marker scan. The copy is never an installed runtime or a provider-home overlay and is deleted after the check. An unsigned build, retired or revoked hash, same-version rebuild, new release series, missing option/marker or unavailable inspection does not receive routine approval. Initial installation and managed Codex qualification retain their separate paths.

This is an explicit adapter support policy. Signatures establish publisher provenance; versions bound the policy; options and markers detect missing surfaces. Their presence does not prove every implementation detail or vendor behavior is unchanged. Runtime initialization, native tool inventories, exact hook/process observations and kernel permission checks still enforce the existing operation boundaries at execution. A signature, help text or marker match is never reported as live worker execution, original-parent ingestion or bound MCP acceptance. New native behavior outside this envelope requires affected-seam qualification, including explicitly authorized model input only when necessary. Review the envelope when adapting to a new release series.

The selected launcher is freshly hashed after checking, inside the guarded write, and after writing. The runtime lock compares the complete prior provider approval and rereads the whole document, preserving another provider's concurrent renewal. A concurrent selected-runtime or prior-approval change refuses the stale transition. If an independent updater races after the write, the command reports `UPDATE_RACED` and `approval_written:true`; the new unknown selected bytes remain refused. It never rolls the entire receipt back over another renewal.

Existing retained approvals remain limited to exact previously verified worker leases and their immutable path/hash/size. New work uses the selected runtime. Retired hashes cannot be readopted through routine renewal, revoked hashes cannot be renewed, and a replacement at an old mutable path cannot inherit its lease. Running workers are not retargeted. Authentication, provider homes, implicit/explicit Claude configuration layout, native permissions, hooks and launchers are unchanged.

## Read the operation and reason

Existing `CAPABILITY_MISMATCH` error codes and legacy native-qualified `PASS`/`APPROVED` states are unchanged. Bounded receipts introduce the additive `SUPPORTED_UPDATE` state in doctor/readiness and `compatibility_state`; ordinary preflight still admits their exact worker/inbox fingerprint. Such receipts and operation rows explicitly retain `native_acceptance: NOT_RUN`. `reason` and `next_action` explain the difference:

| Detail | Meaning and smallest action |
|---|---|
| `UPDATE_UNTESTED` | Selected bytes differ. The next use renews automatically when the bounded checks pass; this detail remains only for passive views or after a transient failure, and the named command repeats the check. Not an observed incompatibility. |
| `SUPPORTED_UPDATE` | Bounded canaries passed. Inspection has `approved:false`; after `--apply`, `approved:true` means worker/inbox admission and the state remains `SUPPORTED_UPDATE`, with native behavior `NOT_RUN`. |
| `APPROVED` | The exact selected runtime matches a legacy qualified receipt; its recorded native scope still limits the claim. |
| `INCOMPATIBLE` | A required option or adapter marker was observed absent. Preserve the approval and qualify/adapt the affected seam. A failed `--apply` records this observation, keyed to the exact fingerprint, for later passive doctor output. |
| `PROVENANCE_UNVERIFIED` | Vendor code-signature requirement failed. Inspect the selected installation. |
| `QUALIFICATION_REQUIRED` | Initial/retired runtime, same-version rebuild, ambiguous banner, unsupported release series, downgrade or managed package needs the existing affected-seam qualification. |
| `REVOKED` | Exact runtime was explicitly withdrawn. Routine renewal cannot override it. |
| `CHECK_UNAVAILABLE` | The bounded inspection could not run. This says nothing about sign-in or quota. |
| `UPDATE_RACED` | Executable or approval changed during the check; inspect the current selection and retry the check. |

Completed qualification refusals from `--apply` remain visible to passive doctor, keyed to the checked fingerprint, without granting admission. Missing executables report `EXECUTABLE_NOT_FOUND`; inspection access failures report `CHECK_UNAVAILABLE`. Authentication failures, stale quota readings and actual quota exhaustion keep their existing distinct errors and states. Unknown or stale quota cannot independently disable otherwise approved worker/inbox operations. A failed check does not send model input or repair native settings.

Worker/inbox renewal never writes quota/status-line receipts. Quota mismatch reports `operation:quota-read` with its own inspection command; Claude PTY authorization is still explicit, and Antigravity backend freshness remains separately qualified. MCP requires a bound native caller and effective inventory. Parent return uses its own passive route check and exact delivery evidence; a successful renewal does not manufacture ingestion or replay uncertain input.

Offline tests use fake executables and temporary journals. Native read-only check results, native model/permission/return checks and daily approval transitions are recorded separately in [verification](VERIFICATION.md). A source PR does not change the installed main checkout or daily receipts.

The public CLI surfaces are documented in the [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) and [Antigravity headless reference](https://www.antigravity.google/docs/cli/headless/). These document interfaces, not a promise of unchanged private inbox or permission semantics across every patch. The local support envelope and exact measured results remain separate.

## Managed Codex quiet queue qualification

Issue #7 qualifies `/Users/danny/.local/share/codex-mcp-manager/releases/rust-v0.166.0-763301510afa-1793404941146559000/bin/codex` (SHA-256 `467acfa394f32091c032d763c2bb0cdbb58bc44d54bdf44ab00f229e72a1ac10`) through normal CodexForge assembly, guarded selection and expected-old worker/inbox approval. Fresh standalone TUI qualification covers44 mixed frames, exact resume, reopen and permitted controls; three actual native worker/controller phases separately cover active push and quiet suppression. Quota metadata has its own check and is not used as worker/inbox approval. Existing immutable leases remain pinned; new work selects current. Commands, exact source scope, preserved predecessor and guarded rollback are in [verification](VERIFICATION.md).

## Managed activation and live recipients

Managed Codex qualification can require the candidate selected for real hook ancestry.
Selection and approval remain distinct. New worker launches verify the selected build;
an unqualified selected build remains refused before model input. Its next action is to
wait for the CodexForge activation owner and retry the unchanged request, never to apply
an approval as an ordinary sender.

A live Codex inbox uses its registered owner's exact executable for both verification
and queue launch. The NativePeers runtime lock covers fresh verification through helper
creation, so a changed selection cannot redirect the helper. Each queue receipt names
the executed fingerprint. Guarded approval replacement retains the old exact runtime
only for live recipient handle/incarnation/owner records observed at that transition,
beside existing verified worker leases. A later incarnation, modified/missing executable
or revoked approval cannot borrow that permission. No selected-runtime fallback exists.

A definitely unsubmitted Codex runtime refusal uses `ADMISSION_DEFERRED`. Retry the
original command with identical target, body, options and client request ID after
qualification. It reuses the original message/attempt and expiry, checks the original
recipient incarnation, and delivers once. This is caller-driven recovery, not a new
polling service. Accepted/uncertain attempts remain inert. Quiet-policy `RETAINED`
remains permanently unsubmitted and is never replayed. Status exposes the original
runtime cause; doctor separates selected-build readiness from live recipient routes.
