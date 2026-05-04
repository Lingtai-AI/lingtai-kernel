# core/codex

Codex capability — durable self-memory across molts. A journal-shaped
knowledge store persisted in `codex/codex.json`. Each entry's id + title +
summary is always visible in the system prompt; content and supplementary
material load on demand via `view()`.

## Components

- `codex/__init__.py` — the entire capability in a single file. `get_description` (`codex/__init__.py:27-28`), `get_schema` (`codex/__init__.py:31-67`), `setup` (`codex/__init__.py:293-306`). The core class is `CodexManager` (`codex/__init__.py:71-290`).

## Public API

The `codex` tool exposes four actions:

| Action         | Description |
|----------------|-------------|
| `submit`       | Add a new entry (requires title + summary; content + supplementary optional) |
| `view`         | Read full content of entries by ID list; optionally include supplementary material |
| `consolidate`  | Merge multiple entries into one new entry (removes originals, creates replacement) |
| `delete`       | Remove entries by ID list |

## Internal Module Layout

```
codex/__init__.py
  ├── CodexManager.__init__         — loads entries from codex/codex.json, sets max_entries
  │
  ├── System prompt catalog
  │   └── _inject_catalog()         — renders entry index (id + title + summary) into system prompt
  │
  ├── Persistence
  │   ├── _load_entries()           — reads codex.json, back-fills missing fields for v0 entries
  │   ├── _save_entries()           — atomic write via tempfile + os.replace
  │   └── _make_id()                — SHA-256(content + timestamp)[:8]
  │
  ├── Dispatch
  │   └── handle()                  — action dispatcher (submit/view/consolidate/delete)
  │
  └── Actions
      ├── _submit()                 — validates fields, checks capacity, appends, saves, injects
      ├── _view()                   — resolves IDs, returns content (optionally + supplementary)
      ├── _consolidate()            — removes source entries, creates merged replacement
      └── _delete()                 — removes entries by ID, saves, injects
```

## Key Invariants

- **Capacity limit:** `DEFAULT_MAX_ENTRIES = 20`. Configurable via `codex_limit` kwarg in `setup()`. When full, `submit` returns an error suggesting consolidation.
- **Entry structure:** Each entry has `id`, `title`, `summary`, `content`, `supplementary`, `created_at`. Only `id + title + summary` are injected into the system prompt; `content` and `supplementary` load on demand.
- **ID generation:** `SHA-256(title + (content || summary) + created_at)[:8]`. The timestamp seed ensures uniqueness even when content is omitted.
- **Atomic persistence:** `_save_entries()` writes to a tempfile then `os.replace()`. On failure, the tempfile is cleaned up.
- **V0 migration:** `_load_entries()` back-fills `title`, `summary`, and `supplementary` fields for legacy entries that only had `content`.
- **Consolidate semantics:** Removes all source entries by ID, then appends a new merged entry. The consolidated entry gets a fresh `created_at` and ID.
- **System prompt injection:** The catalog is injected with `protected=True` so it cannot be overwritten by other prompt sections.

## Dependencies

- `lingtai.i18n` — `t()` for localized strings
- `lingtai_kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `avatar/`, `mcp/`, `library/`, `bash/`.
- **Kernel hooks:** `setup()` is called during capability initialization; `CodexManager._inject_catalog()` runs at boot to populate the system prompt before the first turn.
