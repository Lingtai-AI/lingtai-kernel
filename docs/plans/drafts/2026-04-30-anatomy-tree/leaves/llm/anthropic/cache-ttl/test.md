---
timeout: 180
---

# Test: Anthropic Prompt Caching — TTL Behavior

## Setup

1. Open `src/lingtai/llm/anthropic/adapter.py`
2. Focus on `_build_system_with_cache`, `_build_system_batches_with_cache`, `_build_tools`, and `_parse_response`

## Steps

1. **Inspect `_build_system_with_cache("hello")`** — call with a simple string. Verify it returns a single-element list with `cache_control: {"type": "ephemeral"}` on the block.

2. **Inspect `_build_system_batches_with_cache(["a", "b", "c"])`** — call with 3 batches. Verify:
   - First two blocks have `cache_control` markers.
   - Last block does **not** have a marker.

3. **Inspect `_build_system_batches_with_cache(["a"])`** — single batch. Verify it falls back to single-block form (marker present).

4. **Inspect `_build_system_batches_with_cache([])`** — empty. Verify returns `[]`.

5. **Inspect `_build_system_batches_with_cache(["", "b", ""], max_breakpoints=3)`** — empty batches dropped. Verify only the `["b"]` batch remains with 1 non-empty batch, triggering single-block fallback.

6. **Inspect `_build_tools([schema1, schema2], cache_tools=True)`** — verify `cache_control` is on the **last** tool only.

7. **Inspect `_build_tools([schema1], cache_tools=False)`** — verify no `cache_control` on any tool.

8. **Read `_parse_response` lines 144–167** — verify the normalization formula: `input_tokens = raw_input + cache_read + cache_write`, `cached_tokens = cache_read`.

## Pass criteria

- [ ] `_build_system_with_cache` returns exactly one block with `cache_control`
- [ ] `_build_system_batches_with_cache` with N≥2 batches marks first N-1 batches (capped)
- [ ] Last batch never gets a cache marker in batched mode
- [ ] `_build_tools(cache_tools=True)` marks only the last tool
- [ ] Token normalization sums all three Anthropic counters into `input_tokens`
- [ ] `cached_tokens` equals `cache_read_input_tokens` (not `cache_creation`)

## Output template

```
## Test Results — Anthropic Prompt Caching

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | Single block with cache_control | | |
| 2    | First 2 marked, last unmarked | | |
| 3    | Single-block fallback with marker | | |
| 4    | Empty list | | |
| 5    | Empty batches dropped | | |
| 6    | Cache marker on last tool only | | |
| 7    | No cache markers | | |
| 8    | Formula: input = raw + read + write | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
