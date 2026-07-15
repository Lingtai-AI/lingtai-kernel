---
name: telegram-task-card
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/interface.py
  - src/lingtai/mcp_servers/telegram/task_card/controller.py
  - src/lingtai/mcp_servers/telegram/task_card/__init__.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/telegram/task_card/assets/render_bash_async.py
  - src/lingtai/mcp_servers/telegram/task_card/assets/render_daemon.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/account.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/agent.py
  - pyproject.toml
  - tests/test_task_card_controller.py
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_toggle.py
  - tests/test_telegram_task_card_templates.py
  - tests/test_mcp_skill_manuals.py
  - tests/test_telegram_task_card_singleton.py
  - tests/test_telegram_task_card_last_message.py
  - tests/test_telegram_account_last_message_id.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Telegram Programmable Task Card

## Purpose

This component owns the *programmable* slot of Telegram's **one tracked resident
Task Card target** (per account+chat): the model-facing `task_card` capability
that binds agent state to that card by running an agent-supplied Python renderer
and projecting only validated data. It is Telegram MCP-owned — registration is
gated by the Telegram reverse route, projection targets
`_lingtai_telegram_task_card`, and the Telegram manager/server/service own the
resident slots, in-place edits, the **hard-at-most-one / last-message resident
transport** (send / edit / rotate / provider-confirmed replacement / exact delete
/ durable persistence), the per-chat last-message high-water observation, the
`/taskcard` toggle, and the rendering destination. This unit only drives the
programmable slot and normalizes the manager's transport outcomes for its own
watch lifecycle; the hard-at-most-one *matrix* is Telegram-manager-owned and
described under **Adapters**. There is no cross-channel port and no second
implementation. The manual is [`SKILL.md`](SKILL.md).

## Behavior

Observable obligations and prohibitions for agents that use or modify this unit;
the procedure lives in [`SKILL.md`](SKILL.md), not here:

1. `start` validates and runs the renderer once **synchronously**. A renderer,
   JSON, or schema failure is an immediate tool error and **no** watch handle is
   created. On an accepted first frame it starts a daemon watch and returns
   `{status: ok, watch_id, state: "watching"}`. **Initial successful-partial:** if
   the first frame was sent and is visible but its durable resident-id write failed
   (a validated route-matching new id — point 7), `start` still keeps the watch
   addressable and returns `{status: ok, watch_id, state: "error", partial: True,
   resident_persist_failed: True, message_id, error}`, committing the accepted
   frame/timestamp and a truthful retryable `resident_persist_failed` error so
   later `inspect`/`retry`/`stop` can recover or finalize it (the error clears only
   on a later accepted projection). A malformed, cross-route, or indeterminate
   first-frame result is instead a hard error with **no** watch adoption. `start`
   never collapses a validated partial into a generic backend rejection and never
   claims full persistence success.
2. `inspect` reports the watch state (`watching`, `error`, `stopping`, or
   `stop_failed`), the last valid frame and its **UTC ISO-8601**
   `last_valid_frame_at` timestamp (stamped on every accepted initial/recovered
   frame; unchanged across failed attempts), and the current error. `retry`
   re-runs the renderer now for an active watch, but on a watch where `stop` has
   already been requested it continues the stop path only (re-check quiescence,
   then re-attempt the clear) and **never** re-runs the renderer. `stop` clears
   **only** the programmable frame and removes the watch (returning `stopped`)
   **only after** the watcher thread is quiescent **and** the backend durably
   accepts the clear. `stop` never finalizes, removes, or reports `stopped` while
   the watcher thread is still alive: a renderer — or an `update` projection whose
   reverse call has no total-time bound (a stale-resource restart+retry can exceed
   the per-attempt timeout) — still running past the join budget yields a
   truthful, retryable `stop_failed` (`stop_thread_alive`) with the watch
   retained, and a transient clear failure yields a retryable `stop_failed`
   (`stop_finalize_failed`). A renderer or update that returns after stop was
   requested is dropped — no late-`update` follow-through, no last-valid
   overwrite, no stop-error clear; and if an already-authorized update may have
   landed, the watcher thread compensates by clearing the slot itself so the late
   frame cannot linger, after which a later retry removes the watch without a
   second reverse clear. When the programmable slot is the only resident content,
   finalize delivers a stable nonempty `— WATCH STOPPED —` terminal marker
   (Telegram cannot edit to empty) so the resident stays reusable. Renderer files
   are never deleted.
3. Renderer execution is confined to the agent working directory
   (symlink-resolved containment), runs under a per-run timeout, and requires
   stdout to be **exactly one** schema-valid Task Card JSON object (`title`
   string, `lines` array of ≤20 strings, `footer` string; at least one present).
   Nonzero exit, timeout, empty/multi-object/non-object output, and wrong field
   types are handled failures, never crashes.
4. After a handle exists, failures preserve the last valid frame and emit a
   **deduped, per-episode** fail-loud `task_card.error` wake plus one `recovered`
   wake; raw renderer output and secrets never enter the wake.
5. Projection uses `channel="programmable"` only; the controller forwards a
   validated card object, **never** code. Updating the programmable slot never
   disturbs the automatic slot.
6. `/taskcard off` hides delivery of both slots at the Telegram presentation
   boundary while all mechanics — renderer runs, watches, retries, last-valid
   bookkeeping — continue; the Telegram adapter returns an explicit non-error
   suppression result. Re-enabling needs no restart.
7. **Validated transport outcome shapes surfaced by `_project`.** A resident
   `message_id` is a compound `account:chat_id:message_id` string (rightmost two
   `:`-separated fields are the chat id and terminal Telegram message id). The
   controller **independently validates** any returned id by route match — its
   account and chat MUST equal the projection's `watch.account`/`watch.chat_id` —
   and a real positive-integer terminal id (group chat ids may be negative, so only
   the terminal id must be positive). Outcomes: an accepted edit/send/no-op is
   `{status: ok}` (a clean id, when present, must pass validation; a suppressed
   `/taskcard off` no-op carries none); a **successful-new-id** durable-persist
   failure (`resident_persist_failed` **with** a validated route-matching id) is
   the one allowed **observable partial** `{status: error, partial: True,
   resident_persist_failed: True, message_id}` — the manager keeps the sole new
   visible card and the validated id is surfaced so `start` can keep the handle; a
   **pre-send** `stale_delete_failed` and an **indeterminate send**
   (`indeterminate_send`, a top-level-ok send that returned no usable message id)
   are `{status: error}` and are **never** a successful partial (no id is invented
   or adopted). A malformed, cross-route, or absent id under any
   `resident_persist_failed`/clean result is rejected to a plain `{status: error}`
   — an unknown card is never adopted, so recovery can always address exactly the
   sent card. Any non-`ok` status is an error.
8. **Hard-at-most-one / last-message resident transport (Telegram-manager-owned;
   Jason #5272/#5273/#5275).** Every projection runs inside one per-account+chat
   delivery transaction that re-reads the tracked resident and commits composed
   slot state only after transport success. When a newer chat message is *known*
   to sit below the resident (deterministic `int`-only last-message high-water;
   an unknown high-water stays conservative — edit in place, never delete), the
   manager rotates **old-first**: probe the exact resident (warm: same-content
   edit/no-op using the last committed render; cold: the exact delete outcome is
   the existence probe), require a confirmed exact-old delete **or** explicit
   not-found before any replacement send, then send and persist. Unknown/transient
   probe or delete failure is a pre-send error (no send). At the transport
   boundary the manager accepts a sent message id only when it is a real,
   non-boolean, **positive integer**; a missing/`bool`/`float`/`str`/zero/negative
   id is an explicit **indeterminate send** (`indeterminate_send`) error — never a
   formatted/adopted/persisted fake id (e.g. `:0`) and never a successful partial.
   A replacement send that fails or is indeterminate after a confirmed old delete
   may leave **zero** resident cards and still reports `old_resident_deleted`
   truthfully. Provider-confirmed edit-impossible recovery follows the same
   delete/missing-confirm-before-send rule. Unknown historical orphan cards and
   ordinary Telegram messages are never enumerated, guessed at, or deleted.
9. Agents must read the manual before authoring a renderer and MUST NOT weaken
   these promises to match implementation drift.

## Port

The inbound driving port is the `task_card` tool (`start | inspect | retry |
stop`; schema in `controller.py` `get_schema`). Core's outbound host dependency
is the `TelegramTaskCardAgent` Protocol in `interface.py`: `_working_dir`,
`_mcp_clients_by_tool`, `_telegram_task_card_context`, `_shutdown`, `add_tool`,
and `_enqueue_system_notification`. Core's outbound rendering dependency is the
private Telegram reverse channel `_lingtai_telegram_task_card` invoked with
`channel="programmable"`. No concrete `Agent` or `BaseAgent` type crosses either
boundary; the controller reads only the Protocol members.

## Adapters

- The concrete outer `Agent` (`src/lingtai/agent.py`) satisfies
  `TelegramTaskCardAgent` structurally and is the Composition Root
  (`_maybe_setup_task_card_controller`), wiring the tool only when a Telegram
  reverse channel is present.
- The agent-supplied Python renderer is the user-code adapter that produces
  frames; the controller runs it as a subprocess and treats its stdout as
  untrusted, validated data.
- The `telegram` MCP client is the transport adapter to `TelegramManager`
  (`manager.py`, `server.py`), which owns render, compose, persistence, and the
  hard-at-most-one / last-message transport of the one tracked resident target.
  Its serialized delivery is `_deliver_channel_frame` → `_deliver_channel_frame_locked`
  (`manager.py`); rotation-when-superseded is `_resident_superseded` /
  `_rotate_task_card_to_latest`; old-first replacement is
  `_replace_task_card_after_probe` (delete via `_delete_task_card_message_outcome`,
  distinguishing exact `missing` from `failed`); durable persistence is
  `_set_resident_task_card` (returns success/failure for the `resident_persist_failed`
  partial). The send boundary `send_progress_message` forms/adopts a compound
  resident id **only after** `_sent_message_id_or_none` confirms a real positive
  `int` message id; otherwise it returns `{status: "indeterminate_send"}` and the
  cold-send and old-first replacement paths fail closed (no fake id, no persist,
  preserving `old_resident_deleted`).
- The controller **independently** re-validates every manager `message_id` in
  `_project` via `_route_matched_message_id` (`controller.py`) — route match to
  `watch.account`/`watch.chat_id` plus a positive-integer terminal id — for both
  clean and partial outcomes, so a malformed/cross-route id from either layer is
  never adopted as a resident.
- `TelegramAccount` (`account.py`) is the state adapter that owns the resident-id
  `task_cards` map and the **ephemeral per-chat last-message high-water**:
  `_note_chat_message_id` records only real `int` message ids (rejecting `bool`,
  float, and string; edits/deletes never bump it) from inbound updates and this
  account's own sends, and `get_last_message_id` returns that `int` or `None`
  (conservative, not persisted — refresh starts unknown). The manager reads it
  through `_get_last_message_id` (int-or-`None`) to decide `_resident_superseded`.

## Contract rules

1. Telegram MCP-owned: registration is gated by the Telegram reverse route; there
   is no cross-channel port, no second implementation, and no compatibility alias
   at the retired `lingtai.kernel.task_card_controller` path.
2. The controller depends only on `TelegramTaskCardAgent`, never on the concrete
   `Agent`/`BaseAgent` class.
3. `TelegramManager` is the single render/compose/persistence/transport owner;
   the controller forwards validated card objects only, mutates no durable state,
   and never sends, edits, deletes, or replaces a resident directly — it consumes
   only the normalized `_project` outcome shapes (rule 7).
4. The public actions, schema, and behavior are preserved, together with the
   Telegram-adapter-owned #891 in-place resident-edit semantics and #892
   both-slot toggle suppression (mechanics continue while presentation is hidden).
5. Renderer files are never deleted; `stop` and `shutdown_for_agent_stop` join
   watcher threads without any filesystem deletion.
6. Stop/finalize is commit-after-accept: the watch is removed and `stopped`
   reported only after the programmable clear is delivered. A transient/unknown
   edit failure preserves resident id and slot state and keeps the watch
   retryable; a programmable-only resident is cleared to the nonempty
   `— WATCH STOPPED —` terminal marker rather than empty text; and a hidden
   (`/taskcard off`) programmable finalize clears its committed slot internally
   with no transport, so a stopped hidden watch cannot resurface after
   `/taskcard on`.
7. Validated resident id + hard-at-most-one transport boundary (Behavior 7–8): a
   resident id is a compound `account:chat_id:message_id`. The manager MUST accept
   a sent id only as a real non-boolean **positive `int`** (never form/adopt/persist
   a fake id such as `:0`), and `_project` MUST additionally route-match every
   returned id (account/chat equal the watch route, terminal id positive) for both
   clean and partial outcomes. `_project` MUST surface `resident_persist_failed`
   as a validated-new-id partial (carrying that `message_id`) and MUST treat
   `stale_delete_failed`, `indeterminate_send`, and any malformed/cross-route id as
   a plain error — never a successful partial and never an adopted id. `_start`
   MUST keep the watch handle addressable on a validated persistence partial and
   discard it on any other first-frame error. The manager's rotation/replacement is
   old-first with confirmed-delete-before-send, may truthfully leave zero cards
   (`old_resident_deleted`), accepts only `int` last-message high-water, and never
   deletes unknown historical orphans.

## Contract tests

`tests/test_task_card_controller.py` locks registration, exact-one JSON
validation, workdir path confinement, synchronous initial errors
(timeout/nonzero/invalid frame), the async watch lifecycle, inspect/retry, the
`last_valid_frame_at` timestamp (initial, recovery, failure preservation), the
truthful retryable failed-`stop`/`stop_failed` path, the post-projection
late-`update` drop + watcher compensation (`stop_thread_alive` →
`finalized` handshake) and its failed-compensation retry, the `_project`
normalization of `resident_persist_failed` to an observable partial (surfacing the
validated `message_id`) and the rejection of an impossible
`stale_delete_failed`-with-`ok` payload, the `_project` route + positive-int
`message_id` validation that rejects malformed/cross-route ids for both clean and
partial outcomes (`test_project_rejects_malformed_or_cross_route_partial_id`,
`..._clean_id`, `test_project_suppressed_ok_without_id_is_accepted`), the initial
successful-partial that keeps the watch handle addressable and stops without
rerender (`test_start_initial_persistence_partial_keeps_watch_and_stops`,
`test_start_partial_error_clears_only_on_accepted_recovery`) while a malformed
initial partial is discarded (`test_start_malformed_partial_id_discards_watch`),
and deduped fail-loud error/recovery wakes against a fake reverse client.
`tests/test_telegram_task_card_programmable.py` locks the two-slot composition,
update isolation, programmable `finalize`, the programmable-only `— WATCH STOPPED —`
terminal marker with a reusable resident, secret redaction, and the
commit-after-successful-transport state discipline in the manager.
`tests/test_telegram_task_card_toggle.py` locks the `/taskcard` suppression path,
including that a programmable watch keeps rendering while hidden, projects again
after re-enable, and that stopping a hidden watch does not resurface its stale
frame after re-enable. `tests/test_telegram_task_card_singleton.py` and
`tests/test_telegram_task_card_last_message.py` lock the manager's hard-at-most-one
matrix: update-first edit-in-place, old-first replacement, warm same-content vs
cold exact-delete probe, `stale_delete_failed` fail-closed, zero-card
`old_resident_deleted`, `resident_persist_failed`, rotation-when-superseded, and
cross-account/chat isolation, and the malformed/missing send-id **indeterminate**
outcome on both cold-send and old-first replacement — never a fake id, never a
partial, `old_resident_deleted` preserved
(`test_cold_send_malformed_id_is_indeterminate_never_adopted`,
`test_rotate_replacement_malformed_send_reports_deleted_and_indeterminate`).
`tests/test_telegram_account_last_message_id.py` locks the int-only,
edit/delete-immune, non-persisted last-message high-water.

Packaging traceability: `tests/test_mcp_skill_manuals.py` checks that
`pyproject.toml`
(`[tool.setuptools.package-data]."lingtai.mcp_servers.telegram.task_card"`)
**declares** `SKILL.md`, `ANATOMY.md`, `CONTRACT.md`, and the `assets/**/*`
renderer templates. The test asserts the package-data declaration only — it does
not build or inspect a wheel/sdist — so it guards against the declaration silently
dropping this governed nested unit's docs/assets on a future ownership move, which
is why the entry is required rather than relying on the (non-recursing) parent
`telegram` glob. `tests/test_telegram_task_card_templates.py` locks the two
renderer template assets themselves: both entry-point manuals, asset shape
(stdlib-only, documented schema), and executable behavior through the real
`TaskCardController._run_renderer` — including snapshot truthfulness (no
`starting`/`running` without a recorded identity plus an allow-listed state) and
the byte/type/range bounds (oversize file, container-valued fields, giant
integers, non-finite floats, wrong types, and hostile strings all degrade to an
explicit awaiting frame or are clipped, never fabricated).

Verification commands (repo venv):

```bash
.venv/bin/python -m pytest -q tests/test_task_card_controller.py \
  tests/test_telegram_task_card_programmable.py \
  tests/test_telegram_task_card_toggle.py \
  tests/test_telegram_task_card_singleton.py \
  tests/test_telegram_task_card_last_message.py \
  tests/test_telegram_account_last_message_id.py \
  tests/test_telegram_task_card_in_place.py \
  tests/test_telegram_task_card_templates.py
.venv/bin/python -m pytest -q tests/test_architecture_documents.py \
  tests/test_mcp_skill_manuals.py
```

## Automatic Task Card telemetry and timestamp interface

The automatic slot is a bounded projection of the agent's authoritative
`logs/events.jsonl` event order, broadcast to each resident Task Card. It has
one source of truth for each projection axis:

1. **Tool rows and timestamps.** A row is projected only from a validated
   `type == "tool_call"` event. Its allowlisted fields are `tool_name`,
   `tool_args.action`, and redacted/capped `tool_args._reasoning`. Its optional
   `started_at` is derived only from that same event's top-level Unix-epoch `ts`
   and uses `HH:MM:SS UTC±HH`. Missing, boolean, non-numeric, non-finite, or
   out-of-range `ts` omits `started_at` without failing the tail. `_meta`, row
   arguments, notification payloads, and the current render instant are never
   timestamp sources.
2. **Current telemetry.** Only the latest final-carrier `type == "tool_result"`
   event's whole `_meta.agent_meta` is current. The manager projects only
   `_meta.agent_meta.agent_state.token_usage.session` and only these supported
   fields: `session_cache_rate`, `cache_miss_tokens`, `cache_miss_budget`,
   `api_calls`, `context_tokens`, `context_window`, and `context_usage`.
   Historical `agent_meta` holders are not merged; retired
   `tool_meta.token_usage`, row args, notifications, and render time are not
   telemetry sources. A missing/malformed carrier or field omits safely and
   must not leave an older snapshot visible after a malformed current carrier.
3. **Formatting and composition.** The supported fields are passed to the
   existing `_format_task_card_metadata` projection, which preserves the
   bounded two-line metadata footer and its 150-character joined budget. The
   rows continue through `_format_rows_task_card_text`; telemetry never changes
   row ordering, row timestamps, or the programmable slot. A broadcast occurs
   when either the bounded tool-row window or the current telemetry projection
   changes.
4. **Implementation and tests.** The event path is
   `manager.py:_decode_event_line` → `_project_tool_call_row` /
   `_project_tool_result_metadata` → `_reverse_tail_latest_rows` or
   `_append_new_lines` → `_broadcast_task_card_event_window` →
   `_format_task_card_text`. The formatter anchors are
   `manager.py:_format_task_card_metadata` and
   `manager.py:_format_rows_task_card_text`. The event-log-to-final-render
   regression is
   `tests/test_telegram_task_card_event_tail.py:test_event_log_final_carrier_projects_session_telemetry_into_final_render`;
   malformed-carrier coverage is
   `test_malformed_current_telemetry_carrier_clears_previous_snapshot`, while
   timestamp and malformed-input coverage is in the adjacent
   `test_row_started_at_is_...` cases. Update this contract and its paired
   anatomy/tests together if the event schema, final-carrier path, supported
   fields, formatter budget, or timestamp provenance changes.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Port (`interface.py`), adapters, contract tests, and this contract;
structural or composition changes also update the paired Anatomy and reciprocal
parents.
