---
name: llm-adapters
description: >
  Nested system-manual reference for LingTai's four built-in LLM provider
  families (openai, anthropic, codex, claude-code): what each adapter is, how
  it is configured and dispatched, its standard parameters (wire_api,
  thinking, service_tier), how other vendors are reached through the
  OpenAI/Anthropic-compatible families, and which removed provider names now
  fail validation.
version: 0.2.0
last_changed_at: "2026-09-29T00:00:00Z"
related_files:
- src/lingtai/llm/_register.py
- src/lingtai/llm/service.py
- src/lingtai/llm/openai/adapter.py
- src/lingtai/llm/openai/codex_ws.py
- src/lingtai/llm/anthropic/adapter.py
- src/lingtai/llm/claude_code/adapter.py
- src/lingtai/init_schema.py
- src/lingtai/intrinsic_skills/system-manual/reference/subs-pool/SKILL.md
- ENVIRONMENT_VARIABLES.md
maintenance: |
  Keep one entry per registered provider family and keep the inventory table
  in parity with `LLMService._adapter_registry` (tests/test_llm_adapters_manual.py).
  Keep each section to the adapter's concrete operating facts (dispatch, wire,
  standard parameters, env vars) and point to source rather than restating
  code. The removed-provider list mirrors `init_schema.REMOVED_LLM_PROVIDERS`.
---
# LLM Adapters Manual

This reference documents LingTai's built-in LLM adapters. Adapters turn a
provider wire protocol into the kernel's session/stream contract. The canonical
registration and dispatch table lives in `src/lingtai/llm/_register.py` and
`src/lingtai/llm/service.py`; providers are configured through presets /
`init.json` `llm` blocks. This page is the progressive-disclosure route from
the system manual; source remains the behavioral authority.

## Named adapters

LingTai ships exactly four LLM provider families (source of truth:
`src/lingtai/llm/_register.py`, `LLM_PROVIDERS`):

| Provider keys | Factory / adapter | Transport(s) | Notes |
|---|---|---|---|
| `openai` | `OpenAIAdapter` (`openai/adapter.py`) | REST: Chat Completions (default) or Responses | Any OpenAI-compatible endpoint. `base_url` optional (default official `https://api.openai.com/v1`). `wire_api`: `chat_completions` (default; legacy `auto` means the same) or `responses` (always stateless full-history replay) |
| `anthropic` | `AnthropicAdapter` (`anthropic/adapter.py`) | REST (Messages API) | Any Anthropic-compatible endpoint. `base_url` optional (default official `https://api.anthropic.com`) |
| `codex` | `CodexOpenAIAdapter` (`openai/adapter.py`) | REST (default), WebSocket (opt-in) | Official ChatGPT Codex backend; one OAuth account (`codex_auth_path` or default) with token refresh; `store=false` forced; streaming forced. No built-in account pool — see [subs-pool](../subs-pool/SKILL.md) |
| `claude-code` | `ClaudeCodeAdapter` (`claude_code/adapter.py`) | n/a (local `claude` CLI) | Local Claude Code CLI login used as a main-agent/preset provider |

Each adapter is lazy-imported on first use, so an unconfigured provider's SDK
is never loaded. The CLI-backed `claude-code` provider is distinct from the
daemon CLI backend dispatch system (see `daemon-manual`), which can run external
coding CLIs as subprocesses for a task.

### Other vendors, subscriptions, and pools

LingTai ships no per-vendor adapters. Reach any other vendor by pointing a
family at that vendor's compatible endpoint:

- OpenAI-compatible vendor, gateway, or local server → `provider: "openai"`
  with `base_url` (+ `wire_api: "responses"` when the endpoint serves the
  Responses API).
- Anthropic-compatible vendor or proxy → `provider: "anthropic"` with
  `base_url`.
- Subscription accounts → an external pool such as sub2api or
  [subs-pool](../subs-pool/SKILL.md), reached the same way.

### Removed provider names

`init.json` and preset validation reject these names on `manifest.llm.provider`
and on any capability `provider` that names an LLM route (`web`/`web_search`
engine names are exempt) with a pointer to `openai`/`anthropic` or an external
pool: `deepseek`, `zhipu`, `glm`, `mimo`, `minimax`, `openrouter`, `grok`,
`qwen`, `kimi`, `gemini`, `kimi-code`, `kimi_code`, `custom`, `claude_code`,
`codex-pool`, `codex_pool` (`init_schema.REMOVED_LLM_PROVIDERS`). The retired
manifest keys `api_compat`, `reasoning_effort_vocab`, and `use_responses_api`
are recognized and ignored.

## Standard parameters

- **`thinking`** (`none|minimal|low|medium|high|xhigh|max`, every family) is
  sent verbatim as the standard field: Responses `reasoning: {effort}`, Chat
  Completions `reasoning_effort`. Omitted (`default`) sends no field on
  `openai`; `codex` sends its own explicit `xhigh`; `anthropic` maps the level
  to a Messages thinking budget (omitted hydrates the legacy `high`);
  `claude-code` maps it to `--effort`.
- **`service_tier`** (`openai` and `codex`, one normalizer
  `_normalize_service_tier`): `fast` → wire `priority`; `auto`, `default`,
  `flex`, `priority` pass through verbatim; any other value fails init
  validation. `anthropic`/`claude-code` do not forward it.
- **`wire_api`** belongs to `openai` only; a non-`auto` value on another
  provider fails validation. `codex` always uses Responses.
- Generic `openai` knobs: `inject_reasoning_fallback` (Chat Completions
  `reasoning_content` round-trip stub, default on,
  `LINGTAI_INJECT_REASONING_FALLBACK`), `default_headers`,
  `prompt_cache_namespace` (auto-derived `prompt_cache_key` namespace),
  `max_rpm`, and host-keyed tool-schema quirks.

## Codex adapter

The Codex adapter (`CodexOpenAIAdapter` → `CodexResponsesSession`, both in
`src/lingtai/llm/openai/adapter.py`) talks to ChatGPT's official Codex
`/backend-api/codex/responses` endpoint. It is the single native Codex
provider: single-account binding, token refresh, and `store=false` semantics
are all handled inside the adapter (see `_register.py` and `service.py`).
Omitted/`default` thinking sends an explicit `reasoning.effort = "xhigh"`
(Codex-only default); `service_tier` shares the `openai` normalizer.

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

The Codex variables (`LINGTAI_CODEX_TRANSPORT`, `LINGTAI_CODEX_WS`,
`LINGTAI_CODEX_WS_EPOCH_RESET_TURNS`, and the responses-trace pair) are
registered with their accepted values and defaults in the repo-root
`ENVIRONMENT_VARIABLES.md`, routed via `reference/environment-variables/SKILL.md`.

Two behaviors worth holding here: they are read at session construction time
(per process), and the selector is deliberately opt-in — an inherited or
accidentally-set variable never flips a Codex agent onto WebSocket unless the
value is explicitly the opt-in value.

### Runtime reasoning-effort control (live effort)

Codex sessions (`CodexResponsesSession`) support **process-local live reasoning
effort** — changing the effort used for subsequent dispatches at runtime,
without reconfiguring or restarting the agent. This is the Codex side of the
kernel's neutral reasoning-effort port (`llm/base.py` +
`src/lingtai/kernel/llm/reasoning_effort.py`, shared with Claude Code's live
effort).

- **Supported values**: `low | medium | high | xhigh | max | ultra`. The exact
  vocabulary is validated against the active route's descriptor
  (`codex_effort.py`), so an unsupported value is rejected fail-closed.
- **Runtime surface**: `SessionManager` exposes
  `reasoning_effort_status()`, `set_reasoning_effort(value)`, and
  `clear_reasoning_effort()` — query the current effort/route, override the
  next unsnapshotted dispatch, or restore the construction baseline. These are
  in-process, self-facing methods for agents that want to tune effort per task
  (e.g. low effort for cheap work, xhigh for hard reasoning).
- **Evidence**: each dispatch records the effort actually emitted on the wire
  into `llm_response` event metadata under `codex_reasoning_effort*` keys
  (`codex_reasoning_effort`, `codex_reasoning_effort_source`,
  `codex_reasoning_effort_revision`), alongside the Claude Code
  `claude_reasoning_effort*` keys.
- **Default**: when no route is bound or the route does not support live
  effort, the controller stays truthfully `unavailable` and the adapter keeps
  its construction baseline — no behavior change for existing agents.

## OpenAI adapter

The `openai` adapter (`OpenAIAdapter` in `src/lingtai/llm/openai/adapter.py`)
serves any OpenAI-compatible endpoint over Chat Completions (default) or the
Responses API (`wire_api: "responses"`). The Responses wire is always
stateless: every request replays the full canonical history and never sends
`previous_response_id`, on every endpoint including official OpenAI. The
adapter never sends the Responses `context_management` auto-compaction field; it
would rewrite the context prefix every turn and defeat prompt caching.
`effective_base_url` reports the endpoint the adapter really reaches (the
configured `base_url`, else the SDK default); credential-reusing capabilities
such as default Vision use it instead of guessing.

## Anthropic adapter

The `anthropic` adapter (`AnthropicAdapter` in
`src/lingtai/llm/anthropic/adapter.py`) serves the Anthropic Messages API and
any Anthropic-compatible endpoint through `base_url`. It maps `thinking` to an
extended-thinking budget and exposes `effective_base_url` like the OpenAI
adapter. It has no transport env-var selectors.

## CLI-backed LLM provider (`claude-code`)

`claude-code` is a registered LLM provider whose adapter wraps the local
`claude` CLI (`ClaudeCodeAdapter`) rather than speaking a wire protocol
directly — a valid main-agent/preset provider, lazy-imported like every other
adapter. Auth is owned by the CLI login. (Not the daemon backend axis; see
above.)

### External CLI harnesses (daemon backends)

The daemon tool runs external coding CLIs as task subprocesses through the
`backend` axis. The index of backends, and one page per backend, live under
`daemon-manual` → `reference/cli-backends/SKILL.md` (each page at
`reference/cli-backends/reference/backends/<name>/SKILL.md`). Do not maintain a
second backend list here.

Each backend page carries the same `## Subscription & auth` section, so "what do
I need to pay for / how does LingTai connect" is answered per backend without
reading the vendor's full billing docs. These pages are entrypoints, not flag
catalogs; the installed CLI's live help remains the authority.
