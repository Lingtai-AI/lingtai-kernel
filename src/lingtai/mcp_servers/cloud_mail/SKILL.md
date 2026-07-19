---
name: cloud-mail-mcp-manual
description: |
  Progressive-disclosure usage manual for the Cloud Mail REST email MCP tool.
  Read this when you need detail beyond the one-line action descriptions:
  check/search filters, the compound id (account:emailId) for read, send (needs
  user credentials), plain vs HTML bodies, accounts/add_user basics, and the
  external-email side-effect caveats. Pulled on demand via action='manual'; you
  do not need to call it before every send.
version: 1.0.0
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/mcp_servers/cloud_mail/manager.py
- src/lingtai/mcp_servers/cloud_mail/server.py
- src/lingtai/mcp_servers/cloud_mail/client.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# Cloud Mail MCP — usage manual (progressive disclosure)

Inbound mail also arrives automatically in your inbox via per-account polling,
so you don't have to poll `check` yourself.

Setup, config file/schema, credential and auth model, and watermark state are
owned by the `mcp-manual` skill (`reference/curated-addons.md`, §Cloud Mail
setup). Read it before editing config; do not guess field names.

## EMAIL IDS

- `read` fetches the full content of one email by compound id
  `id='<account>:<emailId>'`, or by `account` plus a numeric `email_id`. Use the
  ids returned by `check`/`search`; do not construct them by hand.

## READING: check / search

- `check`: list recent inbound emails (optional `limit`/`n`, plus the same
  filters as search).
- `search`: filter the public email list by `to_email`, `send_email`,
  `send_name`, `subject`, `content`, `time_sort` (`asc`/`desc`), and paginate
  with `num`/`size`. Filters are LIKE matches.

## SEND

- `send` requires user credentials in config (it logs in, then posts to
  `/email/send`). Provide `address` (recipient or list), and a body via
  `message`/`text` (plain) and/or `html`/`content_html` (HTML). Optional
  `subject`, `name` (sender display name), `send_account_id` (override sender).
- Attachments are NOT supported in this first pass.

## ACCOUNTS / ADD_USER

- `accounts`: redacted per-account status (no tokens/passwords).
- `add_user`: create a Cloud Mail user (`email`, `password`; optional
  `role_name`). Admin operation — use deliberately.

## SIDE EFFECTS & SAFETY

- `send` delivers real email to real recipients — an external, hard-to-undo side
  effect. Confirm the recipient(s) and body before sending unsolicited mail.
- `add_user` mutates the Cloud Mail deployment's user set; double-check before
  running it.
- Actions return `{'status': 'ok', ...}` on success or `{'status': 'error',
  'error': <message>}` on failure. Check the status and surface or act on errors
  rather than assuming delivery.
