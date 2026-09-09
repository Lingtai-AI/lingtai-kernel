---
name: notification-manual-dismissal-safety
description: >
  Safe notification dismissal reference: producer-owned state, atomic targets,
  stale versions, force, protected channels, post-molt acknowledgement, hooks,
  and legacy large-result reminders. Read after notification-manual before a
  clear or when one is refused; compaction belongs to summarize-manual.
version: 0.5.0
tags: [lingtai, notifications, dismiss, force, stale, safety, hooks]
last_changed_at: "2026-09-09T05:30:00Z"
related_files:
- src/lingtai/tools/notification/manual/SKILL.md
- src/lingtai/tools/notification/__init__.py
- src/lingtai/tools/notification/schema.py
maintenance: |
  Keep this reference as the sole owner of dismissal safety and refusal recovery;
  update the parent route when its installed path changes.
---

# Notification Dismissal Safety

## Owner first; mirror second

Read the current payload's `instructions`. If the notification mirrors producer-
owned state, call that producer's read/dismiss verb first (for example,
`email(action='read'|'dismiss', ...)`). A generic notification dismiss clears
only the high-attention mirror; it never removes producer history, mailbox state,
goal semantics, or a source queue.

Choose one atomic target:

```text
notification(action='dismiss_channel',
             input={'channel': 'nudge', 'force': null, 'reason': null},
             reasoning='handled the notification')
```

`dismiss_channel` clears one whole mirror and requires `channel`.
`dismiss_event` removes one matching `event_id`; `dismiss_ref` removes matching
`ref_id` event(s). Event/ref calls default to `channel: 'system'`, target
events in the system mirror; `channel: "daemon"` is also supported through the Core aggregate. Other channels refuse event/ref targets. Clearing the final system event removes that mirror, not producer records. The schema is
the exact field source; cross-action fields are rejected before I/O. All three
verbs delegate to the canonical Core policy path.

## Stale versions and `force`

A non-force clear compares the delivered version with the current on-disk
version. If the producer updated the channel after delivery, it refuses with
`reason: 'stale_channel_version'`. Read the newly delivered state before making
a decision. `force=true` is only for a confirmed stale **mirror**, never a routine
retry and never a substitute for handling the producer. A successful clear still
changes the mirror only.

## Protected and acknowledgement-sensitive channels

`goal` is protected source of truth: generic `dismiss_channel` refuses even with
`force=true`; use `../../../system-manual/reference/goal-manual/SKILL.md` to cancel or complete goal state. `post-molt`
requires a non-empty decision reason in the form `continue: ...`, `defer: ...`,
or `obsolete: ...`:

```text
notification(action='dismiss_channel',
             input={'channel': 'post-molt', 'force': null,
                    'reason': 'continue: recovered the pending work'},
             reasoning='acknowledge continuation')
```

A refusal is a route, not a retry: producer guard → producer verb; stale version
→ reread, then decide whether confirmed mirror-only `force` is justified;
protected channel → owning manual; missing post-molt reason → record the real
decision; one system event → use `dismiss_event` or `dismiss_ref`, not a whole
channel clear.

## Hooks and legacy reminders

A registered hook channel uses the same atomic dismissal and producer guards.
Registration widens only this agent's allowlist, never producer-dismiss policy. `drop` removes the manifest and
revokes the channel; it does **not** kill the hook process. Stop it using the
manifest's `how_to_cancel`. If the process keeps publishing after drop, the
kernel may emit the blocked-channel warning again.

New large tool results are not notification events. They belong to
`_meta.agent_meta.agent_state.current_tool_result_chars` and
`context(action='summarize')`; `../../../context-manual/reference/summarize-manual/SKILL.md` owns digest, recovery, and
summarize-versus-molt procedure. A persisted legacy
`source='large_tool_result'` event can be cleared by successful summarization of
the matching `tool_call_id`; if that is impossible, prefer
`dismiss_ref(ref_id='large_tool_result:<tool_call_id>')` over a whole-channel
system clear, which may remove unrelated events. The original result remains in
chat history and `events.jsonl`.

No dismissal mutates producer state. Never bypass these guards by deleting files.
