---
name: notification-manual-channel-model
description: >
  Notification payload, mirror, allowlist, hook, delivery, delay, and block-cap
  reference. Read after notification-manual when interpreting current channel
  state or diagnosing delivery; dismissal policy belongs to dismissal-safety.
version: 0.8.0
tags: [lingtai, notifications, channels, protocol, sync, delay, alarm, nudge, hooks, whitelist]
last_changed_at: "2026-09-09T05:30:00Z"
related_files:
- src/lingtai/tools/notification/manual/SKILL.md
- src/lingtai/tools/notification/schema.py
- src/lingtai/kernel/notification_store/__init__.py
maintenance: |
  Keep this reference as the sole owner of channel protocol, delivery, hook,
  delay, and block-cap detail; update its parent route when paths change.
---

# Notification Channel Model

## Current channel surface

A channel is the filename stem in `.notification/<channel>.json`; for example,
`system.json` projects to `attention.system` and `mcp.telegram.json` to
`attention["mcp.telegram"]`. Ordinary channel files are current mirrors. Daemon is a logical aggregate from Store-owned `.notification/daemon/<id>.json` mini-files; sibling `daemon.json` is only a non-authoritative compatibility report. Use Core/tool reads and dismissal, not direct edits.
Delivered metadata can be historical, so never treat an old attention snapshot as
canonical producer state.

The effective allowlist is the static built-ins + `mcp.*` + hook channels
registered by **this agent's workdir**. It is per-agent, not process-global.
Unknown JSON names, non-JSON entries, invalid stems, and kernel-private dotfiles
are ignored. A present but unregistered channel emits one deduplicated
`notification_hook` warn-and-flag event with `ref_id:
blocked_channel:<channel>` until registration.

Built-ins include `email`, `system`, `soul`, `nudge`, `post-molt`, `tool_loop_guard`,
`bash`, `btw`, `cron`, `molt`, `goal`, `daemon`, and `delay-alarm`. Runtime/configuration nudges use `nudge`; its System owner routes `kernel_version` to update checks. `source_drift` stays local, never release-migration routing. See `../../../system-manual/reference/runtime-update-checks/SKILL.md`.

## Payload and producer ownership

A producer writes the current channel as an envelope such as:

```json
{
  "header": "1 system notification",
  "icon": "🔔",
  "priority": "normal",
  "published_at": "2026-06-10T00:00:00Z",
  "instructions": "Optional producer-owned handling guidance.",
  "data": {"events": []}
}
```

`instructions` is inside the payload, not a channel. Read it before choosing a
verb: the producer knows whether the file is disposable output, a mirror over
canonical state, a coalesced event summary, or protected source of truth. A
notification clear changes only this mirror. It must not mark mail read, change a
goal, consume an MCP queue, or mutate any other producer-owned record. A producer with canonical state should register a generic-dismiss guard and teach its owner verb in `instructions`; hook registration only widens the allowlist, not dismissal policy. External
writers use atomic sibling-temp replacement so readers never see partial JSON.

## Delivery and voluntary `check`

`check` returns a placeholder; the turn loop stamps the one live payload onto that
same result. IDLE/ASLEEP wake delivery uses the same shape. During ACTIVE work,
the current payload is copied to every eligible final ToolResultBlock, even when
unchanged; only the newest emission is current and older holders are historical
traces. Delivery signatures are bookkeeping, not an attachment gate. Fingerprints
and the live holder belong to kernel sync, not `manual` or this handler.

## Consumer delay and expiry

`delay` is a consumer filter, never a producer operation. One allowed target is
recorded in private `.notification/.delay_state.json`; the target file keeps
receiving updates. A nonzero request replaces the prior live delay, and `seconds:
0` cancels only the matching target and makes it visible again. The finite cap is
read live from `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS`; blank, non-numeric,
non-positive, or non-finite values fall back to `600`.

For ordinary targets, coherent snapshots, voluntary checks, and wake delivery
omit only the delayed target. A `daemon` delay masks attention only: its payload,
byte version, and bounded summary remain readable, while `daemon:delayed=1`
prevents daemon arrivals from moving the wake fingerprint. Other channels,
including registered hooks, continue to wake. `delay-alarm` cannot itself be
delayed.

Expiry or recovery stops filtering and publishes one high-priority latest-only
`delay-alarm` mirror with target, requested/actual duration, byte-level
changed/no-change, and only conservative producer-reported or retained-event
statistics. State is atomically replaced under the established Store mutation
lock; a stable request id makes timer/restart races overwrite one alarm rather
than duplicate it. Malformed or unreadable delay state fails open to target
visibility, never silence.

## Hooks and allowlist lifecycle

1. Write a watcher that atomically publishes the envelope to an allowlisted
   `.notification/<channel>.json` path.
2. Before starting that watcher, register it with `notification(action='add', input={...})`, supplying `name`,
   `channel`, `source`, `description`, `how_to_modify`, and `how_to_cancel`;
   `version` defaults to `1.0.0`, and `instructions` is optional.
3. Read with `check`, then follow the producer instruction or narrowest dismiss.

`list` preserves registry order. `edit` revalidates channel uniqueness; a null-
only edit is a no-op. `drop` revokes the channel but never stops its process.
Corrupt or unreadable `.notification/hooks.json` gives `list`, `add`, `drop`, and
`edit` a bounded load-failed result. Built-in channels and Store-reserved stems
`hooks` and `large_result_acks` cannot be registered.

## Block cap and settings

`notification.max_chars` is one shared live cap (`10000`, clamped to `2048..10000`)
for persistent and attention lanes. Metadata files are not channels. Resolution is environment
`LINGTAI_NOTIFICATION_MAX_CHARS`, then valid System-v2
`notification_max_chars`, then default. At or under cap, serialization is
byte-identical. Above cap, persistent output spills to
`logs/notification-overflow-<ts>.json`; attention output uses the
content-addressed `notification-attention-overflow-<digest8>.json` (with a
collision suffix). Both model copies compact while preserving routing ids. If an id-only copy still cannot fit, the marker-only envelope may omit its long `path` (`path_omitted=true`) while retaining the exact basename in `spill_file`. Read the full spill before acting; if `spill_failed`, use the producer tool for full content. This is context-size control, not
access or delivery accounting.

The SHOW row reports the same effective clamped value and never writes, refreshes,
adds `init.json`, or creates a settings file. The notification footprint also
includes kernel-owned `large_result_acks.json`, `hooks.json`, and the private
delay state. Inspect read-only; never delete the directory or bulk-remove files,
because producer guards and stale checks live in the atomic actions.
