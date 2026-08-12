---
name: wechat-mcp-manual
description: |
  Progressive-disclosure usage manual for the WeChat MCP tool. Read this when you
  need detail beyond the one-line action descriptions: user_id targeting, send vs
  reply, check/read/search, media_path attachments (image/video/voice/file),
  contacts/accounts basics, and external-delivery side-effect caveats. Pulled on
  demand via action='manual'; you do not need to call it before every send.
version: 1.2.0
last_changed_at: "2026-07-29T01:00:00Z"
related_files:
- src/lingtai/mcp_servers/wechat/manager.py
- src/lingtai/mcp_servers/wechat/server.py
- src/lingtai/mcp_servers/wechat/_family.py
- src/lingtai/mcp_servers/wechat/api.py
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# WeChat MCP — usage manual (progressive disclosure)

## PUBLIC TOOL FAMILY: strict LTP-v2

Raw MCP discovery exposes exactly one public tool, `wechat`. It is an
independent strict LTP-v2 family with the closed root
`{action, input, reasoning, summarize?}` (`action`, `input`, and `reasoning`
required). `action` selects one of the 10 actions below; `input` is a closed,
action-owned object — only that action's own fields are accepted, and a field
from another action (or any top-level/host-only key) is rejected before any
WeChat I/O runs. `wechat` actions are exactly `send`, `check`, `read`,
`reply`, `search`, `contacts`, `add_contact`, `remove_contact`, `accounts`,
and `manual`. The `manual` action is the discovery path for this document. Do
not use the retired flat/legacy shape (arguments at the top level alongside
`action`), `_reasoning`, aliases, or a generic dispatcher.

Example call:

```json
{
  "action": "send",
  "input": {"user_id": "wxid_abc123@im.wechat", "text": "hi"},
  "reasoning": "acknowledging the user's question"
}
```

`send`'s `text` and `media_path` are independent, combinable fields (at least
one is required, and both may be given together in one call — see MEDIA /
ATTACHMENTS below), not a mutually exclusive choice.

## RECIPIENTS: user_id

- Messages target a WeChat user by `user_id` (e.g. `wxid_abc123@im.wechat`).
  `user_id` is the routing truth; aliases are convenience labels only. Use the
  `user_id` returned by `check`/`read`/`contacts` and do not invent one — when
  in doubt, especially for replies, take it from `read`/`check`.

## SEND vs REPLY

- `reply` (`message_id` from read results, `text`) threads your response to a
  specific incoming message; prefer it when answering a particular message.
- `send` (`user_id`, `text`) starts a fresh message; use it for unsolicited or
  standalone messages.

## MEDIA / ATTACHMENTS

- `send` with `media_path` attaches a file (absolute or relative to the agent
  working directory; paths outside it are rejected). Type is detected from
  the extension: `.jpg`/`.png` → image, `.mp4` → video, `.wav`/`.mp3` → voice,
  anything else → file.
- For charts, reports, and other artifacts the user should open intact, send them
  as a file/document rather than pasting a local path into the message text.
- `text` and `media_path` may be given together in one `send` call. They are
  delivered as **two separate WeChat messages** — the text first, then the
  media — not as a single captioned attachment. A missing `media_path` file
  is rejected before any text is sent, but a later upload/transport failure
  can still leave the text delivered without the media.

## INBOUND MEDIA / FILES

- Inbound media is rendered into message text as tags such as `[Image: path]`,
  `[Voice: "transcript" (audio: path)]`, `[File: name (path)]`, and
  `[Video: path]`. Use those paths as local artifacts, not as messages to paste
  back to the user.
- WeChat document downloads may be encrypted/cache placeholders rather than the
  real PDF/ZIP/etc. Before parsing a received file, validate its magic bytes
  (for example `%PDF-` for PDFs, `PK` for ZIP/DOCX). If the bytes do not match
  the claimed file type, ask the user to re-export with WeChat "Save As" or send
  a cloud/download link. This is an agent-side validation practice, not a
  guarantee from the MCP transport.
- Images and transcribed voice messages are usually more directly usable, but
  still verify file existence/readability before analysis.

## READING: check / read / search

- `check`: list recent conversations with unread counts; treat previews as
  hints, not complete context.
- `read`: read messages from one user (`user_id`; optional `limit`). The read
  view merges inbox and sent messages, which helps confirm whether you already
  replied.
- `search`: regex search over inbox messages (`query`; optional `user_id`). It is
  for locating inbound content, not proving that no sent reply exists.

## WAKE / REPLAY / DUPLICATE-REPLY DISCIPLINE

- Reply once per inbound `message_id`. Before sending after a refresh, molt, or
  worker-hang recovery, use `read` to reconcile the merged inbox+sent view and
  avoid duplicate replies.
- If a wake notification is based on a preview and an immediate `read`/`check`
  seems blocked by idle/sleep recovery, acknowledge from the preview if safe,
  then retry the producer read once the agent is active. Avoid tight polling
  loops.
- Some runtimes deduplicate upstream inbound replay by provider `message_id` and
  cursor checkpoints; if investigating inflated unread counts, confirm the
  runtime version/state before assuming the MCP lost messages.

## CONTACTS / ACCOUNTS

- `contacts`: list saved contacts.
- `add_contact`: save a contact alias (`user_id`, `alias`).
- `remove_contact`: remove a contact (`alias` or `user_id`).
- `accounts`: list configured WeChat accounts.

## SIDE EFFECTS & ERROR SURFACING

- `send` and `reply` deliver to real users — external side effects. Confirm
  recipient and content before sending unsolicited messages.
- Actions return a result dict on success or `{'error': <message>}` on failure
  (e.g. missing `user_id`, unreadable `media_path`). If a combined text+media
  send delivers text and the later media step fails, the result instead has
  `status: 'partial'`, `partial_delivery: true`, `text_status: 'sent'`,
  `media_status: 'failed'`, a redacted `failure` stage, and
  `automatic_retry_allowed: false`. Do not repeat the whole call automatically;
  reconcile the delivered text before retrying media alone. The CDN upload step
  itself makes at most three immediate attempts for transport/TLS failures,
  HTTP 429/5xx, or a success response missing its encrypted media reference;
  other 4xx responses are not retried. A local media deadline requests coroutine
  cancellation, but an already accepted remote request cannot be revoked. Check
  for the `'error'`/partial fields rather than assuming complete delivery.
