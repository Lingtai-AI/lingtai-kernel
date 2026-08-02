---
name: feishu-mcp-manual
description: |
  Progressive-disclosure usage manual for the Feishu (Lark) MCP tool. Read this
  when you need detail beyond the one-line action descriptions: receive_id vs
  receive_id_type (open_id/chat_id), send vs reply, check/read/search, placeholder
  + edit for long responses, contacts/accounts basics, the notification
  transient-hook vs persistent-context split, normalized inbound conversations,
  preserved inbound media, group @Bot routing, and side-effect caveats.
  Pulled on demand via action='manual'; you do not need to call it before every
  send.
version: 1.5.0
last_changed_at: 2026-08-02T00:00:00Z
related_files:
- src/lingtai/mcp_servers/feishu/account.py
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/service.py
- src/lingtai/mcp_servers/feishu/_family.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# Feishu (Lark) MCP — usage manual (progressive disclosure)

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

## INBOUND CONVERSATIONS

- Direct messages are admitted without an `@Bot` mention. Group and topic
  messages are admitted only when they explicitly mention this bot; `@all`
  alone does not wake it.
- `allowed_users`, when configured for an account, still filters the sender's
  `open_id` in both direct and group chats. Saving a contact does not change
  this admission rule.
- `read` preserves the legacy fields and adds `thread_id`, `root_id`,
  `reply_to`, resolved `mentions`, the SDK-normalized `content` union, normalized
  sender identity fields, downloaded `attachments`, and the complete raw event
  under `feishu`.
- Image, file, audio, video, sticker, video-cover, and rich-post resources are
  stored under the message's `attachments/` directory. Each attachment keeps
  its Feishu `type` and `file_key`; `status='downloaded'` adds the safe local
  `filename`, absolute `path`, and byte `size`, while `status='failed'` keeps
  the original descriptor plus a bounded `error` instead of discarding it.
- Audio messages continue through local Whisper transcription after download.
  A successful transcript remains in `voice_transcript` and becomes the message
  text. Download or transcription failure stays attached to the resource
  record, while the normalized content/raw envelope remain available for
  diagnosis; failure is not collapsed into a text-only message.
- For group commands, the normalized `text` removes this bot's own mention;
  other resolved mentions remain visible. `content.kind` identifies the
  original Feishu content family.
- Topic/thread routing metadata is observational in this slice. Thread-aware
  outbound reply behavior is introduced with rich outbound content; until then,
  pass the compound message ID to `reply` as before.

## NOTIFICATIONS: TRANSIENT HOOK vs PERSISTENT CONTEXT

Inbound Feishu messages surface to the agent in two `_meta` lanes:

- `_meta.agent_meta.notifications.attention.mcp.feishu` — a compact
  high-attention hook only: `data.message_ids` and dismiss guidance, never
  message text or routing context.
- `_meta.agent_meta.notifications.persistent.mcp.feishu` — durable context:
  recent conversation messages (bounded text, both directions), sender/chat
  routing hooks, reply refs when present, and per-message comments for the
  agent's own outgoing messages or truncated text.

The feishu tool remains the source of truth: neither lane marks anything read,
so use `read`/`check` for exact producer state — especially when a persistent
message is truncated. Reply in Feishu when the message arrived through Feishu
(`reply` with the compound message id, or `send` to the chat/open_id). After
handling, dismiss the transient hook with
`notification.dismiss_channel("mcp.feishu")`; the persistent block is context
history, not unread state. Generic mirror-vs-canonical-state and dismiss-safety
rules live in
[`notification-manual`](../../intrinsic_skills/notification-manual/SKILL.md).

## SIDE EFFECTS & ERROR SURFACING

- `send`, `reply`, and `edit` deliver to real users — they are external
  side effects, so confirm recipient and content before sending unsolicited
  messages.
- Actions return a result dict on success or `{'error': <message>}` on failure
  (e.g. missing `receive_id`, bad `message_id`). Check for the `'error'` key and
  surface or act on it rather than assuming delivery.

## PUBLIC TOOL FAMILY: strict LTP-v2

Raw MCP discovery exposes exactly one public tool, `feishu`. It is an
independent strict LTP-v2 family with the closed root
`{action, input, reasoning, summarize?}` (`action`, `input`, and `reasoning`
required) and a closed action-owned input branch. `feishu` actions are exactly
`send`, `check`, `read`, `reply`, `search`, `delete`, `edit`, `contacts`,
`add_contact`, `remove_contact`, `accounts`, and `manual`. The `manual` action
is the discovery path for this packaged doc. Do not use the retired flat/legacy
shape, `_reasoning`, aliases, or a generic dispatcher.
