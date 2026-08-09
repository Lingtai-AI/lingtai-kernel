---
name: notification-manual
description: >
  Router for LingTai's notification filesystem protocol and the standalone
  `notification` tool. Read it when interpreting `.notification/<channel>.json`
  or deciding between producer-specific handling and safe mirror dismissal.
  Routes channel/sync mechanics and dismissal safety into nested references;
  large-result compaction is owned by
  `context-manual` → `reference/summarize-manual/SKILL.md`.
version: 0.7.0
tags: [lingtai, notifications, channels, dismiss, manual, force, stale, nudge, hooks, whitelist]
last_changed_at: "2026-08-07T00:00:00Z"
related_files:
- src/lingtai/tools/notification/__init__.py
- src/lingtai/tools/notification/schema.py
- src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md
- src/lingtai/intrinsic_skills/notification-manual/reference/dismissal-safety/SKILL.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# Notification Manual — Router

LingTai notifications are a filesystem protocol: producers publish allowlisted
`.notification/<channel>.json` surfaces, and the kernel exposes their current
model-visible state. The always-available `notification` tool is the sole
agent-callable home for reading and clearing those surfaces. `system` has no
notification or dismiss alias, and context hygiene is not a notification
operation either — that is `context(action='summarize')`.

## Quick start

The resident tool schema is the source of truth for the nine actions, their
per-action `input` fields, and the `action` + `input` + `reasoning` envelope
(arguments live inside `input`, never at the root). What it does not say:

- `manual` returns **this router body** — it is documentation retrieval, not a
  notification-state read.
- Optional fields are declared required-but-nullable, and `null` is treated
  exactly like omission. The one trap: `reason: null` does **not** satisfy the
  post-molt acknowledgement requirement.
- After handling a notification, use the narrowest correct dismiss action and end
  the turn; do not voluntarily call `check` again merely to confirm the clear.

## Root `summarize`

Notification is a **short-result** family, so leave the root `summarize` boolean
false — especially for `manual`, where summarizing would drop the exact
procedures and constraints you called it for.

## Installed manual retrieval

`notification(action='manual', input={})` reads only:

```text
<agent>/.library/intrinsic/capabilities/notification-manual/SKILL.md
```

Success returns exactly `status`, `notification_manual`, and `manual_path`. A
missing installed file returns `status: degraded`, an empty
`notification_manual`, the same fixed `manual_path`, and an actionable `error`
naming an initializer or capability-install problem. It never falls back to a
source checkout, and it touches neither notification nor producer state.

## Hooks & whitelist

External hooks deliver notifications through channels that are **not** on the
static allowlist (which covers kernel intrinsics and `mcp.` bridge servers).
Registering a hook is the whitelist gate: only registered hook channels pass
through; everything else is ignored (and, when the kernel observes a blocked
attempt, surfaced as a warn-and-flag system event so the agent can investigate).

### Setup flow

1. **Write the hook script** that polls a source (a file, a service, a remote
   node) and, on an event, publishes `.notification/<channel>.json` with the
   standard envelope (`header`, `icon`, `priority`, `published_at`, `data`,
   optional `instructions`).
2. **Register its manifest** with the notification tool:
   `notification(action='add', input={...})`. `add` validates the manifest,
   appends it to the disk registry (`.notification/hooks.json`), and
   **allowlists the manifest's `channel`** — from then on the channel passes
   the kernel's allow predicate.
3. **Publish** `.notification/<channel>.json` from the hook process. The
   notification now appears in `check` / the meta-block payload like any other
   channel.
4. **Read and dismiss** per the producer's `instructions` / the manifest's
   `description`, using the narrowest correct dismiss action. Dismissing the
   mirror does not touch the hook process; `drop` only revokes the
   registration.

### Manifest fields

- `name` — unique hook identifier (required).
- `channel` — the `.notification/<channel>.json` stem this hook owns
  (required; must be unique across hooks).
- `source` — what the hook polls (required, e.g. `G:`).
- `description` — one-line purpose (required).
- `how_to_modify` / `how_to_cancel` — how the agent updates or stops the hook
  (required; cancellation is the owner's job — `drop` never kills a process).
- `version` — manifest version (optional, defaults to `1.0.0`).
- `instructions` — agent-facing handling guidance (optional).

### drop / edit / list semantics

- `list` — read-only; returns the registered manifests in registry order.
- `edit` — update a manifest's fields by `name`; changing `channel` moves the
  allowlist entry (and is refused with `channel_in_use` if another hook owns
  that channel).
- `drop` — remove the manifest **and revoke its channel** from the allowlist;
  unknown names return `not_found`. `drop` is registration evidence only —
  stopping the hook process follows the manifest's `how_to_cancel`.

### Warn-and-flag

When a channel that is neither statically allowlisted nor registered attempts
notification, the kernel emits one `notification_hook` system event
(`ref_id: blocked_channel:<channel>`) per workdir+channel — deduped until the
channel registers. If you see such an event, run `list` to inspect hooks and
`add` to register the hook if the producer is legitimate.

### Worked example: `comm_watcher`

```text
1. A watcher script polls a G: node (source) for changes.
2. On a change it writes .notification/comm_watcher.json with the standard
   envelope and instructions (e.g. "read the relayed message, then dismiss").
3. The agent (or operator) registers it once:
   notification(action='add', input={
     'name': 'comm_watcher', 'channel': 'comm_watcher', 'source': 'G:',
     'description': 'poll G: node and relay',
     'how_to_modify': 'notification(action=edit, ...)',
     'how_to_cancel': 'stop the watcher process',
     'instructions': 'read the relayed message and dismiss the channel'})
4. The channel is now allowlisted: notifications pass through to check, and
   the agent reads/dismisses per the manifest's instructions.
5. To decommission: notification(action='drop', input={'name': 'comm_watcher'})
   revokes the channel, then stop the watcher process per how_to_cancel.
```

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
| Channel names; `.notification/*.json`; allowlist; `mcp.` channels; envelope fields; `instructions`; nudge/update checks; `_meta.agent_meta.notifications.attention`; voluntary `check`; producer state versus mirror | `reference/channel-model/SKILL.md` |
| External-hook registration; `.notification/hooks.json`; `add`/`drop`/`edit`/`list`; whitelist gate; warn-and-flag on blocked channels | this section (`Hooks & whitelist`) + `reference/channel-model/SKILL.md` (effective allowlist) |
| Which dismiss action; producer-specific handling; guarded/stale mirror; `force`; protected `goal`; post-molt reason; legacy `large_tool_result` event | `reference/dismissal-safety/SKILL.md` |
| Tool-result ranking, digest quality, `context(action='summarize')`, recovery by `tool_call_id`, summarize versus molt | `../context-manual/reference/summarize-manual/SKILL.md` |
| Active goal source-of-truth and cancellation/completion | `../system-manual/reference/goal-manual/SKILL.md` |
| Runtime/kernel update nudges | `../system-manual/reference/runtime-update-checks/SKILL.md` |

## Safety boundaries to keep resident

The producer-verb preference and `force` semantics are resident (meta_guidance
`notification_handling` and the schema's `_FORCE_DESCRIPTION`). The two facts
neither of them states:

- Neither `check` nor `manual` writes notification state.
- `force=true` does **not** override protected source-of-truth channels.

Producer guards exist so that clearing a mirror is never mistaken for handling
the producer's canonical state.
