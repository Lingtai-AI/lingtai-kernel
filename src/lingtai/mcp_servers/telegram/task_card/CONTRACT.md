---
name: telegram-task-card
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/interface.py
  - src/lingtai/mcp_servers/telegram/task_card/controller.py
  - src/lingtai/mcp_servers/telegram/task_card/__init__.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/account.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/agent.py
  - tests/test_task_card_controller.py
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_toggle.py
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
   created. On success it projects the first frame, starts a daemon watch, and
   returns a `watch_id`.
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
7. **Transport outcome shapes surfaced by `_project`.** The controller normalizes
   the manager's per-transaction result: an accepted edit/send/no-op is
   `{status: ok}`; a **successful-new-id** durable-persist failure
   (`resident_persist_failed`, with a validated new string `message_id`) is
   surfaced as an **observable partial** `{status: error, partial: True,
   resident_persist_failed: True}` — the manager keeps the sole new visible card;
   a **pre-send** `stale_delete_failed` (the exact old resident could not be
   confirmed deleted/missing) is surfaced as `{status: error}` and is **never** a
   successful partial (no new id is adopted). Any non-`ok` status is an error.
   These are the only partial / pre-send-error shapes the controller returns.
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
   probe or delete failure is a pre-send error (no send). A replacement send
   failure after a confirmed old delete may leave **zero** resident cards and
   reports `old_resident_deleted` truthfully. Provider-confirmed edit-impossible
   recovery follows the same delete/missing-confirm-before-send rule. Unknown
   historical orphan cards and ordinary Telegram messages are never enumerated,
   guessed at, or deleted.
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
  partial).
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
7. Hard-at-most-one transport boundary (Behavior 7–8): `_project` MUST surface the
   manager's `resident_persist_failed` as a validated-new-id partial and
   `stale_delete_failed` as a pre-send error (never a successful partial), and
   MUST NOT invent, adopt, or persist a resident id on any pre-send error. The
   manager's rotation/replacement is old-first with confirmed-delete-before-send,
   may truthfully leave zero cards (`old_resident_deleted`), accepts only `int`
   last-message high-water, and never deletes unknown historical orphans.

## Contract tests

`tests/test_task_card_controller.py` locks registration, exact-one JSON
validation, workdir path confinement, synchronous initial errors
(timeout/nonzero/invalid frame), the async watch lifecycle, inspect/retry, the
`last_valid_frame_at` timestamp (initial, recovery, failure preservation), the
truthful retryable failed-`stop`/`stop_failed` path, the post-projection
late-`update` drop + watcher compensation (`stop_thread_alive` →
`finalized` handshake) and its failed-compensation retry, the `_project`
normalization of `resident_persist_failed` to an observable partial and the
rejection of an impossible `stale_delete_failed`-with-`ok` payload, and deduped
fail-loud error/recovery wakes against a fake reverse client.
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
cross-account/chat isolation. `tests/test_telegram_account_last_message_id.py`
locks the int-only, edit/delete-immune, non-persisted last-message high-water.

Verification commands (repo venv):

```bash
.venv/bin/python -m pytest -q tests/test_task_card_controller.py \
  tests/test_telegram_task_card_programmable.py \
  tests/test_telegram_task_card_toggle.py \
  tests/test_telegram_task_card_singleton.py \
  tests/test_telegram_task_card_last_message.py \
  tests/test_telegram_account_last_message_id.py \
  tests/test_telegram_task_card_in_place.py
.venv/bin/python -m pytest -q tests/test_architecture_documents.py
```

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes require
synchronized Port (`interface.py`), adapters, contract tests, and this contract;
structural or composition changes also update the paired Anatomy and reciprocal
parents.
