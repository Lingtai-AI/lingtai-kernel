---
name: telegram-mcp-manual
description: |
  Progressive-disclosure usage manual for the Telegram MCP tool. The resident
  schema carries safe first-use guidance; call `manual` for the action map,
  inbound-first/reply routing, channel/media/rendering rules, settings, Task Card
  projection, and error handling.
version: 1.9.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/task_card/event_projection.py
- src/lingtai/mcp_servers/task_card/resident.py
- src/lingtai/mcp_servers/local_commands/ANATOMY.md
- src/lingtai/mcp_servers/local_commands/core.py
- src/lingtai/mcp_servers/telegram/manager.py
- src/lingtai/mcp_servers/telegram/account.py
- src/lingtai/mcp_servers/telegram/render.py
- src/lingtai/mcp_servers/telegram/server.py
- src/lingtai/mcp_servers/telegram/_family.py
- src/lingtai/mcp_servers/telegram/settings.py
- src/lingtai/mcp_servers/telegram/service.py
- src/lingtai/mcp_servers/telegram/notification_header.md
- src/lingtai/mcp_servers/telegram/task_card/_family.py
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
- src/lingtai/mcp_servers/telegram/task_card/SKILL.md
- src/lingtai/mcp_servers/telegram/reference/rate-limits/SKILL.md
- src/lingtai/tools/task_card/manual/SKILL.md
- tests/test_telegram_structured_rendering.py
- tests/test_telegram_settings.py
- ENVIRONMENT_VARIABLES.md
maintenance: |
  Tracks the MCP server's manager/config/settings behavior; update when setup,
  settings precedence/redaction, or the public API surface changes. Keep this
  manual a concise router: detailed provider facts belong in the linked
  rate-limit and Task Card references, while this page retains first-use safety
  distinctions.
---

# Telegram MCP — usage manual

This progressive disclosure manual complements the routine schema. Use the schema for routine calls;
`telegram(action='manual', input={}, reasoning='...')` loads deeper guidance.
Registration, activation, private config, and setup belong
to `mcp-manual` → `reference/curated-addons.md`.

## First successful action: inbound first

The closed root is `{action, input, reasoning, summarize?}`: the first three
are required, `summarize` is optional and not action input, and only the selected
action's fields belong in `input`. `reasoning` is audit metadata, not a message;
`manual` and `settings` take `{}`.

1. Begin read-only with `check`, `read`, or `search`. `check` shows recent chats
   and incoming unread counts without marking read; `read` is the one-chat view
   and marks returned records read.
2. Answer on Telegram, not private output. For one incoming message, `reply`
   with its copied compound `message_id` from `read`/`search`; never guess an ID.
3. For a standalone message, `send` to a real numeric `chat_id` with text, media (optionally captioned),
   rich content, or an indicator as described below. Contacts are local aliases, not inbound permission; account
   setup/configuration is not a tool call.
4. Inspect `status`, action fields, and `error` before assuming an effect.
   `send`, `reply`, `edit`, and `delete` are external message changes: verify
   the exact target first. `status='blocked'` is a duplicate-guard decision,
   not proof that the requested attachment was delivered; do not replay blindly.

Account aliases are optional where accepted; omission selects the service
default. With multiple accounts, pass one explicitly for stateful work. IDs are
`account_alias:chat_id:message_id`; synthetic `updates` IDs are read/search-only.

## Action map

| Action | Use and effect |
|---|---|
| `send` | New numeric-chat message: `text`, `media`, `structured_message`, or ephemeral `chat_action`. |
| `check` | Recent conversation summaries and incoming unread counts; no read effect. |
| `read` | Combined incoming/outgoing records for one chat; marks returned records read and clears its wake mirror. |
| `reply` | New durable message to a copied compound target; marks it handled and adds the replied reaction; does not edit it. |
| `search` | Case-insensitive regex over stored inbound text, sender fields, and update type; no read/send effect. |
| `edit` / `delete` | Edit/delete one bot message by compound ID; verify the exact target. |
| `contacts` / `add_contact` / `remove_contact` | Read/change local aliases only; no inbound authorization. |
| `accounts` | List safe account details; credentials are not returned. |
| `settings` | Read-only settings inventory; no writer. |
| `manual` | Return this packaged page and metadata; no Telegram operation. |

## Send, reply, media, and rendering

`send` requires a real numeric `chat_id` and text, media with an optional `text` caption,
native rich content, or an ephemeral indicator. These are not an exclusive
text/media choice. Content-bearing `send`, `reply`, and `edit` default to
`rendering_mode='Markdown'`. Modes are exactly `plain_text`, `HTML`, `Markdown`,
`MarkdownV2`, `entities`, and `rich`: named modes map to Telegram `parse_mode`,
`entities` supplies `MessageEntity[]`, and `plain_text` omits `parse_mode`. Do
not mix entity data with a parse-mode choice. Omit `rendering_mode` for ordinary
messages and write valid Telegram Markdown; use `plain_text` only when literal,
unformatted output is intentional, never as a generic safety fallback.

The resident Telegram Task Card is separate: its automatic and programmable
sections share one original Telegram `parse_mode='HTML'` message. Automatic rows
HTML-escape their dynamic plain text before adding the small supported visual
markup; when the shared frame is within its source budget, the adapter trims
only escaped dynamic content to account for those fixed tags and emojis. A
programmable renderer targeting Telegram must therefore emit valid
Telegram HTML (or common plain text); unsupported or malformed markup fails the
update and preserves the last delivered resident card. This changes no ordinary
`send`/`reply`/`edit` default and no other channel's Task Card renderer.

### Reply vs send

`reply` requires a copied compound `message_id` plus `text` or
`structured_message`. It sends a new response threaded to that target, marks
it handled, and attempts the replied reaction; `send` is a standalone message.
A successful reply is not a guarantee that the best-effort reaction succeeded.

`media` is `{type: 'photo'|'document', path: '...'}`. Use `document` for charts,
plots, reports, HTML/SVG/PNG/PDF exports, CSVs, and other generated artifacts the
user should open intact. Use `photo` only for an inline preview: Telegram may
crop, compress, or thumbnail it, making text-heavy graphics unreadable. Attach
the file, not a local path in message text; the outbound path must be inside the
agent working directory and readable/non-empty. `reply_markup` is only for
`send`/`edit`; `caption_entities` only for media `send` in `entities` mode;
`link_preview_options` and `disable_web_page_preview` are send-only text options.

For native rich content, omit `text` and `media`, set `rendering_mode='rich'`,
and supply `structured_message` with required `title`. Optional fields are
`summary`, `facts` (`{label, value}`), `bullets`, ordered `steps`, `code`
(`text`, optional `language`), `next` (`label`, `text`), and `footer`. Native
heading/paragraph/list/pre/divider/footer blocks preserve authored wording and
meaningful emoji. Rich content can send/reply/edit text or rich messages, but
cannot edit a media caption.

For work likely to exceed five seconds, `placeholder=true` sends interim text
and returns a compound ID; edit it at meaningful phases, then send the final
answer as a separate durable `send` or `reply`. A placeholder is progress-only
and separate from the Task Card. A `chat_action` (`typing`, `upload_photo`,
`upload_document`, `upload_voice`) without text/media is only an ephemeral
indicator, expires after about five seconds, and is not a message record; repeat
it deliberately or pass `''`/omit it.

## Read, search, and inbound records

`read` requires `chat_id` and accepts `limit` (default `10`); it combines
inbound/outgoing records, marks returned records read, and clears the matching
notification mirror. `chat_id='updates'` recovers synthetic non-chat updates
read-only. `search` requires regex `query`, accepts optional `account`/`chat_id`,
searches inbound text, sender names/usernames, and update type, does not mark
read, and errors on invalid regex syntax.

Each record has a concise view plus additive `telegram` with the complete raw Bot
API Update, branch, actor policy result, and unknown nested fields. Edited
messages retain append-only raw `edits`; `event_id` is root identity and
`current_event_id` the latest edit. Reactions, polls, member/boost/business
events, inline callbacks, and unknown branches are synthetic `updates` records
(`synthetic=true`) and never outbound targets. Use the raw envelope when preview
fields are insufficient.

Inbound photo/document/voice/audio retains metadata and, when available, an
absolute inbox `path`; voice may add `voice_transcript`. Use `vision` for
image-like attachments, not filename guesses. `download_error` retains metadata
without a path: read the text and ask for a resend/another transfer method. The
hosted Bot API `getFile` limit is 20 MB; no local Bot API server is configured.
Notification previews follow `notification_header.md`: handle the latest
unresponded incoming message, and use `read` when truncated, ambiguous,
media/callback-heavy, or exact anchoring is needed.

## Slash commands and local preferences

The `/` picker and runtime handlers are separate. Optional per-account `commands`
register menu names without `/` through `setMyCommands`; registration creates no
handler. Built-ins include `/help`, `/status`, `/kanban`, `/system`, `/refresh`,
`/sleep`, `/clear`, and `/taskcard`; unknown commands remain ordinary inbound
messages. `/taskcard`, `/taskcard on|off`, `/taskcard N`, and
`/taskcard lang en|zh` change local preferences. `commands: []` clears the menu;
omitted/`null` uses the built-in menu. Config edits and refresh/restart are setup
operations, not MCP calls. Never print/place a bot token in this manual, chat,
logs, tests, or examples.

## SETTINGS SHOW

Call `telegram(action='settings', input={}, reasoning='inspect Telegram settings')`.
Success is exactly `{"settings": [...]}`; rows have `key`, `current`, `default`,
`configurable`, and `comment`. There is no writer. An unavailable fact returns
one `SETTINGS_UNAVAILABLE` failure with no partial rows. Account/config authority
is redacted; contacts, read markers, message records, offsets, and resident
routes are operational state. All six account/config rows redact both current
and default; all eleven rows are configurable. Account changes use the
authorized private account JSON procedure, preserve siblings/credentials and
policy, then restart/refresh and verify with SHOW plus the account/status path.
Restore the preserved prior value through the same owner if rollback is needed;
SHOW itself grants no change authority.

### Telegram config path
`config.path` is the resolved `LINGTAI_TELEGRAM_CONFIG` captured at startup;
relative values use `LINGTAI_AGENT_DIR` (or cwd). SHOW redacts it. Change through
the authorized launcher/config procedure and restart or refresh the MCP.

### Account aliases
`accounts.aliases` is the live service-order `accounts[].alias` snapshot and is
redacted because aliases bind account state and compound IDs. Change only in the
private account JSON, preserving credentials/policy, then restart/refresh and
verify with SHOW plus `accounts`.

### Bot tokens
`accounts.bot_tokens` aggregates credentials; both values are redacted. Only with
explicit owner authorization, rotate
through BotFather and private JSON, preserve permissions, restart/refresh, and
verify through the established account/status path. Never put a token in chat,
logs, tests, examples, or a settings response.

### Allowed users
`accounts.allowed_users`: omitted, `null`, and `[]` all mean unrestricted
admission; current/default values are redacted because IDs identify humans. A
saved contact does not alter this list.

### Account poll intervals
`accounts.poll_intervals` snapshots each `poll_interval`, default `1.0`; values
are preserved as-is without extra runtime validation and redacted in SHOW.

### Slash-command menu
`accounts.commands`: omitted/`null` means built-in, `[]` clears, and other
Telegram-compatible `{command, description}` objects apply best-effort at
startup. The aggregate is redacted; verify with SHOW, `/`, or safe status.

### Task Card poll interval
`automatic.poll_interval_seconds` is the import-time
`LINGTAI_TASKCARD_POLL_INTERVAL` snapshot (default `5.0`) for journal tailing,
programmable polling, and resident throttling. `float()` is used; non-finite
values make the all-or-nothing response unavailable. Change launcher environment
and fully restart the MCP.

### Task Card delivery
`automatic.enabled` is agent-wide `taskcard` in
`<workdir>/telegram/taskcard.json` (default `true`): it gates both slots without
stopping mechanics. Use `/taskcard on|off`, then `/taskcard` and SHOW.

### Task Card normal rows
`automatic.normal_rows` is the rolling API-call-group window (default `1`,
accepted `1..10`), not a tool-row count. Use `/taskcard N` and SHOW;
compatibility `max_refreshes` is not an active Telegram runtime ceiling.

### Task Card locale
`automatic.locale` is `en` by default and accepts `en` or `zh`; use
`/taskcard lang en|zh` and SHOW.

### Task Card display expression
`automatic.display_expression` is an allowlisted ordered list in
`<workdir>/telegram/taskcard.json`; default
`["footer","header","rows","blank","divider","metadata","time","ask_agent"]`.
A custom nonempty list has at most 32 entries from `header`, `rows`, `blank`,
`footer`, `divider`, `metadata`, `time`, `ask_agent`; invalid values fall back
wholesale. There is no slash editor: use an authorized atomic File/Shell edit,
preserve siblings, and verify with SHOW.

## Task Card: independent automatic and programmable projections

Automatic Task Card mechanically projects safe public `diary`/`tool_call`
events from durable history; it is not a turn-local heartbeat or completion
lifecycle. It omits hidden thinking, raw arguments/results, prompts, credentials,
paths, and private diagnostics; `normal_rows` counts API-call groups. The
`taskcard` boolean suppresses presentation of both slots while mechanics continue.

The public `task_card` tool is intrinsic and channel-neutral. Read
[`../../tools/task_card/manual/SKILL.md`](../../tools/task_card/manual/SKILL.md)
for `start | inspect | retry | stop | remove | settings | manual`, renderer,
recovery, limits, and cleanup. Telegram neither owns that tool/renderer nor
accepts its JSON/controller instructions. It only reads `taskcard/status` and
`taskcard/taskcard.md`: exact `active` + nonempty body projects a programmable
frame; exact `inactive` idempotently excludes only that frame. Missing/unreadable
status, active with missing/blank body, other status, or unchanged bytes are
no-ops. Telegram never rewrites producer files. Projection specifics are in
[`task_card/SKILL.md`](task_card/SKILL.md) and [`task_card/CONTRACT.md`](task_card/CONTRACT.md).

A changed programmable body causes a real Telegram edit/send and consumes quota;
diff-only skipping protects unchanged bytes, not renderer churn. Read
[`reference/rate-limits/SKILL.md`](reference/rate-limits/SKILL.md) before changing
cadence or recovery; it owns published quotas and `retry_after` semantics.

## Errors and rate limits

Inspect every result's `error`, `status`, and action fields; no hidden retries are
scheduled. HTTP 429 returns `status='error'`, `error_code=429`, and
`auto_retry=false`; valid nonnegative `retry_after` adds `retryable=true` and
wait seconds for a new action. Missing/malformed cooldown metadata is omitted,
never guessed; do not send a second notice through the rate-limited route. Read
the rate-limit reference for official quotas, undocumented scope, and safe policy.
A duplicate send is `status='blocked'`, not a replay reason; media download
failure stays on the inbound record and triggers no automatic reply. The current
in-process send guard counts prior successful sends by account/chat/text; it
does not compare attachment bytes or paths. Reconcile the intended artifact
with returned receipts before any new authorized send; never alter wording just
to bypass the guard.
