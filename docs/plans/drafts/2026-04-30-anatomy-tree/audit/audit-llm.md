# Audit: §Source References in LLM Provider Anatomy Leaves

**Auditor**: audit-llm  
**Date**: 2026-04-30  
**Status**: ✅ **FIXES APPLIED** — all 6 Source tables corrected, missing reference added  
**Kernel commit**: HEAD at audit time  
**Methodology**: Every row in every `## Source` table verified against actual file content using `grep` (keyword anchor) → `sed` (ground-truth line numbers). All files confirmed to exist. Note: the `read` tool's internal line numbering is offset by +4 for some adapter files — this report uses `grep`/`sed` as authoritative.

---

## Systematic Finding

**All five `adapter.py` files exhibit a consistent +4 line offset** in their Source references. The module docstrings in each adapter grew by 4 lines after the READMEs were authored. This shifts every function/block down by 4 lines from what the READMEs state.

**Affected files** (all references shifted by +4):
- `src/lingtai/llm/anthropic/adapter.py`
- `src/lingtai/llm/openai/adapter.py`
- `src/lingtai/llm/gemini/adapter.py`
- `src/lingtai/llm/deepseek/adapter.py`
- `src/lingtai/llm/openrouter/adapter.py`

**Unaffected files** (references already correct):
- `src/lingtai/llm/_register.py`
- `src/lingtai/llm/minimax/defaults.py`
- `src/lingtai/llm/interface_converters.py`

---

## Leaf 1: `anthropic/cache-ttl/README.md`

**File**: `src/lingtai/llm/anthropic/adapter.py` (750 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | Single-block cache | 65–80 | **69–84** | ❌ | `_build_system_with_cache` def at L69; return dict at L78–84. Claimed L65–66 are tail of `_build_tools`. |
| 2 | Batched cache with breakpoints | 83–119 | **87–123** | ❌ | `_build_system_batches_with_cache` def at L87; `return blocks` at L123. Claimed L83–86 are tail of `_build_system_with_cache`. |
| 3 | Tool cache_control injection | 46–62 | **50–66** | ❌ | `_build_tools` def at L50; actual injection at L64–65; `return tools` at L66. Claimed L46–62 starts with `# Helpers` header and **stops before L65** (the injection line). |
| 4 | Token normalization (non-stream) | 144–167 | **148–171** | ❌ | Normalization comment at L148; logic at L153–163; debug log ends at L171. Claimed L144–147 are thinking-text parsing (unrelated). |
| 5 | Token normalization (stream) | 450–471 | **454–475** | ❌ | Same normalization in `send_stream`. L454 = comment; L475 = debug log end. Claimed L450–453 are error-handling revert (unrelated). |
| 6 | System prompt batches update | 523–535 | **527–539** | ❌ | `update_system_prompt_batches` def at L527; body ends at L539. Claimed L523–526 are tail of `update_system_prompt`. |

**Summary**: 0 ✅ · 0 ⚠️ · 6 ❌  
**Correction**: Add +4 to all start/end line numbers. Also adjust range ends to capture complete functions.

---

## Leaf 2: `openai/cached-tokens-coercion/README.md`

**File**: `src/lingtai/llm/openai/adapter.py` (1183 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | Chat Completions cached_tokens coercion | 105–106 | **109–110** | ❌ | `prompt_tokens_details` → `cached_tokens` at L109–110. Claimed L105–106 are blank + `# Token usage` comment. |
| 2 | Responses API cached_tokens coercion | 150–151 | **154–155** | ❌ | `input_tokens_details` → `cached_tokens` at L154–155. Claimed L150–151 are blank + `# Token usage` comment. |
| 3 | Streaming cached_tokens coercion | 677–692 | **682–696** | ❌ | Chat Completions streaming path. `cached = getattr(...)` at L682; `cached_tokens=cached_tokens` at L695; UsageMetadata close at L696. Claimed L677–679 are `_chunks()` re-stitching (unrelated). |
| 4 | Input/output token coercion | 107–109 | **111–113** | ❌ | `usage = UsageMetadata(` at L111; `prompt_tokens or 0` at L112; `completion_tokens or 0` at L113. Claimed L107–109 include `# Token usage` + `usage = UsageMetadata()`. |
| 5 | **MISSING**: Responses API streaming cached_tokens coercion | — | **889–907** | ❌ | **Not listed.** In `send_stream()` when `use_responses=True`, the `response.completed` event at L887 triggers `input_tokens_details` coercion at L890–907. Distinct code path from row 3. |

**Summary**: 0 ✅ · 0 ⚠️ · 5 ❌ (4 shifted + 1 missing)  
**Correction**: Add +4 to all existing references. Add new row for Responses API streaming path.

### Missing reference detail (L889–907)

```python
# L887:             elif event.type == "response.completed":
# L888:                 response_id = event.response.id
# L889:                 if event.response.usage:
# L890:                     cached = getattr(event.response.usage, "input_tokens_details", None)
# L891:                     cached_tokens = (getattr(cached, "cached_tokens", 0) or 0) if cached else 0
# L892:                     usage = UsageMetadata(
# ...
# L907:                     )
```

Uses `input_tokens_details` (Responses API field), not `prompt_tokens_details` (Chat Completions field). Row 3 covers Chat Completions streaming (L682–696); this is Responses API streaming — a separate code path.

---

## Leaf 3: `gemini/README.md`

**File**: `src/lingtai/llm/gemini/adapter.py` (885 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | Two session backends (create_chat) | 660–742 | **664–746** | ❌ | `create_chat` def at L664; returns at L744–746. Claimed L660–663 are tail of `__init__`. |
| 2 | `default_api:` prefix stripping (3 sites) | L78, L225, L464 | **L82, L229, L468** | ❌ | All `removeprefix("default_api:")` calls shifted by +4. Note: 4th site at L574 also exists. |
| 3 | Thinking support gating | 109–120 | **113–124** | ❌ | `_supports_thinking()` def at L113; ends at L124. Claimed L109–112 are tail of `_parse_response` + return. |
| 4 | `required: []` sanitization | 178–190 | **182–194** | ❌ | `_sanitize_parameters_for_interactions` def at L182; ends at L194. Claimed L177–181 are section headers. |
| 5 | String→list input wrapping | 590–632 | **595–636** | ❌ | `_convert_input()` def at L595; ends at L636. Claimed L590–594 are `_record_model_turn` tail. |
| 6 | Thinking level hardcoded to "high" | L706–707, L776–777 | **L710–711, L780–781** | ❌ | Both `GEMINI_THINKING_MODEL = "high"` assignments shifted by +4. |
| 7 | Interactions session resume | 744–822 | **748–826** | ❌ | `_create_interactions_session()` def at L748; ends at L826. Claimed L744–747 are tail of `create_chat`. |
| 8 | Interface converter (to_gemini) | `interface_converters.py` 212–242 | **212–242** | ✅ | `to_gemini()` at L212–222 + `_to_gemini_block()` at L225–242. No shift (unaffected file). |

**Summary**: 1 ✅ · 0 ⚠️ · 7 ❌  
**Correction**: Add +4 to all line numbers in `gemini/adapter.py`. Consider adding L574 as 4th `removeprefix` site.

---

## Leaf 4: `minimax/1m-ctx/README.md`

**Files**: `minimax/adapter.py` (27 lines), `minimax/defaults.py` (7 lines), `_register.py` (93 lines), `anthropic/adapter.py` (750 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | MiniMaxAdapter class | `minimax/adapter.py` 1–21 | **1–27** | ❌ | Class at L13–27; `__init__` body (L22–27: effective_url, super(), _setup_gate) missed. L21 is only `def __init__(` signature. |
| 2 | MiniMax defaults | `minimax/defaults.py` 1–7 | **1–7** | ✅ | Entire file, exact match. |
| 3 | MiniMax registration | `_register.py` 31–34 | **31–34** | ✅ | Exact match. `_minimax` factory. |
| 4 | Context window storage | `anthropic/adapter.py` 282–292 | **286–296** | ❌ | `context_window` param at L286; `self._context_window = context_window` at L296. Claimed L282–292 stops 4 lines early, missing the actual assignment. |
| 5 | Context window storage (cont.) | `anthropic/adapter.py` 560–561 | **560–565** | ⚠️ | L560: `__dict__.update` (copies context_window). L564–565: explicit `context_window()` accessor. Claimed range captures update but misses accessor. Off by 4 at end. |
| 6 | Molt pressure monitoring | `anthropic/adapter.py` N/A | **N/A** | ✅ | Correctly noted as kernel-level. |

**Summary**: 3 ✅ · 1 ⚠️ · 2 ❌

---

## Leaf 5: `deepseek/README.md`

**File**: `src/lingtai/llm/deepseek/adapter.py` (135 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | reasoning_content contract docs | 1–33 | **1–33** | ✅ | Module docstring through L33. Exact match (docstring was not affected by shift). |
| 2 | Placeholder string | L54 | **L58** | ❌ | `_REASONING_PLACEHOLDER` at L58. Claimed L54 is comment about DeepSeek's cache-hit echo. |
| 3 | Placeholder injection in `_build_messages` | 60–74 | **64–78** | ❌ | `_build_messages()` def at L64; placeholder injection at L77; return at L78. Claimed L60–63 are class def + docstring. |
| 4 | Echo stripping | 87–110 | **91–114** | ❌ | `_strip_placeholder_echoes()` def at L91; ends at L114. Claimed L87–88 are tail of `send_stream()` (call site). |
| 5 | DeepSeekAdapter class | 113–131 | **117–135** | ❌ | `DeepSeekAdapter` def at L117; `__init__` ends at L135. Claimed L113–116 are blank + tail of echo-stripping function. |
| 6 | DeepSeek registration | `_register.py` 84–89 | **84–89** | ✅ | Exact match. `_deepseek` factory + `register_adapter`. |

**Summary**: 2 ✅ · 0 ⚠️ · 4 ❌  
**Correction**: Add +4 to all line numbers in `deepseek/adapter.py`.

---

## Leaf 6: `openrouter/routing/README.md`

**Files**: `openrouter/adapter.py` (52 lines), `openai/adapter.py` (1183 lines), `_register.py` (93 lines)

| # | Behavior | Claimed Lines | Corrected Lines | Verdict | Notes |
|---|----------|--------------|----------------|---------|-------|
| 1 | OpenRouterAdapter class | `openrouter/adapter.py` 1–48 | **1–52** | ⚠️ | Captures `__init__` (L32–47) but misses `_adapter_extra_body` (L49–52). Include entire file (1–52) since it's only 52 lines. |
| 2 | Base URL constant | `openrouter/adapter.py` L22 | **L26** | ❌ | `_OPENROUTER_BASE_URL` at L26. Claimed L22 is blank line. |
| 3 | Reasoning suppression | `openrouter/adapter.py` 45–48 | **49–52** | ❌ | `_adapter_extra_body` at L49–52. Claimed L45–48 are `__init__` parameters — completely wrong content AND shifted. |
| 4 | `_adapter_extra_body` hook | `openai/adapter.py` 1110–1117 | **1114–1121** | ❌ | Hook definition at L1114–1121. Claimed L1110–1113 are tail of `create_chat` (unrelated). |
| 5 | Extra body merge logic | `openai/adapter.py` 1092–1097 | **1095–1101** | ❌ | Comments at L1095–1097; merge code at L1098–1101. Claimed L1092–1094 are `reasoning_effort` logic (unrelated). |
| 6 | OpenRouter registration | `_register.py` 36–39 | **36–39** | ✅ | Exact match. `_openrouter` factory. |
| 7 | Reasoning field parsing (both names) | `openai/adapter.py` 89–100 | **93–104** | ❌ | Comment block at L93–98; `reasoning = (` at L99; `getattr(message, "reasoning", None)` at L101; `thoughts.append` at L104. Claimed L89–90 are blank + `text = message.content`. |

**Summary**: 1 ✅ · 1 ⚠️ · 5 ❌  
**Correction**: Row 3 is worst — wrong content (not just shifted). Rows 4,5,7 reference `openai/adapter.py` which also has the +4 shift.

---

## Grand Summary

| Leaf | ✅ | ⚠️ | ❌ | Primary Issue |
|------|----|----|----|---------------|
| anthropic/cache-ttl | 0 | 0 | 6 | Systematic +4 shift in adapter.py |
| openai/cached-tokens | 0 | 0 | 5 | Systematic +4 shift + 1 missing reference |
| gemini | 1 | 0 | 7 | Systematic +4 shift in adapter.py |
| minimax/1m-ctx | 3 | 1 | 2 | Mixed: shift + truncation |
| deepseek | 2 | 0 | 4 | Systematic +4 shift in adapter.py |
| openrouter/routing | 1 | 1 | 5 | Shift + wrong content at row 3 |
| **Total** | **7** | **2** | **29** | |

---

## Recommended Fixes

### Priority 1 — Add missing reference (openai/cached-tokens)

Add new Source table row:

```markdown
| Responses API streaming cached_tokens coercion | `src/lingtai/llm/openai/adapter.py` | 889–907 |
```

This is a genuinely distinct code path: `send_stream()` → `response.completed` event → `input_tokens_details` / `cached_tokens` coercion. Not covered by existing row 3 (Chat Completions streaming at L682–696).

### Priority 2 — Apply +4 line corrections to all adapter.py references

The correction is mechanical: add +4 to every line number referenced from these 5 files:

| File | All references need |
|------|-------------------|
| `anthropic/adapter.py` | +4 |
| `openai/adapter.py` | +4 |
| `gemini/adapter.py` | +4 |
| `deepseek/adapter.py` | +4 |
| `openrouter/adapter.py` | +4 |

Also extend range **ends** to capture complete functions (see per-leaf tables above for exact corrected ranges).

### Priority 3 — Fix openrouter/routing row 3 (wrong content, not just shifted)

Claimed: `openrouter/adapter.py` 45–48 ("Reasoning suppression").  
Actual: Lines 45–48 are `__init__` parameters (base_url, timeout_ms, max_rpm).  
Correct: **49–52** (`_adapter_extra_body` returning `{"reasoning": {"include": False}}`).

This is not just a shift — the claimed lines contain completely unrelated code.

### Priority 4 — Fix minimax/1m-ctx row 1 (truncated class)

Claimed: `minimax/adapter.py` 1–21.  
Actual: Class body extends to L27 (`self._setup_gate(max_rpm)`).  
Correct: **1–27** (entire file).

### Priority 5 — Add 4th `removeprefix` site to gemini (optional)

The README lists 3 sites (L82, L229, L468 after correction). A 4th exists at L574 in `_record_model_turn`. Consider adding it for completeness.

---

## Corrected Reference Quick-Reference Table

For convenient copy-paste when fixing the READMEs:

### anthropic/cache-ttl
```
| Single-block cache                                     | src/lingtai/llm/anthropic/adapter.py | 69-84   |
| Batched cache with breakpoints                         | src/lingtai/llm/anthropic/adapter.py | 87-123  |
| Tool cache_control injection                           | src/lingtai/llm/anthropic/adapter.py | 50-66   |
| Token normalization (non-stream)                       | src/lingtai/llm/anthropic/adapter.py | 148-171 |
| Token normalization (stream)                           | src/lingtai/llm/anthropic/adapter.py | 454-475 |
| System prompt batches update                           | src/lingtai/llm/anthropic/adapter.py | 527-539 |
```

### openai/cached-tokens-coercion
```
| Chat Completions cached_tokens coercion                | src/lingtai/llm/openai/adapter.py | 109-110 |
| Responses API cached_tokens coercion                   | src/lingtai/llm/openai/adapter.py | 154-155 |
| Streaming cached_tokens coercion                       | src/lingtai/llm/openai/adapter.py | 682-696 |
| Input/output token coercion                            | src/lingtai/llm/openai/adapter.py | 111-113 |
| Responses API streaming cached_tokens coercion         | src/lingtai/llm/openai/adapter.py | 889-907 |  ← NEW
```

### gemini
```
| Two session backends                                   | src/lingtai/llm/gemini/adapter.py       | 664-746        |
| `default_api:` prefix stripping                        | src/lingtai/llm/gemini/adapter.py       | 82, 229, 468, 574 |
| Thinking support gating                                | src/lingtai/llm/gemini/adapter.py       | 113-124        |
| `required: []` sanitization                            | src/lingtai/llm/gemini/adapter.py       | 182-194        |
| String→list input wrapping                             | src/lingtai/llm/gemini/adapter.py       | 595-636        |
| Thinking level hardcoded to "high"                     | src/lingtai/llm/gemini/adapter.py       | 710-711, 780-781 |
| Interactions session resume                            | src/lingtai/llm/gemini/adapter.py       | 748-826        |
| Interface converter (to_gemini)                        | src/lingtai/llm/interface_converters.py | 212-242        |
```

### minimax/1m-ctx
```
| MiniMaxAdapter class            | src/lingtai/llm/minimax/adapter.py | 1-27  |
| MiniMax defaults                | src/lingtai/llm/minimax/defaults.py | 1-7  |
| MiniMax registration            | src/lingtai/llm/_register.py       | 31-34 |
| Context window storage          | src/lingtai/llm/anthropic/adapter.py | 286-296 |
| Context window storage (cont.)  | src/lingtai/llm/anthropic/adapter.py | 560-565 |
| Molt pressure monitoring        | src/lingtai/llm/anthropic/adapter.py | N/A (kernel-level) |
```

### deepseek
```
| reasoning_content contract docs                | src/lingtai/llm/deepseek/adapter.py | 1-33   |
| Placeholder string                             | src/lingtai/llm/deepseek/adapter.py | 58     |
| Placeholder injection in `_build_messages`     | src/lingtai/llm/deepseek/adapter.py | 64-78  |
| Echo stripping                                 | src/lingtai/llm/deepseek/adapter.py | 91-114 |
| DeepSeekAdapter class                          | src/lingtai/llm/deepseek/adapter.py | 117-135|
| DeepSeek registration                          | src/lingtai/llm/_register.py        | 84-89  |
```

### openrouter/routing
```
| OpenRouterAdapter class           | src/lingtai/llm/openrouter/adapter.py | 1-52    |
| Base URL constant                 | src/lingtai/llm/openrouter/adapter.py | 26      |
| Reasoning suppression             | src/lingtai/llm/openrouter/adapter.py | 49-52   |
| `_adapter_extra_body` hook        | src/lingtai/llm/openai/adapter.py     | 1114-1121|
| Extra body merge logic            | src/lingtai/llm/openai/adapter.py     | 1095-1101|
| OpenRouter registration           | src/lingtai/llm/_register.py          | 36-39   |
| Reasoning field parsing (both)    | src/lingtai/llm/openai/adapter.py     | 93-104  |
```
