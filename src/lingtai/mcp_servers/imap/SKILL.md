---
name: imap-mcp-manual
description: |
  Progressive-disclosure usage manual for the IMAP/SMTP email MCP tool. Read this
  when you need detail beyond the one-line action descriptions: send vs reply,
  check/read/search over folders, the compound email_id (account:folder:uid),
  attachments, move/flag/delete/folders, contacts/accounts basics, Microsoft
  personal-account OAuth bootstrap, and the important external-email side-effect
  caveats. Pulled on demand via action='manual'; you do not need to call it before
  every send.
version: 1.1.0
last_changed_at: 2026-07-20T00:00:00Z
related_files:
- src/lingtai/mcp_servers/imap/account.py
- src/lingtai/mcp_servers/imap/manager.py
- src/lingtai/mcp_servers/imap/oauth.py
- src/lingtai/mcp_servers/imap/server.py
- src/lingtai/mcp_servers/imap/service.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# IMAP/SMTP email MCP — usage manual (progressive disclosure)

Pulled on demand via `action='manual'`; read it for detail beyond the tool
schema's one-line action descriptions.

## ACTIONS

| Action | Purpose | Arguments |
|---|---|---|
| `send` | compose a new email | `address`, `message`; optional `subject`, `cc`, `bcc`, `attachments` |
| `reply` | reply to an existing email; preserves threading/subject from the original | `email_id`, `message`; optional `cc`, `attachments` |
| `check` | list recent envelopes from a folder | optional `folder`, `n` |
| `read` | fetch full email(s) | `email_id` |
| `search` | server-side IMAP search | `query`; optional `folder` |
| `folders` | list available IMAP folders | — |
| `move` | move email(s) to another folder | `email_id`, `folder` (destination) |
| `flag` | set/clear flags | `email_id`, `flags` |
| `delete` | delete email(s) | `email_id` |
| `contacts` | list all contacts | — |
| `add_contact` | add/update a contact | `address`, `name`; optional `note` |
| `edit_contact` | update contact fields | `address`; optional `name`, `note` |
| `remove_contact` | remove a contact | `address` |
| `accounts` | list configured IMAP accounts and connection status | — |

`address`/`cc`/`bcc` accept a single string or a list; `email_id` takes one id or
a list of ids.

## OUTLOOK.COM PERSONAL OAUTH2

Password accounts are unchanged. For a personal Microsoft account, omit
`email_password` and opt in explicitly:

```json
{
  "email_address": "agent@example.com",
  "imap_host": "outlook.office365.com",
  "smtp_host": "smtp-mail.outlook.com",
  "auth": {
    "type": "microsoft_oauth2",
    "client_id": "PUBLIC_CLIENT_ID",
    "token_cache": "imap/oauth2/outlook.cache",
    "smtp_enabled": false
  }
}
```

Register the app for personal Microsoft accounts and enable public-client flows.
With the MCP stopped, run from the agent directory (or set `LINGTAI_AGENT_DIR`):
`lingtai-imap-bootstrap --config PATH --account ADDRESS`. It shows Microsoft's
device instructions and writes an owner-only MSAL cache; it never prints token
results. Runtime acquisition is silent, MSAL adds its reserved `offline_access`
scope automatically, and missing/revoked consent reports
`oauth_reauthorization_required` without password fallback. Set `smtp_enabled`
to `true` only when send/reply is needed; status exposes only `auth_type`,
`auth_state`, and `smtp_enabled`.

## IDS, FOLDERS, ACCOUNTS

- `email_id` is a compound key: `account:folder:uid` (e.g.
  `me@example.com:INBOX:1234`). Use the ids returned by `check`/`search`; do not
  construct them by hand.
- An empty or whitespace-only `folder` (check/search) or `account` (any action)
  is treated as omitted: `folder` defaults to `INBOX`, and `account` uses the
  default/sole account rather than failing with `Unknown account`. Most actions
  accept an optional `account` (email address), defaulting to the primary
  account. `move` is the exception — its destination `folder` is required, must
  be non-empty, and is never defaulted to `INBOX`.
- `flags` is required for `flag` and maps flag name to bool, e.g.
  `flags={"seen": true, "flagged": false}`; `flags={"seen": true}` marks read. A
  missing or empty `flags` returns an error rather than silently doing nothing.
- `search` queries use a server-side search DSL, e.g.
  `from:addr subject:text unseen since:YYYY-MM-DD`; supported fields depend on
  the IMAP addon, so prefer examples returned by this tool over raw RFC IMAP
  search syntax.

## READING & ATTACHMENTS

- You are encouraged to `read` multiple relevant — or even all unread — emails
  and think before acting.
- `attachments` is a list of file paths (absolute or relative to the working
  dir) for `send`/`reply`. Attach generated artifacts (charts, reports, CSVs,
  PDFs) as files rather than pasting a path into the body.

## SIDE EFFECTS & SAFETY

- `send` and `reply` deliver real email to real recipients over SMTP — this is an
  external, hard-to-undo side effect. Confirm the recipient list (including
  `cc`/`bcc`) and the body before sending unsolicited mail.
- When replying to external addresses, follow the caller's standing reply
  policy. Unknown external senders require explicit guidance, or confirmation
  that the sender is the same human who contacted you through an internal
  channel, before sending a real reply.
- `delete` and `move` change server-side mailbox state; double-check the
  `email_id`/`folder` before running them.
- Actions return a result dict on success or one carrying an `'error'` key on
  failure (e.g. unknown account, bad `email_id`, unreadable attachment). Check
  for the error and surface or act on it rather than assuming delivery.
