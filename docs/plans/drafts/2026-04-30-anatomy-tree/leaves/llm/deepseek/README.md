# DeepSeek Provider Quirks

## What

DeepSeek V4's thinking mode requires `reasoning_content` on **every** assistant
turn once any tool call has occurred in the conversation. Omitting it returns
HTTP 400. The kernel's DeepSeek adapter injects a placeholder to satisfy this
contract, then strips the server's echo of that placeholder from response
thoughts.

## When you need this

- **DeepSeek returns HTTP 400: "reasoning_content must be passed back"** — you added a new code path that sends assistant messages without `reasoning_content` after a tool call. This leaf explains why.
- **Agent thoughts contain "(reasoning omitted — not preserved across turns)"** — the echo stripping should catch this; if it doesn't, the placeholder prefix changed or the stripping function is bypassed.
- **You're modifying the placeholder string** — it must be non-empty and stable; DeepSeek validates presence, not content.

## Contract

### reasoning_content round-trip requirement

Once any assistant turn in the conversation has `tool_calls`, **ALL** subsequent
assistant turns (tool-call AND plain-text) must carry `reasoning_content` when
replayed. This is stricter than the docs suggest — even the final plain-text
reply after a tool loop needs it.

DeepSeek validates **field presence, not content**: it doesn't fingerprint the
string. The adapter therefore injects a stable placeholder rather than trying to
preserve actual reasoning text.

### Placeholder injection

`DeepSeekChatSession._build_messages()` walks the message list and injects
`reasoning_content = "(reasoning omitted — not preserved across turns)"` on
every assistant turn **from the first tool_call onward**. Turns before the first
tool call are left untouched.

### Echo stripping

DeepSeek V4's cache-hit fast-path sometimes echoes the placeholder verbatim as
the start of its own reasoning. `_strip_placeholder_echoes(response)` removes
this prefix from each thought. Pure-echo responses (thought == placeholder, no
tail) become empty and are dropped.

### Architecture

`DeepSeekAdapter` extends `OpenAIAdapter` with `_session_class = DeepSeekChatSession`.
Everything else (streaming, overflow recovery, tool pairing) is inherited from the
OpenAI adapter.

### Default base URL

`https://api.deepseek.com` (hardcoded in `_DEEPSEEK_BASE_URL`). No separate
`defaults.py` — the base URL is the only DeepSeek-specific configuration.

## Source

| Behavior | File | Lines |
|----------|------|-------|
| reasoning_content contract docs | `src/lingtai/llm/deepseek/adapter.py` | 1-33 |
| Placeholder string | `src/lingtai/llm/deepseek/adapter.py` | 58 |
| Placeholder injection in `_build_messages` | `src/lingtai/llm/deepseek/adapter.py` | 64-78 |
| Echo stripping | `src/lingtai/llm/deepseek/adapter.py` | 91-114 |
| DeepSeekAdapter class | `src/lingtai/llm/deepseek/adapter.py` | 117-135 |
| DeepSeek registration | `src/lingtai/llm/_register.py` | 84-89 |

## Related

- OpenAI adapter — parent class; all overflow recovery and streaming inherited
- OpenRouter leaf — also inherits OpenAI adapter but with different overrides
- `lingtai-kernel-anatomy reference/file-formats.md` — reasoning_content field in message format
