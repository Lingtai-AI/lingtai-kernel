---
name: whatsapp-mcp-manual
description: |
  Progressive-disclosure usage manual for the WhatsApp Cloud API MCP tool. Read
  this when you need detail beyond the one-line action descriptions: the 24-hour
  customer-service window and approved templates, send vs reply vs react,
  check/read/search, media attachments, contacts/accounts/status basics, and
  external-delivery side-effect caveats. Pulled on demand via action='manual'; you
  do not need to call it before every send.
version: 1.1.0
last_changed_at: "2026-07-06T00:00:00-07:00"
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
  These are convenience aliases only; they do NOT grant inbound permission.
- `accounts`: list configured WhatsApp accounts (redacted).
- `status`: connection/health status for an account. `allowed_wa_ids_count` (when
  present) means inbound sender filtering is active on that account.

## INBOUND SENDER FILTER

- Inbound is optionally restricted by the operator-configured
  `accounts[].allowed_wa_ids` allow-list. A business number is publicly
  reachable, so the Meta signature only proves Meta delivered the event, not that
  the sender is trusted. When the list is set, messages from non-listed wa_ids are
  dropped before storage/notification/wake; when absent, all senders are accepted.
- The allow-list lives in operator-controlled config, not `contacts`. Do not tell
  a human to use `add_contact` to let someone reach the agent — that only saves an
  alias; add the wa_id to `allowed_wa_ids` in config and refresh/restart the MCP.
  This mirrors Telegram's `allowed_users` and WeChat's `allowed_users`.

## SIDE EFFECTS & ERROR SURFACING

- `send`, `reply`, and `react` deliver to real users — external side effects.
  Confirm recipient and content before sending unsolicited messages, and respect
  the 24-hour window rule above.
- Actions return `{'status': 'ok', ...}` on success or `{'status': 'error',
  'error': <message>, 'error_type': ...}` on failure (e.g. missing `to`, invalid
  template, outside-window free text). Check the status and surface or act on
  errors rather than assuming delivery.
