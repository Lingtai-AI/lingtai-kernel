# OpenRouter Routing

## What

OpenRouter is a gateway that routes requests to upstream model providers
(Anthropic, DeepSeek, Qwen, etc.) behind a single OpenAI-compatible endpoint.
The kernel's `OpenRouterAdapter` is a thin subclass of `OpenAIAdapter` that pins
the base URL and injects provider-specific request parameters.

## When you need this

- **You're routing a model through OpenRouter** — use `provider/model` format (e.g. `anthropic/claude-sonnet-4`, `deepseek/deepseek-chat`). The adapter passes names through unchanged.
- **Reasoning text is appearing in agent thoughts unexpectedly** — check if `reasoning: {include: false}` is being applied; this adapter suppresses it by default.
- **You want to enable reasoning logs for debugging** — override `_adapter_extra_body()` to return `{"reasoning": {"include": True}}`.

## Contract

### Fixed base URL

`OpenRouterAdapter` defaults to `https://openrouter.ai/api/v1`. The URL is
hardcoded in `_OPENROUTER_BASE_URL` but can be overridden via `base_url`
constructor parameter (for staging or self-hosted proxies).

### Model name mapping

OpenRouter uses `provider/model` format (e.g. `anthropic/claude-sonnet-4`,
`deepseek/deepseek-chat`). The kernel passes model names through unchanged —
OpenRouter handles the routing. No model name transformation happens in the
adapter.

### Reasoning text suppression

`_adapter_extra_body()` returns `{"reasoning": {"include": False}}`. This tells
OpenRouter NOT to include reasoning text in responses. Reasoning tokens are still
billed, but the text is excluded to save bandwidth. The OpenAI response parser
already reads both `reasoning_content` and `reasoning` field names, so if you
flip `include` to `True`, reasoning text will appear in `LLMResponse.thoughts`.

### Inheritance from OpenAIAdapter

Everything else is inherited:
- Chat Completions API (`/chat/completions`)
- Context overflow recovery (`_run_with_overflow_recovery`)
- Orphan tool call pairing (`_pair_orphan_tool_calls`)
- Streaming with usage tracking
- Thinking/reasoning effort (`reasoning_effort` parameter)

### Provider registration

Registered as `"openrouter"` in `_register.py`. The factory pops the `model`
kwarg and passes remaining kwargs to `OpenRouterAdapter`. OpenRouter does NOT
have a separate `defaults.py` — its base URL is the only fixed configuration.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| OpenRouterAdapter class | `src/lingtai/llm/openrouter/adapter.py` | 1-52 |
| Base URL constant | `src/lingtai/llm/openrouter/adapter.py` | 26 |
| Reasoning suppression | `src/lingtai/llm/openrouter/adapter.py` | 49-52 |
| `_adapter_extra_body` hook | `src/lingtai/llm/openai/adapter.py` | 1114-1121 |
| Extra body merge logic | `src/lingtai/llm/openai/adapter.py` | 1095-1101 |
| OpenRouter registration | `src/lingtai/llm/_register.py` | 36-39 |
| Reasoning field parsing (both names) | `src/lingtai/llm/openai/adapter.py` | 93-104 |

## Related

- OpenAI adapter — parent class; all overflow recovery and streaming inherited
- DeepSeek leaf — also inherits OpenAI adapter but with different overrides
- `web-browsing` skill — OpenRouter API docs for model listing
