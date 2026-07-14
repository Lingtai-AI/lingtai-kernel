---
related_files:
  - docs/references/codex-http-anatomy-investigation.md
  - src/lingtai/auth/ANATOMY.md
  - src/lingtai/llm/ANATOMY.md
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/llm/identity_headers.py
  - src/lingtai/llm/openai/__init__.py
  - src/lingtai/llm/openai/adapter.py
  - src/lingtai/llm/openai/codex_ws.py
  - src/lingtai/llm/openai/defaults.py
  - src/lingtai/llm/service.py
  - tests/test_codex_prompt_cache_key.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/llm/openai/

OpenAI adapter — wraps the `openai` SDK for Chat Completions and Responses APIs, with Codex OAuth variant.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|------|-----|------|
| `__init__.py` | 3 | Re-exports `OpenAIAdapter`, `OpenAIChatSession` |
| `adapter.py` | large | 5 classes + helpers: `OpenAIChatSession`, `OpenAIResponsesSession`, `OpenAIAdapter`, `CodexResponsesSession`, `CodexOpenAIAdapter` |
| `defaults.py` | 12 | `DEFAULTS` dict: `api_compat="openai"`, `use_responses_api=True`, `wire_api="auto"` |

### adapter.py class map

| Class | Lines | Role |
|-------|-------|------|
| `OpenAIChatSession` | `adapter.py:1210` | Chat Completions session with context-overflow fail-loud propagation (no trim); sends optional `prompt_cache_key` |
| `OpenAIResponsesSession` | `adapter.py:1725` | Responses API session. Official OpenAI mode is server-stateful via `previous_response_id`; custom/OpenAI-compatible mode can be internally stateless (`stateless_replay=True`) and replays full canonical history via `to_responses_input` while recording assistant turns and exposing no resume id (`adapter.py:1749-1750`, `adapter.py:1881-1886`, `adapter.py:1917-1918`, `adapter.py:1928-1929`, `adapter.py:2035-2046`). |
| `OpenAIAdapter` | `adapter.py:2069` | `LLMAdapter` implementation; dispatches to Completions or Responses path; receives injected `compact_threshold`; derives the default `prompt_cache_key` via `_default_prompt_cache_key` / `_resolve_prompt_cache_key`; carries the internal `_responses_stateless_replay` constructor mode into Responses sessions (`adapter.py:2092`, `adapter.py:2127`, `adapter.py:2264-2277`). |
| `CodexResponsesSession` | `adapter.py:2492` | Responses session for ChatGPT-backed Codex running the `full`/`incremental` additive continuation state machine over a selectable transport (REST default, WebSocket opt-in): `store=false` always, encrypted reasoning include/replay (always the recorded raw item, never substituted), fail-loud propagation on unverifiable encrypted content, cache-affinity headers, honest client/account identity, and honest Codex metadata envelope. |
| `CodexOpenAIAdapter` | `adapter.py:4307` | Codex provider specialization: forces Responses mode, derives stable per-agent cache/session ids plus a LingTai installation id from the configured anchor, and wires account/metadata hints into Codex sessions. Maps an omitted/`default` thinking level to an explicit `reasoning.effort = "xhigh"` in its `_create_responses_session` (Codex-only default — omitting the field would fall back to the backend's lower default; explicit levels pass through unchanged, and the generic `OpenAIAdapter` keeps omit-on-default). `codex-pool` reuses this adapter, so it inherits the same default. |

### adapter.py helpers

| Function | Lines | Role |
|----------|-------|------|
| `_base_url_namespace()` | `adapter.py:706` | Stable namespace token for an OpenAI-compatible `base_url` (URL host, or short hash fallback) used in the default `prompt_cache_key` |
| `_codex_session_id()` | `adapter.py:135` | Derive the 8-char Codex cache-affinity id (issue #378): `sha256(f"{anchor}\0{molt_count}").hexdigest()[:8]`, lowercase hex, where `anchor` MUST be a per-agent identity (the resolved `init.json` path) and `molt_count` is the agent's current molt count. The same value is used byte-identically for `session_id`, `thread_id`, and the default `prompt_cache_key` on the root/main path. No time/epoch — stable across restarts WITHIN a molt segment, and intentionally changes at each molt boundary |
| `_codex_installation_id()` | `adapter.py:248` | Derives a UUID-shaped, non-secret LingTai installation id for Codex `client_metadata` from the same local anchor/id; never reuses `~/.codex/installation_id`. |
| `_codex_identity_headers()` | `adapter.py:261` | Builds Codex client identity headers (`originator`, `User-Agent`): default requests identify as LingTai via the shared `llm/identity_headers.py` User-Agent helper, while the official Codex CLI-shaped identity remains an explicit local diagnostic opt-in. |
| `_validate_compact_threshold()` | `adapter.py:771` | Validates/normalizes OpenAI Responses auto-compaction threshold; positive `int` or explicit `None` (disable) only |
| `_codex_responses_trace_path()` / `_codex_responses_trace_record()` | `adapter.py:799`, `adapter.py:825` | Opt-in Codex Responses stream diagnostic trace helpers; safe metadata only, default off |
| `_build_http_timeout()` | `adapter.py:896` | `httpx.Timeout` per-phase caps (connect≤30s, read≤60s, pool=10s) |
| `_build_tools()` | `adapter.py:918` | `FunctionSchema` → OpenAI CC tool format (`{type, function: {name, description, parameters}}`) |
| `_build_responses_tools()` | `adapter.py:998` | `FunctionSchema` → Responses API flat format (`{type, name, description, parameters}`); scrubs disallowed top-level JSON-Schema combinators (`allOf`, `oneOf`, etc.) |
| `_parse_response()` | `adapter.py:1047` | ChatCompletion → `LLMResponse` (extracts reasoning from `reasoning_content` or `reasoning`) |
| `_handle_responses_reasoning_event()` | `adapter.py:1114` | Responses stream reasoning-summary event handler; accumulates `summary_text` deltas/done fallback without raw reasoning text |
| `_parse_responses_api_response()` | `adapter.py:1160` | Responses API output → `LLMResponse` (handles `message`, `function_call`, `reasoning` output items) |

## Connections

- **Base class** — `OpenAIAdapter` extends `LLMAdapter` (`from lingtai.llm.base import LLMAdapter`, `adapter.py:29`).
- **Kernel types** — imports `ChatSession`, `FunctionSchema`, `LLMResponse`, `ToolCall`, `UsageMetadata` from `lingtai.kernel.llm.base`.
- **Interface converters** — imports `to_openai` and `to_responses_input` from `lingtai.llm.interface_converters` (`adapter.py:31`).
- **Streaming** — imports `StreamingAccumulator` from `lingtai.kernel.llm.streaming` (`adapter.py:32`).
- **HTTP client** — imports `httpx` for timeout construction (`adapter.py:16`); `openai` SDK for all API calls (`adapter.py:17`).
- **Subclass hooks** — `_session_class` (`adapter.py:2077`) for Completions path; `_adapter_extra_body()` (`adapter.py:2338`) for provider-specific `extra_body`; `_default_prompt_cache_key()` (`adapter.py:2139`) for the provider-namespaced cache key.

## Composition

### Two session paths

The adapter forks at `create_chat()` (`adapter.py:2186`) via `_should_use_responses()` (`adapter.py:2167`):
1. **Responses API** (`_create_responses_session`, `adapter.py:2225`) — when canonical `wire_api="responses"`, or when `wire_api="auto"` and legacy `use_responses=True` AND (`base_url` is None OR `force_responses=True`). Builds `OpenAIResponsesSession`, threading the adapter's `_responses_stateless_replay` into its `stateless_replay` kwarg (`adapter.py:2276`) — so a custom/OpenAI-compatible Responses adapter builds a stateless-replay session while official OpenAI stays stateful.
2. **Chat Completions** (`_create_completions_session`, `adapter.py:2279`) — fallback for compatible providers and when `wire_api="chat_completions"`. Builds `self._session_class` (subclass-overridable).

Both paths return sessions wrapped via `_wrap_with_gate()` for rate limiting. Canonical `wire_api` wins over legacy `use_responses`/`force_responses`; when `wire_api` is absent/`auto`, existing behavior is preserved.

### One-shot generation (`OpenAIAdapter.generate`, `adapter.py:2347`)

`generate()` follows the same wire selection as `create_chat()` via `_should_use_responses()`: Chat Completions by default/legacy, or Responses API when `wire_api="responses"` (or legacy `use_responses=True` without a custom base URL / with `force_responses=True`). This keeps service-level one-shot calls consistent with multi-turn sessions.

### Chat Completions session flow (`OpenAIChatSession.send`, `adapter.py:1367`)

1. Record user input into `ChatInterface` (str → `add_user_message`; list → `add_tool_results`)
2. `_build_kwargs()`: enforce tool pairing → serialize via `_build_messages()` → `_pair_orphan_tool_calls()` wire guard
3. `_run_with_overflow_recovery(_do_call)` — classifies/logs 400 context-length errors and propagates them unconditionally (no trim, no retry)
4. On success: record assistant response into interface via `_record_assistant_response()`

### Responses API session flow (`OpenAIResponsesSession.send`, `adapter.py:1888`)

Two modes share one session class:
1. **Official/stateful** — `_convert_input(message)` builds only the new Responses input items (`adapter.py:1772-1827`), `previous_response_id` is sent when available (`adapter.py:1917-1918`, `adapter.py:1970-1971`), and the returned id becomes the next resume id (`adapter.py:1931`, `adapter.py:2021`). `_convert_input` maps a canonical `ToolResultBlock` continuation to the `function_call_output` wire item (`adapter.py:1812-1822`) using the same shape as `to_responses_input`, so a plain (non-Codex) Responses session serializes tool-result turns correctly instead of forwarding the dataclass unconverted; prebuilt Responses-wire `function_call_output` dicts pass through unchanged, while legacy `role=tool` dicts are converted to the same Responses shape (`adapter.py:1799-1811`).
2. **Custom/stateless** — `_snapshot_interface` captures the pre-stage snapshot before `_stage_input`, `_request_input` records string/tool-result input (or leaves `send(None)` pre-staged entries alone), enforces tool pairing, and serializes full canonical history through `to_responses_input`; no `previous_response_id` is sent (`adapter.py:1829-1832`, `adapter.py:1834-1843`, `adapter.py:1881-1886`, `adapter.py:1917-1918`). On success `_record_assistant_response` persists reasoning/text/tool calls plus usage, including cached tokens, into canonical history (`adapter.py:1858-1879`, `adapter.py:1928-1929`, `adapter.py:2018-2019`). On transport/stream/enforce/serialization/parse/finalize/callback/record failure, `_rollback_staged` restores the pre-send canonical snapshot in place while preserving the recovery lookup (`adapter.py:1845-1856`, `adapter.py:1933-1935`, `adapter.py:2023-2025`).

### Codex continuation flow (`CodexResponsesSession.send_stream`)

Two orthogonal axes drive each Codex turn:

- **Transfer mode** — `full` vs `incremental` (the strict additive
  `previous_response_id` state machine, `_codex_plan_continuation`). Transport-independent.
- **Transport** — `rest` (normal runtime, hardcoded) vs `websocket`. Selects how
  the planned request is sent, never *whether* incremental continuation is used.

**Transport selection:** REST is **hardcoded** for normal runtime
(`_CODEX_TRANSPORT_DEFAULT = "rest"`). There is intentionally **no environment
variable** that selects the transport — an inherited `LINGTAI_CODEX_WS` or
`LINGTAI_CODEX_TRANSPORT` is ignored and cannot flip the runtime to WebSocket
(live testing confirmed REST prompt-prefix caching is sufficient). The WebSocket
transport code is retained for tests / internal / future use and is reachable
**only** via an explicit `transport="websocket"` (or legacy `ws_enabled=True`)
constructor kwarg.

Flow:

1. Record message into canonical `ChatInterface`.
2. `_frozen_responses_input(interface)` — the full conversation as input items
   (with per-`call_id` output freezing for stable replay). Full-history
   replay is NOT Codex-specific: the shared converter
   (`interface_converters.to_responses_input`) does not strip a historical
   holder's `_meta.agent_meta` / `_meta.guidance` / `_meta.notifications` /
   `_meta.notification_guidance` keys in any model-facing full-history
   serialization, without mutating canonical history — the ONLY
   historical tool-result body replacement is `summarize`. Only the latest
   holder per family (`agent_meta`+`guidance`, `notifications`+
   `notification_guidance`) represents current state; older holders are
   historical traces, not current instructions. Within a WS epoch the
   per-`call_id` freeze keeps already-sent output STRINGS byte-identical (so a
   result the model saw keeps its frozen payload) across in-place canonical
   rewrites such as summarize marker/status flips; after an epoch reset
   (`_reset_ws_epoch`) the cleared freeze map re-freezes from the converter's
   serialization, so the fresh replay still carries every historical holder.
3. Run the shared `_codex_plan_continuation` planner: first turn / prefix mismatch
   / epoch reset → **full**; strict additive continuation → **incremental**.
4. Send via the selected transport:
   - REST `client.responses.create(...)` (default): send the full converted input
     in both modes. REST `incremental` means unchanged prefix/cache epoch, not
     wire delta, and never sends `previous_response_id`.
   - WebSocket (`codex_ws`): `full` sends the full input; `incremental` sends the
     strict-additive delta plus `previous_response_id`.
5. If Codex rejects a full replay with `The encrypted content for item ... could
   not be verified`, that is a terminal provider-side condition for this
   request: `_is_codex_unverifiable_encrypted_content_error` (`adapter.py:62`)
   only classifies/logs it. The recorded raw `ThinkingBlock.provider_data`
   reasoning item is NEVER mutated or substituted — `to_responses_input`
   (`src/lingtai/llm/interface_converters.py`) always emits every recorded
   reasoning item as-is, ordinary send or rebuild alike, matching the literal
   provider-context rebuild/replay invariant (only an explicit `summarize`
   replacement may replace historical content; see that module's docstring).
   The error propagates unconditionally — no second request with a different
   historical representation is ever sent — into the existing AED
   over-window recovery path (`base_agent/turn.py`), which is deterministic,
   fully logged, and requires an explicit agent- or operator-driven
   summarize/molt to actually recover.
6. After success: record the assistant response into the interface and recompute
   the converter-stable delta baseline (`_ws_record_baseline_from_interface`) so the
   next turn can strict-prefix-match and stay incremental.
**Usage metadata axes:** `UsageMetadata.extra` carries `codex_transport`
(`rest`/`websocket`), `codex_transfer_mode` (`full`/`incremental`), and the
transport-qualified `codex_request_mode` (`rest_full` / `rest_incremental` /
`ws_full` / `ws_incremental` / `rest_full_fallback`), plus the safe delta-decision
diagnostic (`codex_ws_delta_reason` and counts — never prompt/secret content). The
WS-named `codex_ws_*` diagnostic keys are reused on REST (transport-neutral
metadata); `codex_request_mode` never reads `ws_*` on a REST request. There is
no self-heal request mode: an unverifiable-encrypted-content error propagates
instead of triggering a second request.

### Prompt cache key (`prompt_cache_key`)

**Default-on for every OpenAI-compatible path.** Both `OpenAIChatSession` and `OpenAIResponsesSession` accept an optional `prompt_cache_key` and, when set, add it to the request kwargs on all send paths (Chat Completions `send` / `send_stream`; Responses `send` / `send_stream`; Codex `send_stream`). A bare directly-constructed session leaves it `None` (opt-in) — the *adapter* supplies the namespaced default:

- `OpenAIAdapter._default_prompt_cache_key(model)` derives the namespace from identity: official OpenAI (no `base_url`) → `lingtai-openai:{model}:v1`; any custom/compatible `base_url` → `lingtai-openai-compat:{host}:{model}:v1` (host from `_base_url_namespace`, hash fallback). Distinct endpoints/models never share a cache slot.
- Provider subclasses with a fixed identity override it: DeepSeek → `lingtai-deepseek:{model}:v1`, Zhipu/GLM → `lingtai-zhipu:{model}:v1`, MiMo → `lingtai-mimo:{model}:v1`. **Codex is special:** on the normal/root path `_default_prompt_cache_key` returns the SAME 8-char (agent-path, molt-count) hash as the `session_id`/`thread_id` cache-affinity headers (underscore keys, matching the Codex backend literally — a hyphenated spelling loses cache affinity; all three byte-identical); it falls back to `lingtai-codex:{model}:v1` only on the bare/no-anchor path. The compat probe (`reports/prompt-cache-key-openai-compat-probe-*.json`) confirmed DeepSeek/Zhipu/MiMo Chat Completions accept the field.
- `_resolve_prompt_cache_key(model)` applies the adapter's policy from the constructor kwarg `prompt_cache_key`: `None` (default) → auto-derive; an explicit string → override for every session; `False` → disable (never sent). Both `_create_completions_session` and `_create_responses_session` (and the Codex variant) pass `_resolve_prompt_cache_key(model)` into the session.

`prompt_cache_retention` is deliberately never sent — Codex rejects it (`Unsupported parameter`) and the whole OpenAI-compatible surface is kept uniform — and no Anthropic-style `cache_control` is emitted (Codex rejects `Unknown parameter`). MiniMax is Anthropic-compatible in this repo and is unaffected.

### Codex REST cache-affinity ids (`session_id` / `thread_id` / `prompt_cache_key`)

**Codex-only.** The three cache-affinity values are a **single per-agent id**, byte-identical, stable within a molt segment and refreshed at each molt boundary:

```
prompt_cache_key == session_id == thread_id == <per-agent (anchor, molt) id>
```

`CodexResponsesSession.__init__` **normalizes** whatever candidates it receives (`prompt_cache_key`, `session_id`, `thread_id`) into one id and uses it byte-identically for all three. Priority is `prompt_cache_key` > `session_id` > `thread_id` (the explicit request-body cache-affinity key wins). This closes the leak where explicit request-body/cache-affinity inputs or a directly-constructed session could send three different values.

**The id is a deterministic hash of the agent path AND the current molt count — no time, no epoch, no rotation within a molt, no operator override.** It is `_codex_session_id(anchor, molt_count) = sha256(f"{anchor}\0{molt_count}").hexdigest()[:8]`, where `anchor` is the per-agent durable identity (the resolved `init.json` path) and `molt_count` is read live from `<working_dir>/.agent.json`. Within a molt segment the same agent yields the same id across restarts / refresh / clear, so it keeps routing to the **same sticky-warm backend cache slot**; at each molt boundary the id intentionally changes so the molt starts on a fresh slot. **Molt does NOT rebuild the adapter**, so the id is (re)derived at request time from the live `molt_count` — never cached once at construction (`_resolve_codex_ids` / `_default_prompt_cache_key` both call `_current_codex_id()`, which reads `_current_molt_count()` afresh). (Empirically the backend routes the prompt cache to a replica off a stable session id; churning it *within* a molt re-rolls the routing and discards the warm slot — so the only intentional id change is the molt boundary. Earlier designs epoch-stamped the id on rebuild and rotated it on "stalled cache" dips, and a now-removed operator-level fixed `codex_session_id` override could pin the id; all were removed as counterproductive, along with the `codex-cache-key` request header.)

Stable HTTP headers (`session_id` / `thread_id`, underscore spelling to match Codex CLI, sent via the SDK's per-request `extra_headers` — never request-body fields) plus the request-body `prompt_cache_key` are the cache-affinity lever and are independent of the transfer mode. They ride on **every** request regardless of full vs incremental.

> **REST incremental caveat.** REST `incremental` is not a wire-delta mode. It still sends the full converted context, but labels the request as an unchanged-prefix/cache-epoch continuation so diagnostics and cache-ledger logic can distinguish it from an epoch rebuild. WebSocket remains the only Codex transport that sends delta input plus `previous_response_id`.

- **Header carve-out (NON-NEGOTIABLE).** `session_id` / `thread_id` route the backend cache slot and MUST be per-agent. Headers are emitted **only when an explicit `session_id`/`thread_id` was supplied** (`has_header_identity`); a *lone* `prompt_cache_key` — the model-only fallback `lingtai-codex:{model}:v1`, shared by every agent on a model — stays a **body-only** cache key and promotes **no** headers. Promoting it would collapse all agents onto one session/thread, which is exactly the bug the per-agent design exists to avoid. So `_cache_affinity_headers()` emits headers iff the session was given header identity; a bare/test session with no ids sends neither. The adapter has no per-agent identity of its own, so the host wiring passes the agent path down by default (see below).
- **Default wiring (the normal path — not opt-in, not opt-out).** For a Codex agent, `service.build_provider_defaults_from_manifest_llm(llm, ..., working_dir=...)` injects `codex_session_anchor = str((working_dir / "init.json").resolve())` (the agent path / durable identity anchor). The adapter hashes it together with the live `molt_count` into the id and uses it for all three values via `_resolve_codex_ids` (returns `(id, id)` — the thread tracks the session id exactly) and `_default_prompt_cache_key` (returns the same `id`), both computed fresh per request through `_current_codex_id()`. The default wiring does **not** read the token ledger, molt time, or any clock — only the agent path and the current `molt_count` — so the same `(working_dir, molt_count)` always yields the same id and a molt advances it.
- The `codex_session_anchor` / `codex_thread_salt` keys remain settable on the manifest `llm` block (allowlisted in `../service.py` `_PROVIDER_DEFAULTS_PASS_THROUGH_KEYS`) as an **internal override / testing escape hatch** — `codex_session_anchor` overrides the auto-injected agent path; `codex_thread_salt` survives as a legacy pass-through but no longer derives a separate thread id. There is no operator-level fixed-id override. `_resolve_codex_ids(model)` returns `(None, None)` only when no anchor was passed down at all (the bare/test path).
- **Token-ledger dump.** `CodexResponsesSession._usage_extra` copies the `session_id` / `thread_id` / `prompt_cache_key` for the request into `UsageMetadata.extra` as `codex_session_id` / `codex_thread_id` / `codex_prompt_cache_key`. Because of the normalization above these three are the **same value** — the ledger never records mismatched affinity ids. The same safe seam also records Codex account/auth attribution as non-secret diagnostics: `codex_account_id_sha8` (SHA-256/8 of the raw `ChatGPT-Account-ID`), `codex_auth_path_sha8`, `codex_auth_path_source`, and when `codex-pool` built the chat, pool source/index/size/weight/fallback fields plus `codex_pool_model_scope` (the exact category key of a model-classified pool — a non-secret manifest model string; `None` on flat v1 pools, hence omitted by the non-None guard) copied from the non-secret `codex_pool_selection`. `BaseAgent._save_chat_history()` merges all non-None usage extra fields into `logs/token_ledger.jsonl`, so the ids sit beside input/output/thinking/cached token counts. `SessionManager._track_usage()` also filters an allowlisted safe Codex subset into the `llm_response.usage_extra` event for `events.jsonl`. The values are short, non-secret derived diagnostics — no raw account id, auth path, request body, messages, token, or OAuth secret ride along. A body-only/bare session contributes only the fields whose levers were actually sent.

### Codex client-identity headers (`originator` / `User-Agent`)

The default request identity is explicit LingTai: `_CODEX_ORIGINATOR = "lingtai"` and `User-Agent: LingTai/<version>`, with the version token resolved through the shared LLM HTTP identity helper (`../identity_headers.py:20`). `_CODEX_IMPERSONATE_OFFICIAL_CLI` is reserved for explicit local protocol comparisons and switches both `originator` and `User-Agent` to the official Codex CLI-shaped values only when enabled; account-selection headers remain independent of this identity choice. `adapter.py:140`, `adapter.py:153`, `adapter.py:178`, `adapter.py:214`, `tests/test_codex_prompt_cache_key.py:113`, `tests/test_codex_prompt_cache_key.py:590`

### Codex `ChatGPT-Account-ID` header (the user's own account id)

**Codex-only.** When the user's OAuth auth data supplies an account id (`CodexTokenManager.get_account_id()`, see `../../auth/ANATOMY.md`), `CodexResponsesSession` sends it as the canonical `ChatGPT-Account-ID` header so the request is attributed to the right ChatGPT account. It does NOT change the honest `originator`/`User-Agent` identity above — no Codex-CLI impersonation. The value flows `_register.py` (`codex_account_id=mgr.get_account_id()`) → `CodexOpenAIAdapter.codex_account_id` (mutable; the OAuth-refresh monkey-patch re-reads it via `get_account_id()` so a refresh that changes the id stays current on later-built sessions) → `CodexResponsesSession(account_id=...)` (`self._account_id`). Emitted only when non-empty; otherwise the header is omitted entirely. The raw account id is **NOT** copied into `UsageMetadata.extra`, `token_ledger.jsonl`, or `events.jsonl`; only `codex_account_id_sha8` (SHA-256/8) is recorded for diagnostics.

### Codex honest metadata envelope

See `docs/references/codex-http-anatomy-investigation.md` for the capture history, Codex CLI comparison, and the safety rationale behind which metadata LingTai does and does not send.

When a Codex session has a stable LingTai session/thread identity, `CodexResponsesSession` adds an honest metadata envelope alongside the cache-affinity headers (`adapter.py:2664` and the Codex send path at `adapter.py:3779`). Each request gets a fresh `x-client-request-id`; `x-codex-window-id` is the LingTai window id `<session_id>:0`; `x-codex-turn-metadata` is compact JSON carrying `session_id`, `thread_id`, a generated `turn_id`, a truthful LingTai `sandbox` label, and `turn_started_at_unix_ms`; and body `client_metadata.x-codex-installation-id` is carried through `extra_body` because the OpenAI Python SDK has no typed `client_metadata` argument. This is compatibility metadata, not CLI impersonation: LingTai keeps `originator: lingtai` / `User-Agent: LingTai/<version>`, does not send `x-codex-beta-features`, and derives its installation id from LingTai state rather than from the official Codex CLI installation file.

## State

- `CodexResponsesSession._installation_id` / `_metadata_sandbox`: optional honest Codex metadata state used to build `client_metadata.x-codex-installation-id` and turn metadata without leaking local paths or claiming official CLI features.

- **`OpenAIChatSession._interface`** — canonical `ChatInterface`, single source of truth. Mutated in-place: `add_user_message`, `add_tool_results`, `add_assistant_message`, `drop_trailing`.
- **`OpenAIChatSession._request_timeout`** — per-request HTTP timeout set by caller before dispatch (`adapter.py:1248`). Prevents race between watchdog and SDK.
- **`OpenAIResponsesSession._response_id` / `_stateless_replay`** — in official/stateful mode `_response_id` is the server-side chain pointer and `session_resume_id`; in custom/stateless mode `_stateless_replay=True`, `_response_id` is not advanced, `session_resume_id` returns `None`, and `get_history()` returns full canonical `ChatInterface` history for durable restart (`adapter.py:1749-1750`, `adapter.py:2028-2039`).
- **`CodexResponsesSession._response_id`** — transient debug aid only; never threaded into next request (`adapter.py:2492`).
- **`CodexResponsesSession._current_id`** — the single per-agent affinity id (the hash of the agent path + current molt count) handed to this session, used byte-identically for `_prompt_cache_key` / `_session_id` / `_thread_id`. Set once per session at construction — a NEW session is built for each `create_chat`, and the adapter resolves the molt-current id at that point, so a molt-advanced id reaches the next session without any in-session mutation (no rotation, no epoch, no clock).
- **Codex Responses trace** — opt-in diagnostics write JSONL metadata to `logs/codex_responses_trace.jsonl` when `LINGTAI_CODEX_RESPONSES_TRACE=1` (override path with `LINGTAI_CODEX_RESPONSES_TRACE_PATH`). Default off; stores event/item shapes, lengths/hashes, usage, and accumulator counts, not raw content.
- **`OpenAIAdapter._client`** — shared `openai.OpenAI` instance. `_client_kwargs` stored for session `reset()`. Constructor passes `default_headers=merge_lingtai_identity_headers(...)` (`adapter.py:2125`), so OpenAI-compatible HTTP requests carry non-secret LingTai identity/version headers unless a caller/provider header overrides them case-insensitively.
- **`OpenAIAdapter._session_class`** — class var, subclasses override (e.g. DeepSeek and MiMo inject `reasoning_content` round-trip fallbacks).
- **`CodexResponsesSession` delayed-summarize / hard-boundary forced rebuild** — `_last_provider_input_tokens` holds the previous real provider request's reported input tokens; `_summarize_delay_context()` divides it by `context_window()` for provider-input-based usage (the same ruler the reconstruction event uses). At usage `>= 1.0` the runtime forces a fresh full replay (`_reset_ws_epoch("summarize_delayed")`) **exactly once per continuous `>= 1.0` episode**: `_hb_rebuild_fired` latches the one-shot and `_hb_rebuild_awaiting_verify` stays set until the first post-rebuild provider response is observed. Both automatic entry points — the pre-request boundary check `_maybe_force_rebuild_at_boundary()` and the immediate `on_history_summarized()` release — go through the shared `_fire_boundary_forced_rebuild()`, so they cannot double-fire. `_observe_provider_usage_for_boundary()` (run after each successful send) re-arms the latch when usage drops strictly below `1.0` and clears the pending-verify flag on the first post-rebuild response (a failed forced request records no usage, so verification stays pending). `context_overflow_status()` returns `{"usage": …}` only when the rebuild fired, verification completed, and current usage is strictly `> 1.0` — the seam `meta_block.build_context_overflow_warning` reads (through the gate proxy's `__getattr__`) to keep the fixed `100% context Forced Rebuild Failed to Bring Usage Below 100%. … (xxx %) Molt IMMEDIATELY!!` line on every `tool_meta.context.molt`. Explicit `request_history_rebuild()` is independent and never touches these flags. Transient runtime state — a fresh/restored session starts un-fired.

## Notes

### Provider-specific shape conversions

| Canonical block | Chat Completions wire | Responses API wire |
|----------------|----------------------|-------------------|
| `ToolCallBlock` | `{type: "function", id, function: {name, arguments: <json-str>}}` on assistant message `tool_calls` array | `{type: "function_call", call_id, name, arguments: <json-str>}` as top-level output item |
| `ToolResultBlock` | `{role: "tool", tool_call_id, content}` as separate message | `{type: "function_call_output", call_id, output}` as top-level input item |
| `TextBlock` | `content` string on assistant message | `{type: "output_text", text}` inside message content |
| `ThinkingBlock` | Emitted as `reasoning_content` on assistant message (DeepSeek and MiMo thinking-mode round-trip; other CC providers ignore the field). Captured back from `message.reasoning_content` / `message.reasoning` into a ThinkingBlock by `_record_assistant_response` (non-streaming) and the streaming finalize path. | Replayed as a top-level `{type: "reasoning", summary: [{type: "summary_text", text: ...}]}` item before assistant text/calls by `to_responses_input` (`../interface_converters.py:233-258`) so stateless Codex can retain summarized reasoning context. Responses streaming captures `response.reasoning_summary_text.*` into thoughts and Codex persists those thoughts as ThinkingBlocks before tool calls. |

### Context overflow fail-loud

`OpenAIChatSession._run_with_overflow_recovery()` is inherited from `ChatSession` (`lingtai/kernel/llm/base.py`) and wraps any API call:
- Detects 400 `context_length_exceeded` via `_is_context_overflow_error()` (`adapter.py:1267`) — checks both canonical OpenAI code and loose string heuristics for compatible vendors — purely for logging/diagnostics.
- No canonical or rendered history is ever trimmed here (the former `_trim_context_one_round()` front-drop and its up-to-10-round retry loop were removed): the kernel has no license to silently discard historical tool-result content to fix an overflow — only an explicit `summarize` replacement may replace a historical tool-result body (see `lingtai.tools.system.summarize` and the provider-context rebuild/replay invariant in `lingtai/llm/interface_converters.py`).
- The provider error is logged and re-raised immediately, unconditionally, into the caller's existing AED over-window recovery path (`base_agent/turn.py`'s `_is_over_window_error` / `aed_over_window_detected` / `aed_exhausted`), which is deterministic, fully logged, and requires an explicit agent- or operator-driven summarize/molt to actually recover. No fake success notice is appended.

### Wire-layer orphan guard

`_pair_orphan_tool_calls()` (`adapter.py:1305`) scans the serialized message list for `assistant.tool_calls` without matching `role=tool` messages. Synthesizes placeholder tool results with `[synthesized placeholder — real result was not in context at send time]`. Logs warnings for investigation. Does NOT mutate canonical interface.

The Codex / Responses path has the same invariant: `to_responses_input` ends with `_pair_responses_orphan_function_calls` (`../interface_converters.py:190-250`) which synthesizes a `function_call_output` for any `function_call` without a matching output anywhere in the items list. Same placeholder string, same non-mutating semantics. Without this guard the provider returns `400 No tool output found for function call …` when a continuation request is built from a half-committed tool loop (issue #170).

**Placeholders go at the TAIL, not interleaved.** The guard appends its synthesized placeholders as one contiguous block at the end of the items list, in `function_call` order — it does **not** insert each placeholder immediately after its call. This is a continuation-stability fix: `to_responses_input` always emits an assistant entry's `function_call`s contiguously and all real `function_call_output`s afterwards, so interleaving a placeholder right after each call made placeholder positions drift relative to where the real outputs land. A multi-call turn resolving incrementally then broke the Codex strict-prefix continuation and forced a `*_full` request every turn — the logged `prefix_mismatch` with `mismatch_prev_type=function_call_output` vs `mismatch_cur_type=function_call`. The baseline recorder reinforces this: `_ws_record_baseline_from_interface` strips **all** synthesized placeholders (not just trailing ones) from the recorded baseline via `_strip_synthesized_orphan_outputs` (`adapter.py:539`, `adapter.py:3065`), so the real, position-stable items are the only load-bearing prefix and the real tool result strictly extends the baseline. Fix applies to BOTH transports (shared converter + shared baseline recorder).

### System prompt is `instructions`, frozen per session (Responses/Codex)

On the Responses/Codex path the system prompt is **not** an `input` item — it rides in the top-level `instructions` kwarg. `to_responses_input` deliberately skips system entries (`../interface_converters.py:280-281`, documented `../interface_converters.py:256-257`); the prompt is carried separately as `instructions`.

That `instructions` value is **frozen at session construction** for official/stateful Responses and Codex: `OpenAIResponsesSession.__init__` captures `self._instructions = instructions` (`adapter.py:1745`) and every send replays that value (`adapter.py:1911-1912`, `adapter.py:1964-1965`; Codex sends its own instructions in its subclass path). There is no re-read from the interface in stateful mode.

In-flight official/stateful Responses and Codex sessions keep no-op prompt/tool update behavior for continuation stability. Custom/stateless `OpenAIResponsesSession` is different: because every request is a full canonical replay, `update_system_prompt` updates both `_instructions` and the interface, and `update_tools` rebuilds the Responses tool payload and appends a system/tool snapshot (`adapter.py:2041-2054`).

**Behavior-code contract:** for stateful official Responses/Codex, a pad / system-prompt edit mid-session changes nothing on the wire and does not break warm continuation; a changed system prompt takes effect when a new session is constructed. For custom/stateless Responses, prompt/tool edits are part of the next full replay and do not require a server-side continuation reset.

### Streaming

- **CC streaming** (`adapter.py:1555`) — `stream=True, stream_options={include_usage: True}`. Uses `StreamingAccumulator` for text + tool deltas. Reasoning deltas captured from `delta.reasoning` or `delta.reasoning_content`. Overflow recovery wraps stream open + first chunk in the Chat Completions send-stream path.
- **Responses streaming** (`adapter.py:1938`) — event types: `response.reasoning_summary_text.delta/done` (summary thoughts only), `response.output_text.delta`, `response.function_call_arguments.delta`, `response.output_item.added/done`, `response.completed`. Custom/stateless mode snapshots before staging, replays full canonical history, records the finalized assistant turn, and restores the pre-send snapshot on enforce, serialization, stream-open, iteration, callback, finalize, or record failure (`adapter.py:1945-2026`).
- **Codex streaming** — forces `stream=True` even on `send()`. Runs the `full`/`incremental` planner per request over the selected transport (REST default / WebSocket opt-in): REST carries the whole converted interface in both modes; WebSocket carries the whole interface for `full` and delta + `previous_response_id` for `incremental`. Captured summary thoughts and raw encrypted reasoning items are persisted as ThinkingBlocks so `to_responses_input` replays reasoning items before function calls, always emitting the raw recorded `openai_responses_reasoning_item` as-is — there is no session-local unreplayable-ID set and no retry with a summary/plain-transcript substitute. If Codex rejects a raw encrypted item as unverifiable (`_is_codex_unverifiable_encrypted_content_error`, both REST and WS transports), that is a terminal provider-side condition for the current recorded history: canonical `ThinkingBlock.provider_data` is never mutated or replaced, and the adapter raises `TerminalProviderHistoryError` (`lingtai.kernel.llm_utils`) instead of retrying with a different historical representation. `_run_loop` (`base_agent/turn.py`) treats that error as immediately terminal — no AED attempt spent, no rebuild, no preset fallback — logging `terminal_provider_history_error` and moving the agent STUCK then ASLEEP. Optional diagnostics (`LINGTAI_CODEX_RESPONSES_TRACE=1`) append safe per-event metadata to `logs/codex_responses_trace.jsonl` without changing accumulator/persistence behavior.

### Authentication paths

- **Standard** — `api_key` passed to `openai.OpenAI(api_key=...)` at construction (`adapter.py:2121-2127`).
- **Codex OAuth** — `CodexOpenAIAdapter` built by `../_register.py:54` with `CodexTokenManager.get_access_token()`. Token refreshed by monkey-patching `create_chat` and `generate` to update `adapter._client.api_key` in-place before each call.

### Tool schema conversion

- **CC path** — `_build_tools()` (`adapter.py:918`): `{type: "function", function: {name, description, parameters}}`.
- **Responses path** — `_build_responses_tools()` (`adapter.py:998`): `{type: "function", name, description, parameters}` (flat). Scrubs top-level JSON-Schema combinators (`_RESPONSES_DISALLOWED_TOP_LEVEL`, `adapter.py:942`) that the Responses API rejects.

### Reasoning extraction

- **CC non-streaming** (`_parse_response`, `adapter.py:1047`) — checks `message.reasoning_content` (OpenAI native) then `message.reasoning` (OpenRouter).
- **CC streaming** — `delta.reasoning` or `delta.reasoning_content` accumulated via `acc.add_thought()` in `OpenAIChatSession.send_stream` (`adapter.py:1555`).
- **Responses non-streaming** — `reasoning` output items with `summary_text` blocks (lines 256-259).
- **Responses streaming** — `response.reasoning_summary_text.delta/done` and reasoning output-item summaries are captured as summary thoughts; raw `response.reasoning_text.*` is intentionally not persisted by default.

### Subclass hooks

- `_session_class` (`adapter.py:2070`) — override to inject provider-specific session behavior on the CC path.
- `_adapter_extra_body()` (`adapter.py:2331`) — override to add `extra_body` JSON fields (e.g. OpenRouter `reasoning: {include: true}`).
- `_default_prompt_cache_key(model)` (`adapter.py:2132`) — override to give a provider a clean cache namespace (DeepSeek/Zhipu/MiMo/Codex do).

### `send(None)` contract — continue from wire

All four `send` / `send_stream` paths in this file accept `None` as the "the caller has already staged the canonical interface; just talk to the LLM" signal. This is what `base_agent/turn.py:_handle_tc_wake` calls when `_sync_notifications` has spliced a synthesized `(ToolCallBlock, ToolResultBlock)` pair into the wire — from the LLM's viewpoint the agent appears to have voluntarily called `notification(action="check")` and is now responding to the result, no fake user message and no meta prefix.

Implementation: the input-dispatch ladder at the top of each canonical-replay method treats `None` as "no adapter-owned input to append." In custom/stateless Responses `_snapshot_interface(None)` returns no rollback snapshot and `_request_input` serializes the already-staged interface through `to_responses_input`, so notification-style `(ToolCallBlock, ToolResultBlock)` pairs ride on the same request and are not removed on failure (`adapter.py:1829-1832`, `adapter.py:1881-1886`). In official/stateful Responses `_convert_input(None)` remains `[]`, so the existing `previous_response_id` chain continues with no new input items (`adapter.py:1790-1791`).

### Pre-request hook (mid-turn tc_inbox drain — dormant)

All four `send` / `send_stream` paths in this file fire `self.pre_request_hook(self._interface)` after committing the message to the canonical interface but before the API call. Historically the kernel installed `BaseAgent._drain_tc_inbox_for_hook` here so involuntary tool-call pairs (mail notifications, soul.flow voices) spliced into the wire chat mid-turn. After the `.notification/` redesign (`fadbabf`/`d2da97e`) the hook is still installed but the queue is always empty in production; ACTIVE notifications now defer to the post-turn IDLE synthetic-pair path rather than mutating tool results at send time. Phase 3 will remove the hook entirely. Three regimes (preserved for historical context and future re-use):

- **`OpenAIChatSession.send` / `send_stream`** — canonical-interface; the hook splices into the same interface that's about to be serialized via `_build_messages()`. Spliced pair appears in this same API request. Same-turn delivery.
- **`OpenAIResponsesSession.send` / `send_stream`** — official/stateful mode keeps server-state via `previous_response_id`; the hook splices into `self._interface` but the wire payload remains the new delta input. Custom/stateless mode reserializes `to_responses_input(self._interface)` after the hook, so hook-spliced pairs ride on the same request (`adapter.py:1900-1904`, `adapter.py:1952-1956`).
- **`CodexResponsesSession.send_stream`** — Codex's stateless backend replays the full canonical interface on every request (`to_responses_input(self._interface)`), so the hook delivers same-turn just like the CC path.

### Git history

16 commits. Key: context overflow recovery (`f65e395`), orphan tool_call guard (`8197fdc`), Codex stateless path (`7e88f47`, `a4bf117`), per-phase HTTP timeout caps (`81b95e2`), `cached_tokens` None coercion (`1e715ab`), `_build_messages` hook refactor (`70c0357`), pre-request hook for mid-turn tc_inbox drain (`f46b346`, now dormant), `send(None)` continue-from-wire contract (`f596ec1`).
