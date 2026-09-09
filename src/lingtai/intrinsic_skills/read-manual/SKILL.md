---
name: read-manual
description: "File pagination, truncation and long-line recovery: `next_offset` continuation, `line_truncated`, default/hard-cap limits, and when to switch to bash/grep/sed instead. Use when ordinary `file.read` cannot expose complete content."
version: 0.3.0
tags: [read, files, continuation, truncation, cap, pagination]
last_changed_at: "2026-09-08T00:00:00Z"
related_files:
- src/lingtai/tools/file/_read.py
- src/lingtai/tools/file/__init__.py
- src/lingtai/tools/file/manual/SKILL.md
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# Read depth

This is `file-manual`'s nested reference for read depth, not a separate action. Load it
for a large/complete read, a capped or spilled result, or
`line_truncated`; routine small reads can use the schema directly. After the
one-time manual lookup, resume the ordinary read; repeating the same manual
call is an error loop.

## Caps and bounded windows

The ordinary per-call `max_chars` budget is **100 000** characters. The runtime
hard ceiling is **200 000**; a smaller positive Host cap lowers the effective
ceiling. Larger requests are clamped; File exposes no setting to raise it. A read-level cap returns `truncated=true`; the runtime ceiling may
instead spill a result before it reaches the model. The effective cap appears as
`cap_chars` when read pagination truncates.

For an unknown or large file, start with a narrow `limit` (for example 100–200)
and an explicit `offset`/`max_chars`. To inspect cheap metadata first (not a
`read(dry_run=true)` mode):

```bash
python - <<'PY'
from pathlib import Path
p = Path('/path/to/file')
count = longest = 0
with p.open('r', encoding='utf-8') as f:
    for count, line in enumerate(f, 1):
        longest = max(longest, len(line))
print({'bytes': p.stat().st_size, 'lines': count, 'longest_chars': longest})
PY
```

## Complete reads

`truncated` means the character cap cut the requested window, not that the file
has more lines. For complete stable-file reads, stop only past `total_lines`;
handle errors, spills and partial physical lines before consuming content:

```python
offset = 1
while True:
    result = file(action="read", input={"file_path": path, "offset": offset,
        "limit": 200, "max_chars": None}, reasoning="page through the file")
    if result.get("status") in ("error", "spilled") or result.get("line_truncated"):
        raise RuntimeError("Use targeted processing; this is not a complete page")
    process(result["content"])
    offset = result.get("next_offset", offset + result["lines_shown"])
    if offset > result["total_lines"]:
        break
```

Truncated results also report `cap_chars`, `returned_chars`,
`requested_offset`, `requested_limit`, `last_returned_line`, and
`remaining_lines_estimate`; use them to detect gaps or an unexpectedly narrow
window. `next_offset` is the next physical line, so this loop has no overlap.

`line_truncated=true` is different: one physical line exceeded the cap, so the
result contains only its bounded prefix and `next_offset` skips to the next
line. The hidden tail cannot be recovered by another `read`; inspect that line
with targeted `sed`, `awk`, or `grep` instead.

## Spill recovery

If the runtime returns `status="spilled"`, inspect its `spill_path` artifact and
its `original_char_count`; a `preview` is not complete content. If the artifact
is unavailable or the result is still too large, repeat with a smaller
`limit`/`max_chars` or process the artifact with `bash`/`grep`/Python. Do not
interpret a spill or a capped page as the whole file.
