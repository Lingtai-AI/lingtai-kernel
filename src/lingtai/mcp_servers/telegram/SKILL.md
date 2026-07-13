---
name: telegram-mcp-manual
description: |
  Progressive-disclosure usage manual for the Telegram MCP tool. Read this when
  you need detail beyond the one-line action descriptions: media.type='document'
  vs 'photo' for charts/reports/generated artifacts, placeholder/live-status
  messages, reply vs send, read/check/search, parse_mode/entities, chat_action, dynamic slash commands,
  and error surfacing. Pulled on demand via action='manual'; you do not need to
  call it before every send.
version: 1.3.0
last_changed_at: "2026-07-12T18:20:00-07:00"
---

# Telegram MCP — usage manual (progressive disclosure)

This manual is pulled on demand via `action='manual'` so the per-action tool
schema can stay concise. Read it when you need detail beyond the one-line action
descriptions; you do not need to call it before every send.

## MEDIA: document vs photo

- Charts, plots, reports, HTML/SVG/PNG/PDF exports, CSVs, and any other
  generated artifact the user should open intact: send with
  `media.type='document'`. Documents arrive as a downloadable file, uncropped
  and uncompressed.
- `media.type='photo'` is for native inline photo previews only. Telegram may
  crop, compress, thumbnail, or otherwise degrade text-heavy graphics sent as a
  photo, so a chart can look cropped or unreadable.
- Do not paste a local file path into message text as a substitute for
  attaching the file; attach it with `media={type, path}`.

## PLACEHOLDER / LIVE-STATUS

- For responses that take more than ~5s, send `action='send'` with
  `placeholder=true` (and your interim text, e.g. "Looking into that…").
  This fires a typing indicator and returns a compound `message_id`.
- Edit that **same** message at meaningful phase changes with `action='edit'`,
  `message_id=<that id>`, `text=<updated status>`. The user sees one evolving
  reply — not silence followed by a wall of text.
- The final answer must be a **separate durable message** using `action='send'`
  or `action='reply'`. Do **not** edit the placeholder into the final answer;
  the placeholder shows progress only (it may optionally be deleted).
- When the current agent has `taskcard: True`, an automatic Task Card may update
  separately during Telegram-originated turns (you do not manage it; use
  send/reply for your own messages). While the resident Task Card is still the
  chat's last message, automatic, programmable, heartbeat, and final frames edit
  that one stable resident message ID in place; an identical Telegram edit is a
  successful no-op. See **TASKCARD STATE** below.
- The Task Card's tracked resident target is kept as the last message. When a
  newer message has arrived below it — your own durable send/reply, or an incoming
  user message — the addon same-content-probes the exact old resident with its last committed
  render when available. After a cold in-memory start, the exact delete result is
  itself the existence/removal probe. Unknown/transient probe failures fail closed
  and send nothing.
- Before injecting a replacement, the exact old resident must be confirmed deleted
  or Telegram must explicitly report it already missing. A delete failure blocks
  the new send. Only then is the fresh card sent and persisted, so tracked rotation
  never deliberately displays two cards. A later send failure may leave zero and
  is reported explicitly; a new-id persistence failure retains the in-process id
  and surfaces a partial durability failure. Malformed/cross-bound resident ids
  never reach transport, ordinary messages are never deletion candidates, and
  unknown historical orphan cards are not scanned or deleted. The durable map is
  one tracked target per account+chat, not proof of global chat-history cardinality.
- Automatic and programmable delivery shares one per-account+chat transaction.
  While the tracked resident remains latest it is edited in place; an identical
  Telegram edit is a successful no-op. An unknown latest-message high-water stays
  conservative and does not authorize rotation or deletion.
- For very fast responses (under ~5s), native Telegram typing/👀 presence is
  enough — skip the placeholder.

## REPLY vs SEND

- `action='reply'` (`message_id` from read/check results, `text`) threads your
  response to a specific message and adds a ✅ reaction to it; prefer it when
  answering a particular incoming message.
- `action='send'` (`chat_id`, `text`) starts a fresh message in the chat; use it
  for unsolicited or standalone messages.

## READING: read / check / search

- `check`: list recent conversations with unread counts.
- `read`: read messages from one chat (`chat_id`; optional `limit`). Reading
  marks messages read and clears the wake notification mirror.
- `search`: regex search over message text/sender (`query`; optional `chat_id`,
  `account`).

## RICH TEXT: parse_mode / entities

- `parse_mode` accepts `'HTML'`, `'MarkdownV2'`, or `'Markdown'` for
  send/reply/edit and media captions; omit it or pass `''` for plain text.
- `entities` sets `MessageEntity[]` for message text; `caption_entities` does the
  same for media captions. If `caption_entities` is omitted on a media send,
  `entities` is reused as the caption entities.

## CHAT ACTION

- `chat_action` (`'typing'`, `'upload_photo'`, `'upload_document'`,
  `'upload_voice'`) on a send with no text/media sends just the indicator. It
  auto-expires after ~5s, so re-send periodically during long work. Pass `''`
  for no chat action.

## SLASH COMMANDS: dynamic Telegram menu entries

Telegram has two separate slash-command layers:

1. **Bot menu registration** (`setMyCommands`): what appears in Telegram's `/`
   command picker. The LingTai Telegram addon registers this menu at bot startup
   from each account's optional `commands` config list.
2. **Runtime handling**: what happens when a user sends the slash command. A
   small built-in set is handled locally by the addon without an LLM call
   (`/kanban`, `/taskcard`, `/refresh`, `/sleep`, `/system`). Other slash commands are not
   swallowed; they pass through as normal inbound messages for the host agent to
   answer or route.

To dynamically add a command such as `/tokenstats` to a bot's Telegram menu:

1. Edit the Telegram config file used by that agent (normally
   `<agent>/.secrets/telegram.json`; the active path is exposed as
   `LINGTAI_TELEGRAM_CONFIG` in the MCP process, and `lingtai://status` reports
   only a redacted, non-secret view).
2. Add or update the account's `commands` list. Command names are stored
   **without** the leading slash and should follow Telegram's Bot API
   constraints (lowercase letters, digits, underscores; 1-32 characters; short
   human-readable description):

   ```json
   {
     "accounts": [
       {
         "alias": "codex",
         "bot_token": "<secret>",
         "allowed_users": [6859932159],
         "commands": [
           {"command": "kanban", "description": "Show agent dashboard"},
           {"command": "tokenstats", "description": "Show recent token usage stats"}
         ]
       }
     ]
   }
   ```

3. Run `system(action="refresh")` (or restart the agent). On startup the
   addon calls Telegram Bot API `setMyCommands` best-effort; failure is logged
   but does not block the bot.
4. Verify with the Telegram `/` picker or `lingtai://status` / `telegram.accounts`;
   status shows `commands_count` but never exposes the bot token.

Important behavior notes:

- Adding a command to `commands` **only registers the menu entry**. It does not
  by itself create a local no-LLM implementation. For `/tokenstats`, either
  teach the host agent (via pad/skill/standing instructions) how to respond
  when it receives `/tokenstats`, or add a code-level local handler in
  `TelegramAccount._handle_slash_command()` if the command should be served
  without invoking the agent.
- If you include `commands: []`, the addon sends an empty list to
  `setMyCommands`, which clears the Telegram command menu for that account.
- If `commands` is omitted or `null`, the addon falls back to its built-in
  default command menu.
- Do not edit or print `bot_token` values while documenting or debugging slash
  commands.

## TASKCARD STATE

- `/taskcard` reports the current setting. `/taskcard on` and `/taskcard off`
  change it locally without an LLM call; `/taskcard@BotName on|off` works in
  groups through Telegram's normal command-mention form.
- The setting is one durable boolean for the current agent. It is shared by all
  of that agent's configured Telegram accounts and chats, persists under the
  agent workdir across refresh/restart, and is not project-, network-, session-,
  or chat-scoped.
- Every Telegram message representation shown to the agent carries the current
  boolean: structured message objects use `taskcard: true|false`, and textual
  preview message lines use `taskcard: True|False`. Check/read/search results
  include it on every message item. It is derived when projected or read, so an
  old stored message reflects the current setting without rewriting history.
- `taskcard: True` means automatic and programmable Task Cards may be sent to
  Telegram. `taskcard: False` means delivery of **both** slots is hidden at the
  Telegram presentation boundary. It does **not** mean work stopped: automatic
  rows, heartbeats, reverse calls, renderers, watchers, retries, and last-valid
  bookkeeping continue normally.
- Turning delivery off does not retroactively delete or edit an already resident
  Task Card. Turning it back on needs no restart; the next automatic heartbeat or
  programmable watcher projection may update the resident card again.
- When answering whether Task Cards are on, use the explicit current `taskcard`
  value rather than inferring state from whether an old Task Card is visible.

## ERROR SURFACING

- Actions return `{'status': ...}` on success or `{'error': <message>}` on
  failure (e.g. missing `chat_id`, unreadable `media.path`, bad `parse_mode`).
  Check for the `'error'` key and surface or act on it rather than assuming the
  message was delivered.
- The hosted Telegram Bot API limits `getFile` downloads to 20 MB. If an inbound
  document cannot be downloaded, `read` retains its available Telegram metadata
  without a local path, adds a safe bounded provider reason in `download_error`,
  and includes actionable resend/alternate-transfer guidance in the message text.
  For the hosted size error, ask for parts no larger than 20 MB or another transfer
  method. No reply is sent to the Telegram user automatically.
- Telegram's upstream local Bot API server can download files without that limit,
  but this addon currently uses the official hosted endpoints and does not expose
  local-server configuration or support.
- A duplicate identical send returns `{'status': 'blocked'}`; treat that as
  'already sent', not as a transient error to retry.
