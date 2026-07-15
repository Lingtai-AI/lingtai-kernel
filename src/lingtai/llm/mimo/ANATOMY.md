---
related_files:
  - src/lingtai/llm/ANATOMY.md
  - src/lingtai/llm/mimo/__init__.py
  - src/lingtai/llm/mimo/adapter.py
  - src/lingtai/llm/openai/adapter.py
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/tools/daemon/DAEMON_CONTRACT.md
  - tests/test_mimo_adapter.py
  - tests/test_mimo_responses_compaction.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/llm/mimo

MiMo (Xiaomi) adapter — defaults to the native OpenAI Responses wire with
stateless full-history replay; preserves an explicit Chat Completions escape
hatch that satisfies MiMo thinking-mode's `reasoning_content` round-trip
contract (analogous to DeepSeek).

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|------|------|
| `__init__.py` | Empty |
| `adapter.py` | `MimoAdapter`, `MimoResponsesSession`, `MimoChatSession`, `MimoCompactionHardFailure`, `_fallback_reasoning_for` |

### Classes

- **`MimoAdapter(OpenAIAdapter)`** — pinned to MiMo. Defaults `wire_api="responses"` (an explicit `wire_api="chat_completions"` from provider defaults still selects the Chat Completions escape hatch — the canonical `wire_api` selector inherited from `OpenAIAdapter` already supports this; only the default changes here). Always forces `compact_threshold=None` on the Responses path (never the generic OpenAI `context_management` auto-compaction — MiMo's docs mark it explicitly incompatible) and `responses_stateless_replay=True`. Overrides `_create_responses_session` to build `MimoResponsesSession` instead of the base `OpenAIResponsesSession`, and `_default_prompt_cache_key` → `lingtai-mimo:{model}:v1`. Owns `_mimo_compact_token_limit` (the standalone-compaction threshold; see below).
- **`MimoResponsesSession(_StandaloneCompactionMixin, OpenAIResponsesSession)`** — the default native session. Stateless: every turn replays the full raw canonical interface via `to_responses_input` (never `store`/`previous_response_id`/`conversation` — all three are documented-unsupported on MiMo's Responses API), or, once standalone compaction has fired, the opaque compacted prefix plus the strict-additive delta since the boundary. Overrides `OpenAIResponsesSession._replay_input_items()` (the shared seam both `send`/`send_stream` call internally) to run `_maybe_compact_before_send()` before returning the replay, and overrides `send`/`send_stream` to capture the post-response calibration sample (`_record_calibration_sample`) needed by the mixin's projected-token trigger. See "Standalone MiMo compaction" below for the failure-policy divergence from Codex.
- **`MimoChatSession(OpenAIChatSession)`** — the explicit `wire_api="chat_completions"` escape hatch. Unchanged from the pre-existing adapter: satisfies MiMo's `reasoning_content` round-trip contract (real `ThinkingBlock`-sourced reasoning replays verbatim; assistant turns after the first tool_call that lack one get a per-turn-unique fallback via `_fallback_reasoning_for`, never a constant placeholder — a constant one previously caused MiMo to parrot it back verbatim and trip the 120s LLM hang watchdog).
- **`MimoCompactionHardFailure(RuntimeError)`** — raised by `MimoResponsesSession._compact_now` on any standalone-compaction invocation/provider/parse failure. Propagates through `send`/`send_stream` uncaught (the base `OpenAIResponsesSession` stateless-replay error path still rolls back the just-staged trailing entry via `_rollback_staged`, so a failed turn never strands it).

### Module-level

| Symbol | Purpose |
|--------|---------|
| `_fallback_reasoning_for` | Per-turn-unique reasoning stub for `MimoChatSession` assistant turns lacking real reasoning (inlines tool name + call ids or content snippet + turn index) |

## Connections

- **Inherits**: `OpenAIAdapter` / `OpenAIChatSession` / `OpenAIResponsesSession` / `_StandaloneCompactionMixin` / `_validate_codex_compact_token_limit` from `../openai/adapter.py` — see that module's ANATOMY for `_StandaloneCompactionMixin`'s shared trigger/boundary/replay machinery (extracted from `CodexResponsesSession`, PR #926, so this provider reuses it without duplicating the calibration/boundary logic).
- **Daemon wiring**: `src/lingtai/tools/daemon/__init__.py:_daemon_provider_defaults` threads a task's `context_token_limit` into `mimo_compact_token_limit` when the resolved provider is `mimo` (mirrors the pre-existing Codex `codex_compact_token_limit` branch). `src/lingtai/llm/_register.py:_mimo` reads `wire_api` and `mimo_compact_token_limit` off provider defaults and forwards them to `MimoAdapter`.
- **No additional imports**: Only the `openai` SDK (inherited), no new external deps.

## Composition

### Standalone MiMo compaction

Live wire evidence (2026-07-14): MiMo's official Responses API
(`POST https://api.xiaomimimo.com/v1/responses`, docs at
`https://mimo.mi.com/static/docs/api/chat/responses.md`) accepts a two-turn
`function_call` + exact prior output item + `function_call_output` replay,
preserving a unique tool marker end-to-end — confirming the stateless
full-history/raw-output-item replay this adapter performs by default is
provider-correct. MiMo's docs say only documented parameters are processed,
explicitly mark `previous_response_id` and `context_management` as
incompatible, support `function_call_output`, and require callers to manage
context manually (retaining prior reasoning items themselves) — so this
adapter never sends `store`, `previous_response_id`, `conversation`, or the
generic `context_management` auto-compaction field for MiMo.

MiMo's standalone `POST /v1/responses/compact` endpoint currently returns a
provider error on the live API (as of 2026-07-14). Regardless of whether that
endpoint is currently healthy, the **failure policy is intentionally
different from Codex's**:

| | Codex (`CodexResponsesSession`) | MiMo (`MimoResponsesSession`) |
|--|--|--|
| Compact call/parse fails | Non-fatal — logs `codex.compact_failed`, skips compaction for this turn, continues on full/previously-compacted history | **Hard failure** — raises `MimoCompactionHardFailure`, propagates to the caller |
| No safe boundary yet | No-op (not a failure for either provider) | No-op (not a failure for either provider) |
| Rationale | Compaction is an optimization; Codex still has other context-management levers | MiMo has no generic `context_management` fallback and no server-side state (`store`/`previous_response_id`/`conversation` all unsupported) — silently continuing on ever-growing full history past the configured threshold has no safety net |

Never logs or persists `encrypted_content`/opaque compaction summary text —
only structural item types/counts, mirroring the Codex convention.

### Wire selection

`MimoAdapter.__init__` defaults `wire_api="responses"` (native Responses
wire, `MimoResponsesSession`). A provider-defaults `wire_api="chat_completions"`
still selects `MimoChatSession` via the inherited `OpenAIAdapter._wire_api` /
`_should_use_responses()` machinery — this adapter changes only the default,
not the selector itself. `_register.py:_mimo` forwards `wire_api` from
provider defaults only when present, so an unconfigured agent gets the
Responses default while an explicit manifest override is honored verbatim.

## State

- `MimoAdapter`: inherits `_client`, `_gate`, `_wire_api`; owns `_mimo_compact_token_limit`.
- `MimoResponsesSession`: inherits `_interface`, `_response_id` (unused — stateless), `_compact_threshold` (always `None`); owns the `_StandaloneCompactionMixin` state (`_compact_token_limit`, `_compacted_items`, `_compacted_at_entry_count`, `_last_provider_input_tokens`, `_last_local_estimate_tokens`) plus `_pending_request_representation` (the exact wire items built by `_replay_input_items()` for the CURRENTLY in-flight request, captured there rather than recomputed after the call returns — recomputing post-hoc would include the assistant turn `send()`/`send_stream()` records onto `self._interface` before returning, which the actual request never carried).
- `MimoChatSession`: inherits all from `OpenAIChatSession`.

## Notes

- **Git history**: pre-existing Chat-Completions-only adapter (issue #9 DeepSeek-analogous `reasoning_content` fix); this Responses-default + standalone-compaction extension is a later addition — see `src/lingtai/tools/daemon/DAEMON_CONTRACT.md` §7 for the daemon-facing `context_token_limit` capability boundary and failure-policy contrast.
- **Not to be confused with the `mimocode` CLI backend** — an entirely separate daemon `backend=` option that drives the `mimo` CLI as an external process (see `src/lingtai/tools/daemon/manual/reference/cli-backends/SKILL.md` and `src/lingtai/tools/daemon/DAEMON_CONTRACT.md`'s Backend Support Matrix). This ANATOMY covers the native LLM provider `mimo` (`LLMService.register_adapter("mimo", ...)`), which a LingTai-backend agent/daemon task selects via `manifest.llm.provider`.
