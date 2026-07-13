---
name: notification-manual
description: >
  Notification filesystem and standalone notification tool router for LingTai.
  Read this when using notification(action='manual'|'check'|'dismiss_channel'|
  'dismiss_event'|'dismiss_ref'), interpreting `.notification/<channel>.json`,
  or deciding between producer-specific handling and safe mirror dismissal.
  Routes channel/sync mechanics and dismissal safety into nested references;
  large-result compaction remains owned by summarize-manual.
version: 0.3.0
tags: [lingtai, notifications, channels, dismiss, manual, force, stale, nudge]
last_changed_at: "2026-07-12T19:24:00-07:00"
---

# Notification Manual — Router

LingTai notifications are a filesystem protocol: producers publish allowlisted
`.notification/<channel>.json` surfaces, and the kernel exposes their current
model-visible state. The always-available `notification` tool is the sole
agent-callable home for reading and clearing those surfaces. `system` has no
notification or dismiss alias; it still owns `summarize` because context hygiene
is not a notification operation.

## Quick start

| Action | Use |
|---|---|
| `notification(action='manual')` | Return this installed router body. Strictly read-only: it neither reads nor changes notification state. |
| `notification(action='check')` | Request the live notification payload. The handler returns a placeholder and the kernel stamps `_meta.notifications` plus `_meta.notification_guidance` onto the result. |
| `notification(action='dismiss_channel', channel=...)` | Clear one dismissible channel mirror whole. |
| `notification(action='dismiss_event', event_id=..., channel='system')` | Remove one matching system event; `channel` defaults to `system`. |
| `notification(action='dismiss_ref', ref_id=..., channel='system')` | Remove matching system events by producer reference; `channel` defaults to `system`. |

There is no aggregate `dismiss` action. After handling a notification, use the
narrowest correct producer-specific or atomic dismiss action and end the turn;
do not voluntarily call `check` again merely to confirm the clear.

## Installed manual retrieval

`notification(action='manual')` reads only:

```text
<agent>/.library/intrinsic/capabilities/system-manual/reference/notification-manual/SKILL.md
```

Success returns exactly `status`, `notification_manual`, and `manual_path`.
A missing installed file returns `status: degraded`, an empty
`notification_manual`, the same fixed `manual_path`, and an actionable `error`
that points to an initializer or capability-install problem. It never falls back
to a source checkout and never touches `.notification/`, the Notification Store,
producer state, delivery fingerprints, or acknowledgement state.

## Nested reference catalog

```yaml
- name: notification-manual-channel-model
  location: reference/channel-model/SKILL.md
  description: |
    Nested notification-manual reference for the filesystem channel protocol,
    allowlist, envelopes and instructions, nudge routing, kernel sync, voluntary
    check behavior, and producer canonical-state versus mirror boundaries. Read
    this when interpreting or producing notification payloads.
- name: notification-manual-dismissal-safety
  location: reference/dismissal-safety/SKILL.md
  description: |
    Nested notification-manual reference for atomic dismissal, producer-specific
    verbs, stale-version and force rules, protected channels, post-molt
    acknowledgement, and legacy large_tool_result reminder escape hatches. Read
    this before clearing notification state or diagnosing a refusal.
```

## Routing table

| Need / keywords | Read |
|---|---|
| Channel names; `.notification/*.json`; allowlist; `mcp.` channels; envelope fields; `instructions`; nudge/update checks; `_meta.notifications`; voluntary `check`; producer state versus mirror | `reference/channel-model/SKILL.md` |
| Which dismiss action; producer-specific handling; guarded/stale mirror; `force`; protected `goal`; post-molt reason; legacy `large_tool_result` event | `reference/dismissal-safety/SKILL.md` |
| Tool-result ranking, digest quality, `system(action='summarize')`, recovery by `tool_call_id`, summarize versus molt | `../summarize-manual/SKILL.md` |
| Active goal source-of-truth and cancellation/completion | `../goal-manual/SKILL.md` |
| Runtime/kernel update nudges | `../runtime-update-checks/SKILL.md` |

## Safety boundaries to keep resident

- `manual` is documentation retrieval only; it is not a notification-state read.
- `check` is the notification-state read and does not itself write state.
- Generic dismiss clears a notification mirror, never producer-owned canonical
  state. Prefer the producer's own verb when one exists.
- `force=true` is for knowingly clearing a stale or guarded mirror. It does not
  override protected source-of-truth channels and never mutates producer state.
- Large tool results are ranked under
  `_meta.agent_meta.current_tool_result_chars`, not emitted as new
  notifications. Follow `../summarize-manual/SKILL.md`; do not invent a second
  summarization procedure here.

## Why the boundary is split this way

The filesystem protocol lets in-process and external producers publish one
current surface without sharing a queue. Atomic action names make the clearing
target explicit. Producer guards prevent a mirror clear from being mistaken for
handling source-of-truth state. The read-only `manual` action completes
progressive disclosure without coupling documentation access to notification
persistence, delivery, or dismissal policy.
