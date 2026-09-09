---
name: email-manual
description: >
  Internal LingTai mail: send/read/dismiss/reply, bare-path addressing,
  delayed self-send time capsules, and full-body persistent notifications.
  Not internet email (see `mcp-manual`) or recurring schedules (see `shell-manual`).
version: 1.3.0
tags: [capabilities, email, communication]
last_changed_at: "2026-09-09T10:17:00Z"
related_files:
- src/lingtai/tools/email/__init__.py
- src/lingtai/tools/email/_family_schema.py
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/primitives.py
- src/lingtai/tools/email/settings.py
- src/lingtai/adapters/posix/mail.py
- src/lingtai/tools/email/ANATOMY.md
- src/lingtai/tools/email/CONTRACT.md
- src/lingtai/tools/email/manual/reference/addressing-and-replies/SKILL.md
- src/lingtai/tools/email/manual/reference/actions-and-storage/SKILL.md
- src/lingtai/tools/email/manual/reference/notifications-and-delivery/SKILL.md
- src/lingtai/tools/email/manual/reference/settings-reference/SKILL.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# Email Manual — internal mail

**Internal `.lingtai/` mail, not internet email.** Use the schema for routine
calls; this manual owns the exceptions. `action`, action-local `input`, and root
`reasoning` are required. Unknown/cross-action fields fail before mailbox I/O;
optional `null` means omitted. Root `summarize` is not an input field.

## First action

- **Arrival already visible:** reply on Email with `reply`/`reply_all`, then
  `dismiss` the handled ID. Current replies do not clear unread state; see the
  [documented Contract discrepancy](reference/addressing-and-replies/SKILL.md#same-channel-reply).
  Use `read` for source records or attachments rather than rereading visible text.
- **Browse:** `email(action="check", input={}, reasoning="inspect inbox")`.
  Use returned own-mailbox IDs; `search` is regex search. Never send raw local IDs
  as references in mail/public prose.
- **New message:** verify the recipient directory, then `send` with `address`
  and `message`. Ordinary `peer` addressing is bare/path-based, with no `@`;
  `abs` requires an explicitly authorized cross-network target, not guessed routing.
- Address a sender by non-empty `sender_nickname`, else `sender_name`. Reply on
  the arrival channel, not private text output. Read the routing reference before
  an exceptional channel pivot.

A `sent` receipt is scheduling evidence, not recipient acceptance. Do not blindly
retry a bounce or failed call: earlier deliveries may exist. Delayed mail depends
on this process staying alive. Non-self POSIX delivery snapshots attachments;
self-send does not. There is no attachment source-root/size limit: share only
explicitly authorized files. Details below; none of these limits authorizes
configuration changes, lifecycle intervention, or cleanup.

## Routing table

| Need | Read |
|---|---|
| Recipient discovery, `peer`/`abs`, return routes, identity, replies | [Addressing and replies](reference/addressing-and-replies/SKILL.md) |
| Filters/folders, self-send, attachments, storage and retention | [Actions and storage](reference/actions-and-storage/SKILL.md) |
| Liveness, delivery ordering, bounces, unread/overflow handling | [Notifications and delivery](reference/notifications-and-delivery/SKILL.md) |
| SHOW sources, changes, timing and redaction | [Settings reference](reference/settings-reference/SKILL.md) |

## Settings anchors

`settings` takes `{}` and performs no mailbox I/O. Rows contain exactly `key`,
`current`, `default`, `configurable`, `comment`; missing applied truth fails the
whole inventory as `SETTINGS_UNAVAILABLE`, with no partial rows/private detail.
`manual` also takes `{}` and returns the installed body/path without mailbox I/O.
Keep exact IDs/bodies when needed; reserve result summarization for bulky reads,
not short receipts or procedures being followed.

### Send body character limit
`send.body_char_limit`: [body cap](reference/settings-reference/SKILL.md#send-body-character-limit).

### Duplicate send loop guard
`send.duplicate_free_passes`: [recipient/body guard](reference/settings-reference/SKILL.md#duplicate-send-loop-guard).

### Check result token limit
`check.result_token_limit`: [check budget](reference/settings-reference/SKILL.md#check-result-token-limit).

### Unread notification entry limit
`unread.max_entries`: [entry projection](reference/settings-reference/SKILL.md#unread-notification-entry-limit).

### Pseudo-agent subscriptions
`manifest.pseudo_agent_subscriptions`: [redacted applied snapshot](reference/settings-reference/SKILL.md#pseudo-agent-subscriptions).

## Cleanup / Footprint

Email owns `mailbox/` and `.notification/email.json`; keep decision/handoff evidence.
Use the [retention and inspection procedure](reference/actions-and-storage/SKILL.md#cleanup--footprint),
not ad-hoc deletion. Bug filing goes through `lingtai-issue-report` with permission.

## Nested reference catalog

```yaml
- name: email-manual-addressing-and-replies
  location: reference/addressing-and-replies/SKILL.md
  description: Nested Email reference for recipient discovery and replies.
- name: email-manual-actions-and-storage
  location: reference/actions-and-storage/SKILL.md
  description: Nested Email reference for fields, attachments and retention.
- name: email-manual-notifications-and-delivery
  location: reference/notifications-and-delivery/SKILL.md
  description: Nested Email reference for delivery, bounces and unread state.
- name: email-manual-settings-reference
  location: reference/settings-reference/SKILL.md
  description: Nested Email reference for SHOW sources and authorized changes.
```
