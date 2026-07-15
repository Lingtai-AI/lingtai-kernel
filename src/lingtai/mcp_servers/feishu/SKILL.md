---
name: feishu-mcp-manual
description: |
  Progressive-disclosure usage manual for the Feishu (Lark) MCP tool. Read this
  when you need detail beyond the one-line action descriptions: receive_id vs
  receive_id_type (open_id/chat_id), send vs reply, check/read/search, placeholder
  + edit for long responses, contacts/accounts basics, the notification
  transient-hook vs persistent-context split, and side-effect caveats.
  Pulled on demand via action='manual'; you do not need to call it before every
  send.
version: 1.1.0
last_changed_at: "2026-07-06T00:00:00-07:00"
related_files:
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/service.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# Feishu (Lark) MCP — usage manual (progressive disclosure)

This manual is pulled on demand via `action='manual'` so the per-action tool
schema can stay concise. Read it when you need detail beyond the one-line action
descriptions; you do not need to call it before every send.

## RECIPIENTS: receive_id / receive_id_type

- `send` targets a recipient by `receive_id` plus `receive_id_type`. Use
  `receive_id_type='open_id'` for an individual user (`ou_xxx`) and
  `receive_id_type='chat_id'` for a group chat (`oc_xxx`). `receive_id_type`
  defaults to `open_id` when omitted.
- `email`, `user_id`, and `union_id` are also accepted as `receive_id_type`
  values when you only have that identifier for a user.

## SEND vs REPLY

- `reply` (`message_id` from read/check results, `text`) threads your response to
  a specific incoming message; prefer it when answering a particular message.
- `send` (`receive_id`, `receive_id_type`, `text`) starts a fresh message; use it
  for unsolicited or standalone messages.

## READING: check / read / search

- `check`: list recent conversations with unread counts (optional `account`).
- `read`: read messages from one chat (`chat_id`; optional `limit`, `account`).
- `search`: regex search over inbox messages (`query`; optional `account`,
  `chat_id`).

## PLACEHOLDER / PROGRESS

- For responses that take more than ~5s, send `action='send'` with
  `placeholder=true` and your interim text. This returns a compound `message_id`.
- Update it later with `action='edit'`, `message_id=<that id>`, `text=<final>`
  instead of sending a second message, so the user sees one evolving reply.

## CONTACTS / ACCOUNTS

- `contacts`: list saved contacts (optional `account`).
- `add_contact`: save a contact alias (`open_id`, `alias`; optional `name`,
  `chat_id`). Saving an alias does not grant inbound permission on its own.
- `remove_contact`: remove a contact (`alias` or `open_id`).
- `accounts`: list configured app accounts.

## MESSAGE IDS

- `message_id` is the compound id returned by read/check
  (`{alias}:{chat_id}:{feishu_message_id}`); pass it back verbatim to
  `reply`/`edit`/`delete`.

## NOTIFICATIONS: TRANSIENT HOOK vs PERSISTENT CONTEXT

- Inbound Feishu messages surface to the agent in two `_meta` lanes:
  - `_meta.agent_meta.notifications.attention.mcp.feishu` is a compact high-attention hook only —
    `data.message_ids` (compound `{alias}:{chat_id}:{feishu_message_id}` ids)
    and dismiss guidance, never message text or routing context.
  - `_meta.agent_meta.notifications.persistent.mcp.feishu` carries durable context:
    recent conversation messages (bounded text, both directions), sender/chat
    routing hooks, reply refs when present, and per-message comments for the
    agent's own outgoing messages or truncated text.
- The feishu tool remains the source of truth. Neither lane marks anything
  read; use `read`/`check` for exact producer state, especially when a
  persistent message is truncated.
- Reply in Feishu when the message arrived through Feishu (`reply` with the
  compound message id, or `send` to the chat/open_id).
- After handling, dismiss the transient hook via
  `notification.dismiss_channel("mcp.feishu")`; the persistent block is
  context history, not unread state.

## SIDE EFFECTS & ERROR SURFACING

- `send`, `reply`, and `edit` deliver to real users — they are external
  side effects, so confirm recipient and content before sending unsolicited
  messages.
- Actions return a result dict on success or `{'error': <message>}` on failure
  (e.g. missing `receive_id`, bad `message_id`). Check for the `'error'` key and
  surface or act on it rather than assuming delivery.
