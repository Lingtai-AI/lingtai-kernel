# grep

## What

Searches file contents using a regex pattern across all files in a directory
tree (or a single file). Returns matching lines with file paths and line
numbers. Stops early at `max_matches` to bound output size. Skips binary and
unreadable files silently.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `pattern` | string | **yes** | — | Python `re` regex pattern. |
| `path` | string | no | agent working dir | File or directory to search. |
| `glob` | string | no | `"*"` | Filename filter via `fnmatch` (applied post-scan on results). |
| `max_matches` | integer | no | `200` | Upper bound on results. |

**Return** — `{matches: [{file, line, text}, ...], count: <int>, truncated: <bool>}`.
`file` is absolute path, `line` is 1-based line number, `text` is the full
matching line. `truncated` is `true` when `count >= max_matches`.

**Errors:**
- Missing `pattern` → `{error: "pattern is required"}`
- Regex/search failure → `{error: "Grep failed: <reason>"}`

**Behaviors:**
- If `path` is a file, searches only that file. If a directory, walks
  recursively via `sorted(search_path.rglob("*"))` (`services/file_io.py:120`).
- Each file is read as UTF-8. `UnicodeDecodeError` and `PermissionError`
  cause the file to be skipped silently (`services/file_io.py:127`).
- Regex is compiled via `re.compile(pattern)` (`services/file_io.py:113`);
  `re.search` is used per line, not `re.match`.
- Results capped at `max_matches` — returns immediately when reached
  (`services/file_io.py:132-133`).

> **Note on `glob` filtering:** The `glob` parameter is applied as a
> **post-scan filter** — the underlying `LocalFileIOService.grep` scans all
> files, then the handler discards results whose filename does not match
> the glob via `fnmatch`. This means `max_matches` caps the raw scan,
> not the filtered output. If many non-matching files consume the cap,
> the filtered result may be smaller than `max_matches` while `truncated`
> is still `true`.

## Source

- Handler: `core/grep/__init__.py:39` — `handle_grep`
- Schema: `core/grep/__init__.py:20` — `get_schema` (glob at `:27`)
- Glob filter: `core/grep/__init__.py:47-58` — post-scan `fnmatch`
- I/O backend: `services/file_io.py:110` — `LocalFileIOService.grep`
- Registration: `core/grep/__init__.py:69` — `agent.add_tool`

## Related

- **glob** — pattern-based file discovery (name, not content).
- **read** — read full file after grep narrows the target.
- **edit** — modify files after grep locates the string.
- Grouped capability: `file` bundles read + write + edit + glob + grep.
