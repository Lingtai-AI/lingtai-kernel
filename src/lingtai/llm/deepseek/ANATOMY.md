# src/lingtai/llm/deepseek

DeepSeek adapter — thin OpenAI-compat wrapper with `reasoning_content` round-trip for thinking mode.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|------|-----|------|
| `__init__.py` | 0 | Empty |
| `adapter.py` | 131 | `DeepSeekAdapter`, `DeepSeekChatSession`, helpers |

### Classes

- **`DeepSeekAdapter(OpenAIAdapter)`** — `adapter.py:113` — pinned to DeepSeek endpoint, sets `_session_class`.
- **`DeepSeekChatSession(OpenAIChatSession)`** — `adapter.py:57` — overrides `_build_messages`, `send`, `send_stream`.

### Module-level

| Symbol | Line | Purpose |
|--------|------|---------|
| `_DEEPSEEK_BASE_URL` | 40 | `"https://api.deepseek.com"` |
| `_REASONING_PLACEHOLDER` | 54 | `"(reasoning omitted — not preserved across turns)"` |
| `_strip_placeholder_echoes` | 87 | Strips placeholder prefix from echoed reasoning in response thoughts |

## Connections

- **Inherits**: `OpenAIAdapter` / `OpenAIChatSession` from `../openai/adapter.py`.
- **No additional imports**: Only `openai` SDK (inherited), no new external deps.
- **Hook points used**: `_build_messages` (overridden on session), `_session_class` (overridden on adapter, `adapter.py:116`).

## Composition

### LLMAdapter ABC overrides (`DeepSeekAdapter`)

| Method | Line | Notes |
|--------|------|-------|
| `__init__` | 118 | Calls `super().__init__()` with `base_url=base_url or _DEEPSEEK_BASE_URL` |
| `_session_class` | 116 | Set to `DeepSeekChatSession` (parent's `create_chat` uses this) |

All other `LLMAdapter` methods (`create_chat`, `generate`, `make_tool_result_message`, `is_quota_error`) are **inherited unchanged** from `OpenAIAdapter`.

### ChatSession method overrides (`DeepSeekChatSession`)

| Method | Line | Notes |
|--------|------|-------|
| `_build_messages` | 60 | Calls `super()._build_messages()`, then injects `reasoning_content` placeholder on every assistant turn from the first `tool_calls` onward |
| `send` | 76 | Calls `super().send()`, then `_strip_placeholder_echoes(response)` |
| `send_stream` | 81 | Calls `super().send_stream()`, then `_strip_placeholder_echoes(response)` |

### `reasoning_content` round-trip (`adapter.py:60-74`)

DeepSeek V4 thinking mode **requires** `reasoning_content` on all assistant turns once any tool call has occurred. The contract (empirically determined):

1. Pre-tool-call assistant turns: no `reasoning_content` needed.
2. First assistant turn with `tool_calls`: `seen_tool_call = True`.
3. **All** subsequent assistant turns (tool-call and plain-text) must carry `reasoning_content`.

The adapter injects `_REASONING_PLACEHOLDER` as the value. DeepSeek validates **presence**, not content — any non-empty string works.

### Echo stripping (`adapter.py:87-110`)

DeepSeek V4's cache-hit fast-path echoes the last `reasoning_content` from context verbatim as a "thought". `_strip_placeholder_echoes`:
- If `thought.startswith(_REASONING_PLACEHOLDER)`: strips prefix, keeps only genuine tail.
- Pure-echo (no tail): dropped entirely (empty `cleaned` list item removed).

### Thinking blocks

- **Extraction**: Inherited from `OpenAIAdapter` — reads `reasoning_content` field from OpenAI response.
- **Not preserved**: The actual reasoning text is **not** stored across turns. Only the placeholder is injected. This is intentional — reasoning is scratch work; agent memory lives in system prompt and conversation.

### Authentication

- **API key only** — inherited from `OpenAIAdapter` (`openai.OpenAI(api_key=...)`).
- **Base URL**: defaults to `https://api.deepseek.com`, overridable.

## State

- `DeepSeekAdapter`: inherits `_client`, `_gate`, `_session_class`.
- `DeepSeekChatSession`: inherits all from `OpenAIChatSession`.

## Notes

- **Minimal footprint**: 131 LOC, ~15 lines of unique logic. All OpenAI wire format handling (tool shapes, streaming, etc.) is inherited.
- **No `defaults.py`**: DeepSeek adapter is not registered via the `DEFAULTS` config pattern — likely invoked directly by `LLMService`.
- **Placeholder string is stable**: Changing it would break compatibility with existing stored conversations that have the old placeholder in assistant turns.
- Git history: 3 commits (feature, placeholder fix, echo-stripping fix).
