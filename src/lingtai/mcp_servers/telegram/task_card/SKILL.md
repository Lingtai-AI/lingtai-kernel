---
name: telegram-task-card-projection-notice
description: |
  Shipped retained-legacy/projection notice for Telegram Task Card files. The
  public `task_card` tool is intrinsic and documented at
  src/lingtai/tools/task_card/manual/SKILL.md; Telegram only projects the
  intrinsic taskcard/status + taskcard/taskcard.md artifact read-only.
last_changed_at: 2026-09-04T00:00:00Z
related_files:
- src/lingtai/mcp_servers/telegram/SKILL.md
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
- src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
- src/lingtai/tools/task_card/manual/SKILL.md
maintenance: |
  Keep this shipped subpackage manual aligned with the intrinsic Task Card
  owner. Do not reintroduce the retired Telegram controller schema, endpoint,
  ceiling, or lifecycle as active behavior.
---

# Telegram Task Card Projection Notice

This shipped file is retained so the historical Telegram `task_card` subpackage
still has packaged guidance beside its governed Anatomy/Contract documents. It
is **not** the active public Task Card manual.

The authoritative public capability owner is
[`src/lingtai/tools/task_card`](../../../tools/task_card/ANATOMY.md), and its
model-facing instructions live at
[`src/lingtai/tools/task_card/manual/SKILL.md`](../../../tools/task_card/manual/SKILL.md).
Use that intrinsic manual for renderer authoring, the
`start | inspect | retry | stop | remove | settings | manual` actions, refresh
limits, failure handling, and terminal cleanup — this notice does not restate
that producer/lifecycle contract.

Telegram's own projection contract:

- Telegram is a read-only projector for the intrinsic artifact. It polls
  `taskcard/status` and `taskcard/taskcard.md`, projects exact active +
  nonempty bodies into the resident programmable slot, and preserves the
  automatic event-journal slot. Exact `inactive` idempotently excludes only the
  programmable slot from the resident (the message, automatic content, and
  local body are never touched); missing/unreadable status, or active with a
  missing/blank body, or an unchanged body, remain a no-op.

Do not use this retained Telegram package as an active schema, endpoint,
controller lifecycle, JSON-card renderer contract, private reverse-MCP route, or
refresh-ceiling source. Those were the retired ownership path and are kept only
as historical compatibility code while the current product surface lives in the
intrinsic tools layer.
