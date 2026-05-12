# core/library

Library capability — durable long-term knowledge across molts. The capability is the renamed successor of the old `codex` capability, still reading and writing the legacy `codex/codex.json` store for compatibility. Each entry's id + title + summary is injected into the `library` system-prompt section; content and supplementary material load on demand.

## Components

- `library/__init__.py` — the capability implementation. `get_description` (`__init__.py:27-28`), `get_schema` (`__init__.py:31-67`), `LibraryManager` (`__init__.py:71-299`), and `setup` (`__init__.py:302-333`). `CodexManager` remains as an import alias (`__init__.py:336-337`).
- `library/CONTRACT.md` — public contract companion for the implementation; it cites the code ranges above and the rename-normalization source in `src/lingtai/capabilities/__init__.py`.

## Connections

- `lingtai.capabilities` maps canonical `library` and deprecated `codex` capability names here (`../../capabilities/__init__.py:15-24`).
- `setup()` registers the canonical `library` tool and a deprecated `codex` tool alias on the same handler (`__init__.py:315-328`).
- `_inject_catalog()` writes the `library` prompt section and clears the old `codex` section so the renamed section owns the catalog (`__init__.py:95-115`).

## Public API

The `library` tool exposes four actions:

| Action | Description |
|---|---|
| `submit` | Add a new entry (requires title + summary; content + supplementary optional) |
| `view` | Read full content of entries by ID list; optionally include supplementary material |
| `consolidate` | Merge multiple entries into one new entry (removes originals, creates replacement) |
| `delete` | Remove entries by ID list |

The deprecated `codex` tool alias accepts the same schema and dispatches to the same manager during the migration window.

## State

- Persistent store: `<agent>/codex/codex.json` (`__init__.py:88`). The path intentionally keeps the old directory name in this rename-only change.
- Prompt state: `library` section contains the catalog; `codex` section is cleared when this capability injects (`__init__.py:98-115`).
- Capacity: `DEFAULT_MAX_ENTRIES = 50`; `library_limit` is canonical and `codex_limit` is accepted as a compatibility kwarg (`__init__.py:74-86`).

## Notes

- This change is naming-first, not storage-v2: the on-disk JSON shape and `codex/` directory stay unchanged while user-facing concepts move from Codex to Library.
- The old `lingtai.core.codex` module is now a compatibility wrapper; new code should import `lingtai.core.library`.
- For the stable behavior contract (tool actions, response shapes, persistence, prompt injection, limits, and compatibility rules), read `src/lingtai/core/library/CONTRACT.md` before editing this capability.
