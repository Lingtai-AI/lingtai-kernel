---
name: imap-mcp-manual
description: |
  Progressive-disclosure usage manual for the IMAP/SMTP email MCP tool. Read this
  when you need detail beyond the one-line action descriptions: send vs reply,
  check/read/search over folders, the compound email_id (account:folder:uid),
  attachments, move/flag/delete/folders, contacts/accounts basics, and the
  important external-email side-effect caveats (real outbound mail — confirm
  before sending). Pulled on demand via action='manual'; you do not need to call
  it before every send.
version: 1.0.0
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/mcp_servers/imap/manager.py
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

### Outlook.com IMAP (OAuth2/Modern Auth)
Microsoft's [current Outlook.com settings](https://support.microsoft.com/en-US/Outlook/pop-imap-and-smtp-settings-for-outlook-com) specify `outlook.office365.com` on port `993`, with `SSL/TLS` and `OAuth2/Modern Auth`.

```json
{
  "email_address": "user@outlook.com",
  "imap_host": "outlook.office365.com",
  "auth": {"type": "microsoft_oauth2", "client_id": "PUBLIC_CLIENT_ID", "token_cache": "imap/outlook.cache"}
}
```
Generate the serialized cache with a trusted external MSAL enrollment flow, then place it at `token_cache` while the MCP is stopped.

An app password is still a password used with Basic authentication, not an OAuth token. Microsoft [retired Basic authentication for Outlook.com on September 16, 2024](https://support.microsoft.com/en-us/office/modern-authentication-methods-now-needed-to-continue-syncing-outlook-email-in-non-microsoft-email-apps-c5d65390-9676-4763-b41f-d7986499a90d), so app passwords are not a supported Outlook.com IMAP route. See Microsoft's [app-password definition](https://support.microsoft.com/en-US/accounts-billing/manage/how-to-get-and-use-app-passwords).
