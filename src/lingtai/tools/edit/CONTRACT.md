---
name: edit-contract
tool: edit
contract_version: 1
related_files:
  - src/lingtai/tools/edit/__init__.py
  - src/lingtai/tools/_file_paths.py
  - src/lingtai/services/file_io_sidecar.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Edit capability contract

`edit` performs exact string replacement in a single existing file. It reads the
file through the injected `FileIOService` (`agent._file_io`), replaces
`old_string` with `new_string`, and writes the result back. The implementation
lives in `src/lingtai/tools/edit/__init__.py`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the match-count / `replace_all` guard logic.
- You need the exact ambiguity rule (what happens when `old_string` appears more
  than once) or the "not found" behavior.

**Do not use this for:**
- Full-file replacement or new files: read `src/lingtai/tools/write/CONTRACT.md`.
- Reading content: read `src/lingtai/tools/read/CONTRACT.md`.
- The byte-level read/write: `src/lingtai/services/file_io.py`.

**Fast paths:** tool schema -> §Tool surface; ambiguity / `replace_all` rule ->
§Tool surface; read-then-write flow -> §State & storage.

## Scope

- Canonical tool name: `edit`.
- Registered via `capabilities=["edit"]` or the `file` sugar.
- Non-goals: no regex, no fuzzy matching, no multi-file edits, no file creation
  (the target must already exist). Matching is literal substring counting via
  `str.count` / `str.replace`.

## Tool surface

Single action; the handler is `handle_edit`. The schema requires `file_path`,
`old_string`, and `new_string`.

| Call | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `edit` | `file_path: string`, `old_string: string`, `new_string: string` | `replace_all: bool` (default false) | `{status: "ok", replacements}` (`count` when `replace_all`, else `1`) | see below |

**Error shapes** (plain dicts, not exceptions):
- `{"status": "error", "message": "file_path is required"}` — empty/missing path.
- `{"status": "error", "message": "File not found: <path>"}` — `FileNotFoundError`
  on the initial read.
- `{"status": "error", "message": "Cannot read <path>: <exc>"}` — other read
  failure.
- `{"status": "error", "message": "old_string not found in <path>"}` — zero
  matches.
- `{"status": "error", "message": "old_string found <count> times — use replace_all=true or provide more context"}`
  — more than one match without `replace_all`.
- `{"status": "error", "message": "Cannot write <path>: <exc>"}` — write-back
  failure.

## State & storage

None owned. `edit` does its own read→replace→write against the target file via
`agent._file_io.read` then `agent._file_io.write`; it holds no persistent state.
When `replace_all` is false the replacement is `content.replace(old, new, 1)`;
when true it replaces every occurrence.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** relative `file_path` is resolved against `agent._working_dir`
  via `resolve_workdir_path` (`src/lingtai/tools/_file_paths.py`); absolute paths pass
  through unchanged.
- **Byte I/O / sidecar:** both the read and the write-back go through the injected
  `FileIOService`; the Rust sidecar delegates read/write/edit verbatim to
  `LocalFileIOBackend` (see `src/lingtai/services/file_io_sidecar.py`).
- **Encoding / matching:** matching is on the UTF-8 decoded text; substring
  counting is exact and newline-sensitive (no normalization).

## Anchored claims

| Claim | Source `src/lingtai/tools/edit/...` | Test |
|---|---|---|
| Exact string replacement works through the capability | `__init__.py` (`handle_edit`) | `tests/test_layers_file.py::test_edit_via_capability` |
| Edit surfaces a `{status: error}` dict on failure | `__init__.py` (`handle_edit`) | `tests/test_layers_file.py::test_edit_error_shape` |
| Relative paths resolve under the agent workdir | `__init__.py` (`resolve_workdir_path`) | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` |
| Edit routes through the injected FileIOService | `__init__.py` (`handle_edit`) | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` |
| `file` sugar registers edit alongside the other four file tools | `src/lingtai/tools/registry.py` | `tests/test_layers_file.py::test_file_sugar_expands_to_five` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Single replacement round-trips | `tests/test_layers_file.py::test_edit_via_capability` | `edit` a unique substring, `read` the result | Silent corruption / wrong content |
| Ambiguous match is refused without `replace_all` | `__init__.py` count guard | `edit` a substring that appears twice without `replace_all` | Unintended mass replacement |
| Missing file / missing substring reported clearly | `tests/test_layers_file.py::test_edit_error_shape` | `edit` a nonexistent file or absent substring | Executor crash or silent no-op |
| Relative paths stay in workdir | `tests/test_layers_file.py::test_file_capability_relative_paths_resolve_under_workdir` | `edit` a relative path from a known workdir | Edits escape the sandbox |
| Edit routes through FileIOService | `tests/test_layers_file.py::test_file_capability_uses_file_io_service` | Boot with `capabilities=["edit"]` and confirm `agent._file_io` is used | Backend selection / sandbox bypassed |

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
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
