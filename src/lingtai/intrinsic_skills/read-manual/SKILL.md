---
name: read-manual
description: "Complete guide for the read tool: continuation workflow, next_offset pagination, line_truncated handling, runtime tool-result spill vs read-level pagination, 100k read default / 200k runtime hard cap, and when to use bash/grep/sed for truncated lines. Use when implementing complete file-read workflows, handling large files, or understanding cap and truncation semantics."
version: 0.1.0
tags: [read, files, continuation, truncation, cap, pagination]
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/tools/read/__init__.py
- src/lingtai/intrinsic_skills/file-manual/SKILL.md
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# Read Manual

Complete workflow for reading files with the `read` tool. Load it for large
files, complete-content workflows, truncation, or `line_truncated` results.

For basic tool choice (read vs write vs edit vs grep vs glob), UTF-8 policy, and
the shared `action="manual"` versus ordinary-call rule, see the `file-manual`
skill — including that repeating an identical manual call is an error loop, not
progress. After this manual returns, continue the original task with an ordinary
read.

## Two caps

| Cap | Value | Configurable |
|---|---|---|
| `read` per-call page budget | **100 000 chars** (default) | yes, via per-call `max_chars` |
| Runtime tool-result hard ceiling | **200 000 chars** | no — not by agents or prompts |

`max_chars` requests a smaller or larger chunk for one call. Values above the
hard ceiling are clamped to 200 000; the effective value appears as `cap_chars`
when the result is truncated. Do not assume the old 10 000- or 8 000-character
limits from earlier versions.

These two caps act at different layers:

1. **Read-level pagination** — exceeding the effective per-call budget returns
   `truncated=true` plus continuation metadata. You page on with `next_offset`.
2. **Runtime preventive ceiling** — `ToolExecutor` applies the non-configurable
   200k cap to every tool result just before it reaches the LLM wire. A result
   still over the ceiling is written to `<workdir>/tmp/tool-results/<…>` and
   replaced on the wire by a compact manifest containing `status="spilled"`,
   `spill_path`, `artifact`, `preview`, and `original_char_count`.

A well-formed `read` result normally stays under the outer ceiling because
`max_chars` is clamped to 200k. If you still see a spill manifest from `read`,
inspect the `spill_path` artifact, then re-call `read` with a smaller
`limit`/`max_chars` or process the artifact via `bash`/`grep`/Python.

## Metadata/stats preflight

For unknown or large files, inspect cheap metadata before reading big chunks.
This replaces a dedicated `read(dry_run=true)` mode.

```bash
python - <<'PY'
from pathlib import Path
p = Path('/path/to/file')
count = max_len = max_line = 0
with p.open('r', encoding='utf-8', errors='replace') as f:
    for i, line in enumerate(f, 1):
        count = i
        if len(line) > max_len:
            max_len, max_line = len(line), i
print({'bytes': p.stat().st_size, 'lines': count,
       'longest_line': max_line, 'longest_chars': max_len})
PY
```

Use the result to choose the window:

- `offset` — where to begin or resume (1-based).
- `limit` — how many lines to request; a tight `limit` (e.g. `50`) narrows the
  window, and a large `offset` with a small `limit` reads an arbitrary slice.
- `max_chars` — per-call character budget (default 100k, max 200k).

## Complete-content workflow

For any file that may exceed the cap:

1. Call `read` with the desired `offset` (default 1) and `limit`.
2. If `truncated` is absent or `false`, the whole requested range was returned —
   done. If `true`, continue.
3. Re-call with `offset=next_offset`, keeping the same `limit`, until
   `truncated` is absent or `false`.

```python
offset = 1
while True:
    r = read({"file_path": path, "offset": offset, "limit": 200})
    process(r["content"])
    if not r.get("truncated"):
        break
    offset = r["next_offset"]
```

## Continuation metadata fields

When `truncated=true` the result includes:

| Field | Meaning |
|---|---|
| `truncated` | `true` — content was cut |
| `cap_chars` | effective character cap used for this call |
| `returned_chars` | characters actually returned |
| `requested_offset` | 1-based start line you passed |
| `requested_limit` | line limit you passed |
| `last_returned_line` | 1-based line number of the last line shown |
| `next_offset` | pass this as `offset` on the next call to continue |
| `remaining_lines_estimate` | approximate lines still unread |
| `line_truncated` | `true` only when a single physical line exceeded the cap |

## Handling line_truncated=true

`line_truncated=true` appears when a single physical line is longer than the cap.
Then:

- The result contains only a **prefix** of that line (bounded by the cap).
- `next_offset` points to the **next line**, not to a mid-line continuation.
- The hidden tail of the long line is **not recoverable** through further `read`
  calls.

To inspect a long line fully, use targeted local processing instead of `read`:

```bash
sed -n '42p' /path/to/file                        # print one specific line
awk '{print NR, length($0)}' /path/to/file | head -20   # characters per line
grep -n "pattern" /path/to/file                   # search within a long line

# Extract a byte range from a long line
python - <<'PY'
with open('/path/to/file') as f:
    for i, line in enumerate(f, 1):
        if i == 42:
            print(line[0:2000], "...", line[-500:], sep="\n")
            break
PY
```

## Quick checklist

Before calling `read`:

- Large file? Probe with `limit=100`–`200`, or run the preflight above.
- Need the whole file? Use the continuation loop.
- `line_truncated=true`? Switch to `bash`/`grep`/`sed`/Python.
- `status=spilled`? Read the `spill_path` artifact or reduce `limit`.
- Need a specific region? Pass `offset` and a tight `limit`.

## Manual versus ordinary reads

For backward compatibility, an ordinary read may omit `action` or set
`action="read"`. Use `action="manual"` only as a one-time entry to this guide.
After the manual returns, continue the original task with an ordinary read; do
not repeat the same manual call. Repeating it is an error loop, not progress.
