---
name: migration-workspace
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/migrate/ANATOMY.md
  - src/lingtai/kernel/migrate/migrate.py
  - src/lingtai/kernel/migrate/__init__.py
  - src/lingtai/kernel/migrate/m001_context_limit_relocation.py
  - src/lingtai/kernel/migrate/m002_description_object.py
  - src/lingtai/kernel/migrate/agent_m001_init_procedures_override.py
  - src/lingtai/kernel/migrate/agent_m002_mcp_launch_args_rewrite.py
  - src/lingtai/kernel/migrate/agent_m003_init_prompt_contract.py
  - src/lingtai/adapters/posix/migration_workspace.py
  - src/lingtai/kernel/config_resolve.py
  - src/lingtai/kernel/presets.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/_migration_workspace_helpers.py
  - tests/test_kernel_migrate.py
  - tests/test_architecture_documents.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Migration Workspace

## Purpose

Core owns the versioned, append-only, forward-only migration of kernel-managed
on-disk shapes (registries, current-version derivation, transform order,
success-by-success durability), while one outbound `MigrationWorkspacePort`
expresses every read, write, enumeration, version file, archive, and audit
append. Two domains share the Core: the preset library and the agent workdir.

## Behavior

Each domain has a contiguous append-only registry; the current version is the
registry head, never a hand-maintained constant. Movement is forward-only: a
persisted version above the head is honored, never rolled back, with no downgrade
path. An unavailable workspace runs and persists nothing — a missing preset
directory is a cached no-op, a workdir with no `init.json` an uncached no-op so a
half-created workdir never gets version meta; idempotence is once-per-process, keyed by the opaque `cache_key`.

The version counter advances only after a transform succeeds: migration N
persists exactly N, so a failure at N+1 leaves the persisted version at N and
the next launch resumes there. Preset transforms consume adapter-provided raw
text, parse JSON/JSONC (comment/trailing-comma aware, string-safe URLs
preserved), continue past a malformed or unreadable document with a warning, and
write back the exact current serialization. Agent transforms archive retired
inline content, remove retired fields, rewrite legacy MCP launch args, and emit
the current best-effort pre-Agent JSONL audit events; archive failures abort the
run truthfully and audit remains best-effort. Every replacement is atomic and
uses the same PID-suffixed sibling temp mechanism, including preset m001/m002.

## Port

`MigrationWorkspacePort` is bound at construction to one `MigrationDomain`
(`PRESET_LIBRARY` or `AGENT_WORKDIR`) and one root, and exposes exactly seven
operation families: `inspect() -> MigrationWorkspaceState`; `enumerate_entries()
-> tuple[MigrationEntryRef, ...]`; `read_entry(ref) -> str | None`;
`atomic_replace_entry(ref, content: str) -> None`; `store_version(version: int)
-> None`; `archive(kind, content: str) -> MigrationArchiveResult`; and
`append_audit(event_type: str, fields: dict) -> None`. Values are
technology-neutral: `MigrationEntryKind`/`MigrationEntryRef` are logical document
identities (never `pathlib.Path`), `MigrationWorkspaceState` carries availability,
an opaque `cache_key`, and the persisted version, and `MigrationArchiveKind`/
`MigrationArchiveResult` carry relative audit-visible archive evidence. No
argument, result, or Core migration module exposes or uses `pathlib.Path`, `os`,
file handles, temp names, argv/process results, generic filesystem/KV methods,
direct read/write/enumeration/mkdir/unlink/replace, or POSIX adapter construction.
There is no eighth family, and `MigrationWorkspaceError` is the only failure the
mechanism raises across the Port.

## Adapters

`PosixMigrationWorkspaceAdapter`
(`src/lingtai/adapters/posix/migration_workspace.py`) is the one production
adapter. Bound to a domain/root, it owns availability, entry/path mapping, raw
reads, preset enumeration, PID-suffixed atomic replace for every replacement, the
`_kernel_meta.json` version files, the `system/migrations/` archive + SHA-256
evidence, and best-effort `logs/events.jsonl` audit. A conforming in-memory fake lives in `tests/_migration_workspace_helpers.py`.

## Contract rules

1. `run_agent_migrations` and preset `run_migrations` accept a required bound
   `MigrationWorkspacePort` — never a path, optional, default, or no-op adapter.
2. CLI `load_init` and the concrete `Agent` are composition roots that construct
   `PosixMigrationWorkspaceAdapter` instances; Core constructs none.
3. `Agent` owns one wrapper-level preset-loader that resolves a preset, creates
   the preset-library adapter, and calls Core `load_preset`; `BaseAgent` exposes
   a fail-loud preset-loader hook so daemon/system tools call it instead of
   constructing adapters.
4. `materialize_active_preset` takes a required preset-loader callback, and
   `load_preset` / `discover_presets_in_dirs` take a required migration runner
   from their caller; there is no production default and no dual legacy path.
5. `load_jsonc` delegates to the pure `parse_jsonc(text)` in `config_resolve.py`;
   migration transforms consume adapter-provided text, never paths.
6. Migration Core keeps no service locator, module singleton, hidden adapter
   construction, Path-or-Port overload, nullable workspace, or rollback/downgrade.

## Contract tests

`tests/test_kernel_migrate.py` runs the shared fake/production seven-family
conformance suite and locks registry contiguity, current-version derivation,
forward-only future-version handling, version-after-success durability, retry
resume, PID-suffixed version writes, the exact JSON/JSONC and malformed-document
behavior, and archive/meta evidence across every composition route (CLI, wrapper
Agent/refresh, preset discover/load/materialize/activate, daemon, system listing).
`tests/test_architecture_documents.py` rejects `Path`/`os`/direct mechanism or
adapter imports/construction in migration Core, generic methods, an eighth family,
nullable workspace, rollback/downgrade, version-before-transform, and a dual route.

## Maintenance

Follow the canonical maintenance block in frontmatter: behavioral changes sync
the Port, adapter, tests, and contract; structural changes also update the paired
Anatomy and reciprocal parents.
