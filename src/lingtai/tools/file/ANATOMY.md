---
related_files:
  - crates/lingtai-search-sidecar/ANATOMY.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/file/BEHAVIORS.md
  - src/lingtai/tools/file/CONTRACT.md
  - src/lingtai/tools/file/__init__.py
  - src/lingtai/tools/file/manual/SKILL.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/file/_read.py
  - src/lingtai/tools/file/_write.py
  - src/lingtai/tools/file/_edit.py
  - src/lingtai/tools/file/_glob.py
  - src/lingtai/tools/file/_grep.py
  - src/lingtai/tools/file/settings.py
  - src/lingtai/services/file_io.py
  - src/lingtai/services/file_io_sidecar.py
  - src/lingtai/services/ANATOMY.md
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/tools/_file_paths.py
  - src/lingtai/kernel/execution_workspace.py
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/intrinsic_skills/read-manual/SKILL.md
  - src/lingtai/tools/file/glossary-en.md
  - src/lingtai/tools/file/glossary-wen.md
  - src/lingtai/tools/file/glossary-zh.md
  - tests/test_file_tool_family.py
  - tests/test_execution_workspace.py
  - tests/test_file_tool_plugin_package.py
  - tests/test_file_settings.py
  - tests/test_tool_settings_contract.py
maintenance: |
  Keep this public file Anatomy and its Contract reciprocal, and keep the
  parent link bidirectional. This package is the single owner of the file
  surface: schema, dispatch, all five operations, and the package-owned manual
  body. Do not reintroduce per-operation packages, contracts, or glossaries.
  Update this map with structural code changes and verify citations.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# Unified file capability Anatomy

The `file` package is the sole owner of the public `file` tool and its
operational manual body. It exposes one model-facing handler over seven
canonical children — `read`, `write`, `edit`, `glob`, `grep`, `settings`,
`manual` — and owns their
implementations outright. The integrated installer copies the package manual to
that established runtime path.

## Components

- `__init__.py` — the static `DECLARATION`, five operation schemas plus generic
  settings and manual schemas, the one
  declaration-derived family builder, established installed-manual loader, pure
  bind step, and official-registrar `setup()` wiring
  (`src/lingtai/tools/file/__init__.py:177-237`, `:277-304`, `:315-363`).
- `_load_file_manual()` / `_build_manual_child()` — load the established
  `file-manual` installation (`src/lingtai/tools/file/__init__.py:143-170`).
- `_read.py` — the read operation plus paging/truncation math, per-call cap
  resolution, and spill-aware missing-file hint (`_apply_cap` and
  `_resolve_call_cap` at `src/lingtai/tools/file/_read.py:39-75`).
- `_write.py`, `_edit.py`, `_glob.py`, `_grep.py` — the remaining four
  operations, each self-contained: write receipt, edit ambiguity/missing
  discipline, sorted glob list, and grep match/traversal cap.
- `_strip_nulls()` — the one boundary translating strict-schema `null` back to
  “absent” so each operation applies its historical defaults
  (`src/lingtai/tools/file/__init__.py:272-274`).
- `settings.py` — the exact ordered 13-row File projection. It reads the live
  runtime cap from `FileIOPort`, combines it with source constants, and consumes
  only the immutable bounded backend construction snapshot; the sidecar value
  is excluded from repr and fully redacted before projection.
- `services/file_io_sidecar.py` — the canonical factory that captures applied
  `backend.mode`, resolved sidecar override, and selected canonical/legacy
  source when it constructs the service. The value is excluded from repr and
  fully redacted before SHOW projection.

## Connections

`registry.py` maps public `file` to this package. There are no capability aliases
for the five pre-migration names: `read`, `write`, `edit`, `glob`, and `grep`
are unknown capabilities and fail loudly. The kernel registrar binds the static
declaration once per setup; the bound family closes over `WorkdirPort`,
`FileIOPort`, and an immutable setup-selected `ConfigurationPort` value. Every
operation reaches the working tree through the narrow file port
and resolves relative paths via `_file_paths.resolve_workdir_path`; that helper
uses the task-local execution root when bound and enforces canonical containment.
This package performs no target-file I/O of its own.

The reserved `manual` child reads the installed package-owned body from
`.library/intrinsic/capabilities/file-manual/SKILL.md`, preserving the public
path.

The generic reserved `settings` child is injected immediately before `manual`
because `DECLARATION.settings` is true and `_build_family()` supplies
`FileSettingsProvider`. It has no writer or owner file. Setup passes the
factory-applied backend snapshot through `StaticConfigurationAdapter`; an
unknown/injected service without that snapshot binds normally for operations
but SHOW fails as one unavailable inventory.

## Composition

The parent [`src/lingtai/tools/ANATOMY.md`](../ANATOMY.md) owns capability
registry composition. The generic
[`src/lingtai/tools/tool_family/ANATOMY.md`](../tool_family/ANATOMY.md) owns the
reusable schema-composition/dispatch infrastructure this package composes; it
has no knowledge of File operations. The shared
[`src/lingtai/tools/CONTRACT.md`](../CONTRACT.md) owns the canonical public call
shape. The paired [`CONTRACT.md`](CONTRACT.md) is the single contract for this
surface — family envelope, risk posture, manual promise, and every per-action
input, output, cap, and error string.

## State

No mutable state lives here. The bound family holds only closures over granted
ports, an immutable backend snapshot, and its child registry; it owns no Agent
reference, cache, cursor, or settings writer. The private sidecar snapshot value
is never projected without full redaction. The read cap’s
runtime ceiling is observed through `FileIOPort` rather than stored. Manual
path selection is a stateless lookup; the package owns no installation mutation
or duplicate manual cache.

## Notes

Per `../CONTRACT.md` “Implementation independence”, the five operation children
share nothing but the family name and wire envelope: no operation module imports
another, and each could change shape without touching its siblings. The owner
settings provider deliberately imports their public policy constants so SHOW
cannot drift, without creating an operation-to-operation dependency. The
reserved settings/manual siblings do not change that independence. Co-location
in one package is ownership, not coupling; the package manual is the single
operational body source.
