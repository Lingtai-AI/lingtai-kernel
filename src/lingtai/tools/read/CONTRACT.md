---
name: read-contract
tool: read
contract_version: 2
related_files:
  - src/lingtai/tools/read/__init__.py
  - src/lingtai/tools/_file_paths.py
  - src/lingtai/services/file_io_sidecar.py
  - src/lingtai/intrinsic_skills/file-manual/SKILL.md
  - src/lingtai/intrinsic_skills/read-manual/SKILL.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Read capability contract

`read` returns numbered, character-capped windows of a single text file. It is a
stateless, read-only wrapper over the injected `FileIOService` (`agent._file_io`).
The implementation lives in `src/lingtai/tools/read/__init__.py`; the code is the source
of truth.

## Routing Card

**Use this when:**
- You are editing the paging / truncation math (`_apply_cap`) or the per-call
  character cap resolution (`_resolve_call_cap`).
- You need the exact continuation contract (`truncated`, `next_offset`,
  `line_truncated`) that callers rely on to resume a long read.
- You are reviewing how `read` resolves relative paths or reports missing files.

**Do not use this for:**
- Writing / mutating files: read `src/lingtai/tools/write/CONTRACT.md` or
  `src/lingtai/tools/edit/CONTRACT.md`.
- Recursive discovery / search: read `src/lingtai/tools/glob/CONTRACT.md` or
  `src/lingtai/tools/grep/CONTRACT.md`.
- The underlying byte I/O, sidecar, or traversal budgets: those live in
  `src/lingtai/services/file_io.py` and `file_io_sidecar.py`.

**Fast paths:** tool schema -> §Tool surface; cap constants -> §Scope; resume /
continuation fields -> §Tool surface; path & encoding handling -> §Cross-platform
invariants.

## Scope

- Canonical tool name: `read`.
- Registered via `capabilities=["read"]` or the `file` sugar
  (`capabilities=["file"]`) which expands to all five file tools.
- Non-goals: no writing, no globbing, no recursive scan, no directory listing,
  no cross-file operations. One file, one window per call.
- Cap constants (source of truth is `src/lingtai/tools/read/__init__.py`):
  - `DEFAULT_READ_CAP_CHARS = 100_000` — everyday per-call page budget.
  - `READ_HARD_CAP_CHARS = PREVENTIVE_MAX_CHARS` — non-configurable ceiling
    (imported from `lingtai.kernel.tool_result_artifacts`).
  - The active runtime cap is `min(executor._max_result_chars, READ_HARD_CAP_CHARS)`
    when the executor exposes a positive cap, else `READ_HARD_CAP_CHARS`.
  - Per-call `max_chars` is clamped to that runtime cap; invalid values fall back
    to the read default.

## Tool surface

`handle_read` has two modes:

- **Ordinary:** omit `action` (backward compatible) or set
  `action="read"`; both forms run the same read operation.
- **Manual:** `action="manual"` returns the installed `read-manual` without
  attempting to read a target file.

The schema lists `read` before `manual`. Any other explicit action
returns a plain error before file I/O. After a manual response, the caller
continues the original task with an ordinary call rather than repeating the
manual.

| Call | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `read` | `file_path: string` | `offset: int` (default 1, 1-based), `limit: int` (default 2000 lines), `max_chars: int`, `summary: bool` (default false) | `{content, total_lines, lines_shown}` plus continuation fields when truncated | see below |

`content` is `cat -n`-style: each kept line is `"{lineno}\t{line}"`. When the
window is capped mid-way, the extra fields are added:

`{truncated: true, cap_chars, returned_chars, requested_offset, requested_limit,
last_returned_line, next_offset, remaining_lines_estimate}` and, when a single
line alone exceeds the cap, `line_truncated: true` (a bounded prefix of that line
is returned). Callers resume by re-calling with `offset = next_offset`.

**Error shapes** (all are plain dicts, not exceptions):
- `{"status": "error", "message": "file_path is required"}` — empty/missing path.
- `{"status": "error", "message": "File not found: <path>"}` — `FileNotFoundError`.
- Spill-aware variant: if the missing path is under `tmp/tool-results/` (after
  `..` normalization), the message is the "Spill artifact expired: …" hint
  instead of the generic not-found string.
- `{"status": "error", "message": "Cannot read <path>: <exc>"}` — any other read
  exception.

## State & storage

None. `read` is read-only and holds no persistent state. It only reads the
target file through `agent._file_io.read(path)`.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** relative `file_path` is resolved against `agent._working_dir`
  via `resolve_workdir_path` (`src/lingtai/tools/_file_paths.py`); absolute paths pass
  through unchanged to preserve historical error strings.
- **Byte I/O / sidecar:** all reads go through the injected `FileIOService`.
  The Rust search sidecar delegates read/write/edit verbatim to
  `LocalFileIOBackend`; `default_file_io_service` selects Rust vs. a
  Python-backed fallback per `LINGTAI_FILE_IO_BACKEND` (see
  `src/lingtai/services/file_io_sidecar.py` resolution order). `read` itself is
  backend-agnostic.
- **Encoding:** the service reads text as UTF-8; the source module pins UTF-8
  and parses as Python 3.11.
- **Line splitting:** `content.splitlines(keepends=True)` preserves the file's
  own newline style; `total_lines` counts those lines.

## Anchored claims

| Claim | Source `src/lingtai/tools/read/...` | Test |
|---|---|---|
| Default per-call cap is 100k and the hard cap is 200k | `__init__.py` (`DEFAULT_READ_CAP_CHARS`, `READ_HARD_CAP_CHARS`) | `tests/test_read_continuation.py::test_read_cap_default_is_100k_and_hard_cap_is_200k` |
| Missing `max_chars` uses the read default | `__init__.py` (`_resolve_call_cap`) | `tests/test_read_continuation.py::test_resolve_call_cap_defaults_to_read_default` |
| Per-call `max_chars` is clamped to the runtime hard cap | `__init__.py` (`_resolve_call_cap`) | `tests/test_read_continuation.py::test_resolve_call_cap_clamps_to_runtime_hard_cap` |
| Handler honors a per-call `max_chars` and emits continuation fields | `__init__.py` (`handle_read`, `_apply_cap`) | `tests/test_read_continuation.py::test_read_handler_uses_per_call_max_chars` |
| `read` reaches files through the injected FileIOService | `__init__.py` (`handle_read`) | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` |
| Missing-file error is a `{status: error}` dict | `__init__.py` (`handle_read`) | `tests/test_layers_file.py::test_read_error_shape` |
| Source pins UTF-8 and parses as Python 3.11 | `__init__.py` | `tests/test_utf8_read_text.py::test_source_read_text_calls_pin_utf8_encoding` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Cap constants stay 100k / 200k | `tests/test_read_continuation.py::test_read_cap_default_is_100k_and_hard_cap_is_200k` | `read` a large file and inspect `cap_chars` | Provider-visible tool-result blowups |
| Continuation fields let callers resume | `tests/test_read_continuation.py::test_read_handler_uses_per_call_max_chars` | `read` with small `max_chars`, re-call with `next_offset` | Callers loop or drop tail content |
| Relative paths resolve under workdir | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` | `read` a relative path from a known workdir | Reads escape / miss the sandbox |
| Errors are dicts, not exceptions | `tests/test_layers_file.py::test_read_error_shape` | `read` a nonexistent path | Executor crashes instead of surfacing a message |
| Reads route through FileIOService | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` | Boot with `capabilities=["read"]` and confirm `agent._file_io` is used | Backend selection / sandbox bypassed |

Run before merging:

```bash
python -m pytest tests/test_read_continuation.py tests/test_layers_file.py tests/test_utf8_read_text.py -q
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
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
