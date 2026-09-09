---
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/feishu/SKILL.md
- src/lingtai/mcp_servers/feishu/_family.py
- src/lingtai/mcp_servers/feishu/_errors.py
- src/lingtai/mcp_servers/feishu/account.py
- src/lingtai/mcp_servers/feishu/control_cards.py
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/service.py
- src/lingtai/mcp_servers/feishu/settings.py
- src/lingtai/mcp_servers/feishu/task_card.py
- src/lingtai/mcp_servers/local_commands/core.py
- src/lingtai/mcp_servers/task_card/event_projection.py
- src/lingtai/mcp_servers/task_card/resident.py
- src/lingtai/mcp_servers/feishu/reference/setup.md
- src/lingtai/mcp_servers/feishu/reference/diagnostics.md
- src/lingtai/mcp_servers/feishu/reference/capability-matrix.md
maintenance: |
  This is the deep model-facing Feishu semantics owner. Keep exact schemas,
  IDs, reply/chunk/card/retry behavior, notification routing, and settings
  pointers aligned with the implementation and focused tests. Setup and symptom
  procedures belong to their sidecars; do not copy them here or back to SKILL.md.
---
# Feishu message semantics

Load this sidecar only for exact content, reply, card, media, notification,
Task Card, settings, or failure behavior. The parent [`../SKILL.md`](../SKILL.md)
is the first-call router; [`setup.md`](setup.md) owns deployment and
[`diagnostics.md`](diagnostics.md) owns symptom investigation.

## IDs, accounts, and recipients

- `send` uses `receive_id` and `receive_id_type`; `open_id` is the default for a
  user, `chat_id` targets a group, and `email`, `user_id`, and `union_id` are
  also accepted user identifiers. An omitted `account` means the first account.
- Every returned message ID is `{account_alias}:{chat_id}:{feishu_message_id}`.
  Pass an inbound ID to `reply` verbatim. For `edit`/`delete`, use only an ID
  from this Bot's sent history; the adapter resolves the local record but does
  not prove provider-side authorship.
- Verify account, recipient, content, and operation before any external
  `send`, `reply`, `edit`, `delete`, or `react`. Saving a contact is not
  permission to contact the sender.

## Text, posts, cards, and replies

`send`, `reply`, and `edit` require exactly one legacy `text` shortcut or tagged
`content`; both or neither is invalid. The strict content union is:

```text
text="..."                              # shortcut inside input
content={type: text, text: "..."}
content={type: markdown, markdown: "..."}
content={type: post, post: {...}}
content={type: card, card: {schema: "2.0", ...}}
```

The action-input `text` shortcut and tagged `content.type="text"` are equivalent.
Unknown/mixed fields fail before Feishu I/O. `send` starts a fresh message. `reply` answers the exact incoming target
and defaults `reply_in_thread=true` when that target has a persisted
`thread_id`; an explicit boolean overrides it. Every chunk of a topic reply
stays in that topic. `reply_target_gone="fail"` is the contract: a gone target
fails and never becomes a fresh `send`.

Markdown is converted to a Feishu post and split at safe boundaries. Successful
results include the primary `message_id`, ordered `message_ids`, `chunk_count`,
and `chunks` (each has its one-based `index` and compound `message_id`).

`edit` supports text, Markdown/post, and complete schema-2.0 card replacement;
media is not editable. It resolves any primary or secondary compound ID to the
whole persisted logical chunk group and replaces its logical content only after
all physical edits succeed; mixed outcomes retain lifecycle evidence below. `delete` resolves the same group and deletes every
physical member once, in order. Successful lifecycle results carry the same
ordered chunk projection.

## Chunk partials and lifecycle recovery

Each materialized wire chunk is attempted exactly once, in order. If a later
chunk fails, preserve the already delivered IDs and return the provider error
classification plus `partial_delivery=true`, `failed_chunk_index`,
`chunk_count`, `delivered_chunk_count`, and `automatic_retry_allowed=false`.
The sent record has `status="partial"`; a partial reply does not add the done
reaction. Do not replay the whole action, even when the failed chunk says
`retryable=true`, because delivered chunks would duplicate.

For a mixed `edit`/`delete`, return `partial_failure=true`, exact
`succeeded_message_ids` and `failed_message_ids`, per-member `failures`, and
`automatic_retry_allowed=false`. Persist a restart-visible `lifecycle` with
operation, attempted IDs, successes, failures, and time; preserve prior logical
content on a partial edit. Reconcile provider state before a new lifecycle
operation. A failure with no success does not replace the prior sent record.

## Schema-2.0 cards and callbacks

- A complete `content.type="card"` is business outbound content for `send` or
  `reply`; `edit` replaces it through the native card-update API and persists
  the exact JSON. Visible-text previews do not traverse button callback values.
- A business click is accepted only with a Feishu actor passing the account's
  `allowed_users` gate. It is serialized per account/chat, deduplicated by the
  stable Feishu event ID, persisted in the source conversation as
  `message_type="card_action"`, and wakes the Agent. Distinct event IDs remain
  distinct even for the same button/value.
- `read` exposes `card_action`, `feishu_event_id`, `source_message_ref`, and raw
  `feishu`. A callback record is not a reply target: update its source card by
  `source_message_ref`, or `send` a fresh response to its chat. Card callback
  delivery must be separately configured on the app's long connection; a
  message-event subscription alone is insufficient.

Local command cards are not business cards. After normal actor/admission checks,
`/help`, `/status`, `/kanban`, `/system`, `/refresh`, `/sleep`, `/clear`, and
`/taskcard` are handled locally, update their source control card, and neither
create `card_action` records nor wake the Agent. Unknown slash commands remain
ordinary Agent input. Group/topic commands require a Bot mention; namespaced
control callbacks are bounded-hash claimed in
`feishu/control_callbacks.json` and use localized `zh`/`en`/`wen` rendering
(unknown language falls back to English). `/taskcard on|off` and `/taskcard N`
(1--10) change the Agent-wide projection preference; exact resident routes are
still account + chat + optional thread.

## Media, shares, stickers

`send` and `reply` also accept:

```text
{type: image|file|audio|video, source: SOURCE, ...}
{type: share_chat, chat_id: "oc_..."}
{type: share_user, user_id: "ou_..."}
{type: sticker, file_key: "..."}
```

`SOURCE` is exactly `{type: path, path: "/absolute/file"}` or
`{type: key, key: "provider-key"}`. Relative paths and URLs are rejected. A
provider key must be owned by this Bot; an inbound user's downloadable key may
not be reusable outbound. Image/video may have a Markdown `caption`; file/audio
captions are unsupported. Sent records retain the exact source descriptor;
outgoing notification previews omit path and provider key. Files also accept
optional non-empty `file_name`; an image/video caption produces a Feishu post.
Each media chunk is still one attempt; rejected format/caption is a failure,
not a silent plaintext downgrade.

## Read, inbound content, and passive events

`check` lists conversations/unread counts; `read` takes `chat_id` and optional
`limit` (default `10`) / `account`; `search` takes a regex `query` and optional
account/chat. `remove_contact` by alias removes all matching saved contacts;
use exact `open_id` to target one.
`read` merges inbox and Bot-sent records and marks inbound records read. Its
normalized content identifies the original text, post, task/todo, share, card,
image, file, audio, video, or sticker family. Downloaded attachments include
safe absolute `path`, filename, size, and status; failed downloads retain the
original descriptor and bounded error. Audio transcription is local; a failed
transcription does not discard the audio.

Reaction/read/Bot membership events live in read-only `chat_id="events"`; they
carry an `event` projection and complete raw `feishu`, do not wake the Agent,
and must not be a `send` recipient. Ordinary admitted messages and authorized
business callbacks wake. DMs need no mention; group/topic messages need an
explicit Bot mention (`@all` alone is ignored), and `allowed_users` gates all
actors.

## Notifications and progress

The transient `_meta.agent_meta.notifications.attention.mcp.feishu` lane carries
only bounded IDs/dismiss guidance. Persistent
`_meta.agent_meta.notifications.persistent.mcp.feishu` carries bounded recent
conversation, routing/reply refs, and truncation comments. Neither lane marks
messages read: use `read`/`check` for truth, reply in Feishu, then dismiss the
transient hook with `notification(action="dismiss_channel", input={"channel":
"mcp.feishu", "force":null, "reason":null}, reasoning="handled in Feishu")`.

The current incoming event can include bounded local paths, attachment status,
and download/transcription errors. Provider keys and complete raw envelopes
stay behind `read`; truncated paths must be recovered there before use. Inspect
image content with Vision and other media with the appropriate local tool rather
than replying from its placeholder. Do not export paths or raw content as evidence.

For work taking more than a few seconds, `send` with `placeholder=true` accepts
only text/Markdown/post and creates a native schema-2.0 progress card. Edit that
same card only at meaningful phase changes using text/Markdown/post; those edits
remain progress cards. Media and custom card replacement are rejected for a
placeholder, even though a business card supports complete card replacement.
The final answer is a separate durable `send` or `reply`, never a progress-card
edit. Native `Typing` is transient presence; `OK` seen and `THUMBSUP` after a
complete reply are separate reactions.

Automatic and programmable resident Task Cards are mechanical channel
projections, not public `feishu` messages. One resident belongs to each admitted
account + chat + optional thread route. The exact persisted ID is the only
mutation target. It updates in place while still last; after a newer observed
route message, replacement is old-first. After restart ordering is unknown, so
update the exact resident conservatively and never scan/delete guessed orphans.
The programmable `WATCH` slot is independent: exact `active` plus non-empty
`taskcard/taskcard.md` projects it below the automatic frame under
`— TASK CARD —`; exact `inactive` clears only that slot, and
missing/invalid/blank state preserves its last delivered frame. Turning the
projection off suppresses it without deleting unknown cards.

## Failure shape and settings

Feishu manager and action-validation failures expose `status="failed"`, `error`, identical `message`,
`error_code`, `retryable`, and `retry_after_seconds`. `max_attempts=1` means
retryable is caller guidance, not an automatic retry. For rate limits honor a
returned delay; for timeout/indeterminate outcomes inspect persisted/provider
state before another send. A revoked target is `TARGET_REVOKED`: stop rather
than turn the operation into a new message/reaction.

`settings` is strict-empty, read-only SHOW. It returns seven rows with only
`key`, `current`, `default`, `configurable`, and a manual pointer in `comment`;
missing applied truth fails the whole bounded inventory. Plugin-owned SHOW
failures are separate: `SETTINGS_UNAVAILABLE` returns status/code/message with
no rows; `SETTINGS_RESPONSE_TOO_LARGE` also includes `max_bytes` (65536), not
manager retry fields. Do not assume every family failure has `retryable`.
All seven rows are configurable through their existing owner, never through SHOW.
For `config.path`, `accounts.app_secrets`, and `accounts.allowed_users`, both
`current` and `default` are always `<redacted>`; aliases/app IDs are public with
`default=null` (no universal default). Account rows are startup snapshots from
the protected account config; Task Card getters read current in-memory state.

### Setting config path

`config.path` is the sensitive `LINGTAI_FEISHU_CONFIG` startup reference, relative
to `LINGTAI_AGENT_DIR` when not absolute. After owner authorization, change the
launcher reference and relaunch the MCP, then SHOW again. No built-in path exists.

### Setting account aliases

`accounts.aliases` is the ordered startup snapshot; the first is default and a
later duplicate replaces lookup while remaining in order.

### Setting account app ids

`accounts.app_ids` is the ordered startup snapshot of app IDs paired with aliases.

### Setting account app secrets

`accounts.app_secrets` comes from each required `app_secret` in protected config.
Only with owner authorization, rotate privately in the Developer Console, update
the secret file and relaunch the MCP. Never put a credential in SHOW or evidence.

### Setting account allowed users

`accounts.allowed_users` is the sender set; omitted, null, or empty remains
unrestricted compatibility behavior and SHOW redacts it.

### Setting task card enabled

`taskcard.enabled` is live Agent-wide state initialized from the `taskcard` key in
`<agent>/feishu/taskcard.json`; an exact boolean wins, other/missing/unreadable
values default to `true`. `/taskcard on|off` persists and applies it immediately.
A direct authorized file edit requires an MCP relaunch; it is not hot-read.

### Setting task card normal rows

`taskcard.normal_rows` reads the `normal_rows` key in the same construction-time
file: an exact integer `1`--`10` wins; booleans are not row counts. Other/missing
values use `TaskCardEventProjection.DEFAULT_NORMAL_ROWS=1`. `/taskcard N`
persists and applies it immediately; direct file edits need an MCP relaunch.
Neither Task Card preference has an environment override.

The protected owner config is the only account source. SHOW never writes and
never grants configuration authority.
