---
name: goal-manual
description: >
  Goal notification manual: `.notification/goal.json` source-of-truth, fields,
  instructions, idle reminders, protected dismiss behavior, and cancellation or
  completion semantics.
version: 0.1.0
tags: [lingtai, goal, notifications, reminders]
---

# Goal Manual

A goal is represented by `.notification/goal.json`. This file is the protected
source of truth for the agent's current active goal. The short reminder event only
points the agent back to this file; it does not contain the goal details.

## Minimal goal file

```json
{
  "header": "Active goal: finish notification PR",
  "icon": "🎯",
  "priority": "high",
  "published_at": "2026-06-10T00:00:00Z",
  "instructions": "Current active goal. Read data.objective and data.criteria. This channel is protected: do not dismiss it. To cancel the goal, delete .notification/goal.json. See the goal manual under system-manual.",
  "data": {
    "id": "notification-pr",
    "status": "active",
    "objective": "Implement notification whitelist, atomic system-event dismiss, and goal reminders.",
    "criteria": ["tests pass", "manuals updated", "PR opened"],
    "reminder_delay_seconds": 120
  }
}
```

Recommended fields live under `data`:

- `id`: stable identifier used by reminders as `ref_id="goal:<id>"`.
- `status`: omit or use `active` for active work. `done`, `complete`,
  `completed`, `superseded`, `cancelled`, `canceled`, or `inactive` suppress
  reminders.
- `objective`: what to accomplish.
- `criteria`: what done means.
- `reminder_delay_seconds`: optional idle delay before a reminder. If absent, the
  runtime reuses the agent's soul delay; invalid values fall back safely.

The top-level `instructions` field should explicitly say to read the goal data,
that the `goal` channel is protected, and that this manual has the mechanism
details.

## Protected dismiss behavior

`goal` is not an ordinary dismissible notification mirror. Generic dismiss refuses:

```text
system(action="dismiss", channel="goal")  # refused
```

To cancel the goal, delete `.notification/goal.json`. To complete the goal, either
mark `data.status` as `done`/`superseded` or delete/replace the file. Agents are
trusted to decide when the source-of-truth file should change; the protection only
prevents misleading API semantics where dismissing a notification looks like
canceling a goal.

## Idle reminder behavior

The reminder trigger requires the source-of-truth file itself: if
`.notification/goal.json` is absent, no goal reminder is generated.

When `.notification/goal.json` exists, is active, and the agent remains IDLE for
the configured delay, the kernel publishes one short event into
`.notification/system.json`:

```json
{
  "source": "goal.reminder",
  "ref_id": "goal:<id>",
  "body": "Goal reminder: read .notification/goal.json and follow its instructions; see the goal manual under system-manual."
}
```

The reminder is intentionally brief. The actual goal and instructions stay in
`goal.json`. Dismissing the reminder clears only the system event:

```text
system(action="dismiss", channel="system", ref_id="goal:<id>")
```

Dismissing the reminder does not cancel the goal. If the goal remains active, a
fresh idle interval can generate another reminder. If `goal.json` is deleted or
marked inactive/done while a reminder is already present, the next IDLE goal
check clears that stale `goal.reminder` system event.

## Cross-reference

For notification channels, envelopes, allowlist behavior, and atomic system-event
dismiss, read `reference/notification-manual/SKILL.md`.
