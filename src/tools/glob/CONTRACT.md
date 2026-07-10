---
name: glob-contract
tool: glob
contract_version: 1
related_files:
  - src/tools/glob/__init__.py
  - src/tools/_file_paths.py
  - src/lingtai/services/file_io_sidecar.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Glob capability contract

`glob` finds files matching a pattern under a search root, delegating traversal to
the injected `FileIOService` (`agent._file_io.glob`). The implementation lives in
`src/tools/glob/__init__.py`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the glob wrapper's argument handling or its truncation /
  traversal-stats surfacing (Issue #164 fields).
- You need the exact success shape and how partial-result signals are reported.

**Do not use this for:**
- Content search: read `src/tools/grep/CONTRACT.md`.
- Reading a specific file: read `src/tools/read/CONTRACT.md`.
- Traversal budgets, excluded dirs, or the walker itself: those live in
  `src/lingtai/services/file_io.py` / `file_io_sidecar.py`.

**Fast paths:** tool schema -> §Tool surface; partial-result / `truncated`
fields -> §Tool surface; path handling -> §Cross-platform invariants.

## Scope

- Canonical tool name: `glob`.
- Registered via `capabilities=["glob"]` or the `file` sugar.
- Non-goals: no content search, no file reads, no mutation. It returns a list of
  matching paths and (when the traversal was cut short) budget metadata.

## Tool surface

Single action; the handler is `handle_glob`. The schema requires `pattern`.

| Call | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `glob` | `pattern: string` | `path: string` (defaults to `agent._working_dir`), `summary: bool` (default false) | `{matches: [...], count}` plus traversal fields when the walk was truncated | see below |

When the underlying traversal hit a budget/exclusion limit
(`agent._file_io.last_traversal.truncated_reason is not None`), the result also
carries `{truncated: true, truncated_reason, traversal: {visited, elapsed_ms,
dirs_pruned}}` so the model treats partial results as partial, not definitive.

**Error shapes** (plain dicts, not exceptions):
- `{"status": "error", "message": "pattern is required"}` — empty/missing pattern.
- `{"status": "error", "message": "Glob failed: <exc>"}` — any traversal error.

## State & storage

None owned. `glob` is read-only over the filesystem and holds no persistent state.
It reads `agent._file_io.last_traversal` (a per-service stats snapshot) only to
surface truncation metadata.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** relative `path` (search root) is resolved against
  `agent._working_dir` via `resolve_workdir_path` (`src/tools/_file_paths.py`);
  absolute roots pass through unchanged. The default root is the workdir.
- **Sidecar resolution:** recursive `glob` is one of the two operations routed
  through the Rust search sidecar when present; `default_file_io_service`
  autodiscovers a packaged/dev-tree binary and soft-falls back to the Python
  backend (see `src/lingtai/services/file_io_sidecar.py` resolution order). Both
  backends share traversal defaults so results stay in lock-step.
- **Encoding:** returned matches are string paths.

## Anchored claims

| Claim | Source `src/tools/glob/...` | Test |
|---|---|---|
| Glob returns matching files through the capability | `__init__.py` (`handle_glob`) | `tests/test_layers_file.py::test_glob_via_capability` |
| Glob failures return a `{status: error}` dict | `__init__.py` (`handle_glob`) | `tests/test_layers_file.py::test_glob_error_shape` |
| Relative search roots resolve under the workdir | `__init__.py` (`resolve_workdir_path`) | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` |
| Glob routes through the injected FileIOService | `__init__.py` (`handle_glob`) | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` |
| Default-excluded dirs are skipped by the service glob | `src/lingtai/services/file_io.py` | `tests/test_services_file_io.py::TestFileIOService::test_glob_skips_default_excluded_dirs` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Pattern matching returns expected files | `tests/test_layers_file.py::test_glob_via_capability` | `glob` `**/*.py` under a known tree | Missed or spurious matches |
| Truncated walks are flagged | `tests/test_services_file_io.py::TestFileIOService::test_glob_walltime_budget_returns_partial` | `glob` a huge tree, confirm `truncated` set | Partial results mistaken for complete |
| Excluded dirs pruned | `tests/test_services_file_io.py::TestFileIOService::test_glob_skips_default_excluded_dirs` | `glob` a tree containing `.git`/`node_modules` | Noise / budget exhaustion |
| Errors are dicts, not exceptions | `tests/test_layers_file.py::test_glob_error_shape` | `glob` with an invalid pattern | Executor crash instead of message |
| Glob routes through FileIOService | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` | Boot with `capabilities=["glob"]` and confirm `agent._file_io` is used | Sidecar/backend selection bypassed |

Run before merging:

```bash
python -m pytest tests/test_layers_file.py tests/test_services_file_io.py -q
```
