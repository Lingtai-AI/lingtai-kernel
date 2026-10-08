---
name: notification-manual-dismissal-safety
description: >
  One-shot notification delivery and producer-state safety reference: there is no
  public notification dismiss action; delivery never clears producer state; how
  deliberate check, post-molt, protected channels, hooks, and legacy large-result
  reminders behave. Read after notification-manual when unsure whether a
  notification will reappear or how to resolve its producer state.
version: 0.6.0
tags: [lingtai, notifications, one-shot, delivery, safety, hooks]
last_changed_at: "2026-10-02T00:00:00Z"
related_files:
- src/lingtai/tools/notification/manual/SKILL.md
- src/lingtai/tools/notification/__init__.py
- src/lingtai/tools/notification/schema.py
maintenance: |
  Keep this reference as the sole owner of delivery-versus-producer-state safety;
  update the parent route when its installed path changes. The directory name is
  a retained installed route from when notification had public dismiss actions.
---

# Notification Delivery and Producer-State Safety

## Delivered once; never cleared by delivery

Each notification event is attached to your context automatically once. A new or
changed event (new mail, a new IM message, a new system event, a daemon
completion, a delay alarm, a post-molt record) is attached and wakes you again; an
unchanged one is not re-attached, and later tool results simply carry no
notification keys for it. An absent notification key means *nothing new*, not
*resolved*.

Delivery never clears, completes, or acknowledges anything: notification files
and producer business state (mailbox, IM history, goal, source queues) are left
exactly as they were. There is no public `dismiss_channel`, `dismiss_event`, or
`dismiss_ref` action and no alias for them. Do not delete `.notification/` files.

`notification(action='check', input={}, ...)` is a deliberate read: it returns the
complete current mirrors even if they were already delivered automatically, and
it clears nothing. It is not an automatic replay.

Earlier legitimately delivered messages remain usable for active work after later empty tails; do not revive superseded human instructions or older runtime warnings. Ordinary molt/rebuild/resync retains the delivered ledger within this agent process. Carry needed task facts/IDs into the handoff; use a deliberate check/source read only for genuinely missing content or separately requested review. A new Agent/process restart starts fresh delivery bookkeeping; this is not cross-crash exactly-once.

## Owner first

Read the delivered payload's `instructions`, then act through the owning producer
tool, whose state is the source of truth (for example
`email(action='read'|'reply'|'dismiss', ...)`; those verbs are the producer's own
business operations and keep working unchanged). The notification record for an
event may still exist on disk afterward; that does not re-deliver it.

## Protected and special channels

`goal` is protected source of truth: use
`../../../system-manual/reference/goal-manual/SKILL.md` to cancel or complete goal
state. `post-molt` is a continuation reminder delivered once per molt: reorient
(pad, latest summary, session journal, recent human messages), decide to continue,
defer, or treat as obsolete, and record that decision in your own journal/pad; the
delivery itself does not mean any human task is completed.

## Hooks and legacy reminders

A registered hook channel follows the same one-shot delivery. Registration widens
only this agent's allowlist. `drop` removes the manifest and revokes the channel;
it does **not** kill the hook process. Stop it using the manifest's
`how_to_cancel`. If the process keeps publishing after drop, the kernel may emit
the blocked-channel warning again.

New large tool results are not notification events. They belong to
`current_tool_result_chars` (read via `system(action='meta')`) and
`context(action='summarize')`; `../../../context-manual/reference/summarize-manual/SKILL.md` owns digest, recovery, and
summarize-versus-molt procedure. A persisted legacy
`source='large_tool_result'` event is resolved by successful summarization of the
matching `tool_call_id`. The original result remains in chat history and
`events.jsonl`.
