---
name: telegram-mcp-manual
description: |
  Progressive-disclosure usage manual for the Telegram MCP tool. Read this when
  you need detail beyond the one-line action descriptions: media.type='document'
  vs 'photo' for charts/reports/generated artifacts, placeholder/live-status
  messages, reply vs send, read/check/search, rendering_mode/entities, chat_action, dynamic slash commands,
  the programmable Task Card (task_card tool) — including task-specific watcher
  design for meaningful long-running work — and error surfacing. Pulled on demand
  via action='manual'; you do not need to call it before every send.
version: 1.5.8
last_changed_at: 2026-08-03T00:00:00Z
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/task_card/event_projection.py
- src/lingtai/mcp_servers/task_card/resident.py
- src/lingtai/mcp_servers/local_commands/ANATOMY.md
- src/lingtai/mcp_servers/local_commands/core.py
- src/lingtai/mcp_servers/telegram/manager.py
- src/lingtai/mcp_servers/telegram/server.py
- src/lingtai/mcp_servers/telegram/_family.py
- src/lingtai/mcp_servers/telegram/task_card/_family.py
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
- src/lingtai/mcp_servers/telegram/task_card/SKILL.md
- src/lingtai/mcp_servers/telegram/reference/rate-limits/SKILL.md
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# Telegram MCP — usage manual (progressive disclosure)

This manual is pulled on demand via `action='manual'` so the per-action tool
schema can stay concise. Read it when you need detail beyond the one-line action
descriptions; you do not need to call it before every send.

Registration, `init.json` activation, config-file placement/permissions, and the
setup readiness checklist are **not** here — they belong to `mcp-manual`
(`reference/curated-addons.md`).

## Nested reference catalog

```yaml
- name: telegram-task-card-manual
  location: task_card/SKILL.md
  description: |
    Nested telegram-mcp-manual reference for the programmable Task Card
    (`task_card` tool): when a watcher is warranted, the watcher information
    contract, how to inspect a task's producer evidence, a safe runnable
    custom-renderer example, the renderer contract, the
    start|inspect|retry|stop walkthrough, and terminal/fail-loud cleanup.
    Read this before authoring a renderer.
- name: telegram-rate-limits
  location: reference/rate-limits/SKILL.md
  description: |
    Current Telegram Bot API flood-control guidance and official source links:
    published per-chat, group, and bulk quotas; the exact `retry_after` meaning;
    what Telegram leaves unspecified; and safe client behavior. Read this before
    changing send cadence, programmable Task Card cadence, or 429 recovery.
```

| Need | Read |
|---|---|
| Send/reply/edit, media, reading, rich text, slash commands, `/taskcard`, errors | this file |
| Authoring or operating a programmable Task Card watcher | [`task_card/SKILL.md`](task_card/SKILL.md) |
| Current Telegram quotas, `retry_after`, and safe 429 policy | [`reference/rate-limits/SKILL.md`](reference/rate-limits/SKILL.md) |
| Normative resident/slot promises and code structure | [`task_card/CONTRACT.md`](task_card/CONTRACT.md), [`task_card/ANATOMY.md`](task_card/ANATOMY.md) |

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
- For very fast responses (under ~5s), native Telegram typing/👀 presence is
  enough — skip the placeholder.
- The Task Card is a separate surface from your placeholder. When the current
  agent has `taskcard: True`, the manager-owned automatic Task Card updates on
  its own; it is a mechanical view of recent `tool_call` events in
  `logs/events.jsonl`, not a turn-local heartbeat or completion lifecycle. See
  **AUTOMATIC TASK CARD** and **TASKCARD STATE** below.

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
- `search`: regex search over message text/sender/update type (`query`;
  optional `chat_id`, `account`).
- Every inbound record from `read`/`search` carries an additive `telegram`
  envelope: the complete raw Bot API Update (`update_id`, branch name, actor
  policy result, every nested/unknown field) plus, for edited messages, an
  append-only `edits` history of the raw edit events; `current_event_id`
  tracks the last-applied edit while `event_id` stays the immutable root
  event. Use it for selected-text
  reply quotes (`update.message.quote`), entities, forwards, topics, callback
  identity, etc. The concise top-level fields stay the quick view.
- Non-message updates (reactions, polls, member/boost/business events,
  inline-only callbacks, unknown future branches) land in the synthetic
  conversation bucket with `synthetic: true`; the raw event is in their
  `telegram` envelope. Pass `chat_id='updates'` (the one reserved
  non-numeric value the schema accepts) to `read`/`search` to recover them;
  `send`/`reply` still require a real numeric chat ID.

## RENDERING MODE: explicit per-message choice

- Every content-bearing `send`, `reply`, and `edit` must include
  `rendering_mode`; there is no default. Choose exactly one of `plain_text`,
  `HTML`, `Markdown`, `MarkdownV2`, or `entities`.
- `plain_text` maps to an omitted Bot API `parse_mode`; the other three named
  formats map to Telegram `parse_mode`. The agent must still provide the label
  even when no formatting is wanted.
- `entities` selects explicit `MessageEntity[]` formatting. Pass `entities` for
  message text or `caption_entities` for media captions; do not combine entity
  fields with a parse-mode rendering choice.
- For `send` with only `chat_action` and no text/media, use `plain_text` because
  no message body is rendered. `rendering_mode` is still required by the strict
  public branch.

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
   (`/help`, `/status`, `/kanban`, `/system`, `/brief`, `/refresh`, `/sleep`,
   `/clear`, `/taskcard`). Other slash commands are not
   swallowed; they pass through as normal inbound messages for the host agent to
   answer or route.

To dynamically add a command such as `/tokenstats` to a bot's Telegram menu:

1. Edit the Telegram config file used by that agent (normally
   `<agent>/.secrets/telegram.json`, the path in `LINGTAI_TELEGRAM_CONFIG`).
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

- The built-in commands use the channel-neutral local-command core for Agent
  filesystem reads, signal writes, and Task Card preference parsing. Telegram
  still owns actor admission, slash/callback dispatch, Markdown, emoji, inline
  keyboards, chat/message IDs, and Bot API delivery, so their visible output
  and behavior are unchanged.

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
  commands. `lingtai://status` reports only a redacted, non-secret view.

## AUTOMATIC TASK CARD: `events.jsonl` → resident broadcast

The automatic slot is a bounded projection of the agent's durable behavior
journal, and it feeds the same shared `TaskCardResident` state machine as the
programmable slot. Telegram supplies the provider transport and persistence
adapter:

1. `TelegramManager` owns one tail worker for its lifetime and reads
   `<workdir>/logs/events.jsonl`. The transport-free
   `TaskCardEventProjection` core accepts only canonical public `diary` text
   plus bounded ASCII-validated `tool_call` name/action and sanitized/bounded
   `_reasoning`. Credential shapes, URLs, recognized local absolute paths, and
   high-confidence provider identifiers become typed placeholders. Hidden
   thinking, aliases, raw action/arguments/results, external response bodies,
   raw URLs/tokens/paths, prompts, tracebacks, auth material, and private runtime
   diagnostics are never projected. `tool_result`, completion, elapsed,
   heartbeat, API-error, and provider-error rows are not rendered either.
2. A provider/API call is identified by its `api_call_id`. All public text and
   safe tool events with the same id remain in one atomic group. The card emits
   exactly one TUI-style divider (`──────────`) before each selected group;
   multiple text/tool events do not create extra dividers.
3. `/taskcard N` selects the latest N API-call groups (1–10), not N tool uses.
   The existing persisted `normal_rows` value is reused as this numeric group
   window. If a selected group is larger than the card budget, content is
   truncated inside that group after the group count has been chosen.
4. Each rendered card carries the safe public text and tool rows, the fixed
   no-reply footer naming both `/taskcard` command forms, and the render-time
   timestamp.
5. The shared core renders the bounded groups once; the manager broadcasts that
   same agent-behavior view to every tracked resident Task Card across configured
   Telegram accounts and chats. The shared projection contains no journal I/O,
   account/chat route, resident state, or transport. Groups are not correlated
   to the chat that created a resident card; one target's failure does not block
   the others.
6. There is no durable cursor or second behavior store. The byte offset, groups,
   and channel frames are in-memory optimizations. Startup, refresh, molt, and
   detected log truncation/replacement rehydrate from the existing
   `events.jsonl` and `TelegramAccount.task_cards` state. An unterminated final
   JSONL line is left unconsumed until complete, and read/stat failures fail
   closed rather than advancing past unseen bytes.

Architecture and lifecycle details live in the owning
[`mcp_servers` Anatomy](../ANATOMY.md). The pure event grouping/redaction/render
core is [`task_card/event_projection.py`](../task_card/event_projection.py).
The route/slot/rotation/failure state machine is
[`task_card/resident.py`](../task_card/resident.py); Telegram still owns journal
tailing and implements the real edit/delete/send/persist callbacks. The local
[`telegram/task_card/resident.py`](task_card/resident.py) path is a compatibility
re-export. The programmable renderer/tool structure lives in the separate
[`telegram/task_card` Anatomy](task_card/ANATOMY.md).

### Resident-card behavior you can rely on

Both slots share one per-account+chat delivery transaction over a single tracked
resident target. The provider-neutral resident core serializes and commits slot
state only after success; Telegram callbacks classify the real API outcomes.
While that resident is still the chat's last message it is edited in place (an
identical Telegram edit is a successful no-op). Once a newer message sits below
it — your own durable send/reply, or an incoming user message — the shared state
machine replaces it old-first and fails closed: the exact old card must be
confirmed deleted, or Telegram must explicitly report it already missing, before a
replacement is sent, so rotation never deliberately shows two cards. A replacement
that then fails may leave **zero** cards and says so explicitly; a durable-id write
failure surfaces as a partial, not as success. Ordinary messages are never deletion
candidates and unknown historical orphan cards are never scanned or deleted; the
durable map is one tracked target per account+chat, not proof of global
chat-history cardinality. Normative source:
[`task_card/CONTRACT.md`](task_card/CONTRACT.md) §Behavior 7–8.

## TASKCARD STATE

- `/taskcard` reports both current preferences. `/taskcard on` and `/taskcard off`
  change delivery locally without an LLM call. `/taskcard N` sets the rolling
  API-call-group window to decimal `N=1..10` without changing delivery; invalid,
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
  history rewrites. `normal_rows` is the compatibility persistence key for how
  many of the newest bounded API-call groups the automatic card renders; it is
  not a tool-row count.
- `taskcard: True` means automatic and programmable Task Cards may be sent to
  Telegram. **`taskcard: False` / `/taskcard off` hides delivery of *both* slots**
  at the presentation boundary while every mechanic continues — the event tail
  still follows the journal, and programmable renderers, watches, retries, and
  bookkeeping keep running. Nothing is broadcast while disabled. Turning delivery
  back on needs no restart.
- When answering whether Task Cards are on or how many normal rows they keep, use
  the explicit current `/taskcard` status rather than inferring from a visible card.

## PROGRAMMABLE TASK CARD (`task_card` intrinsic tool)

- The public model-facing `task_card` capability is intrinsic and
  channel-neutral. Read the canonical producer manual at
  [`../../tools/task_card/manual/SKILL.md`](../../tools/task_card/manual/SKILL.md)
  before authoring a watcher. Telegram does not own that tool, does not run
  renderers, and does not accept Task Card JSON/controller instructions.
- Use a Python renderer file inside the agent working directory. Each successful
  run must exit `0` and print a nonempty full Markdown/text body to stdout. The
  intrinsic producer writes that body atomically to
  `<workdir>/taskcard/taskcard.md`, then writes exact `active` to
  `<workdir>/taskcard/status`. `stop` and agent shutdown write exact `inactive`;
  the last body remains on disk.
- Actions are `start | inspect | retry | stop | remove | manual`. `start`
  performs the first render synchronously and starts no watch on failure.
  `retry` updates only the body for the active watch. `stop` deactivates the
  intrinsic artifact while preserving its body; `remove` is terminal cleanup
  that retires the watch and deletes the body. One intrinsic-owned watch may be
  active per agent.
- Telegram owns the resident message, automatic/mechanical event-journal slot,
  composition, persistence, and message updates. It reads
  `taskcard/status` and `taskcard/taskcard.md` only for the agent-owned
  programmable frame: exact `active` plus a nonempty body is sanitized before
  the ceiling/diff comparison, then includes or updates `— WATCH —`; exact
  `inactive` idempotently excludes only that programmable
  frame while preserving the resident, automatic content, and local body.
  Missing/unreadable/other status, active with a missing/blank body, or
  an unchanged sanitized body remain a no-op at the Telegram boundary.
- Skipping unchanged bytes is not a rate-limit exemption. Every time your
  renderer's output actually changes, Telegram performs a real message
  edit/send, subject to the same Bot API flood-control limits as any other
  send (see [`reference/rate-limits/SKILL.md`](reference/rate-limits/SKILL.md)).
  A renderer that churns its body on every tick can hit HTTP 429 exactly like
  frequent manual sends, even though a producer with unchanged output would
  cause zero Telegram traffic. Choose `interval_s` and how often your output
  actually changes deliberately — cadence and churn are a product choice, not
  something the diff-only skip makes safe by default.

## ERROR SURFACING

- Actions return `{'status': ...}` on success or `{'error': <message>}` on
  failure (e.g. missing `chat_id`, unreadable `media.path`, bad `rendering_mode`).
  Check for the `'error'` key and surface or act on it rather than assuming the
  message was delivered.
- Telegram HTTP 429 responses fail fast with `status: 'error'`,
  `error_code: 429`, and `auto_retry: false`. A valid provider `retry_after`
  makes `retryable: true`: wait at least that many seconds before starting a new
  action. Without valid cooldown metadata, both `retryable` and `retry_after`
  are omitted rather than guessed. The addon never sleeps inside a
  tool call or schedules a hidden second side effect.
- Read [`reference/rate-limits/SKILL.md`](reference/rate-limits/SKILL.md) for
  Telegram's currently documented quotas, official source links, and the
  distinction between provider facts and product policy before changing message
  or programmable Task Card cadence.
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

## PUBLIC TOOL FAMILY: strict LTP-v2

Raw MCP discovery exposes exactly one public tool: `telegram`. It is a strict
LTP-v2 family with the closed root `{action, input, reasoning, summarize?}`
(`action`, `input`, and `reasoning` required) and a closed action-owned input
branch. `telegram` actions are exactly `send`, `check`, `read`, `reply`,
`search`, `delete`, `edit`, `contacts`, `add_contact`, `remove_contact`,
`accounts`, and `manual`. The family-owned `manual` action is the discovery path
for these packaged docs. Do not use the retired flat/legacy shape, `_reasoning`,
aliases, or a generic dispatcher.

The public `task_card` tool is now intrinsic (`lingtai.tools.task_card`) and
produces `<workdir>/taskcard/status` plus `<workdir>/taskcard/taskcard.md`.
Telegram only projects that artifact read-only into its resident Task Card. For
Telegram projection details, read [`task_card/SKILL.md`](task_card/SKILL.md).
