# core/codex

Deprecated compatibility wrapper for the renamed durable-knowledge capability. New code should use `src/lingtai/core/library/ANATOMY.md` and import `lingtai.core.library`; this package remains only so old imports of `lingtai.core.codex` and old capability lookups continue through the rename migration.

## Components

- `codex/__init__.py` — re-exports `LibraryManager`, `CodexManager` alias, `PROVIDERS`, `get_description`, `get_schema`, and `setup` from `lingtai.core.library` (`__init__.py:1-20`).

## Connections

- `lingtai.capabilities` maps deprecated capability name `codex` to `lingtai.core.library`, not this wrapper, for normal capability setup (`../../capabilities/__init__.py:15-24`).
- Direct Python imports may still import this package; they receive the library implementation.

## Composition

- **Parent:** `src/lingtai/core/`.
- **Canonical replacement:** `src/lingtai/core/library/ANATOMY.md`.

## State

No state is owned here. The underlying knowledge store remains `<agent>/codex/codex.json`, owned by `lingtai.core.library` during this rename-only migration.
