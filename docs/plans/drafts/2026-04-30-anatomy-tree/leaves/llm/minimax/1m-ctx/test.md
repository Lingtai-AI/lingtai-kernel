---
timeout: 180
---

# Test: MiniMax 1M Context Window

## Setup

1. Open `src/lingtai/llm/minimax/adapter.py` and `src/lingtai/llm/minimax/defaults.py`
2. This test verifies adapter structure and configuration via **code inspection**.

## Steps

1. **Inspect `MiniMaxAdapter.__bases__`** — verify it extends `AnthropicAdapter`.

2. **Inspect default base URL** — verify `effective_url = base_url or "https://api.minimax.io/anthropic"`.

3. **Inspect `defaults.py`** — verify:
   - `api_compat = "anthropic"`
   - `base_url = "https://api.minimax.io/anthropic"`
   - `api_key_env = "MINIMAX_API_KEY"`
   - `model = "MiniMax-M2.7-highspeed"`
   - `max_rpm = 120`

4. **Inspect `_register.py` lines 31–34** — verify MiniMax factory pops `model` kwarg and passes remaining kwargs to `MiniMaxAdapter`.

5. **Inspect `AnthropicAdapter.create_chat` context_window handling** — verify `context_window` is passed through to `AnthropicChatSession.__init__()` and stored as `_context_window`.

6. **Verify no overflow recovery** — search `anthropic/adapter.py` for `overflow` or `trim_context`. Expected: **no matches** (overflow recovery is OpenAI-only).

## Pass criteria

- [ ] `MiniMaxAdapter` is a direct subclass of `AnthropicAdapter`
- [ ] Default base URL is `https://api.minimax.io/anthropic`
- [ ] `max_rpm = 120` in defaults
- [ ] `api_compat = "anthropic"` routes through Anthropic code path
- [ ] `context_window` parameter flows through to session
- [ ] No automatic overflow recovery in Anthropic path

## Output template

```
## Test Results — MiniMax 1M Context Window

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | Extends AnthropicAdapter | | |
| 2    | api.minimax.io/anthropic | | |
| 3    | All 5 defaults correct | | |
| 4    | Factory pops model, passes rest | | |
| 5    | context_window flows to session | | |
| 6    | No overflow recovery in Anthropic path | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
