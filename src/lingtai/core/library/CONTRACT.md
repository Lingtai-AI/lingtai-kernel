# Library capability contract

`library` is the durable long-term knowledge capability. It stores bounded,
curated entries that survive molts and are summarized into the agent's system
prompt. This is the public contract for the capability implemented in
`src/lingtai/core/library/__init__.py`; the code remains the source of truth.

## Scope and compatibility

- Canonical capability name: `library`.
- Canonical tool name: `library`.
- Deprecated compatibility name: `codex`.

The `codex` capability name normalizes to `library` during agent capability
setup, and `setup()` still registers a `codex(...)` tool alias on the same
handler as `library(...)` for one migration window. Direct imports of
`lingtai.core.codex` also re-export the library implementation.

`library` now means durable knowledge. The skill catalog is `skills`. Legacy
manifests are normalized before setup:

| Manifest shape | Meaning after normalization |
|---|---|
| `"codex"` or `codex: {...}` | durable knowledge `library` |
| old bare `"library"` / `library: {}` | skill catalog `skills` |
| old `library: {paths: [...]}` | skill catalog `skills.paths` |
| `library: {library_limit: N}` | explicit durable knowledge `library` |
| `library: {}, skills: {...}` or `library: {library_limit: N}, skills: {...}` | explicit new meanings; both capabilities load |

When an explicit `skills` key is present, the `library` key is treated as durable
knowledge and must not carry legacy skill-catalog `paths`; put skill paths under
`skills.paths`.

## Tool surface

The schema requires `action` and accepts exactly four actions:

| Action | Required fields | Optional fields | Return on success |
|---|---|---|---|
| `submit` | `title`, `summary` | `content`, `supplementary` | `{status: "ok", id, entries, max}` |
| `view` | `ids` | `include_supplementary` | `{status: "ok", entries: [...]}` |
| `consolidate` | `ids`, `title`, `summary` | `content`, `supplementary` | `{status: "ok", id, removed}` |
| `delete` | `ids` | — | `{status: "ok", removed}` |

Unknown actions return an error and do not mutate state. Removed historical
actions such as `filter` and `export` are intentionally rejected.

### `submit`

`submit` trims string fields. `title` and `summary` must be non-empty after
trimming; `content` and `supplementary` are optional and default to empty
strings. If the entry limit has already been reached, `submit` fails with an
error that includes `entries` and `max` counts.

A successful entry has:

```json
{
  "id": "<8 hex chars>",
  "title": "...",
  "summary": "...",
  "content": "...",
  "supplementary": "...",
  "created_at": "<UTC ISO-8601 timestamp>"
}
```

The id is the first eight hex characters of
`sha256(title + (content or summary) + created_at)`. `created_at` is generated
at submit time, so equal titles/content submitted at different times get
different ids.

### `view`

`view` requires a non-empty `ids` list. Every requested id must exist; any
unknown id rejects the whole call with `Unknown library IDs: ...`. The result
preserves the requested id order. Each returned entry contains `id`, `title`,
`summary`, and `content`. `supplementary` is included only when
`include_supplementary` is truthy.

### `consolidate`

`consolidate` requires a non-empty `ids` list plus non-empty `title` and
`summary`. Every requested id must exist; any unknown id rejects the whole call
without mutation. On success, all requested ids are removed, one replacement
entry is appended with the same shape as `submit`, and the response reports the
new `id` plus `removed: len(ids)`. Duplicate ids in the request count toward
`removed` in the response but remove at most one stored entry per unique id.

### `delete`

`delete` requires a non-empty `ids` list. Every requested id must exist; any
unknown id rejects the whole call without mutation. On success, all matching
entries are removed and the response reports the actual number removed.

## Persistence

The store path is intentionally still `<agent>/codex/codex.json`. The rename is
user-facing; it is not a storage-v2 migration.

The file shape is:

```json
{
  "version": 1,
  "entries": [ ... ]
}
```

Writes are atomic within the store directory: create a temporary file in
`codex/`, write UTF-8 JSON with `ensure_ascii=False`, close it, then
`os.replace()` it over `codex.json`. If writing fails, the temporary file is
closed/unlinked best-effort and the exception is re-raised.

Reads are tolerant:

- Missing `codex/codex.json` means an empty library.
- Invalid JSON or an `OSError` while reading means an empty library.
- Legacy entries without `title` are backfilled from old `content`: title is
  the first 50 characters, summary is the first 200 characters, and
  supplementary becomes an empty string.

## Prompt injection

On setup and after every mutating action, the capability rewrites prompt
sections:

- If there are entries, protected prompt section `library` contains a compact
  catalog: total count/max count plus one line per entry with `[id] title:
  summary`, followed by a reminder to call `library(view, ids=[...])` for full
  content.
- If there are no entries, protected prompt section `library` is cleared.
- Protected prompt section `codex` is always cleared so the renamed section owns
  the catalog.

Only ids, titles, and summaries are always injected. Full `content` and
`supplementary` stay out of the prompt until loaded through `view`.

## Capacity configuration

`LibraryManager.DEFAULT_MAX_ENTRIES` is `50`.

`setup(agent, library_limit=N)` is canonical. `codex_limit=N` is accepted as a
compatibility kwarg. If both are present, `library_limit` wins. If neither is
present, the default of 50 applies.

The limit is enforced by `submit`, not by `consolidate`: consolidation removes
old entries before appending the replacement and is the intended path for
reducing pressure when the store is full.

## Source references

- Schema/actions: `src/lingtai/core/library/__init__.py:31-67`.
- Manager setup, capacity, and store path: `src/lingtai/core/library/__init__.py:71-89`.
- Prompt injection: `src/lingtai/core/library/__init__.py:95-115`.
- Load/save/id generation: `src/lingtai/core/library/__init__.py:121-159`.
- Dispatch and action implementations: `src/lingtai/core/library/__init__.py:165-299`.
- Tool registration and `codex` alias: `src/lingtai/core/library/__init__.py:302-337`.
- Capability rename normalization: `src/lingtai/capabilities/__init__.py:42-117`.
- Skill-catalog split/legacy `library.paths`: `src/lingtai/core/skills/__init__.py:11-25` and `src/lingtai/core/skills/__init__.py:314-348`.

## Verification

Primary tests:

```bash
python -m pytest tests/test_library_knowledge.py tests/test_skills.py -q
```

Relevant coverage:

- `tests/test_library_knowledge.py` covers registration, `codex` compatibility,
  submit/view/consolidate/delete behavior, schema shape, capacity enforcement,
  and id generation.
- `tests/test_skills.py::test_old_library_empty_config_normalizes_to_skills_only`
  through `test_new_library_and_skills_config_registers_both` cover the rename
  boundary between durable `library` and skill-catalog `skills`.
