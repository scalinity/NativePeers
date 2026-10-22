<!-- CODEGRAPH_START -->
## CodeGraph: initialize first, then explore code through the graph

Before a nontrivial code task, identify the actual project root (the Git top-level when available). If it has no `.codegraph/` index, run `codegraph init --yes /absolute/project/root` before exploring code. Initialization is authorized for project directories; never initialize the home directory or a filesystem root, and never use `--force`. If CodeGraph is unavailable or initialization fails, report the concrete problem and continue with direct tools rather than repeatedly retrying.

Use CodeGraph FIRST when locating or understanding code, tracing a bug, assessing an edit, or selecting regression tests:

- Prefer the `codegraph_explore` MCP tool with the explicit `projectPath`. If deferred, discover it by name. Ask for the relevant symbols/files and the flow between them in one focused query.
- Shell fallback: `codegraph explore --path /absolute/project/root "symbols or flow question"`. Its output includes current source, call paths and affected tests. Reuse the returned source; do not reopen the same code just to read it again. Use direct reads/search for omitted sections, unsupported files, exact text matches and non-code documents.
- Check `codegraph status /absolute/project/root` when freshness is uncertain. Run `codegraph sync /absolute/project/root` after a meaningful edit batch or external changes. Do not rebuild the whole graph or sync before every read.
- Use `codegraph impact SYMBOL` for affected callers and `codegraph affected CHANGED_FILES` to locate focused tests, then run the real checks. Graph output helps select verification; it does not establish correctness.
- Keep generated graph databases local and ignored; do not stage `.codegraph/` or delete an existing index during cleanup. Use one index per project and pass the right project path when moving between repositories or worktrees.

<!-- CODEGRAPH_END -->
