---
name: telegram-mcp-manual
description: |
  Progressive-disclosure usage manual for the Telegram MCP tool. Read this when
  you need detail beyond the one-line action descriptions: media.type='document'
  vs 'photo' for charts/reports/generated artifacts, placeholder/live-status
  messages, reply vs send, read/check/search, parse_mode/entities, chat_action, dynamic slash commands,
  the programmable Task Card (task_card tool) — including the default to add a
  human-visible watcher for long-running bash-async/daemon work — and error
  surfacing. Pulled on demand via action='manual'; you do not need to call it
  before every send.
version: 1.5.2
last_changed_at: "2026-07-14T19:22:00-07:00"
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/telegram/manager.py
- src/lingtai/mcp_servers/telegram/server.py
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
- src/lingtai/mcp_servers/telegram/task_card/SKILL.md
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
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
- When the current agent has `taskcard: True`, the manager-owned automatic Task
  Card updates separately from your own send/reply messages. It is a mechanical
  view of recent `tool_call` events in `logs/events.jsonl`, not a turn-local
  heartbeat or completion lifecycle. While the resident Task Card is still the
  chat's last message, automatic event-tail and programmable frames edit that one
  stable resident message ID in place; an identical Telegram edit is a successful
  no-op. See **AUTOMATIC TASK CARD: `events.jsonl` → resident broadcast** and
  **TASKCARD STATE** below.
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

## AUTOMATIC TASK CARD: `events.jsonl` → resident broadcast

The automatic slot is a bounded projection of the agent's durable behavior
journal, not a second model-authored progress lifecycle:

1. `TelegramManager` owns one tail worker for its lifetime. It reads
   `<workdir>/logs/events.jsonl`, checks the exact event `type` first, and skips
   every event except `"tool_call"` without inspecting that event's payload.
2. From a matching row it extracts only `tool_name`, `tool_args.action`, and a
   redacted, length-capped `tool_args._reasoning`; raw arguments, results, and
   provider errors are never forwarded. The manager keeps a bounded recent
   window, and the current `/taskcard N` setting chooses how many normal rows the
   card renders.
3. When that window changes, the manager renders the automatic frame once and
   broadcasts the same agent-behavior view to every tracked resident Task Card
   across configured Telegram accounts and chats. Rows carry no account/chat
   route and are not correlated to the chat that created a resident card; a
   failure for one target does not block the others.
4. The tailer never inspects `tool_result` or dispatch events and does not
   reconstruct completion, `DONE`, elapsed time, heartbeat, or API-error rows.
   The automatic path also does not call the private reverse MCP route; that route
   is programmable-slot-only.
5. There is no durable cursor or second behavior store. The byte offset and row
   window are in-memory optimizations. Startup, refresh, molt, and detected log
   truncation/replacement reverse-tail the journal again to rehydrate recent
   matching rows. An unterminated final JSONL line is left unconsumed until it is
   complete, and read/stat failures fail closed rather than advancing past unseen
   bytes.

Architecture and lifecycle details live in the owning
[`mcp_servers` Anatomy](../ANATOMY.md). The separate programmable renderer/tool
structure lives in the
[`telegram/task_card` Anatomy](task_card/ANATOMY.md); its user procedure is the
co-located [`task_card/SKILL.md`](task_card/SKILL.md).

## TASKCARD STATE

- `/taskcard` reports both current preferences. `/taskcard on` and `/taskcard off`
  change delivery locally without an LLM call. `/taskcard N` sets the rolling
  normal-tool-row window to decimal `N=1..10` without changing delivery; invalid,
  non-ASCII, extra-argument, and out-of-range forms return usage rather than being
  clamped. Telegram's normal `/taskcard@BotName ...` mention form works in groups.
- The preferences are agent-wide and shared by all configured Telegram accounts
  and chats. They persist across refresh/restart in
  `<workdir>/telegram/taskcard.json` as `{"taskcard": bool, "normal_rows": N}`;
  legacy boolean-only files remain valid and default `normal_rows` to 1. Writes
  are atomic + fsynced, and memory changes only after durable write success.
- Every Telegram message representation shown to the agent carries the current
  delivery boolean: structured message objects use `taskcard: true|false`, and
  textual preview lines use `taskcard: True|False`. Check/read/search items derive
  it at projection time, so old stored messages reflect the current value without
  history rewrites. `normal_rows` chooses how many of the newest bounded,
  manager-projected `tool_call` rows the automatic card renders; non-`tool_call`
  events never enter that window.
- `taskcard: True` means automatic and programmable Task Cards may be sent to
  Telegram. `taskcard: False` hides delivery of **both** slots at the presentation
  boundary. The event tail still follows the journal and programmable renderers,
  watchers, retries, and bookkeeping continue, but no automatic broadcast is
  delivered while disabled. Turning delivery back on needs no restart.
- Automatic event-tail cards render the safe tool rows, the fixed no-reply footer
  that names both command forms, and the render-time timestamp. They do not render
  `tool_result`, completion, elapsed, heartbeat, API-error, or provider-error rows,
  and they do not forward raw arguments, external response bodies, URLs, tokens,
  prompts, paths, or tracebacks.
- When answering whether Task Cards are on or how many normal rows they keep, use
  the explicit current `/taskcard` status rather than inferring from a visible card.

## PROGRAMMABLE TASK CARD (`task_card` tool)

- **Default during Telegram-originated turns: when you launch a meaningful
  long-running `bash(async=true)` job or daemon task and then go idle to await it,
  create a human-visible programmable Task Card watcher for it** so the person
  watching Telegram sees the latest reported snapshot instead of a silent gap
  (you refresh that snapshot after launch, after meaningful polls/checks, and at
  the terminal result). Two ready, copy-and-adapt renderer templates cover exactly
  these two cases (`render_bash_async.py` and `render_daemon.py`); the full manual
  below routes to them and explains how to copy an asset into your working
  directory. Skip the watcher only for quick or invisible jobs.
- The resident Task Card has two independent slots: the **automatic** event-journal
  slot (manager-owned, described under **AUTOMATIC TASK CARD: `events.jsonl` →
  resident broadcast**) and a **programmable** slot you drive with the separate
  public `task_card` tool. With both present, the
  programmable block appears under a `— WATCH —` header; updating one slot never
  disturbs the other.
- You surface your latest reported snapshot on the programmable slot by supplying
  a small **Python renderer** file inside your working directory whose stdout is
  exactly one Task Card JSON object (`title` string, `lines` array of ≤20 strings,
  `footer` string; at least one present). Telegram never runs your code — the
  controller runs the renderer as a subprocess and forwards only the validated
  data.
- Actions: `start` (validate + run once, then watch on an interval; returns a
  `watch_id`), `inspect` (state + last valid frame), `retry` (re-run now), `stop`
  (end the watch, clear only the programmable frame; renderer files are never
  deleted). A bad first run is an immediate error and starts no watch; later
  failures keep the last valid frame and raise a deduped fail-loud system wake.
- `/taskcard off` hides delivery of **both** slots (see **TASKCARD STATE**) while
  the renderer, watches, and last-valid bookkeeping keep running.
- **Full manual (renderer contract, the two ready bash-async/daemon renderer
  templates, the snapshot truthfulness model, and the full start|inspect|retry|stop
  walkthrough):** follow the relative path `task_card/SKILL.md` from this manual's
  directory (the co-located Programmable Task Card manual). Starting a watch drives
  the programmable slot of the `TelegramManager`-owned single resident card,
  reusing the tracked resident or creating its one resident if none exists yet; it
  does not start another manager or a second card.

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
