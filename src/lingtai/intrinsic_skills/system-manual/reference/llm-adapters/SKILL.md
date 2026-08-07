---
name: llm-adapters
description: >
  Nested system-manual reference for the built-in LLM adapters: what each
  named adapter is, how it is configured and dispatched, its special transport
  or protocol behaviors, and the environment variables that control it.
version: 0.1.0
last_changed_at: "2026-08-06T00:00:00Z"
related_files:
- src/lingtai/llm/_register.py
- src/lingtai/llm/service.py
- src/lingtai/llm/openai/adapter.py
- src/lingtai/llm/openai/codex_ws.py
- src/lingtai/llm/custom/adapter.py
- ENVIRONMENT_VARIABLES.md
maintenance: |
  Keep one entry per named adapter. Add a section when a new adapter ships;
  keep each section to the adapter's concrete operating facts (dispatch, wire,
  special behavior, env vars) and point to source rather than restating code.
---
# LLM Adapters Manual

This reference documents LingTai's built-in LLM adapters. Adapters are the
per-provider implementations that turn a provider's wire protocol into the
kernel's session/stream contract. The canonical registration and dispatch table
lives in `src/lingtai/llm/_register.py` and `src/lingtai/llm/service.py`;
provider-defaults presets are configured through presets / `init.json` `llm`
blocks. This page is the progressive-disclosure route from the system manual;
source remains the behavioral authority.

## Named adapters

LingTai registers the following provider keys (each usable in presets / `init.json`
`llm` blocks; source of truth: `src/lingtai/llm/_register.py`):

| Provider keys (aliases) | Factory / adapter | Transport(s) | Notes |
|---|---|---|---|
| `codex`, `codex-pool`, `codex_pool` | `CodexOpenAIAdapter` (in `openai/adapter.py`) | REST (default), WebSocket (opt-in) | Official ChatGPT Codex backend; account selection + token pool; `store=false` forced; streaming forced |
| `openai` | `OpenAIAdapter` | REST (Chat Completions / Responses) | Responses API optional via `wire_api` / `use_responses_api` |
| `anthropic` | `AnthropicAdapter` | REST | Anthropic Messages API |
| `gemini` | `GeminiAdapter` | REST | Google Gemini API |
| `minimax` | `MiniMaxAdapter` | REST | MiniMax API |
| `deepseek` | `DeepSeekAdapter` | REST | DeepSeek API |
| `glm`, `zhipu` | `ZhipuAdapter` | REST | Zhipu / GLM API |
| `mimo` | `MimoAdapter` | REST | Xiaomi MiMo API |
| `custom`, `grok`, `qwen`, `kimi` | `create_custom_adapter` (in `custom/adapter.py`) | REST | Generic OpenAI-compatible endpoint (`custom` is the canonical key; `grok`/`qwen`/`kimi` are custom-backed aliases) |
| `openrouter` | `OpenRouterAdapter` | REST | OpenRouter-compatible endpoint |
| `claude-code`, `claude_code` | `ClaudeCodeAdapter` (in `claude_code/adapter.py`) | n/a (external CLI) | Local CLI-backed LLM provider (Claude Code harness); used as a main-agent/preset provider |
| `kimi-code`, `kimi_code` | `KimiCodeAdapter` (in `kimi_code/adapter.py`) | n/a (external CLI) | Local CLI-backed LLM provider (Kimi Code harness); used as a main-agent/preset provider |

Each adapter is lazy-imported on first use, so an unconfigured provider's SDK
is never loaded. Prefer the provider's own section below when operating a
specific provider. The CLI-backed providers above (`claude-code`, `kimi-code`)
are registered LLM providers/preset paths; they are distinct from the daemon
CLI backend dispatch system (see `daemon-manual`), which can run external
coding CLIs as subprocesses for a task.

## Codex adapter

The Codex adapter (`CodexOpenAIAdapter` → `CodexResponsesSession`, both in
`src/lingtai/llm/openai/adapter.py`) talks to ChatGPT's official Codex
`/backend-api/codex/responses` endpoint. It is the single native Codex
provider: account selection, token-pool rotation, and `store=false` semantics
are all handled inside the adapter (see `_register.py` and `service.py`).

### Transport: REST vs WebSocket

Codex supports two transports that run the **same** full→incremental
continuation planner; the transport only selects how the planned request is
sent:

- **REST** (default): each turn sends a self-contained full converted context.
  `incremental` only annotates an unchanged cache epoch — the wire payload is
  always the full input and never carries `previous_response_id`.
- **WebSocket**: a persistent connection to `wss://chatgpt.com/backend-api/codex/responses`
  that can transmit a strict-additive delta plus `previous_response_id` on
  incremental turns. The wire driver lives in `src/lingtai/llm/openai/codex_ws.py`;
  the `websockets` package is an optional, lazily-imported dependency — if it is
  missing, the WS path falls back to HTTP.

WebSocket is an **opt-in** transport. Normal runtime stays on REST because live
testing showed REST prompt-prefix caching is sufficient. Resolution priority:

1. explicit `transport=` constructor kwarg (`websocket`/`ws` or `rest`);
2. legacy `ws_enabled=` kwarg (`True` → websocket);
3. environment-variable opt-in (see below);
4. hardcoded normal-runtime default: `rest`.

### Environment variables

| Variable | Purpose | Accepted values | Default |
|---|---|---|---|
| `LINGTAI_CODEX_TRANSPORT` | Opt a Codex agent onto the WebSocket wire | `websocket` / `ws` (opt-in); `rest` / `http` / `https` (explicit REST); anything else → REST | unset → REST |
| `LINGTAI_CODEX_WS` | Legacy boolean opt-in for the WebSocket wire | `1` / `true` / `yes` / `on` → websocket; anything else → REST | unset → REST |
| `LINGTAI_CODEX_WS_EPOCH_RESET_TURNS` | Explicit WS response-chain epoch reset interval (turns); `0` disables the turn-count reset | non-negative integer | `0` |

These variables are read at session construction time (per process). The
selector is deliberately opt-in: an inherited or accidentally-set variable
never flips a Codex agent onto WebSocket unless the value is explicitly the
opt-in value.

## OpenAI adapter

The `openai` adapter (`OpenAIAdapter` in `src/lingtai/llm/openai/adapter.py`)
serves OpenAI-compatible endpoints over Chat Completions or the Responses API.
The Responses API can be selected with `wire_api=responses` or the legacy
`use_responses_api=true` provider default. A host-configured Responses
compaction threshold can be passed via the `compact_threshold` provider
default (see `_register.py`).

## Anthropic / Gemini / MiniMax / DeepSeek / Zhipu / MiMo

Each of these adapters is a straightforward REST provider adapter in
`src/lingtai/llm/<provider>/adapter.py`. They are configured through the
standard provider fields (model, api_key / auth, base_url where applicable) and
have no transport env-var selectors today. See the per-provider source for
constructor details.

## Custom / OpenRouter adapters

`custom` (`src/lingtai/llm/custom/adapter.py`) and `openrouter` target
generic OpenAI-compatible endpoints. `custom` is the provider used for
user-defined third-party routers (see the provider-additions rule: do not
propose adding such intermediaries as core built-in providers; use
custom/user-defined presets).

## CLI-backed LLM providers (`claude-code`, `kimi-code`)

`claude_code` and `kimi_code` are registered LLM providers whose adapters wrap
local code-workspace CLIs (`ClaudeCodeAdapter`, `KimiCodeAdapter`) rather than
speaking a wire protocol directly. They are valid main-agent/preset providers
and are lazy-imported like every other adapter. They are **not** the daemon CLI
backend axis: the daemon system can dispatch external coding CLIs as task
subprocesses (`daemon-manual`), independent of these registered providers.

### External CLI harnesses (daemon backends)

The daemon tool runs external coding CLIs as task subprocesses through the
`backend` axis (`daemon-manual`). Each CLI backend has its own small
progressive-disclosure page under
`daemon-manual` → `reference/cli-backends` — what it is, what LingTai uses it
for, its subscription/auth model, its official docs, and the reserved
harness-owned flags it refuses in `backend_options`. These pages are
entrypoints, not flag catalogs; the installed CLI's live help remains the
authority.

| Daemon backend | Page | Subscription/auth model | Official docs |
|---|---|---|---|
| `claude-p` / `claude-code` | `reference/backends/claude-p/SKILL.md` | Claude subscription (Pro/Max), CLI OAuth login | https://docs.anthropic.com/en/docs/claude-code |
| `codex` | `reference/backends/codex/SKILL.md` | ChatGPT subscription (Plus/Pro), `codex login` or codex-pool | https://developers.openai.com/codex/ |
| `opencode` | `reference/backends/opencode/SKILL.md` | provider-agnostic auth; OpenCode Go subscription via `OPENCODE_GO_API_KEY` | https://opencode.ai/docs/ |
| `mimocode` / `mimo` | `reference/backends/mimocode/SKILL.md` | MiMo Code provider keys | https://github.com/XiaomiMiMo/MiMo-Code |
| `qwen-code` / `qwen` | `reference/backends/qwen-code/SKILL.md` | Qwen provider config | https://github.com/QwenLM/qwen-code |
| `oh-my-pi` / `omp` | `reference/backends/oh-my-pi/SKILL.md` | Oh-My-Pi provider keys | https://github.com/pi-coding-agent/pi-coding-agent |
| `kimicode` / `kimi` | `reference/backends/kimicode/SKILL.md` | Moonshot AI keys | https://github.com/MoonshotAI/kimi-code |
| `cursor` | `reference/backends/cursor/SKILL.md` | Cursor account/subscription login | https://docs.cursor.com/agent |

Each backend page carries the same small `## Subscription & auth` section so
the question "what do I need to pay for / how does LingTai connect" is
answered per backend without reading the vendor's full billing docs.
