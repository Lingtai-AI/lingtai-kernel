---
name: whatsapp-mcp-manual
description: |
  Progressive-disclosure usage manual for the WhatsApp Cloud API MCP tool. Read
  this when you need detail beyond the one-line action descriptions: the 24-hour
  customer-service window and approved templates, send vs reply vs react,
  check/read/search, media attachments, contacts/accounts/status basics, the
  notification transient-hook vs persistent-context split, and
  external-delivery side-effect caveats. Pulled on demand via action='manual'; you
  do not need to call it before every send.
version: 1.1.0
last_changed_at: "2026-07-06T00:00:00-07:00"
related_files:
- src/lingtai/mcp_servers/whatsapp/manager.py
- src/lingtai/mcp_servers/whatsapp/server.py
- src/lingtai/mcp_servers/whatsapp/client.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# WhatsApp MCP — usage manual (progressive disclosure)

This manual is pulled on demand via `action='manual'` so the per-action tool
schema can stay concise. Read it when you need detail beyond the one-line action
descriptions; you do not need to call it before every send.

This client uses the official Meta WhatsApp Cloud API only (no WhatsApp Web
bridge).

## 24-HOUR WINDOW / TEMPLATES

- WhatsApp Cloud API allows free-form business replies only inside the 24-hour
  customer-service window (24h since the user's last message). Outside that
  window you must send an approved message `template`, not free text.
- `templates`: list approved message templates. Use a template's `name` +
  `language.code` to send outside the window.

## RECIPIENTS

- Messages target a recipient by `to` (or `wa_id`) — the WhatsApp `wa_id`. Use
  ids returned by `check`/`read`/`contacts`.

## SEND / REPLY / REACT

- `send` (`to`/`wa_id`, plus `text`, `media`, or `template`) starts a message.
  `media` is an object with `type` (image/document/audio/video) and the media
  fields; `template` is an object requiring `name` and `language.code`.
- `reply` threads to a specific message (`message_id`, then `text`/`media`/
  `template`). `message_id` is the compound `account:wa_id:wamid` id.
- `react` adds an emoji reaction to a message (`message_id`, `emoji`).
- For text sends, `preview_url=true` enables link previews.

## READING: check / read / search

- `check`: list recent conversations.
- `read`: read messages from one conversation (`wa_id`; optional `limit`,
  `mark_read`).
- `search`: regex search over message text (`query`).

## CONTACTS / ACCOUNTS / STATUS

- `contacts`: list saved contacts. `add_contact`/`remove_contact` manage aliases.
- `accounts`: list configured WhatsApp accounts (redacted).
- `status`: connection/health status for an account.

## NOTIFICATIONS: TRANSIENT HOOK vs PERSISTENT CONTEXT

- Inbound WhatsApp messages surface to the agent in two `_meta` lanes:
  - `_meta.agent_meta.notifications.attention.mcp.whatsapp` is a compact high-attention hook only —
    `data.message_ids` (compound `account:wa_id:wamid` ids) and dismiss
    guidance, never message text or routing context.
  - `_meta.agent_meta.notifications.persistent.mcp.whatsapp` carries the durable context:
    recent conversation messages (bounded text, both directions), sender/chat
    routing hooks, and per-message comments for the agent's own outgoing
    messages, truncated text, and non-text/media messages.
- The whatsapp tool remains the source of truth. Neither lane marks anything
  read; use `read`/`check` for exact producer state, especially when a
  persistent message is truncated or is a media placeholder.
- Reply on WhatsApp when the message arrived through WhatsApp (`reply` with the
  compound message id, or `send`), respecting the 24-hour window rule above.
- After handling, dismiss the transient hook via
  `notification.dismiss_channel("mcp.whatsapp")`; the persistent block is
  context history, not unread state — do not treat its presence as a pending
  event.

## SIDE EFFECTS & ERROR SURFACING

- `send`, `reply`, and `react` deliver to real users — external side effects.
  Confirm recipient and content before sending unsolicited messages, and respect
  the 24-hour window rule above.
- Actions return `{'status': 'ok', ...}` on success or `{'status': 'error',
  'error': <message>, 'error_type': ...}` on failure (e.g. missing `to`, invalid
  template, outside-window free text). Check the status and surface or act on
  errors rather than assuming delivery.
