# Native CLI updates

A different executable hash means **untested**, not a demonstrated protocol failure. NativePeers keeps exact executable approval for admission and immutable leases, with a bounded on-demand renewal for supported vendor patch updates.

```sh
npeer doctor --json
npeer runtime-check --provider claude --json
npeer runtime-check --provider claude --apply --json
npeer runtime-check --provider antigravity --apply --json
npeer runtime-check --provider grok --apply --json
```

Inspection is read-only. `--apply` checks and records the selected worker/inbox approval in one command; no hash copying, reinstall, model input, usage refresh or unrelated-session restart is required. It runs only fixed `codesign`, `--version` and `--help` commands with closed stdin. Native metadata runs under the macOS no-network, no-file-write guard. Each command has a five-second deadline and metadata is limited to 256 KiB. No prompt, slash command, login, trust or permission question is answered.

## Why approvals fall behind

| Provider | Update and selection | Renewal boundary |
|---|---|---|
| Claude | Native updater changes the launcher symlink to a versioned executable. The worker lease records the resolved target. | Vendor-signed forward patches in 2.1, starting with qualified 2.1.310. |
| Antigravity | Native update replaces `agy`, including its bytes at the same path. An old lease cannot execute replacement bytes as its old runtime. | Google-signed forward patches in 1.4, starting with qualified 1.4.5. |
| Grok | Native launcher selects a versioned download. Owned turns pin the resolved target and preserve the existing child-only auto-update controls. | xAI-signed forward patches in 1.0, starting with qualified 1.0.70. |
| Managed Codex | The existing manager activates an immutable package containing local queue/history patches. | Manager qualification of the affected queue, hook and worker-history seams remains required; vendor version/help cannot establish local patch compatibility. |

`compat/runtime.json` previously retained only the old fingerprint. The selected new binary therefore failed `compatibility.verify` before normal delegation. Reinstall preserves existing approvals and cannot repair that difference. Status-line approval in `compat/usage-runtime.json` and quota transport approval in `compat/usage-sources.json` are separate; neither certifies workers or inbox delivery.

## Bounded support checks

Routine renewal requires an existing qualified approval, the same supported major/minor release series, a forward patch version, an intact Apple code signature with the expected vendor team and executable identifier, required CLI options, and the adapter's named protocol/permission markers in the signed executable. The check reads the selected binary, not a downloaded substitute. An unsigned build, retired or revoked hash, new release series, missing option/marker or unavailable inspection does not receive routine approval. Initial installation and managed Codex qualification retain their separate paths.

This is an explicit adapter support policy. Signatures establish publisher provenance; versions bound the policy; options and markers detect missing surfaces. Their presence does not prove every implementation detail or vendor behavior is unchanged. Runtime initialization, native tool inventories, exact hook/process observations and kernel permission checks still enforce the existing operation boundaries at execution. A signature, help text or marker match is never reported as live worker execution, original-parent ingestion or bound MCP acceptance. New native behavior outside this envelope requires affected-seam qualification, including explicitly authorized model input only when necessary. Review the envelope when adapting to a new release series.

The selected launcher is freshly hashed after checking, inside the guarded write, and after writing. The runtime lock compares the complete prior provider approval and rereads the whole document, preserving another provider's concurrent renewal. A concurrent selected-runtime or prior-approval change refuses the stale transition. If an independent updater races after the write, the command reports `UPDATE_RACED` and `approval_written:true`; the new unknown selected bytes remain refused. It never rolls the entire receipt back over another renewal.

Existing retained approvals remain limited to exact previously verified worker leases and their immutable path/hash/size. New work uses the selected runtime. Retired hashes cannot be readopted through routine renewal, revoked hashes cannot be renewed, and a replacement at an old mutable path cannot inherit its lease. Running workers are not retargeted. Authentication, provider homes, implicit/explicit Claude configuration layout, native permissions, hooks and launchers are unchanged.

## Read the operation and reason

Existing `CAPABILITY_MISMATCH` error codes and doctor `state` fields remain compatible. Additive `compatibility_state`, `reason` and `next_action` fields explain the difference:

| Detail | Meaning and smallest action |
|---|---|
| `UPDATE_UNTESTED` | Selected bytes differ; run the named bounded check. This is not an observed incompatibility. |
| `SUPPORTED_UPDATE` | Read-only checks passed; use `--apply` to renew worker/inbox approval. |
| `APPROVED` | Exact selected runtime is approved; this is not a live acceptance claim. |
| `INCOMPATIBLE` | A required option or adapter marker was observed absent. Preserve the approval and qualify/adapt the affected seam. A failed `--apply` records this observation, keyed to the exact fingerprint, for later passive doctor output. |
| `PROVENANCE_UNVERIFIED` | Vendor code-signature requirement failed. Inspect the selected installation. |
| `QUALIFICATION_REQUIRED` | Initial/retired runtime, unsupported release series, downgrade or managed package needs the existing affected-seam qualification. |
| `REVOKED` | Exact runtime was explicitly withdrawn. Routine renewal cannot override it. |
| `CHECK_UNAVAILABLE` | The bounded inspection could not run. This says nothing about sign-in or quota. |
| `UPDATE_RACED` | Executable or approval changed during the check; inspect the current selection and retry the check. |

Missing executables report `EXECUTABLE_NOT_FOUND`. Authentication failures, stale quota readings and actual quota exhaustion keep their existing distinct errors and states. Unknown or stale quota cannot independently disable otherwise approved worker/inbox operations. A failed check does not send model input or repair native settings.

Worker/inbox renewal never writes quota/status-line receipts. Quota mismatch reports `operation:quota-read` with its own inspection command; Claude PTY authorization is still explicit, and Antigravity backend freshness remains separately qualified. MCP requires a bound native caller and effective inventory. Parent return uses its own passive route check and exact delivery evidence; a successful renewal does not manufacture ingestion or replay uncertain input.

Offline tests use fake executables and temporary journals. Native read-only check results, native model/permission/return checks and daily approval transitions are recorded separately in [verification](VERIFICATION.md). A source PR does not change the installed main checkout or daily receipts.
