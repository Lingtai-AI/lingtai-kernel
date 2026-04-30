# OpenAI cached_tokens — None→0 Coercion

## What

OpenAI's API sometimes returns `None` for `prompt_tokens_details.cached_tokens`
(e.g. when no caching was used, or on older models). The kernel must coerce this
`None` to `0` to maintain consistent token accounting across providers. This leaf
documents every code path where this coercion happens.

## When you need this

- **Token accounting shows `cached_tokens = None` in logs or data exports** — this leaf explains the coercion that prevents it.
- **You're building a token budget monitor** — understand that `cached_tokens` may be 0 even when caching was used (OpenAI sometimes omits the field).
- **You're adding a new OpenAI-compatible provider** — ensure your adapter applies the same `or 0` guard.

## Contract

### Chat Completions API path

In `_parse_response()`:

```python
cached = getattr(raw.usage, "prompt_tokens_details", None)
cached_tokens = (getattr(cached, "cached_tokens", 0) or 0) if cached else 0
```

The `or 0` handles the case where `cached_tokens` is explicitly `None` (the `or`
falls through from `None` to `0`). The outer `if cached else 0` handles the case
where `prompt_tokens_details` itself is `None`.

### Responses API path

In `_parse_responses_api_response()`:

```python
cached = getattr(raw.usage, "input_tokens_details", None)
cached_tokens = (getattr(cached, "cached_tokens", 0) or 0) if cached else 0
```

Note the field name difference: Responses API uses `input_tokens_details` (not
`prompt_tokens_details`). Same coercion pattern.

### Streaming path — Chat Completions

Streaming usage arrives on the final `chunk.usage` object. The same
`prompt_tokens_details` / `cached_tokens` extraction is applied in
`OpenAIChatSession.send_stream()` at the chunk-usage extraction point.

### Streaming path — Responses API

When using the Responses API (`use_responses=True`), streaming is event-based
rather than chunk-based. Usage arrives on the `response.completed` event. The
coercion uses `input_tokens_details` (not `prompt_tokens_details`) to match the
Responses API field naming convention.

### What is NOT coerced

The `input_tokens` (`prompt_tokens`) and `output_tokens` (`completion_tokens`)
fields use `or 0` on `raw.usage.prompt_tokens` etc. This handles the `None` case
for all top-level token counters too.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| Chat Completions cached_tokens coercion | `src/lingtai/llm/openai/adapter.py` | 109-110 |
| Responses API cached_tokens coercion | `src/lingtai/llm/openai/adapter.py` | 154-155 |
| Chat Completions streaming cached_tokens coercion | `src/lingtai/llm/openai/adapter.py` | 682-696 |
| Responses API streaming cached_tokens coercion | `src/lingtai/llm/openai/adapter.py` | 889-907 |
| Input/output token coercion | `src/lingtai/llm/openai/adapter.py` | 111-113 |

## Related

- `lingtai-kernel-anatomy reference/file-formats.md` — UsageMetadata schema
- Anthropic cache-ttl leaf — Anthropic's equivalent coercion (different field names)
- Gemini leaf — Gemini uses `cached_content_token_count` (always numeric, no None)
