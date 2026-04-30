---
timeout: 180
---

# Test: OpenRouter Routing

## Setup

1. Open `src/lingtai/llm/openrouter/adapter.py`
2. This test verifies adapter structure via **code inspection**.

## Steps

1. **Inspect `OpenRouterAdapter.__bases__`** — verify it extends `OpenAIAdapter`.

2. **Inspect `_OPENROUTER_BASE_URL`** — verify `"https://openrouter.ai/api/v1"`.

3. **Inspect `__init__` default base_url** — verify `base_url or _OPENROUTER_BASE_URL`.

4. **Inspect `_adapter_extra_body()`** — verify returns `{"reasoning": {"include": False}}`.

5. **Trace the merge logic in `OpenAIAdapter._create_completions_session`** — given:
   - `_adapter_extra_body()` returns `{"reasoning": {"include": False}}`
   - No existing `extra_body` in `extra_kwargs`
   Expected: `extra_kwargs["extra_body"] = {"reasoning": {"include": False}}`

6. **Trace the merge with existing extra_body** — given:
   - `_adapter_extra_body()` returns `{"reasoning": {"include": False}}`
   - existing `extra_kwargs["extra_body"] = {"custom": 1}`
   Expected: `extra_kwargs["extra_body"] = {"reasoning": {"include": False}, "custom": 1}`

7. **Verify OpenRouter registration** in `_register.py` — verify it uses `OpenRouterAdapter` factory.

8. **Verify no model name transformation** — read `OpenRouterAdapter` and confirm no override of `create_chat` or model name mutation. Model names pass through to OpenRouter unchanged.

9. **Verify reasoning field parsing** — read `_parse_response` lines 89–100. Verify it checks both `reasoning_content` and `reasoning` field names (OpenRouter uses `reasoning`).

## Pass criteria

- [ ] `OpenRouterAdapter` extends `OpenAIAdapter`
- [ ] Base URL defaults to `https://openrouter.ai/api/v1`
- [ ] `_adapter_extra_body()` returns reasoning suppression
- [ ] Extra body merge is additive (doesn't clobber existing keys)
- [ ] No model name transformation in adapter
- [ ] Response parser handles both `reasoning_content` and `reasoning` fields

## Output template

```
## Test Results — OpenRouter Routing

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | Extends OpenAIAdapter | | |
| 2    | openrouter.ai/api/v1 | | |
| 3    | Default base_url correct | | |
| 4    | reasoning: {include: false} | | |
| 5    | Merge: extra_body set | | |
| 6    | Merge: additive, not clobber | | |
| 7    | OpenRouterAdapter factory | | |
| 8    | No model name transformation | | |
| 9    | Both reasoning field names parsed | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
