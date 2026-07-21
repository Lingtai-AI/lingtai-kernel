---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/llm/__init__.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/identity_headers.py
  - src/lingtai/llm/anthropic/ANATOMY.md
  - src/lingtai/llm/api_gate.py
  - src/lingtai/llm/base.py
  - src/lingtai/llm/claude_code/adapter.py
  - src/lingtai/llm/custom/ANATOMY.md
  - src/lingtai/llm/deepseek/ANATOMY.md
  - src/lingtai/llm/gemini/ANATOMY.md
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/llm/minimax/ANATOMY.md
  - src/lingtai/llm/mimo/ANATOMY.md
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/llm/openai/adapter.py
  - src/lingtai/llm/openrouter/ANATOMY.md
  - src/lingtai/llm/service.py
  - src/lingtai/kernel/llm/ANATOMY.md
  - tests/test_codex_endpoint_pool.py
  - tests/test_codex_native_multiaccount.py
  - tests/test_llm_identity_headers.py
  - tests/test_wire_tool_description.py
  - tests/test_mimo_responses_compaction.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/llm/

LLM adapter layer — multi-provider support with adapter registry, base classes, rate limiting, and interface converters.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|------|-----|------|
| `__init__.py` | 20 | Re-exports kernel types (`ChatSession`, `LLMResponse`, `ToolCall`, `FunctionSchema`, `ChatInterface`) + `LLMAdapter` from `base.py`. Triggers `register_all_adapters()` on import. |
| `_register.py` | 241 | Registers adapter factories for all providers with `LLMService.register_adapter()`. Module constant `CODEX_OFFICIAL_BASE_URL` is the Codex default endpoint; `_normalize_service_tier` (`_register.py:28-50`) is the common Codex `service_tier` boundary (`fast` → wire `priority`). The one `_codex` factory (`_register.py:133-192`) supplies either `FixedAccountSource` or live `WeightedAccountSource` to one native `CodexOpenAIAdapter`; `codex`, `codex-pool`, and `codex_pool` are registry aliases for that exact factory (`_register.py:194-198`), not separate implementations. |
| `identity_headers.py` | 53 | Shared non-secret LingTai HTTP identity/version header helper for SDK-backed LLM adapters. |
| `claude_code/` | — | `claude-code` provider: drives the local `claude` CLI as a stateless reasoning core on a Claude subscription (`adapter.py`, `defaults.py`). |
| `api_gate.py` | 112 | `APICallGate` — RPM rate limiter with deque timestamps, `ThreadPoolExecutor`, daemon gate thread |
| `base.py` | 150 | `LLMAdapter` ABC (4 abstract methods), `_GatedSession` proxy |
| `interface_converters.py` | 335 | Bidirectional converters: `to_*` / `from_*` for Anthropic, OpenAI, OpenAI Responses API, Gemini |
| `service.py` | 454 | `LLMService` concrete class — adapter registry, session management, one-shot generation |

## Connections

- **Kernel types** — `__init__.py:3` imports `ChatSession`, `LLMResponse`, `ToolCall`, `FunctionSchema` from `lingtai.kernel.llm.base`; `ChatInterface` from `lingtai.kernel.llm.interface`.
- **ABC chain** — `LLMAdapter` (`base.py:94`) → abstract `create_chat`, `generate`, `make_tool_result_message`, `is_quota_error`. `LLMService` (`service.py:97`) extends `lingtai.kernel.llm.service.LLMService` ABC.
- **Adapter registration** — `_register.py` registers dedicated factories including native HTTP/OAuth `codex`, local-CLI `claude-code`, and native `mimo`, plus generic-routed providers (`grok`, `qwen`, `kimi`) via `_custom`. Codex ignores generic API keys but honors an explicit `base_url` and resolves OAuth credentials at its request boundary; Claude Code drops generic HTTP settings because its CLI owns transport/auth.
- **Interface converters** — imported by adapter session modules (e.g. `openai.adapter` imports `to_openai`, `to_responses_input` from `interface_converters.py:120`).
- **Rate gating** — `LLMAdapter._setup_gate(max_rpm)` creates `APICallGate`; `_wrap_with_gate()` returns `_GatedSession` proxy for sessions.

## Composition

- **Factory pattern** — `LLMService._adapter_registry` (class-level dict) maps provider name → `Callable[..., LLMAdapter]`. Each factory receives `(model, defaults, **kw)` and lazy-imports the adapter module. HTTP-capable factories forward `default_headers` to SDK-backed adapters; `claude-code` deliberately drops them because the local CLI owns its own HTTP stack.
- **HTTP identity headers** — `identity_headers.py:12-53` builds `X-LingTai-Client`, optional `X-LingTai-Version`, and optional `User-Agent: LingTai/<version>`, then merges them under caller/provider headers case-insensitively. OpenAI-compatible, Anthropic-compatible, and Gemini adapters consume this helper at SDK construction time.
- **Adapter caching** — `LLMService._adapters` keyed by `(provider, base_url)` tuple (`service.py:141`). Double-checked locking via `_adapter_lock` (`service.py:142`).
- **Session tracking** — `LLMService._sessions` dict maps `st_<12-hex>` session IDs to `ChatSession` instances (`service.py:144`). Untracked sessions get `session_id=""`.
- **Gated sessions** — `_GatedSession` (`base.py:19`) proxies `send()` and `send_stream()` through `APICallGate.submit()`. Ordinary attribute writes land on the proxy; reads fall through to the inner session via `__getattr__`. **Exception:** the named `pre_request_hook` property (`base.py:40-52`) writes *through* to the inner session — its setter stores the hook on `self._inner` (`base.py:50-52`) so the object whose `send()` reads `self.pre_request_hook` before the API call actually holds it (a plain proxy-local write would leave the inner's hook `None` and the kernel drain hook would silently never fire under rate gating). Only the named hook delegates; the proxy never invokes the hook itself and the inner adapter continues to own request invocation/timing.
- **Unified Codex factory** — `_register.py:133-198` builds one `CodexOpenAIAdapter` with deferred OAuth binding. A non-blank `codex_auth_path` produces `FixedAccountSource`; otherwise the factory supplies a model-scoped `WeightedAccountSource` that re-reads the configured pool, with the legacy token file retained only as an empty-pool fallback. The same factory owns `codex`, `codex-pool`, and `codex_pool`; it honors the common endpoint, cache-affinity, compaction, and `service_tier` settings and never logs plaintext paths or credentials.

- **Codex endpoint pool (molt-boundary shuffle)** — an OPTIONAL Codex-only `codex_base_urls` provider-default (list/tuple or comma/newline string; blank entries dropped) lets the `CodexOpenAIAdapter` pick one of several endpoints. Empty/one-valid-entry collapse to the single-`base_url` path above; 2+ entries are chosen at *request time* in the adapter's `create_chat` override (`openai/adapter.py`) as `pool[(stable_agent_offset + molt_count) % len]`. The offset is `sha256(agent_anchor)` (different agents distribute); `molt_count` is read fresh from `<working_dir>/.agent.json` (host/test callers may pass a direct `codex_molt_count` provider-default override); that file is the sibling of the `codex_session_anchor` `init.json` path, and missing/invalid values fall back to 0. Selection is computed at request time because the molt path (`psyche/_molt.py`) does NOT rebuild the adapter — so a live process observes molt-boundary changes without a refresh. The endpoint is stable within a molt segment and rotates only at a molt boundary (minimal side effects: a molt already wipes the wire session). On a switch, `create_chat` re-points `self._client` from `_client_kwargs`, dropping the old client and any websocket / `previous_response_id` continuation state it owned so it never crosses endpoints. Endpoint choice never perturbs the request-shape identity, but the identity itself is molt-aware: the generated `session_id`/`thread_id`/default `prompt_cache_key` comes from `codex_session_anchor + current molt_count`, stays stable within a molt, and intentionally changes at a molt boundary. Helpers: `_parse_codex_base_urls`, `_read_molt_count`. Tests: `tests/test_codex_endpoint_pool.py`.

- **Native Codex 1..N account lifecycle** — `CodexOpenAIAdapter._select_codex_account` (`openai/adapter.py:5015-5134`) owns quota-aware selection, token refresh, exclusion state, and safe attribution for both fixed and weighted sources. `CodexResponsesSession.send_stream` calls `_codex_refresh_account_for_request` first (`openai/adapter.py:4232-4265`), so chat construction consumes no draw and every actual provider request selects exactly one live credential. A complete per-account quota snapshot drives dynamic weights; incomplete quota data falls the whole draw back to static, while independently proven-zero accounts remain excluded. Only a truly empty pool may bind the legacy fallback; a non-empty exhausted pool fails closed for AED.
  - **Account/continuation isolation:** when the selected account SHA changes, `_codex_refresh_account_for_request` resets the WebSocket epoch before rebinding (`openai/adapter.py:4243-4248`), closing authenticated transport and previous-response/compaction continuation state so no wire state crosses accounts.
  - **One retry owner:** only the final exception escaping Codex's built-in transport/self-heal paths is reported (`openai/adapter.py:4736-4745`). Structural `usage_limit_reached` adds that safe account identity to the adapter's exclusion set (`openai/adapter.py:5139-5153`) and re-raises; the existing kernel AED loop owns the later session rebuild/replay. Success clears the current exclusion chain but never preselects the next account (`openai/adapter.py:4859-4863`, `openai/adapter.py:5155-5158`).
  - **Partial output is terminal:** once a text delta was delivered, the escaping exception is marked `_lingtai_partial_stream` (`openai/adapter.py:4670-4674`, `openai/adapter.py:5139-5148`); BaseAgent ends that turn rather than replaying already-visible text on another account.
  - **Safe selection metadata:** `codex_pool_selection` retains stable non-secret fields (`source_ref`, source index, pool size, weight, `auth_path_sha8`, model scope, quota/fallback when present) for response-usage and token-ledger attribution. Plaintext token contents never enter metadata, and no account health/cooldown state is persisted. Tests: `tests/test_codex_native_multiaccount.py`, `tests/test_codex_account_source.py`.
  - **Fast (`service_tier`)** — the first common Codex capability at this boundary: `llm.service_tier: "fast"` normalizes to the wire value `priority` (`_normalize_service_tier`, `_register.py:29`); absent is omitted; any other value raises `ValueError` loudly at factory time. Reaches both REST and WebSocket transports identically because `CodexOpenAIAdapter` stores it once (`_codex_service_tier`) and both transports build their request `kwargs` from the SAME `self._extra_kwargs` (`openai/adapter.py`). Applies to both `codex` and `codex-pool` — not pool-specific wiring.

- **Claude Code factory** — `_register.py:_claude_code` builds `ClaudeCodeAdapter` (drops `api_key`/`base_url`: the `claude` CLI owns auth), registered under both the `claude-code` and `claude_code` spellings (no dash/underscore normalization in `LLMService`, so both must be registered to match the connectivity aliases). The adapter drives `claude -p --output-format json` as a *stateless reasoning core* — each turn serialises the canonical `ChatInterface` (system prompt + tools + conversation) into one prompt, the CLI emits a single JSON action (`tool_call`/`tool_calls`/`final`) which is parsed back into an `LLMResponse`; LingTai's own loop executes the tools. The child env strips `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` so usage stays on the subscription/OAuth path; built-in CLI tools are disabled so it acts as a pure brain. `ClaudeCodeChatSession.send` snapshots the canonical history before mutating it and restores that snapshot if the hook / tool pairing / CLI call / parsing raises, so a failed turn never strands the just-added user / tool-result message; on *successful* context-overflow recovery (`rounds > 0`) it injects a `[kernel]` overflow notice via `_inject_overflow_notice` before recording the assistant turn (same idiom as `openai`/`anthropic`). For health checks it is a *local CLI-login provider* (`lingtai.kernel.preset_connectivity._LOCAL_CLI_LOGIN_PROVIDERS`, both spellings): gauged by module importability, never a TCP probe. See `claude_code/adapter.py`.

- **MiMo factory** — `_register.py:_mimo` builds `MimoAdapter`, forwarding `wire_api` and the daemon-only `mimo_compact_token_limit` (→ `compact_token_limit`) from provider defaults when present. `MimoAdapter` defaults to the native OpenAI Responses wire (`MimoResponsesSession` — stateless full-history/opaque-compacted replay; never `store`/`previous_response_id`/`conversation`/generic `context_management`); an explicit `wire_api="chat_completions"` still selects the pre-existing Chat Completions escape hatch (`MimoChatSession`, the `reasoning_content` round-trip session). Standalone compaction reuses `_StandaloneCompactionMixin` from `openai/adapter.py` but — unlike Codex — treats a compact failure as a HARD failure (`MimoCompactionHardFailure`), never silently continuing on full history or falling back to Chat Completions. See `src/lingtai/llm/mimo/ANATOMY.md`.

## State

Tool-result metadata is stored canonically on `ToolResultBlock.metadata` and
projected by Anthropic, OpenAI Chat Completions, OpenAI Responses, and Gemini
converters. Dictionary and string results use the same projected `_meta` roots;
canonical history retains the sidecar separately and replay does not strip old
agent snapshots. No provider converter implies inbound recovery unless that
provider has a matching converter.

Provider converters project the canonical `ToolResultBlock.metadata` sidecar
into one model-visible `_meta` envelope. This projection is deliberately
handler-agnostic: dictionary results merge the envelope and string/non-dict
results use a structured `{result, _meta}` output. Canonical history stores the
sidecar separately so replay/deserialization preserves it without rewriting
historical holders.

- **Class-level** — `LLMService._adapter_registry` (shared across all instances); `LLMAdapter._gate` (per-adapter instance).
- **Instance-level** — `LLMService._adapters` cache; `LLMService._sessions` registry; `APICallGate._timestamps` deque for RPM window.
- **Provider defaults** — `LLMService._provider_defaults` dict injected at construction (`service.py:228`). Drives model, base_url, max_rpm, api_compat, `default_headers` (caller/provider headers preserved under the shared LingTai identity defaults), the Codex per-agent identity (`codex_session_anchor`/`codex_thread_salt`), Codex token-file selection (`codex_auth_path`), the optional Codex endpoint pool (`codex_base_urls`; direct host/test defaults may also pass `codex_molt_count`), OpenAI Responses `compact_threshold` settings, the OpenAI-compatible `wire_api` selector (`auto`/`chat_completions`/`responses`), and the common Codex `service_tier` selector (`fast` → wire `priority`). Build it from `manifest.llm` via `build_provider_defaults_from_manifest_llm()` (`service.py:124`) — opt-in safelists ensure adapter-consulted manifest fields propagate: `_PROVIDER_DEFAULTS_PASS_THROUGH_KEYS` skips `None` values such as `api_compat`, while `_PROVIDER_DEFAULTS_PRESERVE_NONE_KEYS` preserves explicit `None` for settings like `compact_threshold` where `null` means “disable”. Both `cli.py:_load_init` and `agent.py:_setup_from_init` use this helper to stay in sync.
- **Key resolution** — `LLMService._key_resolver` callable (`service.py:94`); defaults to `os.environ.get(f"{PROVIDER}_API_KEY")`.

## Notes

- **Wire tool descriptions are single-sourced** — every provider payload builder for registered `FunctionSchema` tools sends the kernel constant `WIRE_TOOL_DESCRIPTION` (`src/lingtai/kernel/llm/base.py:86`) as the top-level tool description instead of `FunctionSchema.description`: openai `_build_tools` (`openai/adapter.py:918`) / `_build_responses_tools` (`openai/adapter.py:998`), anthropic `_build_tools` (`anthropic/adapter.py:62`), gemini `_build_function_declarations` (`gemini/adapter.py:40`) / `_build_interactions_tools` (`gemini/adapter.py:210`). The full prose stays in the system prompt's `## tools` section and in canonical `ChatInterface` tool snapshots (`FunctionSchema.list_to_dicts` at the `add_system` call sites); nested parameter descriptions are untouched. Structured-output pseudo-tools keep their task-specific descriptions. `claude_code` is deliberately excluded — it has no tools wire array; `_render_prompt` serialises the full description into the CLI prompt, which *is* its system-prompt side. Tests: `tests/test_wire_tool_description.py`.
- **Abstract methods** — `LLMAdapter` requires: `create_chat()` (line 137), `generate()` (line 171), `make_tool_result_message()` (line 188), `is_quota_error()` (line 200).
- **Tool-call ID dual system** — Provider-issued wire IDs (e.g. Anthropic `tool_use_id`, OpenAI `tool_call_id`) flow through `tool_call_id` kwarg. LingTai issues its own `_tool_call_id` (`service.py:35`: `tc_<unix>_<4-hex>`) stamped onto every result dict for agent-level correlation.
- **Interface converters** — Four bidirectional pairs:
  - `to_anthropic`/`from_anthropic` — Anthropic Messages format (system excluded, ThinkingBlock with signature round-trip)
  - `to_openai` — Chat Completions format (tool results as `role=tool`, ThinkingBlocks emit as `reasoning_content` for DeepSeek and MiMo thinking-mode round-trip; other OpenAI-compat providers ignore the field). One-way only — OpenAI history rehydration goes through `content_block_from_dict` on the canonical interface, not a reverse converter.
  - `to_responses_input` — Responses API input items (`function_call` / `function_call_output` shapes; non-empty ThinkingBlocks replay as `reasoning` items with `summary_text`, before assistant text/calls; `interface_converters.py:240-315`). Output is post-processed by `_pair_responses_orphan_function_calls` (`interface_converters.py:184-227`) so every `function_call` carries a matching `function_call_output` — synthesizes a placeholder for any orphan to prevent the provider's `400 No tool output found` rejection when a continuation request is built from a half-committed tool loop (issue #170). Canonical interface is not mutated; the guard runs on every serialization.
  - `to_gemini`/`from_gemini` — Interactions TurnParam format (`role=model`, `function_call`/`function_result`, `thought` blocks)
- **ToolCallBlock shape conversions** — Anthropic: `tool_use` with `input` dict. OpenAI CC: `function_call` with `arguments` JSON string. Responses: `function_call` with `arguments` JSON string and `call_id`. Gemini: `function_call` with `arguments` dict and `id`.
- **APICallGate mechanics** — Gate thread dequeues items, prunes timestamps >60s old, sleeps if RPM window full, dispatches to pool (`api_gate.py:71-103`). Pool size defaults to `max(2, min(32, max_rpm // 3))`.
- **Pre-request hook convention** (`f46b346`, dormant after notification redesign) — every adapter `send()` / `send_stream()` checks `self.pre_request_hook` after committing the message to the canonical `ChatInterface` and before the API call. Historically the kernel used this to drain the involuntary tool-call inbox mid-turn. Post-`.notification/`-redesign the queue is always empty; ACTIVE notifications now defer until the post-turn IDLE boundary instead of using a send-time prefix hook. Phase 3 will remove the hook. See kernel `llm/ANATOMY.md` for the ABC contract and root `ANATOMY.md` for the full notification architecture.
- **`send(None)` contract** — every adapter `send()` / `send_stream()` accepts `None` as the "continue from wire" signal: caller has already pre-staged the canonical interface (e.g. `BaseAgent._inject_notification_pair` spliced a synthesized `notification(action="check")` `(call, result)` pair); the adapter must skip the input-append step, send the wire as-is, and on API failure must NOT run `drop_trailing` (which would corrupt the pre-staged pair). Driven from `base_agent/turn.py:_handle_tc_wake` — the LLM sees the synthesized pair at the wire tail and reacts as if the agent had voluntarily called the tool.
- **Git history** — 16 commits. Key: Codex stateless path (`7e88f47`, `a4bf117`), context overflow recovery (`f65e395`), orphan tool_call guard (`8197fdc`), per-call HTTP timeout (`e279965`), pre-request hook for mid-turn tc_inbox drain (`f46b346`, now dormant), `send(None)` continue-from-wire contract (`f596ec1`).
