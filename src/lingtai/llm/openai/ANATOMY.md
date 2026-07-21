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
  - src/lingtai/llm/openai/codex_quota.py
  - src/lingtai/llm/openai/codex_ws.py
  - src/lingtai/llm/openai/defaults.py
  - src/lingtai/auth/codex_pool.py
  - tests/test_codex_quota.py
  - tests/test_codex_pool_quota_exclusion.py
  - src/lingtai/llm/mimo/adapter.py
  - src/lingtai/llm/mimo/ANATOMY.md
  - src/lingtai/llm/service.py
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/CONTRACT.md
  - tests/test_codex_prompt_cache_key.py
  - tests/test_codex_native_multiaccount.py
  - tests/test_codex_standalone_compaction.py
  - tests/test_mimo_responses_compaction.py
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
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
| `codex_quota.py` | ~230 | `read_remaining_percent(auth_path)` — reads the Codex CLI's own OAuth rate-limit via `codex app-server`'s `account/rateLimits/read` stdio JSON-RPC call; returns the main window's remaining percent or `None` on any failure/malformed field. Used by `lingtai.auth.codex_pool`'s quota-aware pool exclusion. Tests: `tests/test_codex_quota.py`. |
| `defaults.py` | 12 | `DEFAULTS` dict: `api_compat="openai"`, `use_responses_api=True`, `wire_api="auto"` |

### adapter.py class map

| Class | Lines | Role |
|-------|-------|------|
| `OpenAIChatSession` | `adapter.py:1267` | Chat Completions session with context overflow auto-recovery; sends optional `prompt_cache_key` |
| `OpenAIResponsesSession` | `adapter.py:1782` | Responses API session. Official OpenAI mode is server-stateful via `previous_response_id`; custom/OpenAI-compatible mode can be internally stateless (`stateless_replay=True`) and replays full canonical history via `to_responses_input` while recording assistant turns and exposing no resume id (`adapter.py:1806-1807`, `adapter.py:1974-1975`, `adapter.py:1985-1986`, `adapter.py:2092-2103`). |
| `OpenAIAdapter` | `adapter.py:2126` | `LLMAdapter` implementation; dispatches to Completions or Responses path; receives injected `compact_threshold`; derives the default `prompt_cache_key` via `_default_prompt_cache_key` / `_resolve_prompt_cache_key`; carries the internal `_responses_stateless_replay` constructor mode into Responses sessions (`adapter.py:2184`, `adapter.py:2189`, `adapter.py:2282-2334`). |
| `_StandaloneCompactionMixin` | `adapter.py:2561` | Shared standalone `/responses/compact` machinery extracted from `CodexResponsesSession`: projected-token trigger, turn-aware boundary selection (`_prepare_compact_request`, `adapter.py:2736`), opaque compacted-prefix-plus-delta replay. Mixed into both `CodexResponsesSession` (non-fatal failure policy) and MiMo's `MimoResponsesSession` (`src/lingtai/llm/mimo/adapter.py`, hard-failure policy) — see "Standalone Codex compaction" below. |
| `CodexResponsesSession` | `adapter.py:2801` | Responses session for ChatGPT-backed Codex running the `full`/`incremental` additive continuation state machine over a selectable transport (REST default, WebSocket opt-in): every real send first asks its adapter for one fresh credential, account switches reset the wire epoch, `store=false` always, encrypted reasoning include/replay and self-heal remain native, and standalone daemon-task compaction uses `POST /responses/compact`. |
| `CodexOpenAIAdapter` | `adapter.py:4867` | The one Codex provider specialization shared by `codex`/`codex-pool` aliases. It owns fixed-or-weighted AccountSource selection, per-request OAuth token/account binding, quota/exclusion state, safe `codex_pool_selection` attribution, cache/session ids, installation id, endpoint/service-tier settings, and Codex's explicit default `reasoning.effort = "xhigh"`; no pool-specific session or retry adapter exists. |

### adapter.py helpers

| Function | Lines | Role |
|----------|-------|------|
| `_base_url_namespace()` | `adapter.py:708` | Stable namespace token for an OpenAI-compatible `base_url` (URL host, or short hash fallback) used in the default `prompt_cache_key` |
| `_codex_session_id()` | `adapter.py:136` | Derive the 8-char Codex cache-affinity id (issue #378): `sha256(f"{anchor}\0{molt_count}").hexdigest()[:8]`, lowercase hex, where `anchor` MUST be a per-agent identity (the resolved `init.json` path) and `molt_count` is the agent's current molt count. The same value is used byte-identically for `session_id`, `thread_id`, and the default `prompt_cache_key` on the root/main path. No time/epoch — stable across restarts WITHIN a molt segment, and intentionally changes at each molt boundary |
| `_codex_installation_id()` | `adapter.py:249` | Derives a UUID-shaped, non-secret LingTai installation id for Codex `client_metadata` from the same local anchor/id; never reuses `~/.codex/installation_id`. |
| `_codex_identity_headers()` | `adapter.py:262` | Builds Codex client identity headers (`originator`, `User-Agent`): default requests identify as LingTai via the shared `llm/identity_headers.py` User-Agent helper, while the official Codex CLI-shaped identity remains an explicit local diagnostic opt-in. |
| `_validate_compact_threshold()` | `adapter.py:773` | Validates/normalizes OpenAI Responses auto-compaction threshold; positive `int` or explicit `None` (disable) only |
| `_validate_codex_compact_token_limit()` | `adapter.py:789` | Validates/normalizes the Codex-only standalone-compaction context-token threshold (daemon task `context_token_limit`); positive `int` or `None` (no explicit task override — falls back to `context_window()`) only; bool rejected |
| `_estimate_responses_input_tokens()` | `adapter.py:809` | Estimates tokens for an EXACT rendered Responses `input` item list (reuses the same `count_tokens` primitive and instructions/tools overhead accounting as `ChatInterface.estimate_context_tokens`, but over wire items instead of canonical entries) — the compaction-trigger calibration correctness fix (PR #926 Sol source-audit finding); see `CodexResponsesSession._current_request_representation()` / `_projected_provider_tokens()` below |
| `_codex_responses_trace_path()` / `_codex_responses_trace_record()` | `adapter.py:856`, `adapter.py:882` | Opt-in Codex Responses stream diagnostic trace helpers; safe metadata only, default off |
| `_build_http_timeout()` | `adapter.py:953` | `httpx.Timeout` per-phase caps (connect≤30s, read≤60s, pool=10s) |
| `_build_tools()` | `adapter.py:975` | `FunctionSchema` → OpenAI CC tool format (`{type, function: {name, description, parameters}}`) |
| `_build_responses_tools()` | `adapter.py:1055` | `FunctionSchema` → Responses API flat format (`{type, name, description, parameters}`); scrubs disallowed top-level JSON-Schema combinators (`allOf`, `oneOf`, etc.) |
| `_parse_response()` | `adapter.py:1104` | ChatCompletion → `LLMResponse` (extracts reasoning from `reasoning_content` or `reasoning`) |
| `_handle_responses_reasoning_event()` | `adapter.py:1171` | Responses stream reasoning-summary event handler; accumulates `summary_text` deltas/done fallback without raw reasoning text |
| `_parse_responses_api_response()` | `adapter.py:1217` | Responses API output → `LLMResponse` (handles `message`, `function_call`, `reasoning` output items) |

## Connections

- **Base class** — `OpenAIAdapter` extends `LLMAdapter` (`from lingtai.llm.base import LLMAdapter`, `adapter.py:42`).
- **Kernel types** — imports `ChatSession`, `FunctionSchema`, `LLMResponse`, `ToolCall`, `UsageMetadata` from `lingtai.kernel.llm.base`.
- **Interface converters** — imports `to_openai` and `to_responses_input` from `lingtai.llm.interface_converters` (`adapter.py:44`).
- **Streaming** — imports `StreamingAccumulator` from `lingtai.kernel.llm.streaming` (`adapter.py:45`).
- **HTTP client** — imports `httpx` for timeout construction (`adapter.py:23`); `openai` SDK for all API calls (`adapter.py:24`).
- **Subclass hooks** — `_session_class` (`adapter.py:2134`) for Completions path; `_adapter_extra_body()` (`adapter.py:2395`) for provider-specific `extra_body`; `_default_prompt_cache_key()` (`adapter.py:2196`) for the provider-namespaced cache key.

## Composition

### Two session paths

The adapter forks at `create_chat()` (`adapter.py:2243`) via `_should_use_responses()` (`adapter.py:2224`):
1. **Responses API** (`_create_responses_session`, `adapter.py:2282`) — when canonical `wire_api="responses"`, or when `wire_api="auto"` and legacy `use_responses=True` AND (`base_url` is None OR `force_responses=True`). Builds `OpenAIResponsesSession`, threading the adapter's `_responses_stateless_replay` into its `stateless_replay` kwarg (`adapter.py:2333`) — so a custom/OpenAI-compatible Responses adapter builds a stateless-replay session while official OpenAI stays stateful.
2. **Chat Completions** (`_create_completions_session`, `adapter.py:2336`) — fallback for compatible providers and when `wire_api="chat_completions"`. Builds `self._session_class` (subclass-overridable).

Both paths return sessions wrapped via `_wrap_with_gate()` for rate limiting. Canonical `wire_api` wins over legacy `use_responses`/`force_responses`; when `wire_api` is absent/`auto`, existing behavior is preserved.

### One-shot generation (`OpenAIAdapter.generate`, `adapter.py:2404`)

`generate()` follows the same wire selection as `create_chat()` via `_should_use_responses()`: Chat Completions by default/legacy, or Responses API when `wire_api="responses"` (or legacy `use_responses=True` without a custom base URL / with `force_responses=True`). This keeps service-level one-shot calls consistent with multi-turn sessions.

### Chat Completions session flow (`OpenAIChatSession.send`, `adapter.py:1424`)

1. Record user input into `ChatInterface` (str → `add_user_message`; list → `add_tool_results`)
2. `_build_kwargs()`: enforce tool pairing → serialize via `_build_messages()` → `_pair_orphan_tool_calls()` wire guard
3. `_run_with_overflow_recovery(_do_call)` — retries with context trimming on 400 context-length errors
4. On success: record assistant response into interface via `_record_assistant_response()`

### Responses API session flow (`OpenAIResponsesSession.send`, `adapter.py:1945`)

Two modes share one session class:
1. **Official/stateful** — `_convert_input(message)` builds only the new Responses input items (`adapter.py:1829-1884`), `previous_response_id` is sent when available (`adapter.py:1974-1975`, `adapter.py:2027-2028`), and the returned id becomes the next resume id (`adapter.py:1988`, `adapter.py:2085`). `_convert_input` maps a canonical `ToolResultBlock` continuation to the `function_call_output` wire item (`adapter.py:1835-1845`) using the same shape as `to_responses_input`, so a plain (non-Codex) Responses session serializes tool-result turns correctly instead of forwarding the dataclass unconverted; prebuilt Responses-wire `function_call_output` dicts pass through unchanged, while legacy `role=tool` dicts are converted to the same Responses shape (`adapter.py:1856-1868`).
2. **Custom/stateless** — `_snapshot_interface` captures the pre-stage snapshot before `_stage_input`, `_request_input` records string/tool-result input (or leaves `send(None)` pre-staged entries alone), enforces tool pairing, and serializes full canonical history through `to_responses_input`; no `previous_response_id` is sent (`adapter.py:1886-1889`, `adapter.py:1891-1900`, `adapter.py:1974-1975`, `adapter.py:1938-1943`). On success `_record_assistant_response` persists reasoning/text/tool calls plus usage, including cached tokens, into canonical history (`adapter.py:1915-1936`, `adapter.py:1985-1986`, `adapter.py:2082-2083`). On transport/stream/enforce/serialization/parse/finalize/callback/record failure, `_rollback_staged` restores the pre-send canonical snapshot in place while preserving the recovery lookup (`adapter.py:1902-1913`, `adapter.py:1990-1992`, `adapter.py:2087-2089`).

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

Before staging input, `send_stream` calls `_codex_refresh_account_for_request()` (`adapter.py:4232-4265`). Host-wired sessions therefore consume exactly one fresh AccountSource draw per real send; direct/test sessions with no resolver preserve their existing credential. A changed account SHA resets the WebSocket epoch before rebinding, closing authenticated transport plus response/compaction continuation state so no wire state crosses accounts.

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
   not be verified`, treat it as stale raw reasoning state: `_strip_codex_encrypted_reasoning_items`
   (`adapter.py:4092`) removes replay-only `encrypted_content` anchors from
   `ThinkingBlock.provider_data`, `_reset_ws_epoch` (`adapter.py:3258`) clears
   stale baselines/response-id state, and the adapter retries the same visible
   transcript once as `rest_full_self_heal` / `stateless_full_self_heal`
   (`adapter.py:4446-4490`). Summary text, assistant text, and tool calls remain
   in the canonical interface; only the opaque provider blob is dropped.
6. After success: record the assistant response into the interface and recompute
   the converter-stable delta baseline (`_ws_record_baseline_from_interface`) so the
   next turn can strict-prefix-match and stay incremental; the adapter clears the current exclusion chain without preselecting the next credential (`adapter.py:4859-4863`).

Only the final exception escaping all built-in fallback/self-heal paths reaches the adapter account-error callback (`adapter.py:4736-4745`). A structural `usage_limit_reached` excludes that safe account identity for the existing AED rebuild; if any text delta was already delivered, the exception is marked `_lingtai_partial_stream` so BaseAgent terminates rather than replaying visible output (`adapter.py:4670-4674`, `adapter.py:5139-5148`).

**Usage metadata axes:** the session's `codex_pool_selection` records only safe selection fields; `_usage_extra` maps them to the stable `codex_pool_source_ref` / source-index / pool-size / weight / quota / model-scope / failover / fallback fields plus `codex_auth_path_sha8` (`adapter.py:3135-3145`) — never tokens or raw absolute auth paths. `UsageMetadata.extra` also carries `codex_transport`
(`rest`/`websocket`), `codex_transfer_mode` (`full`/`incremental`), and the
transport-qualified `codex_request_mode` (`rest_full` / `rest_incremental` /
`ws_full` / `ws_incremental` / `rest_full_fallback` / `rest_full_self_heal` / `stateless_full_self_heal`), plus the safe delta-decision
diagnostic (`codex_ws_delta_reason` and counts — never prompt/secret content). The
WS-named `codex_ws_*` diagnostic keys are reused on REST (transport-neutral
metadata); `codex_request_mode` never reads `ws_*` on a REST request. When
standalone compaction is active, `codex_compacted` (`"true"`) and
`codex_compacted_delta_entries` (the additive entry count since compaction)
are also recorded — safe structural metadata only, never opaque content
(`adapter.py:2955-2959`, `_usage_extra`).

### Standalone Codex compaction (`context_token_limit`)

**Shared with MiMo since the mixin extraction.** The trigger/boundary/replay
machinery described in this section (`_effective_compact_token_limit`,
`_current_request_representation`, `_projected_provider_tokens`,
`_maybe_compact_before_send`, `_prepare_compact_request`,
`_compacted_replay_input`) now lives on `_StandaloneCompactionMixin`
(`adapter.py:2552`), which `CodexResponsesSession` mixes in unchanged (this
section still describes CodexResponsesSession's behavior byte-for-byte —
extraction was a pure refactor, not a behavior change; see
`tests/test_codex_standalone_compaction.py`, all passing unmodified). MiMo's
`MimoResponsesSession` (`src/lingtai/llm/mimo/adapter.py`) mixes in the SAME
class to reuse the calibration/boundary logic for its own native Responses
wire, differing only in wire-shaping (`_compaction_prefix_input` — MiMo has
no per-session tool-output freeze map, since that exists only for Codex's
WebSocket incremental delta path) and, critically, in **failure policy**:
Codex's `_compact_now` treats a compact failure as non-fatal (this section);
MiMo's treats the same failure class as a HARD failure that propagates to
the caller (see `src/lingtai/llm/mimo/ANATOMY.md`). Each provider still owns
its own `_compact_now()` — the mixin only prepares the request
(`_prepare_compact_request`), it never calls `client.responses.compact`
itself.

A separate axis from the hard-boundary forced-rebuild machinery above:
forced rebuild discards local pressure by re-sending the FULL canonical
history with no server help; standalone compaction asks Codex itself
(`POST /responses/compact`, the SDK's `client.responses.compact(...)`) to
fold prior context into an opaque `compaction_summary` + trailing `message`
pair, which then replays as the new provider-context prefix with only
strict-additive entries appended on top. This is the daemon task
`context_token_limit` feature (`src/lingtai/tools/daemon/__init__.py`
`context_token_limit` → `_daemon_provider_defaults` →
`codex_compact_token_limit` → `CodexOpenAIAdapter` →
`CodexResponsesSession(compact_token_limit=...)`).

Never `context_management`: Codex's backend rejects that field entirely (see
`_create_responses_session`, `adapter.py:5019-5095`, which always forces
`compact_threshold=None`/`context_management` unset for Codex). Standalone
compaction is a wholly separate request (`responses.compact`, not
`responses.create`) with its own SDK method signature — notably **no
`store` parameter at all** (unlike `responses.create`, which needs
`store=false` because Codex rejects `store=true`); passing `store=` to
`compact()` raises `TypeError` and silently disables compaction forever
behind a broad exception guard, which is why `_compact_now` (`adapter.py:3555`)
omits it and a dedicated test binds the sent kwargs against the real SDK
signature (`tests/test_codex_standalone_compaction.py::
test_compact_request_kwargs_bind_against_real_sdk_signature`).

Trigger and lifecycle (`adapter.py:3389-3686`):
- `_effective_compact_token_limit()` — an explicit per-task threshold wins;
  omitted, falls back to the session's resolved `context_window()` (the
  parent service's context window, threaded through unchanged from
  `LLMService.create_session(context_window=...)`); no window configured
  disables compaction (`None`). This value is always the public, unmodified
  `context_token_limit` (or its context-window fallback) — an upper bound the
  provider-visible input should stay under. It is never silently shrunk by a
  fixed fraction; the margin instead comes from projecting ahead (below).
- `_projected_provider_tokens()` — projects the CURRENT provider-visible
  token count for the request that would be sent NEXT, calibrated against
  the last real provider round-trip: `_last_provider_input_tokens` (the
  provider-reported actual) and `_last_local_estimate_tokens` (a local
  estimate of the SAME rendered representation that request actually sent,
  captured together right after a successful response — before that turn's
  assistant entry is recorded) form one paired calibration sample;
  `calibration = provider_actual / local_sample`, and a fresh estimate of the
  CURRENT would-be request representation is scaled by that ratio. Falls
  back to the raw current-representation estimate (implicit 1:1 calibration)
  when no paired sample exists yet (first turn(s) of a session) — a safe,
  no-magic-margin default.

  **Correctness invariant (PR #926 Sol source-audit finding, fixed in the
  same PR as HIGH-1/HIGH-2/LOW-2 above):** both sides of the calibration
  ratio, and the value being projected, MUST be the same kind of thing — a
  local estimate of the actual RENDERED REQUEST REPRESENTATION — never
  `ChatInterface.estimate_context_tokens()` over the full raw canonical
  interface. `_current_request_representation()` builds that representation
  (`_compacted_replay_input()`'s opaque-prefix-plus-delta shape when
  compaction is active, `_frozen_responses_input()` otherwise, plus any
  `prebuilt_items` a caller passed to `send`/`send_stream`), and
  `_estimate_responses_input_tokens(instructions, tools, input_items)`
  (module-level helper, reuses the same `count_tokens` primitive
  `ChatInterface.estimate_context_tokens` uses, applied to a rendered wire
  item list instead of canonical entries) estimates it. The calibration
  SAMPLE captured in `send_stream` (right after `_last_provider_input_tokens`
  is set) uses `full_replay_input_items` — the literal list that was placed
  in that successful request's `kwargs["input"]` (kept in sync across the
  encrypted-reasoning self-heal retry and incremental-fallback paths, which
  reassign it to whatever was actually resent).

  Before compaction, the raw canonical estimate and the rendered
  representation are the same, so this distinction is invisible. After
  compaction they diverge: the real request shrinks to
  `[opaque items + live suffix]` while the raw canonical estimate keeps
  growing forever (compaction never deletes canonical entries — it only
  changes what gets SENT). Calibrating the sample against the raw estimate
  divides by an artificially large, ever-growing denominator, producing a
  calibration ratio that silently under-projects a subsequent large live
  delta and can let a request cross `context_token_limit` with no preceding
  re-arm — the exact silent-under-projection bug this invariant closes. See
  `tests/test_codex_standalone_compaction.py::
  test_calibration_sample_reflects_compacted_representation_not_raw_canonical`
  and `::test_large_live_delta_after_compaction_triggers_rearm_before_crossing_limit`
  (the latter drives a `DynamicResponses` fake whose reported
  `usage.input_tokens` is itself derived from the actual rendered request via
  the same estimator, so "provider actual" and "local estimate" are
  internally consistent by construction — ruling out fixture inconsistency
  as an alternative explanation for the observed re-arm behavior).
- `_maybe_compact_before_send()` — called once per turn, before building the
  request. Triggers when the PROJECTED count reaches the resolved limit
  (`>=`), giving the omitted-default path (which resolves to the full
  `context_window()`) real headroom to act before a request is already at/over
  the window — fixing the design gap where a reactive-only, post-hoc check
  left the documented default path with no margin to actually help (PR #926
  review HIGH-2). Re-arms rather than blocking outright once already active:
  see the re-arm bullet below.
- `_compact_now()` — determines the split point via
  `ChatInterface.find_compaction_boundary(keep_turns=1)` (the same turn-aware
  boundary tested in `tests/test_compaction.py`, called here with an explicit
  `keep_turns=1` rather than the library's generic default of 3). The
  invariant this endpoint actually needs is: **compact everything that is
  safely old, and keep exactly the one newest complete live turn** —
  including any `function_call`/`function_call_output` pair it carries —
  uncompacted. This guarantees the live turn that TRIGGERED this send (a
  plain user message, or a tool-result continuation whose matching
  `function_call` already sits in history) always survives as a verbatim,
  strict-additive suffix, never folded into the opaque summary (fixing PR
  #926 review HIGH-1, where the live turn was compacted away with no live
  item left in the outgoing request), while everything older than that one
  turn is eligible for folding rather than needlessly kept live (PR #926 Sol
  source-audit follow-up #2: `keep_turns=3` retained two extra small turns
  with no reason to stay live, and in a legitimately tight scenario — a large
  new live delta arriving right after a first compaction — those two
  needless turns plus the opaque prefix pushed the final re-armed request a
  few tokens past `context_token_limit` even though re-arm correctly fired;
  `keep_turns=1` folds them too, closing that overage in every case where a
  tighter boundary can help). **Honest limit on what boundary choice can fix:**
  this narrows the request as much as this endpoint's design allows, but it
  does not claim an absolute hard cap under `context_token_limit` — if the one
  irreducible live turn itself, plus the (already-minimal) opaque
  `compaction_summary` output, is large enough on its own to reach or exceed
  the limit, no boundary choice can fold it away without folding the live
  turn itself, which would silently discard the very instruction that
  triggered this send (an explicitly forbidden fallback — the triggering live
  input is never folded, and the public limit is never silently shrunk to
  paper over this case). When there isn't yet enough history for a safe
  boundary, or the boundary hasn't moved since the last compaction,
  compaction is skipped for this turn rather than guessing. Calls
  `client.responses.compact(model=, input=, instructions=,
  prompt_cache_key=, extra_headers=)` with only the OLDER portion (before the
  boundary) as `input`. On success, normalizes the returned `output` items
  (`message` + `compaction_summary`) into plain dicts and stores them as
  `_compacted_items`, with `_compacted_at_entry_count` recording the boundary
  index. On ANY failure (network, malformed output), skips compaction for
  this turn without raising — compaction is an optimization, not a
  correctness requirement.
- **Re-arm (PR #926 review LOW-2).** Once `_compacted_items` is active,
  `_maybe_compact_before_send` no longer blocks outright — it re-checks
  `find_compaction_boundary()` against the FULL current entry list. If that
  boundary has moved strictly past `_compacted_at_entry_count` (the
  post-compaction delta has grown enough to safely re-split without breaking
  a turn or a tool pair), `_compact_now` re-compacts: the compact request's
  `input` is the existing opaque `_compacted_items` verbatim, followed by the
  delta entries between the OLD and NEW boundary (OpenAI's compaction
  endpoint documents accepting prior compaction items as input — this is the
  first-party chained-compaction pattern, not a special case). This keeps the
  small task limit re-enforced across the whole session rather than only
  backstopped by the much higher ~1.0 hard forced-rebuild boundary. If the
  boundary has NOT moved, this is a no-op — no compact-every-turn loop.
- `_compacted_replay_input()` — while active, returns `_compacted_items`
  verbatim followed by the strict-additive delta (canonical interface
  entries at/after `_compacted_at_entry_count`, converted via
  `_interface_entries_to_responses_input` — the same machinery the
  WebSocket incremental delta reuses). Because the boundary always keeps at
  least one complete turn, this delta is never empty once compaction is
  active. `send_stream` substitutes this for the ordinary
  `_frozen_responses_input(self._interface)` full replay when active, and
  routes that turn through REST (`ws_enabled_this_turn = False`) since
  compaction and the WS delta/`previous_response_id` machinery both assume
  the full converted interface as their comparison baseline.
- Invalidation: `_reset_ws_epoch` (every reason — `turn_count`,
  `summarize_delayed`, `summarize_rebuild_only`,
  `encrypted_reasoning_self_heal`) clears `_compacted_items`/
  `_compacted_at_entry_count`, since any local-history rewrite or remote
  epoch rebase makes a previously compacted prefix untrustworthy as a
  replay basis. The encrypted-reasoning self-heal retry path additionally
  invalidates explicitly when its rejected request carried a compacted
  replay, since that retry falls back to full local history and must not
  let the next turn silently diverge by replaying a now-stale compacted
  base. Compaction re-triggers fresh, once the threshold is reached again,
  after any invalidation.

Daemon-only wiring: `codex_compact_token_limit` reaches the adapter only
through `_daemon_provider_defaults`'s Codex bucket
(`src/lingtai/tools/daemon/__init__.py`) — the SAME task-level
`context_token_limit` also reaches the native `mimo` provider as
`mimo_compact_token_limit` through that same function's `mimo` branch (see
`src/lingtai/llm/mimo/ANATOMY.md`). Every other provider and every external
CLI backend (`claude-p`, `opencode`, the `codex` CLI backend, the `mimocode`
CLI backend, …) never sees this field and is behaviorally unchanged.

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

The default request identity is explicit LingTai: `_CODEX_ORIGINATOR = "lingtai"` and `User-Agent: LingTai/<version>`, with the version token resolved through the shared LLM HTTP identity helper (`../identity_headers.py:20`). `_CODEX_IMPERSONATE_OFFICIAL_CLI` is reserved for explicit local protocol comparisons and switches both `originator` and `User-Agent` to the official Codex CLI-shaped values only when enabled; account-selection headers remain independent of this identity choice. `adapter.py:199`, `adapter.py:244`, `adapter.py:190`, `adapter.py:241`, `tests/test_codex_prompt_cache_key.py:113`, `tests/test_codex_prompt_cache_key.py:590`

### Codex `ChatGPT-Account-ID` header (the user's own account id)

**Codex-only.** When the user's OAuth auth data supplies an account id (`CodexTokenManager.get_account_id()`, see `../../auth/ANATOMY.md`), `CodexResponsesSession` sends it as the canonical `ChatGPT-Account-ID` header so the request is attributed to the right ChatGPT account. It does NOT change the honest `originator`/`User-Agent` identity above — no Codex-CLI impersonation. The value flows `_register.py` (`codex_account_id=mgr.get_account_id()`) → `CodexOpenAIAdapter.codex_account_id` (mutable; the OAuth-refresh monkey-patch re-reads it via `get_account_id()` so a refresh that changes the id stays current on later-built sessions) → `CodexResponsesSession(account_id=...)` (`self._account_id`). Emitted only when non-empty; otherwise the header is omitted entirely. The raw account id is **NOT** copied into `UsageMetadata.extra`, `token_ledger.jsonl`, or `events.jsonl`; only `codex_account_id_sha8` (SHA-256/8) is recorded for diagnostics.

### Codex honest metadata envelope

See `docs/references/codex-http-anatomy-investigation.md` for the capture history, Codex CLI comparison, and the safety rationale behind which metadata LingTai does and does not send.

When a Codex session has a stable LingTai session/thread identity, `CodexResponsesSession` adds an honest metadata envelope alongside the cache-affinity headers (`adapter.py:2795` and the Codex send path at `adapter.py:4318`). Each request gets a fresh `x-client-request-id`; `x-codex-window-id` is the LingTai window id `<session_id>:0`; `x-codex-turn-metadata` is compact JSON carrying `session_id`, `thread_id`, a generated `turn_id`, a truthful LingTai `sandbox` label, and `turn_started_at_unix_ms`; and body `client_metadata.x-codex-installation-id` is carried through `extra_body` because the OpenAI Python SDK has no typed `client_metadata` argument. This is compatibility metadata, not CLI impersonation: LingTai keeps `originator: lingtai` / `User-Agent: LingTai/<version>`, does not send `x-codex-beta-features`, and derives its installation id from LingTai state rather than from the official Codex CLI installation file.

## State

- `CodexResponsesSession._installation_id` / `_metadata_sandbox`: optional honest Codex metadata state used to build `client_metadata.x-codex-installation-id` and turn metadata without leaking local paths or claiming official CLI features.

- **`OpenAIChatSession._interface`** — canonical `ChatInterface`, single source of truth. Mutated in-place: `add_user_message`, `add_tool_results`, `add_assistant_message`, `drop_trailing`.
- **`OpenAIChatSession._request_timeout`** — per-request HTTP timeout set by caller before dispatch (`adapter.py:1305`). Prevents race between watchdog and SDK.
- **`OpenAIResponsesSession._response_id` / `_stateless_replay`** — in official/stateful mode `_response_id` is the server-side chain pointer and `session_resume_id`; in custom/stateless mode `_stateless_replay=True`, `_response_id` is not advanced, `session_resume_id` returns `None`, and `get_history()` returns full canonical `ChatInterface` history for durable restart (`adapter.py:1806-1807`, `adapter.py:2092-2103`).
- **`CodexResponsesSession._response_id`** — transient debug aid only; never threaded into next request (`adapter.py:2549`).
- **`CodexResponsesSession._current_id`** — the single per-agent affinity id (the hash of the agent path + current molt count) handed to this session, used byte-identically for `_prompt_cache_key` / `_session_id` / `_thread_id`. Set once per session at construction — a NEW session is built for each `create_chat`, and the adapter resolves the molt-current id at that point, so a molt-advanced id reaches the next session without any in-session mutation (no rotation, no epoch, no clock).
- **Codex Responses trace** — opt-in diagnostics write JSONL metadata to `logs/codex_responses_trace.jsonl` when `LINGTAI_CODEX_RESPONSES_TRACE=1` (override path with `LINGTAI_CODEX_RESPONSES_TRACE_PATH`). Default off; stores event/item shapes, lengths/hashes, usage, and accumulator counts, not raw content.
- **`OpenAIAdapter._client`** — shared `openai.OpenAI` instance. `_client_kwargs` stored for session `reset()`. Constructor passes `default_headers=merge_lingtai_identity_headers(...)` (`adapter.py:2189`), so OpenAI-compatible HTTP requests carry non-secret LingTai identity/version headers unless a caller/provider header overrides them case-insensitively.
- **`OpenAIAdapter._session_class`** — class var, subclasses override (e.g. DeepSeek and MiMo inject `reasoning_content` round-trip fallbacks).
- **`CodexResponsesSession` delayed-summarize / hard-boundary forced rebuild** — `_last_provider_input_tokens` holds the previous real provider request's reported input tokens; `_summarize_delay_context()` divides it by `context_window()` for provider-input-based usage (the same ruler the reconstruction event uses). At usage `>= 1.0` the runtime forces a fresh full replay (`_reset_ws_epoch("summarize_delayed")`) **exactly once per continuous `>= 1.0` episode**: `_hb_rebuild_fired` latches the one-shot and `_hb_rebuild_awaiting_verify` stays set until the first post-rebuild provider response is observed. Both automatic entry points — the pre-request boundary check `_maybe_force_rebuild_at_boundary()` and the immediate `on_history_summarized()` release — go through the shared `_fire_boundary_forced_rebuild()`, so they cannot double-fire. `_observe_provider_usage_for_boundary()` (run after each successful send) re-arms the latch when usage drops strictly below `1.0` and clears the pending-verify flag on the first post-rebuild response (a failed forced request records no usage, so verification stays pending). `context_overflow_status()` returns `{"usage": …}` only when the rebuild fired, verification completed, and current usage is strictly `> 1.0` — the seam `meta_block.build_context_overflow_warning` reads (through the gate proxy's `__getattr__`) to keep the fixed `100% context Forced Rebuild Failed to Bring Usage Below 100%. … (xxx %) Molt IMMEDIATELY!!` line on every `agent_meta.agent_state.context.molt`. Explicit `request_history_rebuild()` is independent and never touches these flags. Transient runtime state — a fresh/restored session starts un-fired.

## Notes

### Provider-specific shape conversions

| Canonical block | Chat Completions wire | Responses API wire |
|----------------|----------------------|-------------------|
| `ToolCallBlock` | `{type: "function", id, function: {name, arguments: <json-str>}}` on assistant message `tool_calls` array | `{type: "function_call", call_id, name, arguments: <json-str>}` as top-level output item |
| `ToolResultBlock` | `{role: "tool", tool_call_id, content}` as separate message | `{type: "function_call_output", call_id, output}` as top-level input item |
| `TextBlock` | `content` string on assistant message | `{type: "output_text", text}` inside message content |
| `ThinkingBlock` | Emitted as `reasoning_content` on assistant message (DeepSeek and MiMo thinking-mode round-trip; other CC providers ignore the field). Captured back from `message.reasoning_content` / `message.reasoning` into a ThinkingBlock by `_record_assistant_response` (non-streaming) and the streaming finalize path. | Replayed as a top-level `{type: "reasoning", summary: [{type: "summary_text", text: ...}]}` item before assistant text/calls by `to_responses_input` (`../interface_converters.py:233-258`) so stateless Codex can retain summarized reasoning context. Responses streaming captures `response.reasoning_summary_text.*` into thoughts and Codex persists those thoughts as ThinkingBlocks before tool calls. |

### Context overflow auto-recovery

`OpenAIChatSession._run_with_overflow_recovery()` is inherited from `ChatSession` (`lingtai/kernel/llm/base.py:384`) and wraps any API call in a retry loop:
- Detects 400 `context_length_exceeded` via `_is_context_overflow_error()` (`adapter.py:1324`) — checks both canonical OpenAI code and loose string heuristics for compatible vendors.
- `_trim_context_one_round()` (`lingtai/kernel/llm/base.py:303`) drops ~10% of non-system entries from the FRONT of the interface. Snaps cut point to never split `assistant[ToolCallBlock]` from `user[ToolResultBlock]`.
- Max 10 rounds (`lingtai/kernel/llm/base.py:291`). On successful recovery, injects a `[kernel]` molt notice via `_inject_overflow_notice()` (`lingtai/kernel/llm/base.py:363`).

### Wire-layer orphan guard

`_pair_orphan_tool_calls()` (`adapter.py:1362`) scans the serialized message list for `assistant.tool_calls` without matching `role=tool` messages. Synthesizes placeholder tool results with `[synthesized placeholder — real result was not in context at send time]`. Logs warnings for investigation. Does NOT mutate canonical interface.

The Codex / Responses path has the same invariant: `to_responses_input` ends with `_pair_responses_orphan_function_calls` (`../interface_converters.py:190-250`) which synthesizes a `function_call_output` for any `function_call` without a matching output anywhere in the items list. Same placeholder string, same non-mutating semantics. Without this guard the provider returns `400 No tool output found for function call …` when a continuation request is built from a half-committed tool loop (issue #170).

**Placeholders go at the TAIL, not interleaved.** The guard appends its synthesized placeholders as one contiguous block at the end of the items list, in `function_call` order — it does **not** insert each placeholder immediately after its call. This is a continuation-stability fix: `to_responses_input` always emits an assistant entry's `function_call`s contiguously and all real `function_call_output`s afterwards, so interleaving a placeholder right after each call made placeholder positions drift relative to where the real outputs land. A multi-call turn resolving incrementally then broke the Codex strict-prefix continuation and forced a `*_full` request every turn — the logged `prefix_mismatch` with `mismatch_prev_type=function_call_output` vs `mismatch_cur_type=function_call`. The baseline recorder reinforces this: `_ws_record_baseline_from_interface` strips **all** synthesized placeholders (not just trailing ones) from the recorded baseline via `_strip_synthesized_orphan_outputs` (`adapter.py:540`, `adapter.py:3220`), so the real, position-stable items are the only load-bearing prefix and the real tool result strictly extends the baseline. Fix applies to BOTH transports (shared converter + shared baseline recorder).

### System prompt is `instructions`, frozen per session (Responses/Codex)

On the Responses/Codex path the system prompt is **not** an `input` item — it rides in the top-level `instructions` kwarg. `to_responses_input` deliberately skips system entries (`../interface_converters.py:280-281`, documented `../interface_converters.py:256-257`); the prompt is carried separately as `instructions`.

That `instructions` value is **frozen at session construction** for official/stateful Responses and Codex: `OpenAIResponsesSession.__init__` captures `self._instructions = instructions` (`adapter.py:1802`) and every send replays that value (`adapter.py:1968-1969`, `adapter.py:2021-2022`; Codex sends its own instructions in its subclass path). There is no re-read from the interface in stateful mode.

In-flight official/stateful Responses and Codex sessions keep no-op prompt/tool update behavior for continuation stability. Custom/stateless `OpenAIResponsesSession` is different: because every request is a full canonical replay, `update_system_prompt` updates both `_instructions` and the interface, and `update_tools` rebuilds the Responses tool payload and appends a system/tool snapshot (`adapter.py:2105-2118`).

**Behavior-code contract:** for stateful official Responses/Codex, a pad / system-prompt edit mid-session changes nothing on the wire and does not break warm continuation; a changed system prompt takes effect when a new session is constructed. For custom/stateless Responses, prompt/tool edits are part of the next full replay and do not require a server-side continuation reset.

### Streaming

- **CC streaming** (`adapter.py:1612`) — `stream=True, stream_options={include_usage: True}`. Uses `StreamingAccumulator` for text + tool deltas. Reasoning deltas captured from `delta.reasoning` or `delta.reasoning_content`. Overflow recovery wraps stream open + first chunk in the Chat Completions send-stream path.
- **Responses streaming** (`adapter.py:1995`) — event types: `response.reasoning_summary_text.delta/done` (summary thoughts only), `response.output_text.delta`, `response.function_call_arguments.delta`, `response.output_item.added/done`, `response.completed`. Custom/stateless mode snapshots before staging, replays full canonical history, records the finalized assistant turn, and restores the pre-send snapshot on enforce, serialization, stream-open, iteration, callback, finalize, or record failure (`adapter.py:2002-2089`).
- **Codex streaming** — forces `stream=True` even on `send()`. Runs the `full`/`incremental` planner per request over the selected transport (REST default / WebSocket opt-in): REST carries the whole converted interface in both modes; WebSocket carries the whole interface for `full` and delta + `previous_response_id` for `incremental`. Captured summary thoughts and raw encrypted reasoning items are persisted as ThinkingBlocks so `to_responses_input` replays reasoning items before function calls; if Codex later rejects a raw encrypted item as unverifiable, the adapter strips only that opaque replay state and retries once with summary/plain transcript. Optional diagnostics (`LINGTAI_CODEX_RESPONSES_TRACE=1`) append safe per-event metadata to `logs/codex_responses_trace.jsonl` without changing accumulator/persistence behavior.

### Authentication paths

- **Standard** — `api_key` passed to `openai.OpenAI(api_key=...)` at construction (`adapter.py:2185-2191`).
- **Codex OAuth** — `CodexOpenAIAdapter` built by `../_register.py:54` with `CodexTokenManager.get_access_token()`. Token refreshed by monkey-patching `create_chat` and `generate` to update `adapter._client.api_key` in-place before each call.

### Tool schema conversion

- **CC path** — `_build_tools()` (`adapter.py:975`): `{type: "function", function: {name, description, parameters}}`.
- **Responses path** — `_build_responses_tools()` (`adapter.py:1055`): `{type: "function", name, description, parameters}` (flat). Scrubs top-level JSON-Schema combinators (`_RESPONSES_DISALLOWED_TOP_LEVEL`, `adapter.py:999`) that the Responses API rejects.

### Reasoning extraction

- **CC non-streaming** (`_parse_response`, `adapter.py:1104`) — checks `message.reasoning_content` (OpenAI native) then `message.reasoning` (OpenRouter).
- **CC streaming** — `delta.reasoning` or `delta.reasoning_content` accumulated via `acc.add_thought()` in `OpenAIChatSession.send_stream` (`adapter.py:1612`).
- **Responses non-streaming** — `reasoning` output items with `summary_text` blocks (lines 256-259).
- **Responses streaming** — `response.reasoning_summary_text.delta/done` and reasoning output-item summaries are captured as summary thoughts; raw `response.reasoning_text.*` is intentionally not persisted by default.

### Subclass hooks

- `_session_class` (`adapter.py:2134`) — override to inject provider-specific session behavior on the CC path.
- `_adapter_extra_body()` (`adapter.py:2395`) — override to add `extra_body` JSON fields (e.g. OpenRouter `reasoning: {include: true}`).
- `_default_prompt_cache_key(model)` (`adapter.py:2196`) — override to give a provider a clean cache namespace (DeepSeek/Zhipu/MiMo/Codex do).

### `send(None)` contract — continue from wire

All four `send` / `send_stream` paths in this file accept `None` as the "the caller has already staged the canonical interface; just talk to the LLM" signal. This is what `base_agent/turn.py:_handle_tc_wake` calls when `_sync_notifications` has spliced a synthesized `(ToolCallBlock, ToolResultBlock)` pair into the wire — from the LLM's viewpoint the agent appears to have voluntarily called `notification(action="check")` and is now responding to the result, no fake user message and no meta prefix.

Implementation: the input-dispatch ladder at the top of each canonical-replay method treats `None` as "no adapter-owned input to append." In custom/stateless Responses `_snapshot_interface(None)` returns no rollback snapshot and `_request_input` serializes the already-staged interface through `to_responses_input`, so notification-style `(ToolCallBlock, ToolResultBlock)` pairs ride on the same request and are not removed on failure (`adapter.py:1886-1889`, `adapter.py:1938-1943`). In official/stateful Responses `_convert_input(None)` remains `[]`, so the existing `previous_response_id` chain continues with no new input items (`adapter.py:1847-1848`).

### Pre-request hook (mid-turn tc_inbox drain — dormant)

All four `send` / `send_stream` paths in this file fire `self.pre_request_hook(self._interface)` after committing the message to the canonical interface but before the API call. Historically the kernel installed `BaseAgent._drain_tc_inbox_for_hook` here so involuntary tool-call pairs (mail notifications, soul.flow voices) spliced into the wire chat mid-turn. After the `.notification/` redesign (`fadbabf`/`d2da97e`) the hook is still installed but the queue is always empty in production; ACTIVE notifications now defer to the post-turn IDLE synthetic-pair path rather than mutating tool results at send time. Phase 3 will remove the hook entirely. Three regimes (preserved for historical context and future re-use):

- **`OpenAIChatSession.send` / `send_stream`** — canonical-interface; the hook splices into the same interface that's about to be serialized via `_build_messages()`. Spliced pair appears in this same API request. Same-turn delivery.
- **`OpenAIResponsesSession.send` / `send_stream`** — official/stateful mode keeps server-state via `previous_response_id`; the hook splices into `self._interface` but the wire payload remains the new delta input. Custom/stateless mode reserializes `to_responses_input(self._interface)` after the hook, so hook-spliced pairs ride on the same request (`adapter.py:1957-1961`, `adapter.py:2009-2013`).
- **`CodexResponsesSession.send_stream`** — Codex's stateless backend replays the full canonical interface on every request (`to_responses_input(self._interface)`), so the hook delivers same-turn just like the CC path.

### Git history

16 commits. Key: context overflow recovery (`f65e395`), orphan tool_call guard (`8197fdc`), Codex stateless path (`7e88f47`, `a4bf117`), per-phase HTTP timeout caps (`81b95e2`), `cached_tokens` None coercion (`1e715ab`), `_build_messages` hook refactor (`70c0357`), pre-request hook for mid-turn tc_inbox drain (`f46b346`, now dormant), `send(None)` continue-from-wire contract (`f596ec1`).
