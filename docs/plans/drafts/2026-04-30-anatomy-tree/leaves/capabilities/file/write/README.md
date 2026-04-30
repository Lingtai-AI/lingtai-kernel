# write

## What

Creates or overwrites a file with the given content. Parent directories are
created automatically (`mkdir -p` semantics). The entire file content is
replaced on every call — there is no append mode. Returns the byte length of
the written content on success.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `file_path` | string | **yes** | — | Absolute or relative path. |
| `content` | string | **yes** | — | Full file content (UTF-8). |

**Return** — `{status: "ok", path: <absolute>, bytes: <int>}`.
`path` is always the resolved absolute path. `bytes` is the UTF-8 encoded
byte length of the content (`len(content.encode("utf-8"))`).

**Errors:**
- Missing `file_path` → `{error: "file_path is required"}`
- I/O failure → `{error: "Cannot write <path>: <reason>"}`

**Behaviors:**
- `parent.mkdir(parents=True, exist_ok=True)` runs before every write
  (`services/file_io.py:77`), so nested directories are created on demand.
- Overwrites silently — no backup, no diff.
- Writes UTF-8 encoding (`services/file_io.py:78`).

## Source

- Handler: `core/write/__init__.py:36` — `handle_write`
- Schema: `core/write/__init__.py:20` — `get_schema`
- I/O backend: `services/file_io.py:75` — `LocalFileIOService.write`
  (mkdir at `:77`, write_text at `:78`)
- Registration: `core/write/__init__.py:49` — `agent.add_tool`

## Related

- **read** — counterpart for reading files.
- **edit** — surgical replacement (preferred for modifying existing files).
- **glob** — find paths to write to.
- Grouped capability: `file` bundles read + write + edit + glob + grep.
