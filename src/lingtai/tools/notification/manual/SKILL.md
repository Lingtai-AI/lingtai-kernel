---
name: notification-manual
description: >
  Entry manual for the standalone `notification` tool and its `.notification`
  mirrors. Routine check uses the schema; read a reference for unfamiliar payload,
  producer, dismissal, delay, or settings detail. Large-result compaction belongs
  to `context-manual`, not here.
version: 0.16.0
tags: [lingtai, notifications, channels, dismiss, delay, alarm, settings, manual, force, stale, nudge, hooks, whitelist]
last_changed_at: "2026-09-09T05:30:00Z"
related_files:
- src/lingtai/prompts/meta_guidance/catalog/notification_handling.md
- src/lingtai/tools/notification/ANATOMY.md
- src/lingtai/tools/notification/CONTRACT.md
- src/lingtai/tools/notification/__init__.py
- src/lingtai/kernel/tool_plugin/CONTRACT.md
- src/lingtai/tools/notification/schema.py
- src/lingtai/tools/notification/settings.py
- src/lingtai/tools/notification/manual/reference/channel-model/SKILL.md
- src/lingtai/tools/notification/manual/reference/dismissal-safety/SKILL.md
maintenance: |
  Keep this entry and its two references concise and aligned with the notification
  capability; preserve their installed relative routes and unique safety rules.
---

# Notification Manual — entry and routes

Use the schema for routine `action` + `input` + `reasoning` calls; start with:

```text
notification(action='check', input={}, reasoning='inspect current notifications')
```

`check` returns a placeholder; read the live payload under `_meta.agent_meta.notifications.attention` and the hook under `_meta.agent_meta.guidance.transient` on that result, not an older delivered snapshot. Follow the payload's producer-specific read/dismiss instructions first. Generic dismissal affects mirrors only, not mailbox, goal, source-queue or other producer state. Choose the narrowest target; do not delete files or call `check` merely to confirm a clear.

## Route one owner

| Need | Read |
|---|---|
| Payload, delivery, allowlist, hooks, delay mechanics or spill recovery | [Channel model](reference/channel-model/SKILL.md) |
| Target selection, producer guard, stale refusal, force, protected goal or post-molt | [Dismissal safety](reference/dismissal-safety/SKILL.md); read before forcing |
| Tool-result compaction or recovery by tool_call_id | `../context-manual/reference/summarize-manual/SKILL.md` |
| Goal cancellation/completion | `../system-manual/reference/goal-manual/SKILL.md` |
| Runtime/configuration nudges | `../system-manual/reference/runtime-update-checks/SKILL.md` |

## Consumer delay and expiry alarm

`notification.delay_max_seconds` is the positive cap from live `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS`, otherwise `600`; missing, blank, non-numeric, non-positive or non-finite values fall back to the default. `delay` hides one allowed consumer target, never stops its producer. Nonzero replaces the prior delay; `0` cancels only the matching target. `delay-alarm` cannot be delayed. Expiry/recovery restores delivery and publishes one latest-only alarm. For daemon, only attention is masked: payload/version/summary remain readable. See [channel model](reference/channel-model/SKILL.md).

Change the environment only with owner approval and apply it through the authorized refresh/relaunch procedure; verify the live SHOW value afterward.

## Block size cap (persistent and attention lanes)

`notification.max_chars` is one live cap for both lanes: environment `LINGTAI_NOTIFICATION_MAX_CHARS`, then valid System-v2 `notification_max_chars`, then `10000`, clamped to `2048..10000`. Malformed values fall through. It limits context size, not access or delivery. Oversized content spills before compaction; follow the exact spill locator (or producer tool on `spill_failed`) before acting on a capped payload. See [channel model](reference/channel-model/SKILL.md).

Use only the authorized environment or closed System-v2 owner procedure. The file layer is hot-read; it does not require a refresh just for this value. Environment/launcher changes need the appropriate authorized refresh/relaunch. SHOW again to verify.

## Notification settings and manual

`settings` and `manual` take strict empty `input={}` and are read-only. SHOW returns two rows in order: `notification.max_chars`, `notification.delay_max_seconds`; each has only `key`, `current`, `default`, `configurable`, `comment`. Comments target the two headings above. Unavailable current truth fails the whole inventory. SHOW grants no mutation authority and creates no settings file.

`manual` reads only initialized `<agent>/.library/intrinsic/capabilities/notification/SKILL.md`, returning `status`, `notification_manual`, `manual_path`. A missing file returns degraded status, empty body and actionable error, never source-tree fallback. It touches no notification, producer, delay or log state. Keep root `summarize=false` so guidance stays exact; compaction belongs to `context`, not Notification or System.
