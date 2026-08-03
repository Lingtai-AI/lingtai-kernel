---
name: whatsapp-mcp-manual
description: |
  Progressive-disclosure usage manual for the personal-account WhatsApp MCP tool.
  Read this when you need detail beyond the one-line action descriptions:
  QR-code pairing via get_qr, send vs reply vs react, check/read/search,
  media attachments, contacts/status basics, the notification transient-hook vs
  persistent-context split, external-delivery side-effect caveats, and the
  whatsapp-web.js bridge (ToS/ban-risk) notes. Pulled on demand via
  action='manual'; you do not need to call it before every send.
version: 2.0.0
last_changed_at: "2026-08-03T00:00:00Z"
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/whatsapp/manager.py
- src/lingtai/mcp_servers/whatsapp/server.py
- src/lingtai/mcp_servers/whatsapp/client.py
- src/lingtai/mcp_servers/whatsapp/_family.py
- src/lingtai/mcp_servers/whatsapp/bridge/index.js
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# WhatsApp MCP — usage manual (progressive disclosure)

This client drives a personal WhatsApp account through a local whatsapp-web.js
bridge (QR-code pairing). It does **not** use the Meta Cloud API.

## PAIRING / QR CODE

- First use: call the `get_qr` action. The bridge emits a QR code (data URL)
  as soon as Puppeteer has started.
- Open WhatsApp on the phone: Settings → Linked Devices → Link a Device.
- Scan the QR. The session persists locally in the session directory, so later
  restarts reconnect without a new scan.
- `status` reports `ready`, the paired `me` (wa_id), and whether a QR is
  available.

## BRIDGE PREREQUISITES

- Node.js >= 18 on PATH (or `node_path` in config).
- `npm install` inside the bridge directory (`whatsapp/bridge/`) to fetch
  whatsapp-web.js, Puppeteer, and qrcode.
- First launch downloads/launches Chromium; allow extra time.

## SEND / REPLY / REACT

- `send` requires `to` (or `wa_id`) plus `text` or `media`.
- `reply` requires `message_id`, `to`, and `text`; it quote-replies through
  the bridge.
- `react` requires `message_id` and `emoji`.
- Recipients use international format, digits only (e.g. `15551234567`); the
  bridge converts to `@c.us` automatically. Group ids may pass through with
  their suffix.

## CHECK / READ / SEARCH

- `check` lists recent chats (unread counts + last message).
- `read` returns stored message history for a `wa_id`, or chat list when no
  wa_id is given.
- `search` queries message bodies across recent chats (bounded).

## NOTIFICATIONS

- Inbound messages are pushed to the agent inbox (LICC event) with
  structured context: conversation_ref `whatsapp:<wa_id>`, recent_messages
  (<=10), latest_incoming. `allowed_users` in config filters who may trigger
  inbound pushes.

## SIDE EFFECTS / RISK

- Sending, replying, reacting, and media delivery reach real WhatsApp users;
  confirm before unsolicited sends.
- whatsapp-web.js is unofficial and violates WhatsApp ToS; account bans are
  possible. Use for personal/experimental purposes only, respond mostly to
  inbound, and do not send automated bulk messages.
- Errors are returned as `{'status':'error','error':...,'error_type':...}`.
