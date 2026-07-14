---
name: licc-notification-contract
description: >
  Contract for LICC/MCP notification events and their model-visible projection
  into _meta.notifications and _meta.notification_persistent.
status: active
contract_version: 3
last_changed_at: "2026-07-14"
related_files:
  - src/lingtai/tools/mcp/ANATOMY.md
  - src/lingtai/services/mcp_inbox.py
  - src/lingtai/services/mcp_licc.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai/mcp_servers/whatsapp/manager.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/messaging.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/llm/claude_code/adapter.py
  - src/lingtai/tools/email/primitives.py
  - tests/test_licc_notification_contract_doc.py
  - tests/test_mcp_inbox.py
  - tests/test_meta_block.py
  - tests/test_notification_sync.py
  - tests/test_telegram_notification_read_state.py
  - tests/test_wechat_notification_metadata.py
  - tests/test_feishu_notification_metadata.py
  - tests/test_whatsapp_notification_metadata.py
  - tests/test_timely_transient_serialization.py
  - tests/test_layers_email.py
review_triggers:
  - src/lingtai/services/mcp_inbox.py
  - src/lingtai/services/mcp_licc.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/imap/manager.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai/mcp_servers/whatsapp/manager.py
  - src/lingtai/mcp_servers/cloud_mail/manager.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/messaging.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/tools/email/ANATOMY.md
  - src/lingtai/tools/email/
  - src/lingtai/tools/email/primitives.py
  - src/lingtai/tools/notification/
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/llm/interface_converters.py
  - src/lingtai/llm/claude_code/adapter.py
maintenance: |
  Keep this file in the same maintenance graph as the ANATOMY.md files listed
  under related_files. If any review_triggers path changes notification event
  identity, preview/body placement, persistent context, dismissal semantics, or
  producer/source-of-truth boundaries, re-read this contract in the same change
  and either update it or explicitly state in the PR why no contract update was
  needed.
---
# LICC Notification Contract

> **Maintenance trigger:** any change to a path listed in `review_triggers` must
> re-check this contract in the same change. The PR should either update this
> document or say why the notification contract still holds.

## What this is

This document is the cross-module contract for **LICC notification events** and
how they become model-visible notification metadata. It is intentionally
anatomy-style: it maps the moving parts, cites the code that owns them, names the
state boundary, and gives future agents a checklist for deciding whether a
change must also update the contract.

Scope:

- LICC v1 producer/consumer files under `.mcp_inbox/`.
- Coalesced `.notification/mcp.<server>.json` files produced from LICC events.
- The `_meta.notifications` high-attention hook visible to the agent.
- The `_meta.notification_persistent` communication-context lane when a producer
  has one (currently Telegram, WeChat, Feishu, WhatsApp, and built-in email).
- Producer-owned read/reply/dismiss state that remains the source of truth.

Non-scope: the low-level Telegram Bot API, IMAP protocol semantics, frontend UI
rendering, and unrelated notification producers such as `soul` or `goal` except
where they share the `.notification/` filesystem protocol.

## Components

1. **LICC event file (producer -> kernel inbox).** Out-of-process MCPs write one
   JSON event to `<agent_dir>/.mcp_inbox/<mcp_name>/<event_id>.json`; the event
   schema is `licc_version`, `from`, `subject`, `body`, optional `metadata`,
   `wake`, and `received_at` (`src/lingtai/services/mcp_inbox.py:58-116`). The
   canonical producer helper is `push_inbox_event(...)`, which validates the
   payload and writes atomically through `.json.tmp` + `fsync` + `os.replace`
   (`src/lingtai/services/mcp_licc.py:80-145`).
2. **LICC inbox consumer (kernel inbox -> `.notification`).** `MCPInboxPoller`
   validates/coalesces events per MCP, extracts bounded preview metadata, and
   publishes one `.notification/mcp.<name>.json` per sweep
   (`src/lingtai/services/mcp_inbox.py:183-219`,
   `src/lingtai/services/mcp_inbox.py:320-391`).
3. **Notification filesystem envelope.** `notifications.submit` is the standard
   envelope builder (`header`, `icon`, `priority`, `published_at`, optional
   `instructions`, and producer-owned `data`) and `notifications.publish` writes
   it atomically (`src/lingtai/kernel/notifications.py:260-275`,
   `src/lingtai/kernel/notifications.py:1054-1087`).
4. **Model-visible transient hook.** `_meta.notifications` is sparse and
   update-driven. `attach_active_notifications` attaches or moves the canonical
   payload only on first appearance, material change, or deliberate
   `notification(action="check")` (`src/lingtai/kernel/meta_block.py:2939`).
   For IM channels with a persistent lane (Telegram, WeChat, Feishu, WhatsApp),
   the shared
   `_sanitize_im_notification_after_persistent` (via the per-channel
   `sanitize_telegram_notification_after_persistent` /
   `sanitize_wechat_notification_after_persistent` /
   `sanitize_feishu_notification_after_persistent` /
   `sanitize_whatsapp_notification_after_persistent` wrappers) reduces
   `_meta.notifications.mcp.<channel>.data` to stable `message_ids` only; content,
   sender/subject, routing details, counts, and summaries must not remain in the
   transient lane (`src/lingtai/kernel/meta_block.py:2589-2655`).
5. **Model-visible persistent communication context.** When structured IM metadata is available, `build_notification_persistent_payload` emits `_meta.notification_persistent.mcp.<channel>` with `messages`, `events`, and comments through the shared `_ImPersistentLane` machinery. Delta lanes (Telegram, WeChat, Feishu) also carry `previous_block`; WhatsApp is snapshot/no-previous-block because the producer sends the current bounded conversation window per event. Telegram additionally carries full out-of-window reply targets under `referenced_messages`. For built-in email it emits `_meta.notification_persistent.email` with `email_ids` plus full unread email bodies for the current unread snapshot (ordinary sends are capped at 50,000 characters so the notification layer does not truncate)
   (`src/lingtai/kernel/meta_block.py:1857-2489`). The Telegram MCP supplies the
   structured `recent_messages`, `latest_incoming`, and `referenced_messages`
   metadata. Every Telegram message object in those fields carries the explicit
   current-agent boolean `taskcard`; it is derived at producer projection time
   (including old/referenced messages) and means Task Card delivery is allowed or
   hidden, not that automatic or programmable mechanics have stopped
   (`src/lingtai/mcp_servers/telegram/manager.py:950-985`,
   `src/lingtai/mcp_servers/telegram/manager.py:1032-1055`). Telegram's textual
   notification body likewise labels every rendered message/reply-target line
   with the current `taskcard: True|False`, including its degraded fallback
   (`src/lingtai/mcp_servers/telegram/manager.py:1075-1114`); the WeChat
   MCP supplies `recent_messages` and `latest_incoming` built from its merged
   inbox+sent preview window with per-message text bounded at 500 chars
   (`src/lingtai/mcp_servers/wechat/manager.py:835-956`); Feishu supplies the
   same bounded structured fields from its merged inbox+sent preview window; and
   WhatsApp supplies a bounded current snapshot from its local inbox/sent store
   without raw Cloud API payloads. Each delta lane's seed/delta boundary matches
   its producer preview window (Telegram 20, WeChat 10, Feishu 10).
6. **Producer source of truth.** Notification files are mirrors/hooks, not the
   authoritative mailbox/chat store. Producer tools own real state changes and
   side effects. Email is the built-in mirror example: unread mail state is rendered into `.notification/email.json`, model-visible content is projected into `_meta.notification_persistent.email`, and read/dismiss/reply actions live on the email tool (`src/lingtai/kernel/base_agent/messaging.py:60-130`).

## Connections

The flow is:

```text
MCP/server state
  -> LICC event file (.mcp_inbox/<mcp>/<event>.json)
  -> MCPInboxPoller coalesced notification (.notification/mcp.<mcp>.json)
  -> BaseAgent notification sync (IDLE synthetic notification check, or ACTIVE
     sparse _meta attachment)
  -> _meta.notifications high-attention hook
  -> optional _meta.notification_persistent communication context
  -> producer tool read/reply/dismiss for exact data and state changes
```

The transient and persistent lanes have different jobs:

| Lane | Job | Must not become |
|---|---|---|
| `.notification/<channel>.json` | Producer-owned live mirror/hook that wakes the agent and carries enough structured data for the kernel projection. | A durable arrival log. |
| `_meta.notifications.<channel>` | High-attention hook saying "this producer needs handling" and carrying only the minimal identity needed to correlate/dismiss. | A second copy of chat/mail content when a persistent lane exists. |
| `_meta.notification_persistent...` | Context lane for conversation content, routing hooks, and continuity comments that should survive sparse notification movement. | A dismiss/action channel or producer source of truth. |
| Producer tool/store | Exact read/reply/dismiss state and external side effects. | A passive mirror inferred from `_meta`. |

### New human-message LICC channel acceptance gate

A new human-message LICC channel is not complete until its PR defines both
model-visible surfaces and the producer authority boundary:

1. **Transient attention hook** (`_meta.notifications.mcp.<channel>`): wakes the
   agent and carries only identity needed to correlate, reply, read, or dismiss
   (for IM channels, `data.message_ids` or an equivalent stable event-id list,
   plus generic scaffolding such as `header`, `priority`, and `published_at`). It
   must not carry message bodies, summaries, sender/subject, conversation refs,
   platform/routing refs, counts, or `content_moved_to` breadcrumbs once a
   conventional persistent path exists.
2. **Persistent context lane** (`_meta.notification_persistent...`): carries
   bounded conversation/mail context, events/routing hooks, continuity comments,
   and producer-safe metadata. Delta lanes should include `previous_block`;
   snapshot lanes must explicitly document why they have no `previous_block` or
   delivery tracker. This lane is replay context, not an action channel or source
   of truth.
3. **Producer authority**: exact read/reply/dismiss state and external side
   effects remain on the producer tool/store.
4. **Tests**: the PR must add or update shape tests proving the transient hook is
   identity-only and that content/context lands in the persistent lane; snapshot
   lanes should also lock the no-`previous_block` / no-delivery-tracker behavior.

If a producer cannot yet provide persistent context, bounded previews may remain
only as transitional generic LICC triage, and the PR must state why the two-lane
contract is not active yet.

## Contract rules

### 1. Stable event identity is required

Human-message producers MUST expose stable IDs for the actionable event. Telegram
uses compound message IDs; after PR #705 the transient hook carries them as
`data.message_ids` and nothing else. Email migration should use the analogous
`data.email_ids` shape when email content moves to a persistent lane.

### 2. Content has one model-visible home

When a producer has a persistent context lane, message content and routing context
MUST be moved there, not duplicated into `_meta.notifications`. For Telegram that
means:

```json
"_meta": {
  "notifications": {
    "mcp.telegram": {
      "header": "Telegram event",
      "data": {"message_ids": ["mimo-1:6859932159:6281"]},
      "instructions": "High-attention Telegram hook: use notification_persistent for content/context; when handled, dismiss this notification."
    }
  },
  "notification_persistent": {
    "mcp": {
      "telegram": {"messages": [...], "events": [...], "previous_block": {...}}
    }
  }
}
```

WeChat follows the same shape at `mcp.wechat`, with `data.message_ids` carrying
the producer's landed local message ids and content/context living in
`_meta.notification_persistent.mcp.wechat`.

Feishu follows the same delta-lane shape at `mcp.feishu`. WhatsApp follows the
same transient identity-hook shape at `mcp.whatsapp`, but its persistent lane is
snapshot-style: every material block carries the producer's current bounded
conversation context and deliberately has no `previous_block`.

The transient hook may keep generic notification scaffolding (`header`, `icon`,
`priority`, `published_at`) but not body text, previews, sender/subject,
conversation refs, platform/routing refs, counts, summaries, or explanatory
`content_moved_to` fields when the key path already identifies the producer and
the persistent lane is conventional.

### 3. Bounded previews are transitional unless no persistent lane exists

Generic LICC notifications still publish bounded `data.previews` in the
producer-owned `.notification/mcp.<name>.json` envelope. That raw file is an
internal mirror and may be used by the kernel to build a persistent lane. Once a
specific human-message producer has a persistent lane, the model-visible
transient projection MUST sanitize those previews away. For producers that have
not yet been migrated, bounded previews are tolerated as a transitional triage
surface; adding a persistent lane turns on the move-not-duplicate rule.

### 4. Persistent blocks are context, not unread state

`_meta.notification_persistent` can remain in history after the transient hook is
handled. It is a communication-context lane, not a notification action channel.
Do not dismiss it, do not mutate producer state from it, and do not treat its
presence as proof that there is still an unhandled event. Telegram persistent
blocks must keep `previous_block` hooks so later deltas can point at earlier
context without re-sending all history.

Snapshot lanes with no `previous_block` (email, WhatsApp) make a stronger
promise than the delta lanes: each block IS the producer's ENTIRE current
bounded/unread state as of that stamping — an atomic whole, not an increment
on top of history and not a set of independent per-id records. Email's
correlated fields (`count`, `newest_received_at`, `context_comment`,
`email_ids`, `emails`) all describe that one snapshot together and must never
be read or reconstructed as a mix of two different snapshots.

Because canonical history keeps every historical block (no retroactive
strip), a stale nonempty email snapshot would otherwise sit in model-facing
full-history replay looking exactly as current as a later one — or, once
unread count reaches zero and the producer stops emitting any email payload
at all, would remain the ONLY email evidence in history forever with no
signal that it had been superseded, including across a process restart where
no in-memory holder survives to notice the transition. Three mechanisms
together close this, none of them by having a converter query live
mailbox/disk state:

- **Whole-snapshot replay filtering.** `lingtai/llm/interface_converters.py`
  scans the full history once per full-history render
  (`newest_email_snapshot_holder`) and keeps exactly one authoritative
  `notification_persistent.email` child — the block holding the newest
  occurrence in wire order (a live snapshot or an explicit clear tombstone)
  — while removing the ENTIRE child (never a partial id/field subset) from
  every other block. All five model-facing full-history renderers
  (`to_anthropic`, `to_openai`, `to_responses_input`, `to_gemini`, and
  `llm/claude_code/adapter.py::ClaudeCodeChatSession._render_conversation`)
  call the same internal `_render_full_history_result` primitive, so none of
  them can silently skip this projection. The established two-argument
  public helper `filter_stale_timely_transient(block, newest)` is a SEPARATE,
  unchanged function — it never looks at `notification_persistent` at all —
  preserved for any caller outside these five renderers.
- **In-process explicit clear marker.** The producer (`tools/email/
  primitives.py::_rerender_unread_digest`) deletes `.notification/email.json`
  when unread count reaches zero, which on its own leaves no wire evidence
  that the snapshot ended. `attach_active_notifications`
  (`src/lingtai/kernel/meta_block.py`) is the owner of this clear-transition
  WHILE THE PROCESS KEEPS RUNNING: whenever it observes that a live email
  snapshot has become absent, it stamps a durable, append-only tombstone in
  its place — `{"cleared": true, "cleared_at": <iso>}`, built by
  `meta_block.build_email_persistent_cleared_marker` — onto whatever
  dict-shaped tool result is available that turn. The tombstone carries no
  message content. Once stamped, it is itself the newest authoritative email
  state and the whole-snapshot filter above removes every earlier nonempty
  snapshot the same way it would for a newer nonempty snapshot. If no
  dict-shaped result exists that turn, the witnessed transition is retained
  as a bounded pending-clear intent (`agent._email_pending_clear`, a single
  boolean, not a log) and consumed exactly once on the next turn that offers
  a carrier. The context-molt batch is a DISTINCT seam: it bypasses
  `attach_active_notifications` entirely and instead calls
  `skeletonize_notification_holder` directly on the live holder
  (`base_agent/turn.py`). That holder can be a SYNTHESIZED IDLE/ASLEEP-wake
  pair (built by `_inject_notification_pair`), whose content
  `skeletonize_notification_holder` destructively wipes in place — unlike an
  ordinary tool-result holder, whose content it leaves untouched. Before that
  destructive wipe, `meta_block.note_email_clear_intent_before_holder_destroyed`
  inspects the outgoing holder and notes the same pending-clear flag if it
  was carrying a live email snapshot, so the obligation survives the
  destruction of its only evidence. This mechanism has an implicit deadline
  (the next real tool result), which is always reachable while the process
  is alive; if the process exits first, the next start's reconciliation
  (below) independently re-derives the same conclusion.
- **Startup/refresh reconciliation — the actual immediate-first-render
  guarantee.** A freshly started process has no live
  `_notification_live_holder` to compare against (it always initializes to
  `None`), AND the very first model-facing render can happen before any tool
  call exists at all — so the in-process mechanism above, and a bare
  in-memory flag, are both structurally too late: nothing would read a flag
  until a tool result exists, but the leak happens on render, not on tool
  dispatch. `meta_block.reconcile_email_persistent_history` closes this gap
  directly rather than deferring it: it runs once, synchronously, from
  `base_agent/lifecycle.py::_start` immediately after chat history is
  restored and BEFORE the main message-loop thread (`agent._thread`) is
  created — so before anything could possibly drive a
  `session.send()`/render. (The heartbeat thread is already running by this
  point, but it only writes liveness; it does not yet run notification sync
  or signal handling until `_heartbeat_runtime_ready` flips true, so it does
  not race this reconciliation.) It scans restored history for the newest
  email child and compares it against the CURRENT authoritative producer
  state (read once via the same `_collect_active_notifications_payload`
  helper `attach_active_notifications` uses — never a converter-side disk
  query), with both sides normalized through the SAME redaction boundary
  `_save_chat_history` already applies
  (`trace_redaction.redact_for_trajectory`) before comparing — a historical
  child was necessarily redacted before reaching disk, while the freshly
  rebuilt current child is raw, so comparing them raw would never match once
  an unread body contains a secret-shaped substring, and would append (and
  re-save) a new record on every single restart even though the producer's
  state never changed. `redact_for_trajectory` is deterministic and
  idempotent on already-redacted input, so no second redactor, weaker
  redaction, or stored raw-secret fingerprint is needed to make this stable.
  When the two do not already match, it appends exactly one well-paired,
  NARROW startup reconciliation record directly into the restored
  `ChatInterface` — an `(assistant ToolCallBlock, user ToolResultBlock)` pair
  shaped like the live sync loop's synthesized `notification` pair (same
  block types/names, `synthesized=True` on the result, appended via the
  interface's own `add_assistant_message`/`add_tool_results`, never a fake
  user-text message), but it is intentionally NOT byte-shape-identical to a
  real `_inject_notification_pair` delivery: it carries only `_synthesized`
  plus `_meta.notification_persistent.email` — no `_meta.notifications`
  attention hook, no `notification_guidance`, no `build_meta`
  freshness/`injection_seq` fields, no poison/session-ensure handling, and no
  live-holder/fingerprint/logging side effects, since this is a one-shot
  startup correction of already-true state rather than a new attention event,
  and a fresh process has no live holder to register against and no poisoned
  prior interface to protect. It then best-effort persists the appended
  record via `_save_chat_history` — that call is wrapped in its own
  `try/except`; a write failure there is not fatal and is not silently
  treated as guaranteed durability: the append already improved THIS
  process's own in-memory history and therefore its renders, and if the save
  failed and the process exits before a later successful one, the next
  restart's reconciliation independently re-derives and re-appends the same
  conclusion. This makes the authoritative email state ACTUALLY PRESENT in
  canonical wire history before any renderer runs, rather than merely
  recording an intent for something else to act on later. It is a no-op when
  history has no email child at all, when the newest child already matches
  current producer state exactly at the redaction-normalized comparison
  (including matching on "already cleared"), and deterministic/idempotent
  across repeated restarts — re-running it against the same history and
  producer state reaches the same conclusion, including when the standing
  unread email's body contains secret-shaped text that would otherwise
  differ byte-for-byte between the raw current value and its redacted
  historical copy. It never rewrites/mutates an existing block and never
  queries a converter.

The producer (`email.read`) remains the source of truth regardless of any of
these three mechanisms; together they only prevent a superseded snapshot (or
a snapshot that has since gone to zero, including across a restart) from
misrepresenting itself as current sender/body/count during replay — and,
critically, they do so before the FIRST render a restarted process can
produce, not merely before some later render. Delta lanes are unaffected by
any of them — their `previous_block` continuity is exactly why they are NOT
filtered this way.

### 5. Producer tools own side effects and clearing

The producer tool remains the source of truth for exact reads, replies, and
state-clearing side effects. Generic notification dismissal only clears the
notification mirror/hook; it must not be used to pretend producer state changed.
For source-of-truth mirrors (email today), prefer producer-specific
`read`/`dismiss` verbs. For content-free LICC hooks whose event was handled via
the producer tool, generic `notification.dismiss_channel("mcp.<name>")` may clear
the high-attention hook.

### 6. Secret and volume boundaries are part of the contract

Producer metadata copied into `.notification` or `_meta.notification_persistent`
MUST be bounded and secret-safe. The MCP inbox copies only allowlisted scalar IM
metadata and bounded JSON-safe structured fields (`src/lingtai/services/mcp_inbox.py:183-219`).
Curated MCPs that add new structured metadata must update this contract and tests
before the new field becomes model-visible. Telegram's structured message shape
includes the non-secret boolean `taskcard` on every item in `recent_messages`,
`latest_incoming`, and `referenced_messages`; the MCP inbox bounded JSON copy and
persistent lane preserve that field without moving it into the transient identity
hook.

## State

Persistent/on-disk state involved in this contract:

- `.mcp_inbox/<mcp_name>/<event_id>.json` — transient LICC inbox file, consumed
  and deleted by the poller after successful dispatch; invalid files move to
  `.dead/`.
- `.notification/mcp.<name>.json` — producer-owned live mirror. One file per
  channel, overwritten or cleared as current producer state changes.
- Producer stores such as Telegram/WeChat/Feishu/WhatsApp inbox/sent JSON and
  email mailbox state —
  authoritative source for exact read/reply/dismiss semantics.
- Chat history/tool results — where `_meta.notifications` and
  `_meta.notification_persistent` blocks are recorded for the model and replay.
  For email, this is also where the `{"cleared": true, "cleared_at": ...}`
  tombstone lives once unread count reaches zero: it is ordinary durable wire
  content (a tool result's `_meta`), not a separate store, so it survives
  refresh/full-history replay the same way any other stamped `_meta` block
  does — no new persistence mechanism was added.

In-memory state involved in this contract:

- `agent._notification_live_holder` and `_notification_payload_signature` control
  sparse movement of the transient notification payload. The in-process
  clear-transition detection for email (comparing the outgoing live holder's
  `notification_persistent.email` against the freshly computed payload) reads
  this same holder.
- `agent._email_pending_clear` — a single bounded boolean (not a log), set
  in two places: (1) by `attach_active_notifications` when it witnesses a
  live-to-absent email transition it cannot stamp this turn (no dict-shaped
  carrier); and (2) by
  `meta_block.note_email_clear_intent_before_holder_destroyed`, called from
  `base_agent/turn.py`'s context-molt branch immediately before it calls
  `skeletonize_notification_holder` directly on the live holder — a
  SYNTHESIZED IDLE/ASLEEP-wake holder's live email content is destructively
  wiped in place by that call (unlike an ordinary tool-result holder, whose
  content skeletonize leaves untouched), so the flag must be noted from the
  still-intact holder before that happens. Consumed exactly once, by
  `attach_active_notifications`, on the next turn that offers a dict-shaped
  carrier; consuming it never manufactures a false clear, because the
  consumer is itself gated on the current round's email actually being
  absent. This flag is an IN-PROCESS-ONLY mechanism: it plays no role in the
  cross-restart/first-render guarantee, which
  `reconcile_email_persistent_history` satisfies by appending a startup
  reconciliation record directly into canonical history (see Contract rule
  4) rather than by setting this flag; it is used only as a narrow fallback
  if `reconcile_email_persistent_history` itself finds the wire has
  unanswered tool_calls at startup (an unexpected shape for freshly
  restored, quiescent history) and refuses to append rather than risk a
  malformed pairing.
- `agent._notification_persistent_telegram_message_ids` /
  `_notification_persistent_telegram_last_tool_id`, the WeChat counterparts
  `agent._notification_persistent_wechat_message_ids` /
  `_notification_persistent_wechat_last_tool_id`, and the Feishu counterparts
  `agent._notification_persistent_feishu_message_ids` /
  `_notification_persistent_feishu_last_tool_id` track per-channel delivery into
  the current provider context (reset on molt). WhatsApp is snapshot-only and
  keeps no agent-side delivery tracker. Email is also snapshot-only and keeps
  no per-message delivery tracker.

## Review triggers

Re-check this contract whenever a change touches any of these areas:

- LICC schema, validation, atomic write, or dead-letter handling:
  `src/lingtai/services/mcp_inbox.py`, `src/lingtai/services/mcp_licc.py`.
- `.notification` helper semantics, channel allowlists, publish/clear/dismiss,
  or generic-dismiss guards: `src/lingtai/kernel/notifications.py` and
  `src/lingtai/tools/notification/`.
- Notification injection, sparse live-holder movement, `notification_guidance`,
  `_meta.notifications`, `_meta.notification_persistent`, or sanitizer logic:
  `src/lingtai/kernel/meta_block.py`, `src/lingtai/kernel/base_agent/__init__.py`,
  and `src/lingtai/kernel/base_agent/turn.py`.
- Built-in producers that create notification mirrors or human-message metadata:
  `src/lingtai/kernel/base_agent/messaging.py`, `src/lingtai/tools/email/`,
  and curated messaging MCP managers under `src/lingtai/mcp_servers/`.
- Tests that lock notification shape, curated IM persistent context, MCP inbox
  delivery, or email notification behavior.

## Current implementation status

- **Telegram:** compliant with the content split. Transient
  `_meta.notifications.mcp.telegram` is an identity-only high-attention hook;
  content/context lives in `_meta.notification_persistent.mcp.telegram`.
- **WeChat:** compliant with the content split. The producer attaches
  structured `recent_messages`/`latest_incoming` plus generic routing keys to
  its LICC events; transient `_meta.notifications.mcp.wechat` is an
  identity-only high-attention hook; content/context lives in
  `_meta.notification_persistent.mcp.wechat`. WeChat inbox/sent records and
  `wechat.read` remain source of truth.
- **Feishu:** compliant with the content split. The producer attaches bounded
  `recent_messages`/`latest_incoming` plus generic routing keys; transient
  `_meta.notifications.mcp.feishu` is identity-only; content/context lives in
  the Feishu delta lane at `_meta.notification_persistent.mcp.feishu`.
- **WhatsApp:** compliant with the content split. The producer attaches bounded
  current conversation context plus generic routing keys; transient
  `_meta.notifications.mcp.whatsapp` is identity-only; content/context lives in
  the WhatsApp snapshot lane at `_meta.notification_persistent.mcp.whatsapp`
  with no `previous_block` or delivery tracker.
- **Generic LICC/MCP:** still publishes bounded previews into the raw
  `.notification/mcp.<name>.json` mirror. That is allowed until the producer has
  a persistent context lane.
- **Email:** migrated to the same attention/context split. Transient `_meta.notifications.email` is an identity-only high-attention hook carrying `email_ids`; unread context lives in `_meta.notification_persistent.email` as an atomic whole-snapshot lane (no `previous_block`); a transition to zero unread is an explicit `{"cleared": true, "cleared_at": ...}` tombstone, not silence; model-facing full-history replay keeps only the newest whole snapshot-or-clear state and removes every earlier child in full (never a per-id splice); the email tool/store remains source of truth.

## Notes

This contract deliberately separates **attention** from **context** from
**authority**. The agent needs an attention hook to wake and decide what to do,
context to understand human messages without re-reading every time, and producer
tools to perform real state changes. Mixing those three was the source of the
Telegram transient-content regressions that PRs #700, #704, and #705 resolved.
