---
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/BEHAVIORS.md
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - src/lingtai/kernel/session_stats/ANATOMY.md
  - src/lingtai/kernel/session_stats/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/service.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/tools/task_card/ANATOMY.md
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_toggle.py
  - tests/test_telegram_task_card_event_tail.py
  - tests/test_task_card_event_projection_shared.py
  - tests/test_telegram_task_card_rows.py
  - tests/test_telegram_task_card_display_expression.py
  - src/lingtai/mcp_servers/telegram/task_card/__init__.py
  - src/lingtai/mcp_servers/telegram/task_card/_family.py
  - src/lingtai/mcp_servers/telegram/task_card/controller.py
  - src/lingtai/mcp_servers/telegram/task_card/interface.py
  - src/lingtai/mcp_servers/telegram/task_card/api_cost.py
  - tests/test_telegram_task_card_api_cost.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this Anatomy reciprocal with its paired CONTRACT.md and packaged manual.
  Update it when resident ownership, programmable projection, or the relation to
  the intrinsic producer changes.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# Telegram Task Card Projection Anatomy

This package owns Telegram's provider adapter and programmable projection. The
route/slot/delivery state machine is shared under `mcp_servers/task_card/`; the
local `resident.py` remains a compatibility re-export. The public model-facing
`task_card` capability has moved to
[`src/lingtai/tools/task_card/`](../../../tools/task_card/ANATOMY.md), which
produces the agent-local artifact. Telegram reads that artifact and projects it
onto its one tracked resident Task Card target per account+chat.

## Components

- `resident.py` — compatibility re-export of the shared `TaskCardResident`,
  `TaskCardResidentTransport`, and `TaskCardRoute` symbols.
- `../../task_card/resident.py` — provider-neutral route, dual-slot composition,
  route locks, commit-after-success, edit/rotation/delete/send/persist state
  machine, and explicit partial/indeterminate outcomes.
- `manager.py` — the Telegram adapter that tails `events.jsonl`, reads only a bounded
  recent tail of the existing main `token_ledger.jsonl` when a generated-summary
  event needs token correlation, and consumes the kernel-validated
  `agent_record.async_work` snapshot for daemon+Shell presentation. It performs
  no daemon/Shell fallback scan. It also implements compound-ID binding,
  high-water supersession, Telegram API classification, real transport, resident
  persistence, and programmable file projection callbacks. Representative
  owning ranges are delivery/deferred coordination
  (`src/lingtai/mcp_servers/telegram/manager.py:2349-2760`), event and usage
  projection (`src/lingtai/mcp_servers/telegram/manager.py:2829-3523`), and
  programmable/resident lifecycle (`src/lingtai/mcp_servers/telegram/manager.py:3526-4375`).
  Beside the SESSION reducer state it keeps `_task_card_session_cost_state`,
  folded by `api_cost.fold_session_cost` in both live append and bounded
  rehydrate, and `_task_card_event_metadata_snapshot()` prices it at render
  into the `session_cost` metadata value.
  `_taskcard_display_expression()` reads the durable declarative display
  expression from `TelegramService` at each automatic projection tick
  (`_broadcast_task_card_event_window`, `_ensure_task_card_resident`) and
  passes it into `TaskCardEventProjection.render_event_groups`.
- `service.py` — besides the enabled/normal_rows/max_refreshes/locale
  presentation preferences, owns the durable `display_expression` field of
  `<agent-workdir>/telegram/taskcard.json`: `taskcard_display_expression()` /
  `set_taskcard_display_expression()`, validated through
  `TaskCardEventProjection.validate_display_expression`, and
  `_maybe_reload_taskcard_state()`, which hot-reloads the whole file (bounded
  to one `stat` per call, re-parsing only on a changed mtime) so a direct
  atomic external edit becomes visible at the next projection tick without a
  process restart. Every persistence setter also calls
  `_maybe_reload_taskcard_state()` under `self._taskcard_lock` before
  deriving the siblings it writes back, so a setter invoked with no
  preceding getter can never overwrite an unseen external edit with a stale
  in-memory copy of the other fields.
- `../../task_card/event_projection.py` — the channel-neutral pure core for safe
  event allowlisting, redaction, API-call grouping, budgets, metadata, and text
  rendering, including compact per-call output/thinking/cache metrics from the
  normalized current-call carrier or `llm_response` fallback and the safe
  `(summary, time, input in, output out)` line correlated from already-recorded
  event/ledger facts. It also owns strict `lingtai.token_usage.session/v1`
  validation and the journal-ordered SESSION reducer: fresh `llm_response`
  snapshots are authoritative, legacy notification carriers are fallback only,
  carrier-less legacy responses invalidate stale fallback, monotonic generation/
  API ordering rejects regressions, and `psyche_molt` clears the old generation.
  Malformed/incoherent values fail closed. It owns no journal I/O, route,
  resident, or transport state. `llm_response.stream_timing` carries optional
  adapter-measured first-visible-text seconds and compatible generation speed
  evidence; `apply_tool_usages` preserves it across current-call carriers.
  Both Telegram render sites opt into `stream_metrics=True` (time line, then
  established token symbols plus speed); other channels remain opt-out.
  `DISPLAY_SLOTS`/`DEFAULT_DISPLAY_EXPRESSION`/`validate_display_expression`/
  `compose_display` define and enforce the small declarative display-expression
  grammar: an ordered, allowlisted selection of the fragments
  (`header`/`rows`/`blank`/`footer`/`divider`/`metadata`/`time`/`ask_agent`)
  `format_rows_task_card_text` already renders, never arbitrary interpolated
  data. Telegram's automatic adapter converts this shared Markdown frame to
  supported HTML by escaping the complete frame before substituting only exact
  static presentation lines; within the shared source budget it shortens only
  escaped dynamic content for fixed tag overhead, while Feishu consumes the
  shared frame unchanged. `format_metadata` renders an adapter-supplied,
  preformatted `session_cost` metadata string as one `Cost · …` row inside the
  Session section (budgeted like Session; absent key means byte-identical
  output). In Telegram HTML, `_telegram_task_card_html`
  (`src/lingtai/mcp_servers/telegram/manager.py:292-418`) gives Session the
  cumulative compact `out` value and a bold `Cost` row, puts Async Work in a
  separate icon-free section, and leaves the per-call metrics line as plain
  text. The separate
  `_telegram_resident_task_card_html` transport adapter
  (`src/lingtai/mcp_servers/telegram/manager.py:196-288`) isolates only the
  programmable suffix after Telegram's injected header and escape-first renders
  its ATX headings, strong spans, inline code, and list items to the closed
  Telegram HTML subset; malformed delimiters remain safe literal text. The
  resident's committed programmable frame remains the authored Markdown bytes.
  It renders only a pre-projected allowlisted pending-activity label
  (`src/lingtai/mcp_servers/task_card/event_projection.py:1281-1289`);
  `TelegramManager` derives that label for canonical `shell.run` from literal
  `input.async`, so no command/path/environment argument enters the row
  (`src/lingtai/mcp_servers/telegram/manager.py:2972-2989`).
- `api_cost.py` — Telegram-owned pure `usage_line` formatter (passed to
  `render_event_groups(usage_line=...)` by both automatic render sites) and a
  small process-local `PriceCatalog` of LiteLLM public standard list prices with
  one bounded background refresh (`_http_fetch`: fixed URL, `read1` chunks under
  an 8 MiB cap and a monotonic total deadline; a failed fetch or thread start
  releases the single in-flight slot and paces the retry). It consumes only
  `usage["bill"]` facts that `TaskCardEventProjection.project_llm_response_usage`
  validated from `llm_response.usage_billing` (kernel `session.py`, adapter-set
  `UsageMetadata.cache_write_*`/`billable_output_tokens`, checked with the shared
  `checked_count`/`safe_billing_model` in `kernel/llm/base.py`; absent, negative
  or bool counts are unknown, never zero) and never blocks rendering on I/O.
  Catalog entries keep a present-but-invalid tier rate as `None` so a bad tier
  price cannot fall back to the cheaper base rate; every charge/average is
  finite-checked and unknown on overflow. Providers that state the counts on
  their wire: Anthropic (write, 1h TTL, output), Claude Code (write, output),
  and OpenAI chat/Responses and native Codex (output only) — the four LLM
  provider families. `fold_session_cost` records each reducer-accepted v1
  `llm_response`'s bill facts once per since-molt `api_call_index` (molt
  resets); `session_cost_text` prices them at render with each round's own
  model through `estimate_parts` and marks gaps or unknown/lower-bound totals `partial`, and unknown
  allocations `?` (contract behavior 15).
- `SKILL.md` — packaged Telegram-facing manual/procedure material for this
  component.
- Retained legacy files in this package (`controller.py`, `_family.py`,
  `interface.py`, `__init__.py`) are no longer the public ownership path for
  `task_card` in this slice. They remain on disk because this migration does not
  delete or rename pre-existing paths.

## Connections

- The intrinsic producer writes `<workdir>/taskcard/status` and
  `<workdir>/taskcard/taskcard.md`.
- `TelegramManager` alone tails `<workdir>/logs/events.jsonl`; when an existing
  `apriori_summary_generated` event appears, it additionally reads at most 64 KiB
  from the end of `<workdir>/logs/token_ledger.jsonl` to find the existing
  correlated summary accounting row. It reads Async Work only through
  `kernel.session_stats.query_published_async_work`; stale/malformed/missing
  snapshots disappear rather than triggering a private store scan. It delegates
  pure safe-field correlation/projection/grouping/rendering and SESSION reduction
  to `TaskCardEventProjection`; both forward append and bounded reverse-tail
  rehydrate feed that reducer in journal order, producing one in-memory provenance
  state and the same render metadata. Unrelated private helpers remain
  compatibility wrappers.
- `TelegramManager` constructs `TaskCardResidentTransport` with dynamic provider
  callbacks and supplies Telegram's HTML programmable-section header. The shared
  core composes that injected provider label with the raw Markdown frame; it
  never imports Telegram, reads its state file, or classifies Bot API errors.
  Telegram send/edit keep `parse_mode=HTML`. Immediately before those provider
  calls, the Telegram adapter locates only the programmable suffix, escapes its
  authored text, and converts supported Markdown presentation to generated
  Telegram HTML. The automatic frame is already independently escaped/rendered,
  and other channels never enter this adapter.
- `TelegramManager._broadcast_programmable_task_card_file()` reads
  `taskcard/status` first: exact `active` reads the body and projects it
  (diff-only against the last committed programmable frame); exact `inactive`
  calls `_clear_programmable_task_card_frame()` to exclude only the
  programmable frame from the resident, idempotently; any other status is
  unchanged.
- The shared `TaskCardResident` composes the programmable frame with the existing
  automatic frame under one tracked resident message and serializes delivery.

## Composition

- Parent: [`src/lingtai/mcp_servers/ANATOMY.md`](../../ANATOMY.md)
- Paired contract: [`CONTRACT.md`](CONTRACT.md)
- Producer owner: [`src/lingtai/tools/task_card/ANATOMY.md`](../../../tools/task_card/ANATOMY.md)
- Shared projection core: [`src/lingtai/mcp_servers/task_card/event_projection.py`](../../task_card/event_projection.py)
- Shared resident core: [`src/lingtai/mcp_servers/task_card/resident.py`](../../task_card/resident.py)

## State

- In-memory resident channel frames and per-route delivery locks owned by the
  shared core instance
- Durable Telegram resident message ids in each account's `task_cards` map
- Durable agent-wide presentation preferences (`taskcard` enabled,
  `normal_rows`, `max_refreshes`, `locale`, `display_expression`) in
  `<agent-workdir>/telegram/taskcard.json`, owned by `TelegramService`;
  hot-reloaded (mtime-bounded) so an external edit lands at the next
  automatic projection tick. This file is distinct from the bootstrap
  `.secrets/telegram.json` account/token config, which never carries
  presentation settings.
- No programmable renderer or Async Work collector state of its own; producer
  state lives under `<workdir>/taskcard/`; the kernel-owned common snapshot is
  the `async_work` child inside `<workdir>/system/agent_record.json`

## Notes

- Missing, unreadable, or `active`-with-blank/missing-body producer state is a
  Telegram no-op that preserves the last good projected programmable frame.
- Exact `inactive` producer state instead excludes only the programmable frame
  from the resident (idempotently); it never touches the resident message, the
  automatic frame, or the local producer body.
- Telegram-specific transport, diff-only updates, and toggle behavior belong
  here, not in the intrinsic producer contract.
