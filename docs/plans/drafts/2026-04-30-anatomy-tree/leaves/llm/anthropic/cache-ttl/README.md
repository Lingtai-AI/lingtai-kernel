# Anthropic Prompt Caching — TTL Behavior

## What

Anthropic's API supports **prompt caching** via `cache_control` markers injected
into system prompts and tool definitions. The kernel injects these markers
automatically on every request to reduce input-token billing. This leaf documents
the injection strategy, the four-slot budget, and how cached tokens are reported.

## When you need this

- **Anthropic costs are unexpectedly high** — check if `cached_tokens` in logs is 0 (cache miss).
- **You're adding or changing system prompt sections** — the batched breakpoint strategy limits you to 3 system + 1 tool = 4 slots max.
- **You see `cache_creation_input_tokens` spiking** — your prompt prefix changed (cache churn) or the 5-min TTL expired between requests.

## Contract

### System prompt caching

The kernel builds system prompts as **cached content blocks** (list of dicts with
`cache_control: {type: "ephemeral"}`). Two modes:

| Mode | Function | When used |
|------|----------|-----------|
| Single-block | `_build_system_with_cache(str)` | Single system string; one breakpoint at end |
| Batched | `_build_system_batches_with_cache(batches)` | Multiple mutation-frequency batches; breakpoints on all but the last |

**Batched mode** partitions the system prompt by mutation frequency (immovable →
rarely-mutated → per-idle). The **last batch is left unmarked** because it is
volatile (grows every idle) and caching it would churn. Breakpoints are capped at
`max_breakpoints - 1` inside the system list, so combined with the tools
breakpoint the total stays at `max_breakpoints` (default 3 system + 1 tools = 4,
matching Anthropic's **4-slot-per-request** cap).

### Tool caching

`_build_tools(schemas, cache_tools=True)` places a single `cache_control` marker
on the **last tool** in the list. This is always called with `cache_tools=True`
from `create_chat` and `update_tools`.

### Token accounting normalization

Anthropic reports input tokens as three separate counters:
- `input_tokens` — tokens **after** the last cache breakpoint
- `cache_read_input_tokens` — tokens served from cache
- `cache_creation_input_tokens` — tokens written to cache

The kernel **normalizes** these into a single `input_tokens` field matching
OpenAI/Gemini semantics:

```
input_tokens = raw_input + cache_read + cache_write
cached_tokens = cache_read
```

This normalization is applied in both `_parse_response()` and `send_stream()`.

### TTL

Anthropic's cache TTL is **5 minutes** (server-side, not configurable). If no
request with matching prefix arrives within 5 minutes, the cache expires and the
next request pays full cache_creation cost. The kernel does not implement
keep-alive pings — it relies on natural request frequency.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| Single-block cache | `src/lingtai/llm/anthropic/adapter.py` | 69-84 |
| Batched cache with breakpoints | `src/lingtai/llm/anthropic/adapter.py` | 87-123 |
| Tool cache_control injection | `src/lingtai/llm/anthropic/adapter.py` | 50-66 |
| Token normalization (non-stream) | `src/lingtai/llm/anthropic/adapter.py` | 148-171 |
| Token normalization (stream) | `src/lingtai/llm/anthropic/adapter.py` | 454-475 |
| System prompt batches update | `src/lingtai/llm/anthropic/adapter.py` | 527-539 |

## Gotchas

- Exceeding 4 cache breakpoints causes an API error — the kernel caps at 4 by design, but custom integrations must respect this.
- `input_tokens` in logs is the **sum** of all three Anthropic counters, not just the uncached portion. Don't double-count when reconciling billing.
- The TTL is server-side; the kernel has no keep-alive. Agents with long idle gaps (>5 min between turns) will always pay full cache_creation cost.

## Related

- Anthropic's 4-slot limit: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- `lingtai-kernel-anatomy reference/file-formats.md` — UsageMetadata schema
- MiniMax leaf — inherits Anthropic caching via `AnthropicAdapter` subclass
