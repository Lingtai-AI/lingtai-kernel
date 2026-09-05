---
name: notification-manual-channel-model
description: >
  Nested notification-manual reference for LingTai's notification filesystem
  channel protocol, allowlist, payload envelopes and instructions, nudge routing,
  kernel sync, voluntary check behavior, and canonical producer state versus
  notification mirrors. Read after notification-manual when interpreting,
  producing, or debugging notification payloads; skip for dismissal policy.
version: 0.5.1
tags: [lingtai, notifications, channels, protocol, sync, delay, alarm, nudge, hooks, whitelist]
last_changed_at: "2026-09-04T00:00:00Z"
related_files:
- src/lingtai/tools/notification/manual/SKILL.md
- src/lingtai/tools/notification/schema.py
- src/lingtai/kernel/notification_store/__init__.py
maintenance: |
  Tracks the notification channel-model/protocol topic it documents; update when that integration changes.
---

# Notification Channel Model

## Files and allowlist

A channel is the filename stem in `.notification/<channel>.json`:

- `.notification/email.json` becomes `_meta.agent_meta.notifications.attention.email`;
- `.notification/system.json` becomes `_meta.agent_meta.notifications.attention.system`;
- `.notification/mcp.telegram.json` becomes
  `_meta.agent_meta.notifications.attention["mcp.telegram"]`;
- `.notification/goal.json` becomes `_meta.agent_meta.notifications.attention.goal`.

The kernel accepts built-in channels including `email`, `system`, `soul`,
`nudge`, `post-molt`, `tool_loop_guard`, `bash`, `btw`, `cron`, `molt`, `goal`,
`daemon`, and `delay-alarm`; MCP bridge channels use the `mcp.` prefix. The **effective allowlist**
is `static ∪ mcp.* ∪ the agent's own registered hook channels`, and it is
**per-agent, not process-global**: a hook channel is allowed only for the agent
whose workdir registered it (external hooks register manifests via
`notification(action='add', ...)`, which appends to
`.notification/hooks.json` and allowlists the manifest's `channel` for that
workdir — see the
parent manual's `Hooks & whitelist` section). Unknown JSON filenames are
ignored by collection, and kernel publish/dismiss helpers reject names outside
the effective allowlist, so arbitrary workdir files cannot enter the
model-visible notification lane. Blocked attempts by unregistered channels are
now observable: the kernel emits a deduped `notification_hook` system
warn-and-flag event (`ref_id: blocked_channel:<channel>`) so the agent can
investigate and register the hook if legitimate.

The D2 warn-and-flag scan runs only when a present channel file appears for a
channel that is not on the effective allowlist, and only for stems that can
actually become channels: kernel-private dotfiles (e.g. `.nudge_state.json`),
non-`.json` entries, and syntactically invalid stems are skipped, so no
unresolvable "register this hook" event is emitted for files that could never
be a channel.

`nudge` is the formal channel for mechanical, throttled checks: runtime update
checks publish `data.nudges[]` entries with `kind: kernel_version`, and
source-freshness checks may publish `kind: source_drift` — which stays local and
never enters release-migration routing. The sole normal install/update route is
`https://lingtai.ai/install.sh` (let Shell run its `--help` and follow that), and
real updates, configuration writes, and refresh require
explicit human/config-owner authority. Those rules are owned in full by
`../../../system-manual/reference/runtime-update-checks/SKILL.md`; this manual
owns only the generic channel protocol.

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

`check` returns a dict placeholder that the turn-loop post-hook stamps with the
canonical live payload; the handler assembles no second channel representation
and writes no notification state.

When notifications arrive while an agent is IDLE or ASLEEP, the kernel can
synthesize the same `notification(action='check')` tool-call/result shape —
same `action`/`input`/`reasoning` envelope, indistinguishable from a read you
issued yourself — and wake the agent. During ACTIVE work the post-hook moves the
single live payload onto a suitable dict-shaped tool result only on first
appearance, material change, or a deliberate check. Delivery fingerprints and
the live holder belong to kernel synchronization, not to the `manual` action.

## Consumer delay filtering

The `daemon` target is masked, not filtered: its payload and byte version stay
visible while its attention entry collapses to a constant token, so daemon
arrivals stay readable but do not wake until the delay expires.

`notification(action='delay', input={'channel': ..., 'seconds': 0 or a live configured cap})` is
not a producer operation. Its private `.notification/.delay_state.json` record
causes the coherent consumer snapshot, delivery fingerprint, synthetic wake, and
voluntary `check` projection to omit exactly one allowed target while it is live;
the target file itself continues receiving and retaining producer updates. A
nonzero delay replaces the prior one, and `seconds: 0` cancels the matching target
and makes it visible again. See `notification-manual` → "Consumer delay and
expiry alarm" for the exact nonzero-cap source, default, and precedence.

The process timer is only a prompt path. Every coherent sync also recovers a
persisted overdue delay. Recovery stops filtering and publishes one high-priority
`delay-alarm` mirror in the same read/wake cycle. Its state uses the established
native notification mutation lock plus atomic sibling replacement, and a stable
request id makes a stale callback/restart retry overwrite the same latest-only
alarm rather than append a duplicate. The alarm contains only byte-level change
comparison plus producer-reported or retained-event measurements; such values are
not claimed to be exact totals for overwrite/capped payloads. `delay-alarm` is a
built-in mirror consumers may dismiss, but it cannot itself be delayed. Missing or
malformed delay state fails toward visible target delivery.

## Canonical producer state versus mirror

A generic channel clear changes only the `.notification/<channel>.json` surface.
It does not mark an email read, change a goal, consume an MCP source queue, or
mutate any other producer-owned state. A producer whose notification is a mirror
over canonical state must register a generic-dismiss guard and teach the
producer-specific verb in `instructions`.

This separation is deliberate: the filesystem protocol gives the kernel one
current high-attention surface, while canonical state remains under the
producer's own schema and lifecycle.

## Footprint

The protocol footprint is `.notification/<channel>.json` plus kernel-owned
notification metadata such as legacy acknowledgement state
(`.notification/large_result_acks.json`), the hook-manifest registry
(`.notification/hooks.json`, a single non-channel file invisible to collection),
and consumer-delay state (`.notification/.delay_state.json`, a private dotfile
invisible to collection). Inspect it read-only
before diagnosing a producer. Never delete the directory or bulk-remove files —
that bypasses the guards and stale checks that only the producer verb or an
atomic notification action honor.
