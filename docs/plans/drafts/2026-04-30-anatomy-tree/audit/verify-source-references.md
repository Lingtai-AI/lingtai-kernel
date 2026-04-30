# Verify Source References — Anatomy Leaf §Source Audit

## Purpose

Verify §Source table entries in anatomy README.md files against actual kernel source code.

## Inputs

- `leaf`: path to the README.md under audit
- `src_root`: kernel source root (e.g. `lingtai-kernel/src/lingtai/`)

## Method

### 1. Collect references

Read the leaf README.md. Find the `## Source` table. Extract each row's:
- `What` — the described content
- `File` — the source file (may be relative to `src_root` or use short form)
- `Line(s)` — single line, range `N-M`, or `full file`

### 2. Resolve file paths

For each `File`:
- Try `src_root + File` directly
- If not found, `find` under `src_root` by basename
- Record existence. If missing → ❌ immediately.

### 3. Verify each reference

For each row with numeric lines:
- `read` the file at `offset=<start>, limit=<end-start+1>`
- Check: does the `What` column's key token (function name, class name, field name, string literal) appear in those lines?
- **Tolerance:** if content is within ±3 lines, note as ⚠️; beyond ±3 or absent → ❌
- Check 1 line before the start and 1 line after the end — these boundaries are where off-by-one hides

For `full file` references:
- Confirm the file exists and is non-empty

### 4. Write results

Per leaf:
```
## capabilities/shell/<leaf>/
- ✅ file:N-M — accurate
- ⚠️ file:N-M — shifted +K
- ❌ file:N-M — <reason>
Summary: X ✅ / Y ⚠️ / Z ❌
```

End with global summary table and assessment.

## Common Pitfalls

| Pitfall | How to avoid |
|---------|-------------|
| Range starts at first content line, misses `def` or `@decorator` on prior line | Check `claimed_start - 1` for the signature |
| Range ends at last prose line, misses closing delimiter (`"""`, `}`, `)`) | Check `claimed_end + 1` for delimiters |
| Class name changed in refactor | Grep for the class/function name first, then verify line range |
| File moved to subpackage | `find` by basename under entire `src/` tree |
| `full file` claimed but file renamed | `ls` the exact path |

## Efficiency Notes

- Batch-read files that appear in multiple leaves (common for `__init__.py` files)
- Read the full file once, then verify all line references against the cached content
- For leaves that share a source file, verify all at once rather than per-leaf
