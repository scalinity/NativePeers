# Worker history visibility

NativePeers keeps background Codex workers as durable, independent native conversations. Their source is `{"custom":"nativepeers-worker"}`. NativePeers owns the application-level parent/group/worker links; the source creates no vendor-private subagent relationship.

## Creation and capability

The managed patch is maintained in `/Users/danny/Documents/Tools/CodexMCP/patches/worker-visibility.patch`. `codex app-server --nativepeers-worker` selects the source before the thread manager creates or indexes a history. `initialize.nativepeersWorkerSource` must be exactly `true`. NativePeers requires that acknowledgment before new thread creation, then checks the returned source before task input. Unknown or unsupported runtimes refuse new-worker admission rather than create ordinary visible histories. A source mismatch retains the native UUID and submits no task input.

`threadSource` and `clientInfo.name` are not used as visibility controls. Normal app-server/TUI launches retain their source. A normal conversation with an NP-looking title remains ordinary. A worker rename retains its classification. Workers use `ephemeral:false`, their selected provider home, and the existing immutable executable lease.

The explicitly synthetic Codex parent launchers under `tests/live/` use `--nativepeers-worker --no-daemon`. The TUI option refuses shared or remote app-server routes, so it cannot change a shared server's source. Genuine user parents receive no option.

## Native and Desktop readers

Codex's existing interactive-history allowlist excludes this custom source; the patch does not globally rewrite `thread/list`. Exact-ID read/resume remains available, and resume inherits the saved source.

The installed Desktop application identified during implementation was `/Applications/ChatGPT.app`, bundle `com.openai.codex`, version `26.1016.51333`, build `21161`. Read-only inspection of its packaged JavaScript found:

- `.vite/build/src-CsH31EEu.js`: `listRecentThreads` sends `thread/list` with `archived:false`, `sourceKinds:[]`, and `useStateDbOnly:true`.
- `.vite/build/main-JI9qDiXp.js`: the local thread catalog uses the same empty source filter.
- `.vite/build/src-CSKkIr_z.js`: the exported interactive filter is the empty array, which delegates to the native reader's defaults.
- The bundled local reader reports `codex-cli 0.163.0-alpha.11`.

These are source findings. The computer-use tool refuses access to `com.openai.codex`, so no observed sidebar before/after result is claimed. The manager's metadata smoke accepts a separate reader binary to check the bundled native reader with the Desktop request shape. Its result and the actual UI result must remain separate in verification reports.

## Inspection and exact resume

`npeer groups --mine --json`, `npeer status GROUP_ID --json`, and retained results continue to expose workers and exact native IDs. `history_visibility` separates requested policy, capability acknowledgment, returned native source, and `desktop_verification`. A runtime acknowledgment never fills in Desktop verification.

Use `npeer resume-worker WORKER_ID --task-file /absolute/followup.txt --completion-mode wait --client-request-id UNIQUE_ID --json` for owned automated resume. Reuse the exact request ID after a lost response; do not start another writer. `npeer resume-command WORKER_ID --json` provides the exact manual command, native environment, and visibility limitations.

Native resume rejects archived IDs until unarchive. Histories archived by NativePeers cleanup have an explicit exact-ID unarchive prerequisite. Automated resume performs it after ownership reservation and stops on an uncertain response without replay or task input. Manual commands repeat the pinned `CODEX_HOME` for both unarchive and resume. Legacy leases keep their old executable: an unclassified legacy worker can become visible after unarchive, and its output states that limitation.

## History-preserving cleanup

Create an owned private directory with mode `700`, inside the project's ignored evidence directory, before selecting a new manifest path. The manifest must not already exist.

```sh
mkdir -m 700 /Users/danny/Documents/Tools/NativePeers/evidence/private/history-cleanup-run
npeer history-cleanup --plan --manifest /Users/danny/Documents/Tools/NativePeers/evidence/private/history-cleanup-run/preview.json --json
npeer history-cleanup --apply --manifest /Users/danny/Documents/Tools/NativePeers/evidence/private/history-cleanup-run/preview.json --json
```

Repeated `--worker-id UUID` narrows selection. `NPEER_HOME=/exact/state` selects an existing NativePeers journal. `--automation-manifest /exact/run/codex-case.json` selects a supported completed synthetic-parent record, corroborated against its exact sibling launch/prepared manifests, registry identity and released groups. With automation selection, worker IDs must be supplied explicitly to include workers in the same preview. Titles never establish cleanup eligibility.

Every operation rechecks exact home/UUID, matching-generation process/output closure, terminal tasks, dead supervisor, native writer ownership, completion/delivery settlement, pinning/promotion and all native spawned descendants. Native archive obtains cross-process UUID writer locks before moving history. Active, ambiguous or unresolved records are skipped. The current approved metadata executable performs housekeeping; the worker's immutable execution lease is unchanged.

Pre-mutation exact-ID intents and selected post-state checks retain attribution when a native response is lost, without replaying the mutation. Partial native archive effects remain visible in results. A helper cleanup failure retains completed outcomes and prevents later admission. Reapplying a completed manifest reports already-archived identities. No history is deleted.

Reverse only that cleanup, subject to fresh ownership checks:

```sh
NPEER_HOME=/exact/state npeer history-cleanup --unarchive --manifest /absolute/exact-preview.json --json
```

Unarchive affects the exact selected IDs. The reversal requires the matching cleanup receipt or intent; it cannot restore unrelated histories. A newly active or changed identity is skipped.

## Verification boundaries

Offline fixtures cover creation acknowledgment, source mismatch, cancellation at preparation boundaries, follow-up and exact resume, legacy runtime preservation, selected homes, explicit synthetic parents, active/ambiguous history exclusion, native subtree safety, archive/unarchive, lost responses, partial effects and idempotence. Manager/package checks and the native-reader matrix are recorded separately from model inference and Desktop UI observations. No paid native model input is required to test metadata visibility or archival.
