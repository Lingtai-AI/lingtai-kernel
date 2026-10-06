---
name: email-manual-addressing-and-replies
description: >
  Focused Email reference for absolute-path addressing, reply return routes,
  legacy mail without a route, safe same-channel replies, sender identity
  display, and local mailbox-ID privacy. Read when routing or answering internal
  mail.
version: 1.1.0
tags: [lingtai, email, routing, replies, privacy]
last_changed_at: "2026-10-06T00:00:00Z"
related_files:
- src/lingtai/tools/email/manual/SKILL.md
- src/lingtai/tools/email/primitives.py
- src/lingtai/tools/email/manager.py
- src/lingtai/tools/email/CONTRACT.md
maintenance: |
  Tracks Email addressing, reply routing, identity display, and local-ID privacy; update when the manager or routing contract changes.
---

# Email addressing and replies

## Addresses

Every address in `send` (`address`, `cc`, `bcc`) is an absolute agent-workdir
path, such as the `.lingtai/<name>` directory of the recipient. Bare names,
`@` addresses, and relative paths are refused before anything is written.
Verify the recipient first: its `.agent.json` must exist, and a non-human
recipient must have a fresh heartbeat. A directory whose manifest is valid with
`admin` null is the human mailbox and counts as live. Do not invent a target or
infer one from a sibling directory, a bare `from` name, or a name in a message
body. Non-self delivery and bounce limits are in
[Notifications and delivery](../notifications-and-delivery/SKILL.md).

## Replies and return routes

`reply` and `reply_all` answer the sender's absolute address, resolved in this
order:

1. the message's top-level `_return_route.address`, when it is an absolute path.
   The route object is exactly `{"address": "<absolute sender workdir>",
   "sender_agent_id": "<sender id, or empty>"}`. Every `send` writes it, and the
   TUI writes it on human mail;
2. otherwise the message's `from`, when that is itself an absolute path;
3. otherwise the reply is refused with `Reply target is not an absolute address`.

There is no bare-name fallback. A bare `from` (for example `human` or a peer
name) is never resolved against your own directory or a sibling, so a reply
cannot self-deliver or reach another network by accident.

`reply_all` also copies the original `to` and `cc` entries, except your own absolute address,
the primary target, and the original `from` value. Those entries must be absolute paths too, so a bare name
there is refused by `send`. For mail addressed by bare name, use `reply`, or
send to verified absolute addresses instead.

## Legacy mail without a route

Mail written before routes existed has neither `_return_route` nor an absolute
`from`, so `reply` refuses it. The refusal is correct; do not work around it by
guessing. To answer such mail, send a new message with `send` to an absolute
address for the same sender that you have verified, and say briefly in the body
why it is a new message. Take the address from a source you can name:

- the absolute `_return_route.address` of another message from that sender;
- an absolute address in your contacts, or one the operator gave you;
- a path the operator has confirmed.

Before sending, check that the target's `.agent.json` matches the original
sender's identity card. Never build the address from a bare name or a sibling
path.

## Same-channel reply

**Answer email on Email.** When `reply` or `reply_all` can resolve a route, use
it. Do not answer with a new `send`, Telegram, IM, or text output (text is a
private diary). The one exception is legacy mail above: when `reply` refuses for
lack of an absolute route, a `send` on Email to a verified absolute address of
the same sender is the same-channel answer, and the body should explain why. If
the sender is dead and a channel pivot is unavoidable, explain the pivot in the
new message before sending elsewhere.

`reply` addresses the resolved sender. Both `reply` and `reply_all` derive
`Re: ` unless a subject is supplied; they do not create thread metadata. Review
the complete fan-out including CC/BCC before delivery; reply actions use the
first supplied ID.

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
