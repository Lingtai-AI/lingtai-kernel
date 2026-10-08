---
name: task_card-manual-notifications
description: >
  Focused Task Card reference for typed producer notifications, reminders,
  change-gated resident projection, and limits on consumer guarantees.
version: 0.1.0
last_changed_at: "2026-09-09T00:00:00Z"
tags: [lingtai, task-card, notifications, projection, reminders]
related_files:
- src/lingtai/tools/task_card/manual/SKILL.md
- src/lingtai/tools/task_card/__init__.py
- src/lingtai/tools/task_card/CONTRACT.md
- src/lingtai/adapters/tool_plugin_host.py
maintenance: |
  Tracks Task Card's typed notification and consumer-projection boundary;
  update with the owner manual and contract when event forms, reminder seams,
  or projection guarantees change.
---

# Task Card notifications and projection

## Typed producer boundary

Only `TaskCardErrorNotification`, `TaskCardRecoveredNotification`, and
`TaskCardLimitNotification` cross the family adapter. Its native port exposes
`publish_error`, `publish_recovered`, `publish_limit`, `submit_reminder(turns)`,
and `clear_reminder()`. Generic publishers, arbitrary keyword fields, foreign
source/channel, and caller-supplied priority/extra metadata are refused.

The host pins the established wire policy: error and recovered use
`task_card.error`, refresh exhaustion uses `task_card.limit`; events use the
system channel, bounded extras, idempotency, and priority. The producer chooses
event content and deduplication identity, never a transport.

A non-exhausting renderer failure keeps the last body and emits a deduped error;
a later success emits recovery. An exhausted final failure suppresses that error
and emits one limit event telling the agent to start a new watch if work remains.
Notification failure does not turn producer state into false success.

## Reminders and resident projection

After the configured `reminder_turns` completed text turns (default `10`), the
producer asks whether the card is absent or stale and whether an update or
retirement is useful. Successful publication resets the counter; a reminder does
not re-inject an unchanged body.

The agent's resident `_meta.agent_meta.taskcard` view is change-gated: unchanged
body/status bytes are not repeatedly injected, while first appearance or material
change may attach a fresh payload. A missing card gets a generic route to this
manual. The resident projection has a separate fixed `TASKCARD_MAX_CHARS=2000` cap: a
larger active body is reported as refused without its text. Raising the producer
`max_body_chars` does not raise this resident cap. The producer also refuses
over-limit output rather than truncating it. Keep goal, status and next step
within the resident budget; link complex evidence elsewhere.

## Consumer boundary

Task Card owns producer artifacts and typed events, not Telegram/Feishu/portal
IDs, message edits, retries, or delivery guarantees. Consumers independently read
`taskcard/status` and `taskcard/taskcard.md` and interpret missing, invalid, or
inactive state. Consumer display is never producer progress.
