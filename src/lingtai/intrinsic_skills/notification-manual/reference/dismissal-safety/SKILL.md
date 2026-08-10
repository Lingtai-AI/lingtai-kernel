---
name: notification-manual-dismissal-safety
description: >
  Nested notification-manual reference for choosing safe atomic notification
  dismissal, producer-specific verbs, stale-version and force behavior,
  protected channels, post-molt acknowledgement, and legacy large_tool_result
  reminder escape hatches. Read after notification-manual before clearing a
  channel or diagnosing a dismissal refusal; summarization mechanics live in
  summarize-manual instead.
version: 0.4.0
tags: [lingtai, notifications, dismiss, force, stale, safety, hooks]
last_changed_at: "2026-07-27T00:00:00Z"
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
state (e.g. `email(action='read'|'dismiss', ...)`) — a generic channel dismissal
would clear only the high-attention mirror.

For a dismissible notification-owned surface, choose one atomic target:

```text
notification(action='dismiss_channel',
             input={'channel': 'nudge', 'force': null, 'reason': null},
             reasoning='...')
```

`dismiss_event` and `dismiss_ref` take the same envelope with `event_id` /
`ref_id` instead of `channel`; the schema is the exact-shape source of truth.
What it does not say: those two **default to the `system` channel** and remove
only matching entries from `system.data.events`, and **removing the final event
clears the file**.

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

`force=true` (semantics in the schema) is for a confirmed stale mirror — not a
routine retry, and not a substitute for handling the producer.

## Protected and acknowledgement-sensitive channels

`goal` is protected source of truth. A generic
`notification(action='dismiss_channel', input={'channel': 'goal', ...})`
refuses even with `force=true`; use `../../../system-manual/reference/goal-manual/SKILL.md` to cancel or complete active goal
state correctly.

The kernel-owned `post-molt` continuation channel requires a non-empty reason
recording the decision in `continue|defer|obsolete` form — e.g.
`reason='continue: recovered the pending work'` on
`dismiss_channel(channel='post-molt')`.

## Hook channels and producer-guard interplay

A registered hook channel (see the parent manual's `Hooks & whitelist`
section) is dismissed exactly like any other allowlisted channel: the atomic
dismiss actions clear only the `.notification/<channel>.json` mirror. Hook
registration widens the **allowlist** for the registering agent's workdir
(hook channels are per-agent, not process-global), not the dismissal policy —
a hook producer whose notification mirrors canonical state should still
register a generic-dismiss guard and teach its producer-specific verb in
`instructions`, and the guarded refusal still applies.

`notification(action='drop', input={'name': ...})` removes the hook's manifest
and revokes its channel from the allowlist; it does **not** kill the hook
process. Stopping the hook is the owner's job, documented in the manifest's
`how_to_cancel` field. After a drop, an unregistered channel's notifications
stop passing through and the kernel's warn-and-flag event may reappear if the
process keeps publishing — the blocked-channel warning is cleared when a
channel registers, so a later re-block can warn again. The warn-and-flag scan
only flags present stems that can become channels (skipping kernel-private
dotfiles like `.nudge_state.json`, non-`.json` entries, and syntactically
invalid stems), so an unregistered file that could never be a channel does not
produce a spurious "register this hook" event.

## Large results and legacy reminder escape hatch

New large tool results are not notification events — they are ranked under
`_meta.agent_meta.agent_state.current_tool_result_chars`, and
`../../../context-manual/reference/summarize-manual/SKILL.md` owns the digest,
`context(action='summarize')`, recovery, and summarize-versus-molt procedure.

A persisted or pre-molt `source='large_tool_result'` system event may still
exist. A successful summarize of the matching `tool_call_id` clears it. If
summarization is no longer possible, use
`dismiss_ref` with `ref_id='large_tool_result:<tool_call_id>'`. Whole-channel
system dismissal also covers it but may clear unrelated system events, so prefer
ref/event targeting. Either way the original tool result stays unchanged in chat
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

No dismissal ever removes producer history, mailbox state, or goal semantics.
