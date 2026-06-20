---
name: notification-manual
description: >
  Notification filesystem + standalone notification tool manual for LingTai:
  channel whitelist, `.notification/<channel>.json` files, envelope shape,
  the `notification` tool (check / dismiss_channel / dismiss_event / dismiss_ref),
  generic vs producer-specific dismiss, protected channels, stale-version and
  force semantics, and the undismissable large-result reminders discharged only
  by system(action="summarize").
version: 0.2.0
tags: [lingtai, notifications, channels, dismiss, large-result, force, stale]
---

# Notification Manual

LingTai notifications are a filesystem protocol. Producers write JSON files under
an agent's `.notification/` directory; the kernel reads allowlisted files and
syncs the current notification block into the agent's model context. The
agent-facing verbs for reading and clearing those channels live on the
standalone **`notification` tool** — `system` owns none of them.

## Channel files and whitelist

A notification channel is the filename stem in `.notification/<channel>.json`.
For example:

- `.notification/email.json` → `notifications["email"]`
- `.notification/system.json` → `notifications["system"]`
- `.notification/mcp.telegram.json` → `notifications["mcp.telegram"]`
- `.notification/goal.json` → `notifications["goal"]`

The kernel uses an allowlist: built-in channels such as `email`, `system`,
`soul`, `nudge`, `post-molt`, `tool_loop_guard`, `bash`, `btw`, `cron`, `molt`,
and `goal` are accepted; MCP bridge channels are accepted by the `mcp.` prefix.
Unknown `.json` files are ignored by `collect_notifications()` and kernel helper
publish/dismiss calls reject non-allowlisted channel names.

## Envelope shape

Producer helpers write a standard envelope:

```json
{
  "header": "1 system notification",
  "icon": "🔔",
  "priority": "normal",
  "published_at": "2026-06-10T00:00:00Z",
  "instructions": "Optional agent-facing handling guidance.",
  "data": {"events": []}
}
```

`instructions` is a field inside a channel payload, not a channel name. It should
say what the agent should do with this notification and how to clear it.

## The `notification` tool

The notification verbs live **only** on the dedicated, always-available
`notification` tool. The `system` tool no longer accepts any notification or
dismiss action — there are no compatibility aliases.

| Action | What it does |
| --- | --- |
| `notification(action="check")` | Read the live notification payload (placeholder + kernel-stamped `notifications`). |
| `notification(action="dismiss_channel", channel=...)` | Clear one channel whole. |
| `notification(action="dismiss_event", event_id=..., channel="system")` | Remove one `system` event by `event_id`. |
| `notification(action="dismiss_ref", ref_id=..., channel="system")` | Remove `system` event(s) by `ref_id`. |

To compress a large tool result, use `system(action="summarize")` — `summarize`
is a context-hygiene operation owned by `system`, **not** a notification verb.

## Model-visible notification block

`notification(action="check")` returns a placeholder; the kernel stamps the live
notification payload onto that tool result. The same payload is synthesized when
notifications arrive while the agent is IDLE or ASLEEP. The payload contains a
global `_notification_guidance` plus `notifications:{...}` keyed by channel.
After handling a notification, dismiss it and end your turn — do not call
`check` voluntarily again.

## Atomic dismiss

Dismissal is **atomic**: each removal target has its own action, so the call
states exactly what is being cleared. There is no kitchen-sink `dismiss`.

Use producer-specific verbs for channels that mirror producer-owned state. For
example, email unread notifications should be cleared by `email(action="read"...)`
or `email(action="dismiss"...)`, not by a generic channel dismiss.

Clear a whole channel surface:

```text
notification(action="dismiss_channel", channel="nudge")
notification(action="dismiss_channel", channel="system")
```

`dismiss_channel` clears `.notification/<channel>.json` whole. It rejects
`event_id`/`ref_id` — use the atomic event verbs for targeted removal.

Remove a single `system` event without clearing the whole channel:

```text
notification(action="dismiss_event", event_id="evt_...")
notification(action="dismiss_ref", ref_id="goal:current")
```

`dismiss_event`/`dismiss_ref` default `channel` to `system` (the only channel
with per-event structure). They remove only matching entries from
`system.data.events`; if the last event is removed, `.notification/system.json`
is deleted.

## Stale-version and force semantics

Generic dismiss refuses to clear a channel whose on-disk version changed after
the delivered notification version, returning `reason="stale_channel_version"`.
Read the current state first, or pass `force=true` to knowingly clear a stale
mirror. `force=true` also bypasses a producer-registered generic-dismiss guard.
`force` never touches producer-owned state, and (see below) never bypasses the
large-result reminder guard.

## Undismissable large-result reminders

System events with `source="large_tool_result"` are **undismissable**. They
cannot be cleared by any notification action:

- `dismiss_channel` on `system` (with or without `force=true`) is refused;
- `dismiss_event` matching one is refused;
- `dismiss_ref` matching one is refused, including with `force=true`.

All return `reason="undismissable_large_result_reminder"`. There is no `force`
backdoor.

These reminders represent a large tool result that still costs context budget.
The only way to clear one is to **summarize** the result via the system tool:

```text
system(action="summarize", items=[{"tool_call_id": "toolu_...", "summary": "..."}])
```

A successful summarize of that `tool_call_id` auto-clears the matching
`large_tool_result:<tool_call_id>` reminder. A failed summarize item leaves its
reminder in place.

## Protected channels

Some channels are source-of-truth files, not dismissible mirrors. `goal` is
protected: `notification(action="dismiss_channel", channel="goal")` refuses even
with `force=true`. To cancel or complete a goal, edit or delete
`.notification/goal.json` as described in the goal manual.

## post-molt acknowledgement

The kernel-owned `post-molt` continuation channel requires a `reason` to clear:

```text
notification(action="dismiss_channel", channel="post-molt", reason="continue: ...")
```

## Producer canonical state vs notification mirror

A channel dismiss clears only the notification *mirror* surface. Producer-owned
canonical state (mailbox read-state, goal source of truth, etc.) is never
touched by a generic dismiss — that is why producer-owned channels are guarded
and steer you to the producer's own verb.

## Cross-reference

For active goal state and goal reminders, read `reference/goal-manual/SKILL.md`.
For `summarize` (context hygiene, the only large-result discharge), see the
substrate manual's system-operations section under `system-manual`.
