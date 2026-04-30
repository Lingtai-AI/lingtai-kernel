# glob

## What

Finds files matching a glob pattern by walking the directory tree recursively.
Uses `os.walk` + `fnmatch` under the hood, matching the pattern against paths
relative to the search root. Returns sorted absolute paths.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `pattern` | string | **yes** | — | Glob pattern (e.g. `**/*.py`, `*.md`). |
| `path` | string | no | agent working dir | Root directory to search. |

**Return** — `{matches: [<path>, ...], count: <int>}`.
`matches` is a sorted list of absolute file paths. `count` is its length.

**Errors:**
- Missing `pattern` → `{error: "pattern is required"}`
- Walk failure → `{error: "Glob failed: <reason>"}`

**Behaviors:**
- Walks the search root via `os.walk` (`services/file_io.py:101`), collecting
  all files.
- Matches each file's path relative to the search root against the pattern
  using `fnmatch.fnmatch` (`services/file_io.py:106`).
- `fnmatch` has no special `**` semantics. Because `*` already matches
  `/` in `fnmatch`, a pattern like `*.py` matches files at *any* depth.
  Using `**/*.py` is harmless on Windows but **narrows** the result on
  POSIX: the literal `/` in the pattern requires a separator in the
  relative path, so root-level files (e.g. `validate.py`) are excluded
  while subdirectory files (e.g. `core/main.py`) match. Prefer bare
  glob patterns (e.g. `*.py`, `*.txt`) for recursive searches.
- Results are `sorted()` (`services/file_io.py:108`) — deterministic ordering.
- Only files are returned (not directories).

## Source

- Handler: `core/glob/__init__.py:36` — `handle_glob`
- Schema: `core/glob/__init__.py:20` — `get_schema`
- I/O backend: `services/file_io.py:95` — `LocalFileIOService.glob`
- Registration: `core/glob/__init__.py:49` — `agent.add_tool`

## Related

- **read** — read files found by glob.
- **grep** — search file contents (alternative discovery path).
- **edit** / **write** — modify files found by glob.
- Grouped capability: `file` bundles read + write + edit + glob + grep.
