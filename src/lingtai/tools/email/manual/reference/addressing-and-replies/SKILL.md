---
name: email-manual-addressing-and-replies
description: >
  Focused Email reference for bare-path addressing, peer/abs resolution, safe
  same-channel replies, sender identity display, and local mailbox-ID privacy.
  Read when routing or answering internal mail.
version: 1.0.0
tags: [lingtai, email, routing, replies, privacy]
last_changed_at: "2026-09-09T10:17:00Z"
related_files:
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/email/primitives.py
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/CONTRACT.md
maintenance: |
  Tracks Email addressing, reply routing, identity display, and local-ID privacy; update when the manager or routing contract changes.
---

# Email addressing and replies

## Bare paths and recipient verification

Use a verified recipient working-directory basename or path, not an internet
`@` address. Inspect that directory's `.agent.json` to identify its owner;
`agent_name` is a display identity and need not equal the routing basename.
`human` is the usual operator mailbox; your own address is a self-inbox.
Do not invent targets or broaden a metadata scan to unrelated networks.

## `peer` and `abs`

`mode` belongs to `send` and is normally omitted. `peer` (default) resolves a bare
name against the parent of this working directory. `abs` treats `address` as a
literal absolute working-directory path in another, explicitly authorized
`.lingtai/` network on the same machine. The resolver also accepts paths in peer
mode; that capability is not cross-network authorization. Non-self POSIX delivery
requires manifest presence and Core liveness; human/self exceptions and bounce
limits are in [Notifications and delivery](../notifications-and-delivery/SKILL.md).
`abs` does not bypass those checks.

Absolute sends embed a `_return_route` with sender workdir and identity. Replies
use that route first, then an absolute `from`, then a bare peer `from`. The manager
refuses a bare route that would resolve to the responder's own workdir when the
original identity names a different agent: do not guess; use an explicitly
authorized `abs` send instead. Older messages without `_return_route` retain the
absolute-`from` fallback.

## Same-channel reply

**Email must be answered with `reply` or `reply_all` on Email.** Do not replace it
with a new `send`, Telegram, IM, or text output (text is a private diary). If the
sender is dead and a channel pivot is unavoidable, explain the pivot in the new
message before sending elsewhere.

`reply` addresses the resolved sender. `reply_all` also keeps original `to`/`cc`
recipients except self and the primary target. Both derive `Re: ` unless a subject
is supplied; they do not create thread metadata. Review the complete fan-out
including CC/BCC before delivery; reply actions use the first supplied ID.

**Contract discrepancy:** current `reply` and `reply_all` do not mark the source inbox ID read
or rerender its unread mirror, despite the source Contract's stated invariant.
After handling, call `dismiss` explicitly (or `read` when you need its body).
This describes the observed implementation and workaround, not a relaxation of
the Contract or an engine fix.

## Identity and local IDs

Inbound mail carries an identity card. In prose use non-empty `sender_nickname`,
otherwise `sender_name`; `from` remains a routing address. A mailbox UUID is local
to one working directory: pass IDs copied from this agent's notification or `check`
result to `read`, `dismiss`, `reply`, or `reply_all`, never into mail or public
prose. Refer to another agent's mail by subject, sender, and approximate time.

Internet mail (Gmail, Outlook, IMAP/SMTP), Telegram, Feishu, WeChat, and WhatsApp
belong to their MCP addons. Recurring work belongs to `shell-manual`; do not use
internal Email for external addresses.
