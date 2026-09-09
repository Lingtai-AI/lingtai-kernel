---
name: email-manual-notifications-and-delivery
description: >
  Focused Email reference for daemon delivery, heartbeat/liveness, bounce
  recovery, unread payloads, persistent bodies, overflow, and read-state refresh.
  Read when delivery or notices need diagnosis.
version: 1.0.0
tags: [lingtai, email, notifications, delivery, recovery]
last_changed_at: "2026-09-09T10:17:00Z"
related_files:
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/email/manual/reference/actions-and-storage/SKILL.md
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/primitives.py
- src/lingtai/adapters/posix/mail.py
- src/lingtai/tools/email/CONTRACT.md
maintenance: |
  Tracks Email delivery, unread notification projection, bounce recovery, and read-state mirror semantics; update when producer or mail-adapter behavior changes.
---

# Email notifications and delivery

## Delivery, liveness, and recovery

`send` writes each recipient's outbox entry, then starts that recipient's daemon
`_mailman` thread before that call's unified sent record is written. Even `delay=0`
can return `sent` before delivery finishes. A later thread-start or persistence
failure can leave earlier deliveries; neither a failed call nor a missing sent
record proves zero effect. Delay waits in a daemon thread: this is not a restart-resilient queue.
A process exit can strand outbox files; no automatic replay is promised.

Non-self POSIX targets need `.agent.json` presence and a Core-fresh
`.agent.heartbeat`; use current kernel liveness policy, not a fixed Email timeout.
Core currently counts even malformed manifests as present; a valid manifest with
missing/null `admin` identifies a human and bypasses heartbeat checks. This is
implementation truth, not permission to deliver to an unverified owner. Self-send
bypasses this transport handshake. Refresh/relaunch windows can produce
`not running`; refused attempts are not queued for a later retry. Failures publish
`email.bounce` in `.notification/system.json`.

A process may appear in `ps` before a fresh heartbeat. If a CPR attempt exits
because the duplicate-process guard sees the existing same-workdir process, do not
stack CPR attempts: wait for a heartbeat. Any lifecycle intervention needs its
own authorization and diagnosis, not just a bounce. An authorized non-self `abs`
target has the same handshake.

For a known POSIX pre-publication refusal (unknown/dead recipient or rejected
attachment), no entry was published for that target. A generic bounce is not a
universal zero-effect receipt: custom adapters or post-publication exceptions can
fail after an effect, and other fan-out targets may already have received mail.
Inspect the exact failure and recipient state; retry once only when a safe
pre-delivery refusal is established and the cause is corrected. Never guess a
replacement address or replay the whole fan-out blindly.

## Unread producer mirror

Arrival and every read-state mutation render the current unread set to
`.notification/email.json`; the next heartbeat exposes it through
`notification(action="check")`. Its relevant shape is:

```json
{"instructions":"handle, then prefer email.dismiss or email.read/reply",
 "data":{"count":3,"newest_received_at":"<time>",
         "email_ids":["<local-id>"],
         "emails":[{"id":"<local-id>","from":"peer",
                    "subject":"...","message":"full body",
                    "message_chars":10,"message_truncated":false}]}}
```

The attention lane carries IDs; full structured entries live in
`_meta.agent_meta.notifications.persistent.email`. New bodies are not truncated.
The send layer rejects bodies over 50,000 characters; only legacy records may be
marked `message_truncated=true`. The projection limits newest entries (see
`unread.max_entries`) but preserves total unread count.

## Handling and overflow

Persistent context can make `read` unnecessary for ordinary text. After handling a
visible entry, call `dismiss` to mark it read without returning another body. Use
`read` for source metadata, attachments, or deliberate refresh. `read`, `dismiss`,
`archive`, and `delete` rerender the Email mirror, which disappears when no unread
mail remains. Replies currently leave the source unread; see the
[Contract discrepancy and explicit-dismiss workaround](../addressing-and-replies/SKILL.md#same-channel-reply).

If persistent context has an `overflow` marker, follow its local spill file or use
the Email producer action; do not assume the body is complete. Generic
`notification(action="dismiss_channel", input={"channel":"email", ...})` only
clears the mirror: it does not mark source messages read and cannot replace Email
`dismiss`, `read`, `archive`, or `delete`.
