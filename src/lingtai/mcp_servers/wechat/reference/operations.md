---
name: wechat-operations-reference
description: |
  WeChat operation details for exact recipient/message IDs, bounded reads, result
  meanings, and replay-safe handling of provider acceptance. Load from the main
  WeChat manual for external operations or recovery.
version: 1.1.0
last_changed_at: "2026-09-09T03:15:00Z"
related_files:
- src/lingtai/mcp_servers/wechat/SKILL.md
- src/lingtai/mcp_servers/wechat/manager.py
- src/lingtai/mcp_servers/wechat/_family.py
- src/lingtai/mcp_servers/wechat/api.py
- tests/test_wechat_toolfamily_ltpv2.py
- tests/test_wechat_history_index.py
- tests/test_wechat_reply_read_state.py
- tests/test_wechat_inbound_replay.py
maintenance: |
  Tracks WeChat action/result semantics and replay-safe operation guidance; update
  when dispatch, history views, acknowledgement, or local side effects change.
---

# WeChat operations

The parent [`SKILL.md`](../SKILL.md) owns the closed envelope and first-action route.
This reference adds the operation semantics that matter after discovery.

## Send and reply

- `send` starts a new message for the supplied exact `user_id`; it requires `text`,
  `media_path`, or both.
- `reply` takes an inbound `message_id` from `read`, resolves its original sender,
  and sends `text`. Missing message or sender is an error, never a fresh send.
- A successful reply marks its target inbound message read. A failed send does not.
- Provider acceptance is not delivery confirmation: successful results keep
  `delivery_confirmed: false`. Do not replay an accepted request automatically.

## Check, read, and search

- `check` returns conversation aggregates with `user_id`, optional alias, total and
  inbound-unread counts, latest preview, and date. Outgoing records add context but
  are not unread.
- `read` requires `user_id`; optional `limit` defaults to 10. It returns the newest
  bounded view merged from inbox and sent records, labels outgoing records, and
  marks returned inbound records read.
- `search` requires a regular-expression `query`, optionally filtered by `user_id`;
  it searches inbox bodies and returns at most 20 matches. Invalid regex is an error.
- After refresh, worker failure, or recovery, read the merged history before
  replying. A preview or absent search match does not prove that a reply is new or
  absent.

## Contacts and account view

`contacts` lists local aliases. `add_contact` persists an alias for a `user_id` and
`remove_contact` accepts an alias or user ID; neither changes or proves a remote
contact relationship. `accounts` reports configured account metadata and does not
authorize credential changes. Treat IDs and paths as sensitive metadata.

## Results and replay

Failures return an `error` (for example, missing IDs, unknown message, invalid regex,
or unreadable file); surface it. Send acknowledgement accepts missing/null `ret` or
non-boolean integer zero only when `errcode` is absent or non-boolean integer
zero. Strings, floats, booleans and an explicit null `errcode` fail. An empty,
malformed or non-object response also fails.

Text-plus-media can return `status: partial` with `partial_delivery`, precise
provider-acceptance fields, and `automatic_retry_allowed: false`: text may already
be accepted while media failed. Reconcile before a human-authorized new action.

Not every uncertain outcome has these fields. If a later text chunk fails, file
reading fails after text, or persistence fails after acceptance, the error may
lack partial fields and a sent record. A missing sent record is not evidence of
zero acceptance. Stop and reconcile with the recipient/provider before a newly
authorized attempt; do not automatically replay the whole request.

The manager's cursor/signature guard suppresses normally repeated inbound landings;
it is not permission to send twice. Reply at most once per inbound `message_id`.
