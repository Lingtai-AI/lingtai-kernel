---
name: grep-contract
tool: grep
contract_version: 1
related_files:
  - src/tools/grep/__init__.py
  - src/tools/_file_paths.py
  - src/lingtai/services/file_io_sidecar.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Grep capability contract

`grep` searches file contents by regex under a search root, pushing the glob
filter into the injected `FileIOService` (`agent._file_io.grep`) so excluded files
are pruned before read. The implementation lives in `src/tools/grep/__init__.py`;
the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the grep wrapper's argument handling, its glob-filter
  pass-through, or the truncation / traversal-stats surfacing (Issue #164).
- You need the exact match shape and how the `truncated` signal is computed.

**Do not use this for:**
- Finding files by name/pattern: read `src/tools/glob/CONTRACT.md`.
- Reading a specific file: read `src/tools/read/CONTRACT.md`.
- The regex engine, traversal budgets, or size/binary skips: those live in
  `src/lingtai/services/file_io.py` / `file_io_sidecar.py`.

**Fast paths:** tool schema -> §Tool surface; match shape & `truncated` ->
§Tool surface; glob-filter semantics -> §Tool surface.

## Scope

- Canonical tool name: `grep`.
- Registered via `capabilities=["grep"]` or the `file` sugar.
- Non-goals: no file writes, no filename-only search, no context lines beyond the
  matched line. Returns matching `{file, line, text}` records.

## Tool surface

Single action; the handler is `handle_grep`. The schema requires `pattern`. Note
the tool-facing names differ from the service kwargs: the schema exposes `glob`
and `max_matches`, which the handler maps to the service's `glob_filter` and
`max_results`.

| Call | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `grep` | `pattern: string` | `path: string` (defaults to `agent._working_dir`), `glob: string` (default `"*"`), `max_matches: int` (default 200), `summary: bool` (default false) | `{matches: [{file, line, text}], count, truncated}` plus traversal fields when the walk was cut short | see below |

Glob-filter semantics: `glob` values `None`, `""`, or `"*"` mean "no filter"
(the handler passes `glob_filter=None`); any other value is pushed into the
service so non-matching files are pruned before stat/read. `truncated` is `true`
when the (already glob-pruned) scan returned at least `max_matches` results, and
is additionally forced `true` (with `truncated_reason` and a `traversal` block of
`{visited, elapsed_ms, dirs_pruned, files_skipped_size, files_skipped_binary}`)
when the service's `last_traversal.truncated_reason` is set.

**Error shapes** (plain dicts, not exceptions):
- `{"status": "error", "message": "pattern is required"}` — empty/missing pattern.
- `{"status": "error", "message": "Grep failed: <exc>"}` — any search error.

## State & storage

None owned. `grep` is read-only over the filesystem and holds no persistent state.
It reads `agent._file_io.last_traversal` only to surface truncation metadata.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** relative `path` (search root) is resolved against
  `agent._working_dir` via `resolve_workdir_path` (`src/tools/_file_paths.py`);
  absolute roots pass through unchanged. Default root is the workdir.
- **Sidecar resolution:** recursive `grep` is one of the two operations routed
  through the Rust search sidecar when present; `default_file_io_service`
  autodiscovers a packaged/dev-tree binary and soft-falls back to the Python
  backend (see `src/lingtai/services/file_io_sidecar.py` resolution order). Both
  backends share traversal / size / exclusion defaults.
- **Encoding / skips:** oversized and binary files are skipped by the service and
  counted in `files_skipped_size` / `files_skipped_binary`.

## Anchored claims

| Claim | Source `src/tools/grep/...` | Test |
|---|---|---|
| Grep returns matching lines through the capability | `__init__.py` (`handle_grep`) | `tests/test_layers_file.py::test_grep_via_capability` |
| Grep failures return a `{status: error}` dict | `__init__.py` (`handle_grep`) | `tests/test_layers_file.py::test_grep_error_shape` |
| The glob filter prunes non-matching files before read | `__init__.py` (`handle_grep`, `service_glob`) | `tests/test_services_file_io.py::TestFileIOService::test_glob_filter_via_tool_wrapper_prunes_before_read` |
| `max_matches` caps results at the service layer | `__init__.py` (`max_matches` → `max_results`) | `tests/test_services_file_io.py::TestFileIOService::test_grep_max_results` |
| Grep routes through the injected FileIOService | `__init__.py` (`handle_grep`) | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Regex matching returns expected lines | `tests/test_layers_file.py::test_grep_via_capability` | `grep` a known token under a tree | Missed or spurious matches |
| Glob filter prunes before read | `tests/test_services_file_io.py::TestFileIOService::test_glob_filter_via_tool_wrapper_prunes_before_read` | `grep` with `glob="*.py"` on a mixed tree | Wasted reads / wrong scope |
| `max_matches` cap and `truncated` flag | `tests/test_services_file_io.py::TestFileIOService::test_grep_max_results` | `grep` a common token with a low `max_matches` | Callers assume results are complete |
| Oversized / binary files skipped | `tests/test_services_file_io.py::TestFileIOService::test_grep_skips_oversized_files` | `grep` a tree with a large binary | Budget exhaustion / crashes |
| Grep routes through FileIOService | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` | Boot with `capabilities=["grep"]` and confirm `agent._file_io` is used | Sidecar/backend selection bypassed |

Run before merging:

```bash
python -m pytest tests/test_layers_file.py tests/test_services_file_io.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m tools.glossary_validator --check`.
