# LLM Provider Quirks — Index

## What

The kernel supports 6 LLM providers, each with unique API behaviors that require
adapter-level workarounds. This index routes you to the right leaf by **symptom**
— you don't need to know which provider caused the problem.

## Symptom → Leaf

| Symptom | Leaf | Provider |
|---------|------|----------|
| High Anthropic billing / `cached_tokens` is 0 | [cache-ttl](anthropic/cache-ttl/) | Anthropic |
| `cached_tokens` is `None` in logs or data exports | [cached-tokens-coercion](openai/cached-tokens-coercion/) | OpenAI |
| Tool call names prefixed with `default_api:` | [gemini quirks](gemini/) | Gemini |
| Tool schema rejected with 400 (`"required": []`) | [gemini quirks](gemini/) | Gemini |
| Thinking not working on Gemini model | [gemini quirks](gemini/) | Gemini |
| HTTP 400: "reasoning_content must be passed back" | [deepseek quirks](deepseek/) | DeepSeek |
| Agent thoughts contain placeholder text | [deepseek quirks](deepseek/) | DeepSeek |
| Context window overflow with no auto-recovery | [1m-ctx](minimax/1m-ctx/) | MiniMax |
| Rate limit at 120 RPM | [1m-ctx](minimax/1m-ctx/) | MiniMax |
| Reasoning text appearing unexpectedly in thoughts | [openrouter routing](openrouter/routing/) | OpenRouter |
| Want to enable/disable reasoning logs for OpenRouter | [openrouter routing](openrouter/routing/) | OpenRouter |
| Wrong model routed through OpenRouter | [openrouter routing](openrouter/routing/) | OpenRouter |

## Provider → Leaf

| Provider | Leaf | Key quirk |
|----------|------|-----------|
| Anthropic | [cache-ttl](anthropic/cache-ttl/) | 4-slot cache budget; batched breakpoints |
| OpenAI | [cached-tokens-coercion](openai/cached-tokens-coercion/) | `None`→`0` coercion; two session backends |
| Gemini | [gemini quirks](gemini/) | Two APIs; model-version thinking gate; `default_api:` prefix |
| MiniMax | [1m-ctx](minimax/1m-ctx/) | Thin Anthropic wrapper; no overflow recovery |
| DeepSeek | [deepseek quirks](deepseek/) | `reasoning_content` round-trip; placeholder injection |
| OpenRouter | [openrouter routing](openrouter/routing/) | Fixed endpoint; reasoning suppression |

## Inheritance map

```
OpenAIAdapter
├── OpenAIChatSession (Chat Completions)
├── OpenAIResponsesSession (Responses API — OpenAI only)
├── DeepSeekAdapter → DeepSeekChatSession (injects reasoning_content)
└── OpenRouterAdapter (fixed URL + reasoning suppression)

AnthropicAdapter
├── AnthropicChatSession
└── MiniMaxAdapter (inherits everything; overrides URL + rate limit)

GeminiAdapter
├── GeminiChatSession (Chat API — json_schema mode only)
└── InteractionsChatSession (Interactions API — default)
```

## Keeping leaves in sync with code

Every adapter file contains an **`Anatomy leaf:`** beacon in its docstring,
pointing to its leaf directory. The beacon lists exactly which behaviors are
documented (e.g. "If you change caching, token accounting, or system prompt
batching logic, update the leaf").

**Rule: if you change an adapter, read its `Anatomy leaf:` beacon. If the
beacon names the behavior you changed, update the corresponding README.md
and test.md.** The beacon is the contract between code and documentation.

To find all beacons: `grep -r "Anatomy leaf:" src/lingtai/llm/`

## How to add a new provider leaf

1. Create `leaves/llm/<provider>/README.md` following the leaf contract
   (## What / ## When you need this / ## Contract / ## Source / ## Related).
2. Create `leaves/llm/<provider>/test.md` with filesystem-observable pass criteria.
3. Add entries to the two tables above (Symptom → Leaf, Provider → Leaf).
4. If the new provider inherits from an existing adapter, add it to the
   inheritance map.
