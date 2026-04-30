# MiniMax 1M Context Window

## What

MiniMax provides models with very large context windows (1M+ tokens). The
MiniMax adapter is a **thin subclass of AnthropicAdapter** — it inherits all
Anthropic behaviors and only overrides the base URL and default rate limit.
This leaf documents the adapter structure and how context window size is handled.

## When you need this

- **MiniMax context window overflows** — unlike OpenAI, there is no automatic recovery; the error propagates to the agent, which should trigger a molt.
- **You're diagnosing MiniMax rate limits** — default is 120 RPM (much lower than Anthropic's unlimited default).
- **You're wondering what MiniMax-specific behavior exists** — the answer is "almost none"; it's a thin Anthropic-compatible wrapper.

## Contract

### Adapter inheritance

`MiniMaxAdapter` extends `AnthropicAdapter` with:

```python
class MiniMaxAdapter(AnthropicAdapter):
    def __init__(self, api_key, *, base_url=None, max_rpm=120, timeout_ms=300_000):
        effective_url = base_url or "https://api.minimax.io/anthropic"
        super().__init__(api_key=api_key, base_url=effective_url, timeout_ms=timeout_ms)
        self._setup_gate(max_rpm)
```

Key differences from direct `AnthropicAdapter`:
- **Default base URL**: `https://api.minimax.io/anthropic` (Anthropic-compatible endpoint)
- **Default max_rpm**: 120 (Anthropic defaults to 0/unlimited)
- **All other behavior inherited**: caching, alternation enforcement, thinking, etc.

### Provider registration

MiniMax is registered as `api_compat: "anthropic"` in defaults.py, meaning the
provider defaults system routes it through the Anthropic code path.

### Context window handling

The `context_window` parameter is passed through `LLMService.create_session()` →
`adapter.create_chat()` → `AnthropicChatSession.__init__()`. The session stores
it as `_context_window` and exposes it via `context_window()`.

MiniMax's large context windows are handled **identically** to any other
Anthropic-compatible provider — there is no special truncation or partitioning
logic. The kernel's context pressure monitoring (molt warnings at 70%/95%) is
the only guard against overflow.

### No deepseek-style overflow recovery

Unlike the OpenAI adapter's `_run_with_overflow_recovery` (which auto-trims on
400 errors), the Anthropic adapter path has **no automatic overflow recovery**.
If the context exceeds the model's limit, the API error propagates directly.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| MiniMaxAdapter class | `src/lingtai/llm/minimax/adapter.py` | 1-27 |
| MiniMax defaults | `src/lingtai/llm/minimax/defaults.py` | 1-7 |
| MiniMax registration | `src/lingtai/llm/_register.py` | 31-34 |
| Context window storage | `src/lingtai/llm/anthropic/adapter.py` | 286-296, 560-565 |
| Molt pressure monitoring | `src/lingtai/llm/anthropic/adapter.py` | N/A (kernel-level) |

## Related

- Anthropic cache-ttl leaf — MiniMax inherits all Anthropic caching behavior
- OpenAI overflow recovery — contrast with the Chat Completions path's auto-trim
- `lingtai-kernel-anatomy reference/molt-protocol.md` — context pressure thresholds
