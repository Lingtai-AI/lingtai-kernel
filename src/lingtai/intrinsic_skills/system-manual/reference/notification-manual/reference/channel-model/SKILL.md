---
name: notification-manual-channel-model
description: >
  Nested notification-manual reference for LingTai's notification filesystem
  channel protocol, allowlist, payload envelopes and instructions, nudge routing,
  kernel sync, voluntary check behavior, and canonical producer state versus
  notification mirrors. Read after notification-manual when interpreting,
  producing, or debugging notification payloads; skip for dismissal policy.
version: 0.1.0
tags: [lingtai, notifications, channels, protocol, sync, nudge]
last_changed_at: "2026-07-12T19:24:00-07:00"
---

# Notification Channel Model

## Files and allowlist

A channel is the filename stem in `.notification/<channel>.json`:

- `.notification/email.json` becomes `_meta.notifications.email`;
- `.notification/system.json` becomes `_meta.notifications.system`;
- `.notification/mcp.telegram.json` becomes
  `_meta.notifications["mcp.telegram"]`;
- `.notification/goal.json` becomes `_meta.notifications.goal`.

The kernel accepts built-in channels including `email`, `system`, `soul`,
`nudge`, `post-molt`, `tool_loop_guard`, `bash`, `btw`, `cron`, `molt`, and
`goal`; MCP bridge channels use the `mcp.` prefix. Unknown JSON filenames are
ignored by collection, and kernel publish/dismiss helpers reject names outside
the allowlist. This prevents arbitrary workdir files from entering the
model-visible notification lane.

`nudge` is the formal channel for mechanical, throttled checks. For example,
runtime update checks publish `data.nudges[]` entries with
`kind: kernel_version`; route those through
`../../../runtime-update-checks/SKILL.md` before asking a human to update or
refresh.

## Envelope and producer instructions

Producer helpers write the current channel surface as a standard envelope:

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

`instructions` is an optional field inside one channel payload, not a channel
name. It tells the agent how that producer expects the event to be handled or
cleared. Producers own that directive because only they know whether the file is
a disposable output, a mirror over canonical state, a coalesced event summary,
or protected source of truth.

External producers that can write the workdir may publish the same envelope to
an allowlisted `mcp.<server>.json` path. They must use atomic sibling-temp
replacement so readers never observe a partial JSON file.

## Voluntary check and model-visible delivery

`notification(action='check')` returns a dict placeholder. The turn-loop
post-hook stamps the canonical live payload onto that same result under
`_meta.notifications` and `_meta.notification_guidance`. The handler does not
assemble a second bare channel representation and does not write notification
state.

When notifications arrive while an agent is IDLE or ASLEEP, the kernel can
synthesize the same `notification(action='check')` tool-call/result shape and
wake the agent. During ACTIVE work, the post-hook moves the single live payload
to a suitable dict-shaped tool result only when required by first appearance,
material change, or a deliberate check. Delivery fingerprints and the live
holder belong to kernel synchronization, not to the notification tool's
`manual` action.

After handling the payload, clear it through the producer-specific action or the
narrowest safe notification dismiss action and end the turn. Do not call `check`
again only to confirm dismissal; sparse synchronization will expose a material
change when needed.

## Canonical producer state versus mirror

A generic channel clear changes only the `.notification/<channel>.json` surface.
It does not mark an email read, change a goal, consume an MCP source queue, or
mutate any other producer-owned state. A producer whose notification is a mirror
over canonical state must register a generic-dismiss guard and teach the
producer-specific verb in `instructions`.

This separation is deliberate: the filesystem protocol gives the kernel one
current high-attention surface, while canonical state remains under the
producer's own schema and lifecycle. Read `../dismissal-safety/SKILL.md` before
clearing guarded, stale, protected, or event-granular state.

## Footprint

The protocol footprint is `.notification/<channel>.json` plus kernel-owned
notification metadata such as legacy acknowledgement state. Inspect it
read-only before diagnosing a producer. Do not delete the directory or bulk
remove files: use the producer's action or an authorized atomic notification
action so guards and stale checks remain effective.
