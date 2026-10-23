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

## Admission and verification

The delegation entry point accepts both writing modes and keeps analysis read-only. It
freezes the complete selected batch once, materializes each workspace from those bytes,
and validates the complete source selection again before starting a native worker.
A failed validation submits no model input. Captured file bytes stay in memory during
preparation; leases retain only the input manifest and workspace metadata.

Both modes use the owned workspace ID for patch capture. Copy source baselines remain
`git: false`; the Git operations used for results run only inside the owned copy.
Offline tests cover bounded copies, source changes, private paths, patch reconstruction
and real fake-provider controller integration. Check the current verification record for
provider-backed acceptance; offline tests do not establish native entitlement or policy.

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


## Check, preview and cleanup

Use `npeer spawn --check --spec /absolute/batch.json --json` before a batch when defaults, input selection or native configuration are uncertain. It validates without reserving capacity, materializing workspaces or starting model input; catalog metadata helpers are identified in its response.

Use `npeer delta-preview WORKER_ID --json` to compare a retained worker-only delta with current source. It checks relevant frozen inputs and bounded application without changing files or the source index. New files, deletions, binary patches and both isolated workspace types are supported. Applicability is a local content check, not a verified implementation result.

Use `npeer cleanup --plan --worker-id WORKER_ID --json` for a conservative retained-workspace inspection. The planner executes no removal and never sweeps native histories/artifacts. Active or resumed generations, uncertain ownership and unique/unavailable changes are retained. Only verified unchanged candidates receive the appropriate Git-worktree or selected-copy procedure. Recheck immediately before any authorized removal.

Copy capture and pre-admission revalidation exclude the canonical NativePeers journal root and every registered provider home, including offline and nonambient homes. Explicit selection does not override the private-native-state exclusion.

Git workspace input has one 10 MiB byte budget across dirty tracked files and explicitly selected untracked files. Each component consumes the remaining shared allowance before materialization. Workspace Git mutations and patch capture remove ambient repository/index/object routing variables, preserving ordinary Git configuration and author/committer identity; a private index is an explicit owned override.
