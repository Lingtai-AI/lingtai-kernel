# Gemini Provider Quirks

## What

Gemini has the most divergent adapter in the kernel: a completely separate SDK
(`google-genai`), two chat session backends (Chat API and Interactions API),
thinking-gated model detection, tool name prefix stripping, and
Interactions-specific parameter sanitization. This leaf covers the unique
behaviors.

## When you need this

- **Gemini tool calls return names prefixed with `default_api:`** — this is expected; the adapter strips it automatically.
- **Thinking doesn't work on Gemini 2.x models** — thinking_config is only injected for models with major version ≥3.
- **The Interactions API rejects your tool schema with a 400** — check for `"required": []` in your parameter schemas; the Interactions API rejects empty arrays.

## Contract

### Two session backends

The Gemini adapter supports **two** session types:

| Backend | Class | When used | History model |
|---------|-------|-----------|---------------|
| **Interactions API** (default) | `InteractionsChatSession` | Always, except `json_schema` mode | Server-side (via `previous_interaction_id`) |
| **Chat API** | `GeminiChatSession` | When `json_schema` is set | Client-side (SDK-managed) |

`create_chat()` always returns an `InteractionsChatSession` unless `json_schema`
is provided (Interactions API does not support `response_mime_type`).

### Model naming: `default_api:` prefix stripping

Gemini's function calling returns tool names prefixed with `default_api:`. The
adapter strips this prefix in all response parsers:

```python
name=part.function_call.name.removeprefix("default_api:")
```

This applies to `_parse_response`, `_parse_interaction_response`, and all
`_record_model_turn` methods.

### Thinking support gating by major version

`_supports_thinking(model)` parses the model name and returns `True` only for
Gemini **3.x+** models. Gemini 2.x models (including 2.5-flash-preview) do NOT
get `thinking_config` injected.

```python
parts = model.lower().replace("models/", "").split("-")
major = int(parts[1].split(".")[0])
return major >= 3
```

### Interactions API: `required: []` sanitization

The Interactions API rejects `"required": []` (empty array) in tool parameter
schemas — unlike the Chat API. `_sanitize_parameters_for_interactions(params)`
strips the key when empty. Applied in `_build_interactions_tools()`.

### Input format: bare strings rejected

The Interactions API requires `input` to be a **list of dicts**, not a bare
string. `_convert_input()` wraps strings as `[{"type": "text", "text": ...}]`.

### Thinking config: always "high"

Both the orchestrator and sub-agent thinking levels are hardcoded to `"high"` in
`GEMINI_THINKING_MODEL` and `GEMINI_THINKING_SUB_AGENT`.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| Two session backends | `src/lingtai/llm/gemini/adapter.py` | 664-746 (create_chat) |
| `default_api:` prefix stripping | `src/lingtai/llm/gemini/adapter.py` | 82, 229, 468, 574 |
| Thinking support gating | `src/lingtai/llm/gemini/adapter.py` | 113-124 |
| `required: []` sanitization | `src/lingtai/llm/gemini/adapter.py` | 182-194 |
| String→list input wrapping | `src/lingtai/llm/gemini/adapter.py` | 595-636 |
| Thinking level hardcoded to "high" | `src/lingtai/llm/gemini/adapter.py` | 710-711, 780-781 |
| Interactions session resume | `src/lingtai/llm/gemini/adapter.py` | 748-826 |
| Interface converter (to_gemini) | `src/lingtai/llm/interface_converters.py` | 212-242 |

## Related

- `lingtai-kernel-anatomy reference/mcp-protocol.md` — Gemini MCP integration
- Anthropic cache-ttl leaf — contrast with Gemini's `cached_content_token_count`
- DeepSeek leaf — contrast model-name parsing approaches
