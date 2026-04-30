---
timeout: 180
---

# Test: Gemini Provider Quirks

## Setup

1. Open `src/lingtai/llm/gemini/adapter.py`
2. This test verifies code behaviors via **inspection and unit-level reasoning**.
   No live API key needed for most checks.

## Steps

1. **Inspect `_supports_thinking("gemini-3-flash-preview")`** — Expected: `True`.
   Call: `model="gemini-3-flash-preview"` → parts = `["gemini", "3", "flash", "preview"]` → major = `3` → `True`.

2. **Inspect `_supports_thinking("gemini-2.5-flash-preview")`** — Expected: `False`.
   parts = `["gemini", "2", "5", "flash", "preview"]` → major = `2` → `False`.

3. **Inspect `_supports_thinking("models/gemini-3-pro")`** — Expected: `True`.
   `models/` prefix stripped, major = `3`.

4. **Inspect `_sanitize_parameters_for_interactions({"required": []})`** — Expected: `{}` (key removed).

5. **Inspect `_sanitize_parameters_for_interactions({"required": ["a"]})`** — Expected: `{"required": ["a"]}` (unchanged).

6. **Inspect `_sanitize_parameters_for_interactions({})`** — Expected: `{}` (no-op).

7. **Read `create_chat()` lines 674–695** — verify `use_interactions = True` (hardcoded), and `json_schema` presence routes to Chat API path.

8. **Read `_convert_input("hello")` in `InteractionsChatSession`** — verify returns `[{"type": "text", "text": "hello"}]`.

9. **Grep for `removeprefix("default_api:")`** — count occurrences. Expected: ≥3 (in `_parse_response`, `_parse_interaction_response`, `_record_model_turn`, streaming delta handler).

10. **Inspect defaults.py** — verify `api_key_env = "GEMINI_API_KEY"` and default model = `"gemini-3-flash-preview"`.

## Pass criteria

- [ ] `_supports_thinking` returns `True` for 3.x, `False` for 2.x
- [ ] `_sanitize_parameters_for_interactions` strips empty `required` arrays
- [ ] `create_chat` defaults to Interactions API
- [ ] `_convert_input` wraps bare strings in list-of-dicts
- [ ] All response parsers strip `default_api:` prefix
- [ ] Thinking level hardcoded to `"high"` for both orchestrator and sub-agent

## Output template

```
## Test Results — Gemini Provider Quirks

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1    | True (gemini-3) | | |
| 2    | False (gemini-2) | | |
| 3    | True (models/ prefix) | | |
| 4    | required key removed | | |
| 5    | required key kept | | |
| 6    | no-op | | |
| 7    | Interactions API default | | |
| 8    | Wrapped in list | | |
| 9    | ≥3 removeprefix calls | | |
| 10   | GEMINI_API_KEY, gemini-3-flash-preview | | |

Overall: PASS / FAIL / INCONCLUSIVE (reason: ...)
```
