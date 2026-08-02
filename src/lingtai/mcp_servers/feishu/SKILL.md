---
name: feishu-mcp-manual
description: |
  Progressive-disclosure usage manual for the Feishu (Lark) MCP tool. Read this
  when you need detail beyond the one-line action descriptions: receive_id vs
  receive_id_type (open_id/chat_id), send vs reply, check/read/search, placeholder
  + edit for native progress cards, text/markdown/post/card outbound content,
  topic-aware replies, public and automatic reactions, retryable error results,
  interactive card callbacks, outbound media/share/sticker sources, contacts/accounts basics, the notification
  transient-hook vs persistent-context split, normalized inbound conversations,
  preserved inbound media, passive channel events, group @Bot routing, and
  automatic and programmable resident Task Cards, localized local-command
  control cards, and side-effect caveats.
  Pulled on demand via action='manual'; you do not need to call it before every
  send.
version: 1.15.1
last_changed_at: 2026-08-03T00:00:00Z
related_files:
- src/lingtai/mcp_servers/feishu/account.py
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/service.py
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
maintenance: |
  Tracks the MCP server's manager/config behavior; update when the server's setup or API surface changes.
---

# Feishu (Lark) MCP — usage manual (progressive disclosure)

## OPERATOR REFERENCES

Keep this file focused on Agent-facing action and message semantics. Load the
packaged sidecar that matches the operational question:

| Need | Read |
|---|---|
| App permissions, event/card callback setup, complete config fields, multi-account behavior, canary, acceptance, rollback | [`reference/setup.md`](reference/setup.md) |
| Safe status interpretation and symptom-based startup, WebSocket, admission, media, card, reaction, Task Card, refresh, and error diagnosis | [`reference/diagnostics.md`](reference/diagnostics.md) |
| Feishu v1 vs Telegram coverage, action/content inventory, and explicit non-goals | [`reference/capability-matrix.md`](reference/capability-matrix.md) |

The sidecars are packaged with LingTai but are not embedded into the
`action='manual'` result. Follow these relative paths only when that detail is
needed; do not load all three for an ordinary message send.

## RECIPIENTS: receive_id / receive_id_type

- `send` targets a recipient by `receive_id` plus `receive_id_type`. Use
  `receive_id_type='open_id'` for an individual user (`ou_xxx`) and
  `receive_id_type='chat_id'` for a group chat (`oc_xxx`). `receive_id_type`
  defaults to `open_id` when omitted.
- `email`, `user_id`, and `union_id` are also accepted as `receive_id_type`
  values when you only have that identifier for a user.

## SEND vs REPLY

- `send`, `reply`, and `edit` require exactly one of legacy `text` or structured
  `content`; passing both is rejected before Feishu I/O. `text='...'` remains the
  plain-text shortcut.
- Structured content is a strict tagged union in this slice:
  `{'type':'text','text':'...'}`,
  `{'type':'markdown','markdown':'...'}`, or
  `{'type':'post','post':{...}}`,
  `{'type':'card','card':{'schema':'2.0',...}}`, plus the
  media/share/sticker forms below.
  Unknown keys or mixed variants are rejected.
- `reply` (`message_id` from read/check results plus `text` or `content`) replies
  to a specific incoming message; prefer it when answering that message. It
  defaults `reply_in_thread=true` when the persisted target has a `thread_id`,
  otherwise false. An explicit boolean overrides that default. If the reply
  target is gone, the call fails and never silently starts a fresh message.
- `send` (`receive_id`, `receive_id_type`, `text` or `content`) starts a fresh
  message; use it for unsolicited or standalone messages.
- Markdown is converted by the channel SDK to a Feishu post and split at safe
  boundaries when long. Successful send/reply results include the primary
  compound `message_id`, ordered `message_ids`, `chunk_count`, and `chunks`;
  every chunk of a topic reply stays in that topic.
- `edit` accepts text, markdown, post, or a complete schema-2.0 card and updates
  the persisted sent record after Feishu confirms the edit. Card edits replace
  the existing card in place through Feishu's native card update API. Feishu
  does not expose media messages through the same edit path.

## INTERACTIVE CARDS AND BUSINESS CALLBACKS

- `send` and `reply` accept a complete schema-2.0 interactive card through
  `content.type='card'`; `edit` replaces a previously sent card with another
  complete schema-2.0 card. The sent record keeps the exact card JSON, while
  its text preview extracts visible card text and never traverses button
  callback values.
- A business button click is admitted only when Feishu supplies an actor and
  that actor passes the account's `allowed_users` gate. Authorized callbacks
  are serialized per account/chat, durably deduplicated by Feishu's stable
  event id, persisted in the original conversation with
  `message_type='card_action'`, and wake the agent. Distinct later clicks on
  the same button remain distinct events even when actor, source card, and
  callback value are identical.
- `read` exposes the normalized callback under `card_action`, its exact
  `feishu_event_id`, the source card's `source_message_ref`, and the complete
  raw envelope under `feishu`. A callback record is not itself a Feishu
  message that can be replied to: use its `source_message_ref` to update the
  source card when appropriate, or `send` a fresh response to the callback's
  chat.
- The Feishu application must enable card callback delivery over the same
  long-connection mode and publish that configuration. If clicking a button
  produces only client-side success feedback but no `card_action` record or
  agent wake, verify that application callback setting; ordinary event
  subscriptions and messaging permissions do not prove card callbacks are
  being delivered.

## LOCAL COMMANDS AND CONTROL CARDS

- `/help`, `/status`, `/kanban`, `/system`, `/brief`, `/refresh`, `/sleep`,
  `/clear`, and `/taskcard` execute inside the Feishu MCP without an LLM call.
  Direct-message commands are handled immediately. Group and topic commands
  still pass the normal account `allowed_users` gate and require an explicit
  `@Bot`; unknown slash commands remain ordinary Agent input.
- Responses are updateable Feishu schema-2.0 control cards. `/kanban` exposes
  seven drill-down layers, `/system` provides document navigation, and buttons
  update their source control card in place. Internal control callbacks never
  become `card_action` inbox records and never wake the Agent. Ordinary
  business-card values keep the business callback behavior described above.
- Control-card clicks reuse the account actor/allowlist gate and the manager's
  per-account/chat serialization. An internal command executes only when its
  exact account/chat/source-message binding was registered after LingTai
  successfully sent the local control card; a reserved-looking value from any
  other card remains an ordinary business callback. Source bindings and stable
  Feishu event ids are stored only as bounded SHA-256 hashes in
  `feishu/control_callbacks.json`, so neither source IDs nor callback event IDs
  are exposed and a replay after refresh cannot repeat a local signal.
- User-facing card titles, navigation, command descriptions, and feedback use
  `agent.language`: `zh` is Chinese, `en` is English, and `wen` is literary
  Chinese. Unknown or missing languages use English.
- `/taskcard on|off` and `/taskcard N` (1–10) configure Feishu's Agent-wide
  resident-card presentation. The durable owner is
  `<workdir>/feishu/taskcard.json`; exact resident targets remain independently
  routed and persisted by `account + chat + optional thread`. Turning cards
  off suppresses projection without guessing or deleting unknown cards;
  turning them on reprojects known routes conservatively.
- `/refresh`, `/sleep`, and `/clear` write the same established Agent signals
  as the shared command core. Their control-card feedback stays local and does
  not create a second Agent conversation turn.

## OUTBOUND MEDIA, SHARES, AND STICKERS

- `send` and `reply` additionally accept `image`, `file`, `audio`, `video`,
  `share_chat`, `share_user`, and `sticker` content.
- Media uses one strict source: `{'type':'path','path':'/absolute/file'}` uploads
  a readable local file, while `{'type':'key','key':'<provider key>'}` reuses an
  already uploaded Feishu key. Relative paths and URL downloads are rejected;
  use a downloaded attachment path from `read`, or an explicit provider key.
  Provider keys must be owned by this Bot; a key copied from an inbound user
  message may be readable yet still be rejected for outbound reuse by Feishu.
- Shapes:
  `{'type':'image','source':SOURCE,'caption':'optional markdown'}`,
  `{'type':'file','source':SOURCE,'file_name':'optional name'}`,
  `{'type':'audio','source':SOURCE}`, and
  `{'type':'video','source':SOURCE,'caption':'optional markdown'}`.
  Image/video captions are rendered as Feishu post messages. File/audio
  captions are not supported by Feishu and are intentionally absent.
- Sharing/sticker shapes are
  `{'type':'share_chat','chat_id':'oc_...'}`,
  `{'type':'share_user','user_id':'ou_...'}`, and
  `{'type':'sticker','file_key':'...'}`.
- Sent records preserve the exact source descriptor for `read`, while bounded
  notification previews expose only safe media summaries such as type,
  filename, and size — never provider keys or the local source path.
- Each materialized wire chunk is attempted exactly once. A rejected post or
  caption is returned as a failure; it is never silently resent as plain text.

## READING: check / read / search

- `check`: list recent conversations with unread counts (optional `account`).
- `read`: read messages from one chat (`chat_id`; optional `limit`, `account`).
- `search`: regex search over inbox messages (`query`; optional `account`,
  `chat_id`).
- Reactions, read receipts, and Bot join/leave events are retained in the
  reserved `chat_id='events'` conversation. They do not enter the LICC
  notification mirror and never wake the agent; use `read` or `search` when
  the event history is relevant. Each record carries a concise `event`
  projection plus the complete raw envelope under `feishu`.
- Channel-event actors pass through the same account `allowed_users` gate.
  The reserved events conversation is read-only: do not use it as a `send`
  recipient.

## PLACEHOLDER / PROGRESS

- For responses that take more than ~5s, send `action='send'` with
  `placeholder=true` and interim text, Markdown, or post content. Feishu sends
  it as a native schema-2.0 progress card and returns a compound `message_id`.
- Update that same card with `action='edit'` only when the work enters a
  meaningful new phase. A progress-card edit remains a progress card even when
  the edit input uses text/Markdown/post; custom-card and media replacement are
  rejected on this path.
- Send the final answer as a separate durable `send` or `reply` message. Never
  edit the progress card into the final answer, and do not update it for every
  token or trivial internal step.
- Incoming messages receive the native `Typing` reaction while work is pending;
  it is removed when the first response/progress card is sent. Existing `OK`
  (seen) and `THUMBSUP` (done after reply) reactions continue independently.

## AUTOMATIC RESIDENT TASK CARD

- The Bot automatically maintains one schema-2.0 resident Task Card for every
  admitted `account + chat + optional thread` route. This is a mechanical,
  bounded projection of the agent's safe public `events.jsonl` rows; the model
  should not send, edit, answer, or otherwise manage it through the public
  `feishu` actions.
- Direct chats and ordinary group conversations receive a card in the chat.
  Topic messages receive their own resident card inside that exact topic. The
  automatic route never guesses a topic from another conversation.
- A card that is still last is updated in place. After this process observes a
  newer message below it, rotation is old-first: the exact persisted card is
  deleted (or confirmed gone) before one replacement is sent. A refresh has no
  trusted ordering high-water mark, so it conservatively updates the persisted
  card in place until a later message is actually observed; it never guesses
  and sends a duplicate.
- Automatic Task Cards and `placeholder=true` progress cards are independent.
  The automatic card summarizes agent behavior; a placeholder communicates a
  user-meaningful phase. Final answers remain separate durable messages.
- The same resident also carries the channel-neutral intrinsic Task Card body
  from `<workdir>/taskcard/taskcard.md` when `<workdir>/taskcard/status` is
  exact `active`. It is composed below the automatic frame under
  `— WATCH —`; the model manages that artifact only through the public
  intrinsic `task_card` tool, never through Feishu message actions.
- Exact `inactive` clears only the programmable `WATCH` slot and preserves the
  automatic frame. Missing, unreadable, invalid, or blank producer state is a
  no-op that preserves the last successfully delivered programmable frame.
  One route's delivery failure does not stop projection to other chats/topics.

## REACTIONS

- `react` adds or removes one Feishu reaction on a compound `message_id`.
  Adding requires `operation='add'` plus Feishu's symbolic `emoji_type` (for
  example `SMILE`) and returns the provider `reaction_id`. Removing requires
  `operation='remove'` plus that exact `reaction_id`; do not substitute an
  emoji glyph or `emoji_type` for removal.
- Add and remove are each attempted exactly once. A missing or revoked target
  returns `error_code='TARGET_REVOKED'` and is never converted into a new
  message or another reaction.

## CONTACTS / ACCOUNTS

- `contacts`: list saved contacts (optional `account`).
- `add_contact`: save a contact alias (`open_id`, `alias`; optional `name`,
  `chat_id`). Saving an alias does not grant inbound permission on its own.
- `remove_contact`: remove a contact (`alias` or `open_id`).
- `accounts`: list configured app accounts.

## MESSAGE IDS

- `message_id` is the compound id returned by read/check
  (`{alias}:{chat_id}:{feishu_message_id}`); pass it back verbatim to
  `reply`/`edit`/`delete`.

## INBOUND CONVERSATIONS

- Direct messages are admitted without an `@Bot` mention. Group and topic
  messages are admitted only when they explicitly mention this bot; `@all`
  alone does not wake it.
- `allowed_users`, when configured for an account, still filters the sender's
  `open_id` in both direct and group chats. Saving a contact does not change
  this admission rule.
- `read` preserves the legacy fields and adds `thread_id`, `root_id`,
  `reply_to`, resolved `mentions`, the SDK-normalized `content` union, normalized
  sender identity fields, downloaded `attachments`, and the complete raw event
  under `feishu`.
- Image, file, audio, video, sticker, video-cover, and rich-post resources are
  stored under the message's `attachments/` directory. Each attachment keeps
  its Feishu `type` and `file_key`; `status='downloaded'` adds the safe local
  `filename`, absolute `path`, and byte `size`, while `status='failed'` keeps
  the original descriptor plus a bounded `error` instead of discarding it.
- The current message's persistent notification context includes at most eight
  secret-safe attachment projections (`type`, download/transcription status,
  local `path`, filename, and size). Provider file keys and the raw envelope
  remain on `read` only. When the user's intent depends on an image, inspect
  the listed path with `vision`; use the appropriate local tool/skill for
  documents, audio, or video instead of replying from the media placeholder.
- Audio messages continue through local Whisper transcription after download.
  A successful transcript remains in `voice_transcript` and becomes the message
  text. Download or transcription failure stays attached to the resource
  record, while the normalized content/raw envelope remain available for
  diagnosis; failure is not collapsed into a text-only message.
- For group commands, the normalized `text` removes this bot's own mention;
  other resolved mentions remain visible. `content.kind` identifies the
  original Feishu content family.
- Topic/thread routing metadata drives `reply`: an omitted `reply_in_thread`
  follows the persisted target's `thread_id`, so topic messages stay in their
  topic while ordinary messages remain flat.

## NOTIFICATIONS: TRANSIENT HOOK vs PERSISTENT CONTEXT

Inbound Feishu messages surface to the agent in two `_meta` lanes:

- `_meta.agent_meta.notifications.attention.mcp.feishu` — a compact
  high-attention hook only: `data.message_ids` and dismiss guidance, never
  message text or routing context.
- `_meta.agent_meta.notifications.persistent.mcp.feishu` — durable context:
  recent conversation messages (bounded text, both directions), sender/chat
  routing hooks, reply refs when present, and per-message comments for the
  agent's own outgoing messages or truncated text.

The feishu tool remains the source of truth: neither lane marks anything read,
so use `read`/`check` for exact producer state — especially when a persistent
message is truncated. Reply in Feishu when the message arrived through Feishu
(`reply` with the compound message id, or `send` to the chat/open_id). After
handling, dismiss the transient hook with
`notification.dismiss_channel("mcp.feishu")`; the persistent block is context
history, not unread state. Generic mirror-vs-canonical-state and dismiss-safety
rules live in
[`notification-manual`](../../intrinsic_skills/notification-manual/SKILL.md).

## SIDE EFFECTS & ERROR SURFACING

- `send`, `reply`, `edit`, `delete`, and `react` affect real Feishu state — they are external
  side effects, so confirm recipient and content before sending unsolicited
  messages.
- Every action failure has the stable fields `status='failed'`, compatible
  `error` text, identical `message`, `error_code`, `retryable`, and
  `retry_after_seconds` (number or null). Permission, format, target-revoked,
  and rate-limit failures retain their channel classification. Start a new
  attempt only when `retryable=true`, and honor a non-null
  `retry_after_seconds`; the Bot never hides an automatic outbound retry.

## PUBLIC TOOL FAMILY: strict LTP-v2

Raw MCP discovery exposes exactly one public tool, `feishu`. It is an
independent strict LTP-v2 family with the closed root
`{action, input, reasoning, summarize?}` (`action`, `input`, and `reasoning`
required) and a closed action-owned input branch. `feishu` actions are exactly
`send`, `check`, `read`, `reply`, `react`, `search`, `delete`, `edit`,
`contacts`, `add_contact`, `remove_contact`, `accounts`, and `manual`. The `manual` action
is the discovery path for this packaged doc. Do not use the retired flat/legacy
shape, `_reasoning`, aliases, or a generic dispatcher.
