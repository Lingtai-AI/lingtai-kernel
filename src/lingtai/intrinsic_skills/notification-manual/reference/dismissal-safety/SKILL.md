---
name: notification-manual-dismissal-safety
description: >
  Nested notification-manual reference for choosing safe atomic notification
  dismissal, producer-specific verbs, stale-version and force behavior,
  protected channels, post-molt acknowledgement, and legacy large_tool_result
  reminder escape hatches. Read after notification-manual before clearing a
  channel or diagnosing a dismissal refusal; summarization mechanics live in
  summarize-manual instead.
version: 0.1.0
tags: [lingtai, notifications, dismiss, force, stale, safety]
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/notification-manual/SKILL.md
- src/lingtai/tools/notification/__init__.py
- src/lingtai/tools/notification/schema.py
maintenance: |
  Tracks the notification dismissal-safety topic it documents; update when that integration changes.
---

# Notification Dismissal Safety

## Choose the narrowest owner and target

Use a producer-specific verb first when a notification mirrors producer-owned
state. For example, handle email unread state with
`email(action='read', ...)` or `email(action='dismiss', ...)`; generic channel
dismissal would clear only the high-attention mirror.

For a dismissible notification-owned surface, choose one atomic target:

```text
notification(action='dismiss_channel', channel='nudge')
notification(action='dismiss_event', event_id='evt_...')
notification(action='dismiss_ref', ref_id='goal:current')
```

`dismiss_channel` clears one allowlisted `.notification/<channel>.json` whole
and rejects `event_id` or `ref_id`. `dismiss_event` and `dismiss_ref` default to
the `system` channel and remove only matching entries from
`system.data.events`; removing the final event clears the file. There is no
kitchen-sink `dismiss`, so the call always states what may disappear.

All three actions delegate to the canonical notification Core dismissal helper.
That one policy path enforces allowlists, producer guards, stale-version checks,
protected channels, post-molt acknowledgement, and legacy reminder
acknowledgement. The standalone tool does not reimplement Store or producer
policy.

## Stale versions, guards, and force

A non-force generic dismiss compares the delivered notification version with the
current on-disk version. If a producer updated the channel after delivery, the
call refuses with `reason='stale_channel_version'` rather than erase unseen
state. Read the newly delivered state first.

`force=true` may knowingly bypass that stale-version refusal and a
producer-registered generic-dismiss guard. It still clears only a mirror and
never mutates producer canonical state. Use it for a confirmed stale mirror, not
as a routine retry or a substitute for handling the producer.

## Protected and acknowledgement-sensitive channels

`goal` is protected source of truth. A generic
`notification(action='dismiss_channel', channel='goal')` refuses even with
`force=true`; use `../../../system-manual/reference/goal-manual/SKILL.md` to cancel or complete active goal
state correctly.

The kernel-owned `post-molt` continuation channel requires a non-empty reason
that records the decision, for example:

```text
notification(
  action='dismiss_channel',
  channel='post-molt',
  reason='continue: recovered the pending work'
)
```

## Large results and legacy reminder escape hatch

New large tool results are not notification events. The kernel ranks formal
results under `_meta.agent_meta.agent_state.current_tool_result_chars`; read
`../../../system-manual/reference/summarize-manual/SKILL.md` for the canonical digest,
`system(action='summarize')`, recovery, and summarize-versus-molt procedure. Do
not duplicate that workflow here.

A persisted or pre-molt `source='large_tool_result'` system event may still
exist. If its tool result remains accessible, a successful summarize of the
matching `tool_call_id` also clears the legacy reminder. If summarization is no
longer possible, use an atomic notification escape hatch:

```text
notification(action='dismiss_ref', ref_id='large_tool_result:<tool_call_id>')
notification(action='dismiss_event', event_id='<event_id>')
```

Whole-channel system dismissal also covers such an event but may clear unrelated
system events, so prefer event/ref targeting. Dismissal acknowledges and removes
the reminder surface only; the original tool result remains unchanged in chat
history and `events.jsonl`.

## On a refusal

1. Check whether the channel's `instructions` name a producer action; if so, use
   it instead of retrying the generic dismiss.
2. Stale — read the current delivered payload before deciding on `force=true`.
3. Protected — follow the channel's owning manual; do not retry.
4. `post-molt` without a reason — record the real continue/defer/obsolete
   decision.
5. Targeting one system event — use `dismiss_event`/`dismiss_ref`, not a
   whole-channel clear.

These rules keep a mirror operation from silently becoming a producer-state
decision or erasing concurrent unseen events. No dismissal ever removes producer
history, mailbox state, or goal semantics.
