# Implementation workers

Writing workers use `permission_profile: "workspace"` and an owned workspace. They receive
separate native conversations and return changes for inspection and explicit integration.
The source is never reset or automatically merged. Read-only workers use
`permission_profile: "analysis"` and `workspace.mode: "inherit-read-only"`.

## Workspace selection

| Source | Workspace mode | Inputs |
| --- | --- | --- |
| Git checkout with a HEAD commit | `isolated-worktree` | HEAD, the current tracked patch, and each worker's explicit `untracked_allowlist` |
| Directory outside a Git checkout | `isolated-copy` | Only each worker's explicit `file_allowlist` |

Git worktrees live at `<source repository>/.worktrees/<worker UUID>` on `npeer/<worker UUID>`
branches. Dirty tracked edits are captured once for the batch. Selected untracked files are
frozen as bytes, permission bits, and SHA-256 digests; other untracked files are omitted.
The source index and tracked files are preserved. Storage is excluded through the repository's
local Git exclude file. A dirty tracked secret or unsafe selected input rejects preparation.

An isolated copy lives at `<source directory>/.worktrees/<worker UUID>`. The source directory
remains outside Git: no `.git` directory, branch, or configuration is created there. Only
the owned copy is initialized as a Git repository, using the existing Git identity to create
its input commit. The source baseline explicitly reports `git: false` and its original `cwd`.
`workspace_mode: "isolated-copy"`, `copy_inputs`, `head`, and `input_tree` describe the copy
and its selected inputs. The returned non-null workspace ID identifies an owned copy; it
does not mean the source is a Git worktree.

Copy allowlists must contain 1–256 unique, explicit relative regular-file paths. Directories,
traversal, absolute paths, symlinks at any selected component, special files, and special
permission bits are rejected. Selection does not recurse or expand wildcards. Known credential paths,
private native state, transcript names, `.git`, `.worktrees`, and `.nativepeers` are excluded;
file contents are not scanned for secrets. Choose nonsecret inputs deliberately. Native home
overrides and NativePeers state cannot be used as the copy source.

The union of selected copy inputs across the batch is limited to **256 files and 10 MiB**.
That union is frozen once in memory. Each copy receives only its worker's selection, with
the captured bytes and permission bits, and reports those selected digests and modes.
Git attributes in the copy's private `.git/info/attributes` preserve raw bytes and disable
external filters and built-in content conversions for patch capture. Git patches retain
the executable bit; the copy manifest retains the complete captured permission bits.

Before and after preparation, and immediately before any native worker starts, validation
rechecks the source and all frozen selected inputs. Changed bytes, permission bits, missing
files, unsafe replacements, or a source that becomes a Git checkout reject admission.
Unselected files do not enter copy validation. A failed copy preparation removes only the
new incomplete copy; an existing workspace is never overwritten. Completed copies and
unique changes remain available for review and explicit cleanup.

## Non-Git example

From a verified parent whose working directory is the non-Git source directory, submit a
batch such as this with `npeer spawn --spec /absolute/path/copy-batch.json --json`:

```json
{
  "schema_version": 1,
  "workers": [
    {
      "provider": "codex",
      "title": "Repair standalone parser",
      "model": "gpt-6.1-sol",
      "reasoning_effort": "xhigh",
      "task": "Fix the empty-input case in parser.py. Keep changes within parser.py and tests/test_parser.py. Run the selected tests and report their actual results.",
      "permission_profile": "workspace",
      "workspace": {
        "mode": "isolated-copy",
        "file_allowlist": ["parser.py", "tests/test_parser.py"]
      }
    }
  ],
  "limits": {
    "max_concurrent_workers": 1,
    "max_depth": 1,
    "wall_time_seconds": 300,
    "max_task_turns_per_worker": 4
  },
  "completion_mode": "wait"
}
```

The allowlist must also include any local imports or fixtures needed by those tests. There
is no dependency installation or automatic discovery of additional files. Git sources
continue to use `isolated-worktree`; mixed copy/worktree writing batches are rejected.

## Parent integration contract

The workspace helpers support both writing modes. The delegation entry point must admit
`isolated-copy` before the CLI example is available; its existing `isolated-worktree`-only
profile assertion rejects copies until that integration is applied.

1. In `nativepeers/delegation.py`, the assertion beginning
   `require(profile!='workspace' or workspace.get('mode')=='isolated-worktree', ...)`
   must allow `workspace.get('mode') in ('isolated-worktree', 'isolated-copy')`. Keep
   the analysis profile restricted to `inherit-read-only`.
2. Keep the existing single `freeze(parent['cwd'], [w['workspace'] for w in prepared],
   expected_baseline=batch_baseline)` call. Retain the returned `FrozenWorkspace` in memory;
   do not serialize its captured file bytes into leases. `FrozenWorkspace.untracked` holds
   selected copy bytes/modes for copy batches as well as untracked bytes/modes for Git batches.
3. Keep each `prepare(journal, source_cwd, worker_id, workspace, frozen=batch_workspace)`
   call and persist all three returned values as today. It returns `(owned_cwd, worker_id,
   baseline)` for either writing mode, including copies whose source baseline is `git: false`.
4. The final guard before `ensure_supervisor(group_id)` must test for either writing mode
   before `validate_frozen(source_cwd, batch_workspace)`. This final validation checks the
   batch union, including files used only by another worker. Start no native worker after
   any preparation or validation failure.
5. `nativepeers/worker_process.py` needs no workspace-specific capture change: its
   `if self.row['worktree_id']:` path already calls `changes(owned_cwd, baseline)`, stores
   `baseline`, reads the owned repository's HEAD, and retains both patch artifacts. Preserve
   that non-null ownership test; do not replace it with `baseline['git']`.

These are source-level integration requirements, not a claim of live provider acceptance.
Offline copy and patch tests do not run inference or change provider configuration.

## Reviewing changes

`changes.patch_artifact` reconstructs the final workspace from its baseline HEAD. For Git
worktrees it includes inherited dirty edits and selected inputs. `worker_delta_artifact`
contains changes relative to the frozen materialized input tree, so it excludes inherited
edits. For isolated copies the input commit and tree contain exactly the selected inputs;
both views describe the worker's changes. New safe files, binary data, deletions, and staged
or committed edits are captured. Known private paths and symlinks are excluded from copy
patches.

An empty delta is a no-op only when `worker_delta_empty` is true. Missing input trees,
timeouts, size limits, or unsupported normalization produce explicit unavailable reasons;
inspect the retained owned workspace in those cases. Artifacts are bounded independently
at 10 MiB for each patch view. No result automatically changes the source directory.

Inspect worker identity, requested/observed model, selected input manifest, both patch views,
and actual checks before integration. Preserve unique or uncommitted changes. Remove an
owned copy after its changes are integrated or safely backed up. Git worktrees require
`git worktree remove`, branch cleanup, and `git worktree prune`; copies are standalone
repositories and must not be passed to `git worktree remove` on the non-Git source.
