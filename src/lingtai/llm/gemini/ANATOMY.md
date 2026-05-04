# src/lingtai/llm/gemini

Gemini adapter — `google-genai` SDK with Chat API and Interactions API, thinking budget, function declarations.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|------|-----|------|
| `__init__.py` | 3 | Re-exports `GeminiAdapter`, `GeminiChatSession` |
| `adapter.py` | 881 | Adapter + 2 session classes + helpers |
| `defaults.py` | 4 | `DEFAULTS` dict: `api_key_env=GEMINI_API_KEY`, `model=gemini-3-flash-preview` |

### Classes

- **`GeminiAdapter(LLMAdapter)`** — `adapter.py:640` — wraps `genai.Client`.
- **`GeminiChatSession(ChatSession)`** — `adapter.py:139` — Chat API session (used for `json_schema` mode).
- **`InteractionsChatSession(ChatSession)`** — `adapter.py:328` — Interactions API session (server-side history, primary path).

### Helper functions

| Function | Line | Purpose |
|----------|------|---------|
| `_build_function_declarations` | 38 | `FunctionSchema` → `types.FunctionDeclaration` |
| `_parse_response` | 54 | Chat API response → `LLMResponse`; reads `thought` parts, `function_call` parts |
| `_supports_thinking` | 109 | Model version check: Gemini 3+ only |
| `_thinking_config` | 123 | Level string → `types.ThinkingConfig(include_thoughts=True, thinking_level=...)` |
| `_sanitize_parameters_for_interactions` | 178 | Strip `"required": []` (Interactions API rejects empty array) |
| `_build_interactions_tools` | 193 | `FunctionSchema` → Interactions API tool dicts (`type: "function"`) |
| `_parse_interaction_response` | 210 | Interactions API response → `LLMResponse`; reads `function_call`, `text`, `thought` outputs |
| `_convert_history_to_turns` | 260 | Chat API history dicts → Interactions `TurnParam` format |

## Connections

- **Imports from `lingtai_kernel`**: `ChatSession`, `FunctionSchema`, `LLMResponse`, `ToolCall`, `UsageMetadata`, `ToolResultBlock`, `ChatInterface`, `StreamingAccumulator`
- **Imports from `lingtai`**: `LLMAdapter` ABC, `to_gemini` converter (lazy in `create_chat`)
- **External**: `google.genai`, `google.genai.errors`, `google.genai.types`
- **No inheritance from other adapters** (standalone implementation)

## Composition

### LLMAdapter ABC overrides (`GeminiAdapter`)

| Method | Line | Notes |
|--------|------|-------|
| `create_chat` | 660 | Routes to Interactions API (default) or Chat API (json_schema mode); wraps in `_GatedSession` |
| `generate` | 824 | One-shot via `client.models.generate_content`; gated |
| `make_tool_result_message` | 856 | Returns `ToolResultBlock` with `id=tool_call_id or tool_name` |
| `is_quota_error` | 866 | `ClientError` with code 429 or `RESOURCE_EXHAUSTED` in message |

### Dual session architecture

**`GeminiChatSession`** (Chat API — `adapter.py:139`):
- Only used when `json_schema` is set (response schema requires Chat API).
- `send()` at `adapter.py:152` delegates directly to `self._chat.send_message(message)`.
- `get_history()` at `adapter.py:157` returns `model_dump()` of chat history.

**`InteractionsChatSession`** (Interactions API — `adapter.py:328`):
- Primary path. Server-side conversation state via `previous_interaction_id`.
- `send()` at `adapter.py:364`: converts input → `client.interactions.create()` → records model turn in `_client_history`.
- `send_stream()` at `adapter.py:409`: passes `stream=True`; events: `interaction.start`, `content.delta` (text/function_call/thought), `interaction.complete`.
- `commit_tool_results()` at `adapter.py:516`: appends to `_client_history` (not API call).
- `update_tools()` at `adapter.py:521`: replaces `config_kwargs["tools"]` with Interactions format.
- `update_system_prompt()` at `adapter.py:532`: replaces `config_kwargs["system_instruction"]`.
- `get_history()` at `adapter.py:540`: returns `[{_interaction_id}, {_client_history}]`.
- `get_client_history()` at `adapter.py:547`: returns client-side mirror.
- `_convert_input()` at `adapter.py:590`: str → `[{"type": "text", "text": ...}]` (bare strings rejected by Interactions API).

### Provider-specific shape conversions

- **Tool calls**: `function_call` parts → `ToolCall(name=..., args=..., id=...)`. Note `removeprefix("default_api:")` at `adapter.py:78` strips Gemini's default prefix.
- **Tool results**: `ToolResultBlock` → `{"type": "function_result", "call_id": ..., "result": ..., "name": ...}` (`adapter.py:618-626`).
- **Interactions tools**: Different from Chat API — `{"type": "function", "name": ..., "description": ..., "parameters": ...}` (flat, not nested under `function_declarations`).
- **`"required": []` stripped**: `_sanitize_parameters_for_interactions()` at `adapter.py:178` — Interactions API rejects empty required arrays.

### Thinking blocks

- **Chat API**: `adapter.py:65-70` — `part.thought == True` + `part.text` → `thoughts.append(text)`.
- **Interactions API**: `adapter.py:230-236` — `output.type == "thought"` → iterates `output.summary` for text items.
- **Thinking config**: Only sent for Gemini 3+ models (`_supports_thinking()` at `adapter.py:109`). Levels: `"high"`/`"default"` → `GEMINI_THINKING_MODEL` (high), other → `GEMINI_THINKING_SUB_AGENT` (also high currently, `adapter.py:706-707`).
- **Interactions thinking**: Set via `generation_config.thinking_level` (`adapter.py:784-785`), not `ThinkingConfig`.

### Streaming protocol (Interactions API)

- `adapter.py:443`: iterates `client.interactions.create(stream=True)`.
- Event types: `interaction.start` (captures ID), `content.delta` with `delta.type` = `text` / `function_call` / `thought`, `interaction.complete` (usage + final ID).
- Function call deltas arrive atomically (full args in one event, `adapter.py:462-467`) — no incremental JSON merging needed.
- `StreamingAccumulator` used for text + thoughts; tool calls added directly via `acc.add_tool()`.

### Session fork / resume

- **Resume by `interaction_id`**: `adapter.py:796-804` — server retrieves history automatically.
- **Seed from history**: `adapter.py:810-821` — if `interface` has entries and no `interaction_id`, converts via `to_gemini()` → sets `_pending_seed_turns` on session → first `send()` prepends them.
- **Client-side mirror**: `_client_history` at `adapter.py:357` tracks all turns for `get_client_history()`.

### Authentication

- **API key only** — `genai.Client(api_key=...)` at `adapter.py:645`.
- **No base_url override** (Google SDK doesn't support it).
- **No OAuth**.
- **HTTP options**: `timeout_ms` passed via `types.HttpOptions(timeout=...)` with retry options.

## State

- `GeminiAdapter`: `_client` (SDK), `_default_model`, `_use_interactions` (bool), `_gate`.
- `GeminiChatSession`: `_chat` (genai chat), `_context_window_size`, `_interface`.
- `InteractionsChatSession`: `_client` (genai client), `_model`, `_config_kwargs`, `_interaction_id`, `_context_window_size`, `_interface`, `_pending_seed_turns`, `_client_history`.

### Usage mapping (Interactions API, `adapter.py:239-248`)

| Interactions field | LLMResponse field |
|---|---|
| `total_input_tokens` | `input_tokens` |
| `total_output_tokens` | `output_tokens` |
| `total_thought_tokens` | `thinking_tokens` |
| `total_cached_tokens` | `cached_tokens` |

### Usage mapping (Chat API, `adapter.py:88-98`)

| Chat field | LLMResponse field |
|---|---|
| `prompt_token_count` | `input_tokens` |
| `candidates_token_count` | `output_tokens` |
| `thoughts_token_count` | `thinking_tokens` |
| `cached_content_token_count` | `cached_tokens` |

## Notes

- **`default_api:` prefix**: Gemini sometimes prefixes tool names with `default_api:`. Both parsers strip it with `removeprefix()` (`adapter.py:78,225,464`).
- **Interactions API is default**: `use_interactions = True` is hardcoded at `adapter.py:674`. Chat API only used for `json_schema` mode.
- **`json_schema` enforcement**: Chat API path sets `response_mime_type="application/json"` + `response_schema` (`adapter.py:727-728`). Interactions API does not support this — falls back to Chat API.
- **`client` property**: Escape hatch at `adapter.py:879`.
- **`make_bytes_part`**: Static utility at `adapter.py:874` for binary input (images/documents).
- Git history: 4 commits.
