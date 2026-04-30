# read

## What

Reads a text file and returns its contents with line numbers prepended to each
line. Supports pagination via `offset` and `limit` so large files can be
inspected in slices. Relative paths resolve against the agent's working
directory.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `file_path` | string | **yes** | — | Absolute or relative path. |
| `offset` | integer | no | `1` | 1-based start line. |
| `limit` | integer | no | `2000` | Max lines to return. |

**Return** — `{content, total_lines, lines_shown}`.
`content` is a single string; each line is formatted as `<line_num>\t<text>`.
Lines retain their original trailing newlines.

**Errors:**
- Missing `file_path` → `{status: "error", message: "file_path is required"}`
- File not found → `{status: "error", message: "File not found: <path>"}`
- Other I/O errors → `{status: "error", message: "Cannot read <path>: <reason>"}`

**Behaviors:**
- Binary files that fail UTF-8 decode raise a generic read error.
- `offset` clamps to 0 internally (`max(0, offset - 1)`); requesting offset
  beyond EOF returns empty content with `lines_shown: 0`.
- Relative paths are joined to `agent._working_dir`.

## Source

- Handler: `core/read/__init__.py:39` — `handle_read`
- Schema: `core/read/__init__.py:22` — `get_schema`
- I/O backend: `services/file_io.py:72` — `LocalFileIOService.read`
- Registration: `core/read/__init__.py:59` — `agent.add_tool`

## Related

- **write** — creates or overwrites files (the counterpart).
- **edit** — surgical string replacement (reads then writes).
- **glob** — find files before reading them.
- **grep** — search file contents without reading entire files.
- Grouped capability: `file` bundles read + write + edit + glob + grep
  (`capabilities/__init__.py:37`).
