---
timeout: 180
---

# Test: DeepSeek Provider Quirks

## Setup

1. Open `src/lingtai/llm/deepseek/adapter.py`
2. This test verifies the reasoning_content round-trip logic via **code inspection**.

## Steps

1. **Inspect `_REASONING_PLACEHOLDER`** — verify value is `"(reasoning omitted — not preserved across turns)"`.

2. **Trace `_build_messages` injection logic** — given messages:
   ```
   [assistant(text="hi"), user("q"), assistant(tool_calls=[...]), user(tool_results), assistant(text="done")]
   ```
   Expected: `reasoning_content` added to the 3rd and 5th assistant turns (from first tool_call onward), NOT to the 1st.

3. **Trace `_strip_placeholder_echoes`** — given `response.thoughts = ["(reasoning omitted — not preserved across turns) actually I think..."]`.
   Expected: `["actually I think..."]`.

4. **Trace `_strip_placeholder_echoes`** — given `response.thoughts = ["(reasoning omitted — not preserved across turns)"]` (pure echo, no tail).
   Expected: `[]` (empty, dropped).

5. **Trace `_strip_placeholder_echoes`** — given `response.thoughts = ["some real thought"]` (no placeholder prefix).
   Expected: `["some real thought"]` (unchanged).

6. **Trace `_strip_placeholder_echoes`** — given `response.thoughts = []` or `None`.
   Expected: no-op.

7. **Inspect `DeepSeekAdapter._session_class`** — verify it is `DeepSeekChatSession`.

8. **Inspect `_DEEPSEEK_BASE_URL`** — verify `"https://api.deepseek.com"`.

9. **Verify DeepSeek registration** in `_register.py` — verify it uses `DeepSeekAdapter` factory, not generic custom.

## Pass criteria

- [ ] Placeholder injected from first tool_call onward only
- [ ] Placeholder echo stripped from response thoughts
- [ ] Pure echoes dropped (empty list)
- [ ] Real thoughts without prefix preserved
- [ ] `_session_class = DeepSeekChatSession`
- [ ] Base URL hardcoded correctly

## Output template

```
## Test Results — DeepSeek Provider Quirks

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | Exact placeholder string | | |
| 2    | Injected on turns 3, 5 only | | |
| 3    | Prefix stripped, tail kept | | |
| 4    | Pure echo → empty list | | |
| 5    | No prefix → unchanged | | |
| 6    | None/empty → no-op | | |
| 7    | DeepSeekChatSession | | |
| 8    | api.deepseek.com | | |
| 9    | DeepSeekAdapter factory | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
