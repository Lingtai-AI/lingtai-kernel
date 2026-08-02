---
name: telegram-task-card-projection
contract_version: 6
root_contract: CONTRACT.md
related_files:
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/task_card/resident.py
  - src/lingtai/mcp_servers/task_card/resident.py
  - src/lingtai/mcp_servers/telegram/task_card/SKILL.md
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/service.py
  - src/lingtai/mcp_servers/telegram/server.py
  - src/lingtai/tools/task_card/CONTRACT.md
  - pyproject.toml
  - tests/test_telegram_task_card_programmable.py
  - tests/test_telegram_task_card_toggle.py
  - tests/test_telegram_task_card_event_tail.py
  - tests/test_mcp_skill_manuals.py
maintenance: |
  This component contract is governed by the root CONTRACT.md. Keep related
  files complete and repo-relative, keep the paired Anatomy/manual reciprocal,
  and update Telegram tests plus the intrinsic producer contract together when
  the projection boundary changes.
---
# Telegram Task Card Projection

## Purpose

Own Telegram's provider adapter and read-only projection of the intrinsic
declarative Task Card artifact. Provider-neutral resident state/delivery lives
in `src/lingtai/mcp_servers/task_card/resident.py`; Telegram-specific consuming
semantics live here. The public producer contract lives in
`src/lingtai/tools/task_card/CONTRACT.md`.

## Behavior

1. The shared resident core owns one tracked route transaction and composes two
   independent channels: `automatic` and `programmable`. Its route includes
   account, chat, and an optional thread. Telegram continues to route resident
   cards per account+chat and passes no thread in this slice.
2. The programmable channel is read-only with respect to the intrinsic
   producer. It reads `<workdir>/taskcard/status` and `<workdir>/taskcard/taskcard.md`.
3. Telegram reads and composes the programmable body only when `taskcard/status`
   is exactly `active` and the body is nonempty. Exact `inactive` instead
   excludes only the programmable frame from composition and updates the same
   resident using Telegram's own automatic content. Missing status, unreadable
   status content that is neither exactly `active` nor exactly `inactive`, or
   `active` with a missing/blank body remain a no-op.
4. A no-op (rule 3's third case) preserves the last valid programmable Telegram
   frame. Exact-`inactive` handling (rule 3's second case) is itself idempotent:
   once the programmable frame is already excluded, repeated `inactive` delivers
   nothing further. Neither case ever deletes/hides the resident message,
   deletes the local producer body, or pauses Telegram's own automatic updates.
5. Projection is diff-only. If the body bytes match the committed programmable
   frame, Telegram performs no transport update.
6. When the Telegram `/taskcard` setting is off, presentation is suppressed.
   Automatic mechanics continue, and hidden programmable finalize still clears
   the committed programmable slot internally so a stale frame cannot resurface
   after re-enable.
7. Telegram owns `events.jsonl` tail I/O/lifecycle, compound-ID and high-water
   semantics, Bot API error classification, real transport, and persistence.
   The shared `TaskCardResident` owns route locking, proposed-slot composition,
   commit-after-success, edit-first delivery, conservative old-first rotation,
   peer adoption, and failure-state projection. It invokes Telegram only through
   `TaskCardResidentTransport` callbacks.

## Port

Internal `TaskCardResidentTransport` boundary implemented by `TelegramManager`.
There is no public MCP `task_card` family in this component.

## Adapters

- Filesystem reader for `<workdir>/taskcard/status` and `taskcard/taskcard.md`
- Telegram transport adapter in `TelegramManager`
- Durable Telegram account state for tracked resident message ids
- Shared in-memory route locks and automatic/programmable slot frames

## Contract rules

1. Telegram must not expose a public MCP `task_card` tool from `server.py`.
2. Programmable projection must remain read-only; no Telegram code may rewrite
   the intrinsic producer files.
3. Missing/unreadable producer status, or `active` with a missing/blank body,
   is a no-op, not an implicit clear. Exact `inactive` is instead a deliberate,
   idempotent exclusion of only the programmable frame — never an implicit
   clear of the resident message, the automatic frame, or the local body.
4. Diff-only comparison is against the committed programmable frame, not the
   composed resident text.
5. The automatic Task Card behavior remains independent of the programmable
   file projector.
6. The shared resident core must preserve Telegram's result fields, call order,
   slot commit timing, singleton/rotation behavior, and private compatibility
   helpers. Telegram's journal tail, ID/high-water semantics, API classification,
   state-file I/O, and provider transport must remain in `TelegramManager`.
7. The shared core must not import Telegram or assume numeric chat/message ids;
   provider authorization and exact route binding stay adapter responsibilities.
8. This package's manual and governed docs remain explicitly packaged through
   `pyproject.toml`.

## Tests

- `tests/test_telegram_task_card_programmable.py` covers active projection,
  diff-only updates, exact-`inactive` frame exclusion (idempotent, resident/
  automatic/body preserved), reactivation, and last-good preservation for
  missing/blank producer state.
- `tests/test_telegram_task_card_toggle.py` covers toggle suppression and the
  hidden-finalize clear semantics.
- `tests/test_telegram_task_card_event_tail.py` continues to cover the automatic
  channel independently.
- `tests/test_task_card_event_projection_shared.py` pins shared-core safety and
  byte compatibility with Telegram's established render surface.
- `tests/test_task_card_resident_shared.py` pins provider-neutral route/slot,
  old-first rotation, peer adoption, and partial-failure state transitions.
- `tests/test_mcp_skill_manuals.py` covers packaged docs for this subpackage.
