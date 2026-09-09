---
name: feishu-mcp-manual
description: |
  Concise progressive-disclosure usage manual for the Feishu (Lark) MCP.
  Ordinary schema-sufficient calls can start here; unfamiliar setup, cards,
  callbacks, compound replies, partial delivery, and recovery route to one
  focused reference. It covers the strict action envelope, recipient/account
  checks, and the no-fallback/no-blind-retry safety boundary.
version: 1.18.0
last_changed_at: 2026-09-09T03:15:00Z
related_files:
- ENVIRONMENT_VARIABLES.md
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/feishu/account.py
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/service.py
- src/lingtai/mcp_servers/feishu/settings.py
- src/lingtai/mcp_servers/feishu/control_cards.py
- src/lingtai/mcp_servers/local_commands/core.py
- src/lingtai/mcp_servers/feishu/task_card.py
- src/lingtai/mcp_servers/task_card/event_projection.py
- src/lingtai/mcp_servers/task_card/resident.py
- src/lingtai/mcp_servers/feishu/_family.py
- src/lingtai/mcp_servers/feishu/_errors.py
- src/lingtai/mcp_servers/feishu/reference/setup.md
- src/lingtai/mcp_servers/feishu/reference/diagnostics.md
- src/lingtai/mcp_servers/feishu/reference/capability-matrix.md
- src/lingtai/mcp_servers/feishu/reference/message-semantics.md
- tests/test_feishu_settings.py
maintenance: |
  Tracks the Feishu MCP's model-facing entry, strict action surface, and
  focused manual routes. Keep the deep references aligned with their owning
  setup, diagnostic, capability, and message contracts; keep this file small
  enough for first-call discovery.
---
# Feishu (Lark) MCP

This is the packaged `action='manual'` entry point and the progressive disclosure
router. A normal schema-sufficient call does **not** need to load it first. Load
one route only when the call is unfamiliar, consequential, or recovery-sensitive.

## PUBLIC TOOL FAMILY: strict LTP-v2

Every call is `{action, input, reasoning, summarize?}`. `action`, `input`, and
string `reasoning` are required; `summarize`, if present, is boolean. The root
and each action input are closed: unknown fields, cross-action fields, the old
flat/`_reasoning` form, and missing required fields fail before provider I/O.

| Goal | Action and required input |
|---|---|
| New message | `send`: `receive_id` + exactly one `text` or `content` |
| Inspect | `check`: `{}`; `read`: `chat_id`; `search`: `query` |
| Answer a message | `reply`: exact compound `message_id` + exactly one `text` or `content` |
| Change Bot output | `edit`: sent `message_id` + exactly one `text` or `content` |
| Delete Bot output | `delete`: sent `message_id` |
| Reaction | `react`: `message_id` + `operation="add"` + `emoji_type`, or `operation="remove"` + exact `reaction_id` |
| Contacts/accounts | `contacts`/`accounts`: `{}`; `add_contact`: `open_id` + `alias`; `remove_contact`: exactly one `open_id` or `alias` |
| Read settings/manual | `settings`/`manual`: `{}`; settings is SHOW-only |

`send`/`reply` content can be tagged `text`, `markdown`, `post`, complete
schema-2.0 `card`, media, shares, or sticker. `edit` accepts text/markdown/post
or complete card replacement, not media. See the message reference for exact
unions and media source shapes.

A safe inbound start is `feishu(action="check", input={}, reasoning="inspect incoming chats")`,
then `read` the affected chat; obtain its exact ID before replying.

## BEFORE A SIDE EFFECT

Configuration belongs to the orchestrator/admin; an avatar must not configure
or reconfigure this MCP. Setup, credential changes and rollout require explicit
owner authorization; routine message schemas do not grant it.

- `receive_id_type` defaults to `open_id`; use `chat_id` for a group. An omitted
  `account` selects the first configured account. Verify the account and
  recipient before `send`; verify the target before `reply`, `edit`, `delete`,
  or `react`.
- IDs are `{account_alias}:{chat_id}:{feishu_message_id}`. Pass an inbound
  compound ID back to `reply` verbatim. A reply target that is gone **fails**;
  it never becomes a fresh `send`. Group/topic inbound messages require an
  explicit Bot mention (`@all` alone is ignored); `allowed_users` gates senders,
  and saving a contact does not grant admission.
- Each physical outbound chunk is attempted once. A partial result lists exact
  delivered IDs and the failed chunk; do not replay the whole action or silently
  downgrade it. Reconcile provider state before another lifecycle operation.

## CARDS ARE THREE DIFFERENT THINGS

1. A complete schema-2.0 business `card` is outbound content. An authorized
   click becomes one deduplicated `card_action` inbox record and wakes the
   Agent; update its `source_message_ref` or `send` a fresh response. A callback
   record is not a reply target.
2. Local command cards (`/help`, `/status`, `/kanban`, `/system`, `/refresh`,
   `/sleep`, `/clear`, `/taskcard`) update themselves. Their callbacks stay
   local: no business inbox record and no Agent wake. Group commands still need
   admission and a Bot mention.
3. Automatic/programmable resident Task Cards are mechanical projections, not
   messages to manage with these actions. `placeholder=true` is separate native
   progress feedback; edit it only at meaningful phase changes and send the
   final durable answer separately.

## SETTINGS SHOW

`settings` accepts `{}` only and returns a read-only inventory. Its seven row
owners are linked below; SHOW never writes or grants configuration authority.

### Setting config path

See [`reference/message-semantics.md#setting-config-path`](reference/message-semantics.md#setting-config-path).

### Setting account aliases

See [`reference/message-semantics.md#setting-account-aliases`](reference/message-semantics.md#setting-account-aliases).

### Setting account app ids

See [`reference/message-semantics.md#setting-account-app-ids`](reference/message-semantics.md#setting-account-app-ids).

### Setting account app secrets

See [`reference/message-semantics.md#setting-account-app-secrets`](reference/message-semantics.md#setting-account-app-secrets).

### Setting account allowed users

See [`reference/message-semantics.md#setting-account-allowed-users`](reference/message-semantics.md#setting-account-allowed-users).

### Setting task card enabled

See [`reference/message-semantics.md#setting-task-card-enabled`](reference/message-semantics.md#setting-task-card-enabled).

### Setting task card normal rows

See [`reference/message-semantics.md#setting-task-card-normal-rows`](reference/message-semantics.md#setting-task-card-normal-rows).

## ONE OWNER FOR DEPTH

| Situation | Read |
|---|---|
| Exact content, reply/thread, card callback, media, notification, reaction, chunk/lifecycle, or Task Card semantics | [`reference/message-semantics.md`](reference/message-semantics.md) |
| App permissions, event/callback setup, config fields, accounts, canary, or rollback | [`reference/setup.md`](reference/setup.md) |
| A startup, admission, WebSocket, media, card, reaction, Task Card, or error symptom | [`reference/diagnostics.md`](reference/diagnostics.md) |
| Feishu-vs-Telegram scope and deliberate limits | [`reference/capability-matrix.md`](reference/capability-matrix.md) |

For failures, preserve `error_code`, `retryable`, and `retry_after_seconds`.
`max_attempts=1` makes retryable guidance a new caller decision, not an
adapter retry. Never expose secrets, raw envelopes, provider keys, or local
paths in external evidence.
