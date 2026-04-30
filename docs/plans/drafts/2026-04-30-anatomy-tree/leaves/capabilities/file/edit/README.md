# edit

## What

Performs precise string replacement in an existing file. The `old_string` must
be found exactly once in the file (or use `replace_all=true` for multiple
occurrences). Returns the number of replacements performed. This is the
preferred tool for modifying existing files — `write` replaces the entire file.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `file_path` | string | **yes** | — | Absolute or relative path. |
| `old_string` | string | **yes** | — | Exact substring to find. |
| `new_string` | string | **yes** | — | Replacement text. |
| `replace_all` | boolean | no | `false` | Replace all occurrences. |

**Return** — `{status: "ok", replacements: <int>}`.

**Errors:**
- Missing `file_path` → `{error: "file_path is required"}`
- File not found → `{error: "File not found: <path>"}`
- `old_string` not in file → `{error: "old_string not found in <path>"}`
- Multiple matches without `replace_all` → `{error: "old_string found N times — use replace_all=true or provide more context"}`

**Behaviors:**
- Reads file, performs replacement, writes back — atomic from the agent's
  perspective but not OS-atomic.
- With `replace_all=false` (default), replaces only the first occurrence via
  `content.replace(old, new, 1)` (`core/edit/__init__.py:61`).
- With `replace_all=true`, replaces all via `content.replace(old, new)`
  (`core/edit/__init__.py:59`).
- Counts occurrences before deciding (`content.count(old)` at `:53`).

## Source

- Handler: `core/edit/__init__.py:38` — `handle_edit`
- Schema: `core/edit/__init__.py:20` — `get_schema`
- Ambiguity check: `core/edit/__init__.py:53-57`
- I/O backend: `services/file_io.py:80-93` — `LocalFileIOService.edit`
  (note: the handler uses read+write directly, not the service's `edit` method)
- Registration: `core/edit/__init__.py:68` — `agent.add_tool`

## Related

- **read** — inspect file before editing.
- **write** — full-file replacement (when edit is impractical).
- **glob** / **grep** — locate the file and string to edit.
- Grouped capability: `file` bundles read + write + edit + glob + grep.
