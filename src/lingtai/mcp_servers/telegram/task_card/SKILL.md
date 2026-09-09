---
name: telegram-task-card-projection-notice
description: |
  Shipped retained-legacy/projection notice for Telegram Task Card files. The
  public `task_card` tool is intrinsic and documented at
  src/lingtai/tools/task_card/manual/SKILL.md; Telegram only projects the
  intrinsic taskcard/status + taskcard/taskcard.md artifact read-only.
last_changed_at: 2026-09-09T00:00:00Z
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

This shipped file is retained beside the governed projection docs; it is **not**
the active public Task Card manual. The public, channel-neutral owner is the intrinsic Task Card, with packaged
procedures at [`task_card/manual/SKILL.md`](../../../tools/task_card/manual/SKILL.md).
Source-checkout architecture lives at `src/lingtai/tools/task_card/ANATOMY.md`
(not a packaged manual link).
Use that owner for `start | inspect | retry | stop | remove | settings | manual`,
renderer authoring, limits, recovery, and cleanup.

Telegram only projects the producer's `taskcard/status` and
`taskcard/taskcard.md` read-only. Exact `active` plus a nonempty body projects
the programmable slot; exact `inactive` idempotently excludes only that slot.
Missing/unreadable status, active with a missing/blank body, other status text,
and unchanged bytes are no-ops. The resident message, automatic event-journal
slot, and producer files are not deleted or rewritten.

Do not use this retained package as the old Telegram-owned schema, endpoint,
JSON-card renderer, reverse-MCP route, or refresh-ceiling source.
