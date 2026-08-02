---
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/service.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/tools/task_card/ANATOMY.md
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_toggle.py
  - tests/test_telegram_task_card_event_tail.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this Anatomy reciprocal with its paired CONTRACT.md and packaged manual.
  Update it when resident ownership, programmable projection, or the relation to
  the intrinsic producer changes.
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
- `manager.py` — the Telegram adapter that tails `events.jsonl`, supplies safe
  events to the shared projection core, and implements compound-ID binding,
  high-water supersession, Telegram API classification, real transport,
  resident persistence, and programmable file projection callbacks.
- `../../task_card/event_projection.py` — the channel-neutral pure core for safe
  event allowlisting, redaction, API-call grouping, budgets, metadata, and text
  rendering. It owns no journal I/O, route, resident, or transport state.
- `SKILL.md` — packaged Telegram-facing manual/procedure material for this
  component.
- Retained legacy files in this package (`controller.py`, `_family.py`,
  `interface.py`, `__init__.py`) are no longer the public ownership path for
  `task_card` in this slice. They remain on disk because this migration does not
  delete or rename pre-existing paths.

## Connections

- The intrinsic producer writes `<workdir>/taskcard/status` and
  `<workdir>/taskcard/taskcard.md`.
- `TelegramManager` alone tails `<workdir>/logs/events.jsonl`; it delegates only
  pure event projection/grouping/rendering to `TaskCardEventProjection` and
  keeps the existing private helpers as compatibility wrappers.
- `TelegramManager` constructs `TaskCardResidentTransport` with dynamic provider
  callbacks. The shared core never imports Telegram, reads its state file, or
  classifies Bot API errors.
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
- No programmable renderer state of its own; producer state lives under
  `<workdir>/taskcard/`

## Notes

- Missing, unreadable, or `active`-with-blank/missing-body producer state is a
  Telegram no-op that preserves the last good projected programmable frame.
- Exact `inactive` producer state instead excludes only the programmable frame
  from the resident (idempotently); it never touches the resident message, the
  automatic frame, or the local producer body.
- Telegram-specific transport, diff-only updates, and toggle behavior belong
  here, not in the intrinsic producer contract.
