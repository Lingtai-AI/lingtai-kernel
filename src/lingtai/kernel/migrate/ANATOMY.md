---
related_files:
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/migrate/CONTRACT.md
  - src/lingtai/kernel/migrate/__init__.py
  - src/lingtai/kernel/migrate/agent_m001_init_procedures_override.py
  - src/lingtai/kernel/migrate/agent_m002_mcp_launch_args_rewrite.py
  - src/lingtai/kernel/migrate/agent_m003_init_prompt_contract.py
  - src/lingtai/kernel/migrate/m001_context_limit_relocation.py
  - src/lingtai/kernel/migrate/m002_description_object.py
  - src/lingtai/kernel/migrate/migrate.py
  - src/lingtai/adapters/posix/migration_workspace.py
  - src/lingtai/kernel/config_resolve.py
  - tests/_migration_workspace_helpers.py
  - tests/test_cli.py
  - tests/test_deep_refresh.py
  - tests/test_kernel_migrate.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# migrate

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues/mail/PR proposals; do not silently fix.

Retained historical/test-only migration machinery for older kernel-managed on-disk states. The current boot/refresh init reader does not invoke this registry, version chain, workspace, archive, or write-back path: `init.json` remains user-owned input and compatibility is diagnosed read-only by `lingtai.init_reader.read_init`. The files remain present because deletion/cleanup is not authorized; any future retirement must name exact paths and obtain approval. The paired [`CONTRACT.md`](CONTRACT.md) documents the retained Port for historical tests and explicit future maintenance, not current runtime semantics.

## Components

- `__init__.py` — public facade. Re-exports the Port and value objects (`MigrationWorkspacePort`, `MigrationDomain`, `MigrationEntryKind`/`MigrationEntryRef`, `MigrationWorkspaceState`, `MigrationArchiveKind`/`MigrationArchiveResult`, `MigrationWorkspaceError`, `INIT_DOCUMENT_REF`/`MCP_REGISTRY_REF`), the two runners (`run_migrations`/`run_agent_migrations`), `CURRENT_VERSION`/`AGENT_CURRENT_VERSION`, and `meta_filename`.
- `migrate.py` — Core owner. Defines the technology-neutral domain values and the `MigrationWorkspacePort` (exactly seven abstract families: `inspect`, `enumerate_entries`, `read_entry`, `atomic_replace_entry`, `store_version`, `archive`, `append_audit`). Holds the append-only registries (`_PRESET_MIGRATIONS`, `_AGENT_MIGRATIONS`, back-compat alias `_MIGRATIONS`), the import-time `_validate_registry` (contiguous, strictly-increasing, callable), derived `CURRENT_VERSION`/`AGENT_CURRENT_VERSION`, the shared `_run_versioned_migrations` (inspect → cache gate → availability → forward-only/downgrade guard → per-transform run then `store_version`), and the `_migrated` process cache keyed by the adapter-provided opaque `cache_key`. `meta_filename()` names `_kernel_meta.json` so preset listing hides it. Imports no `os`/`pathlib` and constructs no adapter.
- `m001_context_limit_relocation.py` — preset m001: moves `manifest.context_limit` → `manifest.llm.context_limit`. Consumes workspace-provided text via `config_resolve.parse_jsonc`, iterates `enumerate_entries()`, and writes back via `atomic_replace_entry`.
- `m002_description_object.py` — preset m002: promotes string `description` to `{summary, tier?}`; folds `tags:[tier:N]` into `description.tier`; deletes `tags`. Same Port surface; `_extract_tier()` helper.
- `agent_m001_init_procedures_override.py` — agent m001: archives non-empty `init.json.procedures` via `workspace.archive(INIT_PROCEDURES, ...)`, removes `procedures`/`procedures_file`, replaces `init.json`, and `append_audit`s `init_procedures_override_migrated` (or `…_failed`).
- `agent_m002_mcp_launch_args_rewrite.py` — agent m002: rewrites legacy `["-m", "lingtai_<name>"]` MCP launch args to `lingtai.mcp_servers.<name>` in the `INIT_DOCUMENT` and `MCP_REGISTRY` entries; `append_audit`s on change.
- `agent_m003_init_prompt_contract.py` — agent m003: archives non-empty inline `substrate` via `workspace.archive(INIT_SUBSTRATE, ...)`, removes retired `substrate`/`substrate_file` and deprecated `brief`/`brief_file`, replaces `init.json`, and `append_audit`s `init_prompt_contract_migrated`.

## Connections

- **Port implementation:** `src/lingtai/adapters/posix/migration_workspace.py` `PosixMigrationWorkspaceAdapter` implements all seven families for the local filesystem, bound to a `MigrationDomain`/root. It owns availability, entry→path mapping, raw reads, top-level preset candidate enumeration, PID-suffixed atomic replace (including preset m001/m002), `_kernel_meta.json` version files, the `system/migrations/` archive layout + SHA-256 evidence, and the best-effort `logs/events.jsonl` audit append.
- **JSONC:** preset transforms call `config_resolve.parse_jsonc(text)` (the pure parse extracted from `load_jsonc`) on adapter-provided text, never a path.
- **Inbound — preset domain:** `lingtai.presets.discover_presets_in_dirs` and `load_preset` receive a preset-library migration runner from their caller and invoke it (per directory / on the file's parent) before listing/reading; the caller builds the adapter.
- **Historical agent domain:** older tests and explicit maintenance callers may construct an `AGENT_WORKDIR` workspace and call `run_agent_migrations`; current `lingtai.cli.load_init` and `lingtai.Agent._read_init` deliberately do not.
- **Boundary contract:** public imports remain available for the retained historical/test surface. The production init boundary is `lingtai.init_reader.read_init`; do not add a runtime migration call, cleanup helper, or second reader path.

## Composition

- **Parent:** `src/lingtai/kernel/` (see `src/lingtai/kernel/ANATOMY.md`).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md).
- **Composition roots:** `src/lingtai/cli.py` (`load_init`) and `src/lingtai/agent.py` (`Agent` / module-level `load_preset` + `_run_preset_library_migrations`) construct `PosixMigrationWorkspaceAdapter` instances and inject them into the Core runners; the adapter package is never imported by Core.
- **Subfolders:** none.

## State

- **Preset on-disk:** `<presets_dir>/_kernel_meta.json` — `{"version": N}`, written by the adapter's `store_version` after each successful preset migration step. Created only when at least one preset migration runs.
- **Agent on-disk:** `<workdir>/system/migrations/_kernel_meta.json` — `{"version": N, "domain": "agent"}`, written after each successful agent migration step. The same directory holds archive artifacts such as `init-procedures-<sha256>.md` / `init-substrate-<sha256>.md`.
- **Process-level:** `_migrated: set[str]` — adapter-provided `cache_key`s already migrated this process. Populated on no-op/current/future/success paths; a missing preset directory is cached, a workdir with no `init.json` is not.
- **Ephemeral:** transforms rewrite target documents in place through the adapter (PID-suffixed sibling temp + replace). No rollback artifacts on success except intentional archives.

## Notes

- **This is the reminder:** if you are about to change an on-disk kernel-owned shape (preset JSON, agent `init.json`, or another durable kernel file), first inspect/extend this migration Core. Do not “just add a cleanup helper” in the boot path and call it a migration.
- **Validation ordering:** agent migrations run before `validate_init()` so retired keys can be removed/archived before schema validation.
- **Behavioral invariants** (forward-only, version-after-success, per-domain contiguity, PID-suffixed concurrency safety) are normative and live in [`CONTRACT.md`](CONTRACT.md).
- **Tests:** runner/domain behavior and the shared fake/production seven-family conformance suite live in `tests/test_kernel_migrate.py` (with `tests/_migration_workspace_helpers.py`); boot/refresh integration lives in `tests/test_deep_refresh.py` and `tests/test_cli.py`.
