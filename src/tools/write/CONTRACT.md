---
name: write-contract
tool: write
contract_version: 1
related_files:
  - src/tools/write/__init__.py
  - src/tools/_file_paths.py
  - src/lingtai/services/file_io_sidecar.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Write capability contract

`write` creates or overwrites one file with the supplied content, auto-creating
parent directories through the injected `FileIOService` (`agent._file_io`). The
implementation lives in `src/tools/write/__init__.py`; the code is the source of
truth.

## Routing Card

**Use this when:**
- You are editing the write wrapper's argument handling or success/error shape.
- You need to confirm what `write` returns (`path`, `bytes`) or how parent-dir
  creation is delegated to the backend.

**Do not use this for:**
- Surgical in-place replacement: read `src/tools/edit/CONTRACT.md`.
- Reading content back: read `src/tools/read/CONTRACT.md`.
- The byte-level write / parent-dir creation itself: that lives in
  `src/lingtai/services/file_io.py` (`LocalFileIOBackend`).

**Fast paths:** tool schema -> §Tool surface; overwrite / mkdir behavior ->
§State & storage; path handling -> §Cross-platform invariants.

## Scope

- Canonical tool name: `write`.
- Registered via `capabilities=["write"]` or the `file` sugar.
- Non-goals: no partial edits, no appends, no read-back, no directory listing.
  Every call replaces the whole file content.

## Tool surface

Single action; the handler is `handle_write`. The schema requires `file_path`
and `content`.

| Call | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `write` | `file_path: string`, `content: string` | — | `{status: "ok", path, bytes}` where `bytes = len(content.encode("utf-8"))` | see below |

**Error shapes** (plain dicts, not exceptions):
- `{"status": "error", "message": "file_path is required"}` — empty/missing path.
- `{"status": "error", "message": "Cannot write <path>: <exc>"}` — any backend
  write failure.

## State & storage

`write` owns no capability-private store, but it is the primary mutation path for
arbitrary workdir files. The backend (`agent._file_io.write`) auto-creates
missing parent directories, so a call to a nested path both makes the directories
and writes the file. Existing files are overwritten in full (no append, no merge).

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** relative `file_path` is resolved against `agent._working_dir`
  via `resolve_workdir_path` (`src/tools/_file_paths.py`); absolute paths pass
  through unchanged.
- **Byte I/O / sidecar:** writes go through the injected `FileIOService`. The
  Rust search sidecar delegates read/write/edit verbatim to `LocalFileIOBackend`
  so sandbox root-resolution and parent-dir creation stay in one place (see
  `src/lingtai/services/file_io_sidecar.py`).
- **Encoding / byte count:** the reported `bytes` is the UTF-8 encoded length of
  `content`.

## Anchored claims

| Claim | Source `src/tools/write/...` | Test |
|---|---|---|
| A written file can be read back through the capability | `__init__.py` (`handle_write`) | `tests/test_layers_file.py::test_write_and_read_via_capability` |
| Relative paths resolve under the agent workdir | `__init__.py` (`resolve_workdir_path`) | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` |
| Write failures return a `{status: error}` dict | `__init__.py` (`handle_write`) | `tests/test_layers_file.py::test_write_error_shape` |
| Writes route through the injected FileIOService | `__init__.py` (`handle_write`) | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` |
| `file` sugar registers write alongside the other four file tools | `src/tools/registry.py` | `tests/test_layers_file.py::test_file_sugar_expands_to_five` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Round-trip write→read works | `tests/test_layers_file.py::test_write_and_read_via_capability` | `write` a file then `read` it | Data silently lost or corrupted |
| Parent dirs are created | `tests/test_layers_file.py::test_write_and_read_via_capability` | `write` to `a/b/c.txt` under a fresh workdir | Nested writes fail spuriously |
| Relative paths stay in workdir | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` | `write` a relative path, confirm location | Writes escape the sandbox |
| Errors are dicts, not exceptions | `tests/test_layers_file.py::test_write_error_shape` | `write` to an unwritable path | Executor crashes instead of surfacing a message |
| Writes route through FileIOService | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` | Boot with `capabilities=["write"]` and confirm `agent._file_io` is used | Backend selection / sandbox bypassed |

Run before merging:

```bash
python -m pytest tests/test_layers_file.py -q
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
