---
timeout: 180
---

# Test: OpenAI cached_tokens — None→0 Coercion

## Setup

1. Open `src/lingtai/llm/openai/adapter.py`
2. This test verifies code paths via **inspection** — no live API needed.

## Steps

1. **Read `_parse_response` lines 104–114** — trace the expression:
   `getattr(cached, "cached_tokens", 0) or 0` when `cached = None`.
   Expected: `cached_tokens = 0`.

2. **Read `_parse_response` lines 104–114** — trace when `cached` exists but
   `cached_tokens = None`. Expected: `None or 0` → `0`.

3. **Read `_parse_response` lines 104–114** — trace when `cached.cached_tokens = 42`.
   Expected: `42 or 0` → `42`.

4. **Read `_parse_responses_api_response` lines 148–158** — verify same pattern
   with `input_tokens_details` instead of `prompt_tokens_details`.

5. **Read streaming path lines 677–692** — verify the `prompt_tokens_details`
   extraction in the chunk-usage handler uses the same `or 0` pattern.

6. **Grep the entire adapter** for `cached_tokens` — verify no path assigns
   `cached_tokens` without an `or 0` guard.

## Pass criteria

- [ ] Every `cached_tokens` extraction uses `(getattr(..., "cached_tokens", 0) or 0)`
- [ ] No path allows `None` to leak into `UsageMetadata.cached_tokens`
- [ ] Both Chat Completions and Responses API paths covered
- [ ] Streaming path matches non-streaming pattern

## Output template

```
## Test Results — OpenAI cached_tokens Coercion

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | cached_tokens = 0 when cached=None | | |
| 2    | cached_tokens = 0 when field=None | | |
| 3    | cached_tokens = 42 when field=42 | | |
| 4    | Responses API same pattern | | |
| 5    | Streaming path same pattern | | |
| 6    | No unguarded cached_tokens | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
