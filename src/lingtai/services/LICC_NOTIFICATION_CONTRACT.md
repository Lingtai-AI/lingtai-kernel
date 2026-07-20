---
name: licc-notification-contract
description: >
  Contract for LICC/MCP notification events and their model-visible projection
  into _meta.agent_meta.notifications.attention and _meta.agent_meta.notifications.persistent.
status: active
contract_version: 2
last_changed_at: "2026-07-15"
related_files:
  - docs/references/licc-notification-wake-runbook.md
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
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/notifications.py
  - tests/test_licc_notification_contract_doc.py
  - tests/test_mcp_inbox.py
  - tests/test_meta_block.py
  - tests/test_notification_sync.py
  - tests/test_telegram_notification_read_state.py
  - tests/test_wechat_notification_metadata.py
  - tests/test_feishu_notification_metadata.py
  - tests/test_whatsapp_notification_metadata.py
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
  - src/lingtai/tools/email/ANATOMY.md
  - src/lingtai/tools/email/
  - src/lingtai/tools/notification/
  - src/lingtai/kernel/meta_block.py
  - src/lingtai/kernel/notifications.py
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

For diagnosis and live recovery when producer input lands but no reasoning turn
starts, use the operational
[LICC notification wake runbook](../../../docs/references/licc-notification-wake-runbook.md).
This contract remains the normative source for metadata shape and authority.

Scope:

- LICC v1 producer/consumer files under `.mcp_inbox/`.
- Coalesced `.notification/mcp.<server>.json` files produced from LICC events.
- The `_meta.agent_meta.notifications.attention` high-attention hook visible to the agent.
- The `_meta.agent_meta.notifications.persistent` communication-context lane when a producer
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
4. **Model-visible transient hook.** `_meta.agent_meta.notifications.attention` is sparse and
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
   `_meta.agent_meta.notifications.attention.mcp.<channel>.data` to stable `message_ids` only; content,
   sender/subject, routing details, counts, and summaries must not remain in the
   transient lane (`src/lingtai/kernel/meta_block.py:2589-2655`).
5. **Model-visible persistent communication context.** When structured IM metadata is available, `build_notification_persistent_payload` emits `_meta.agent_meta.notifications.persistent.mcp.<channel>` with `messages`, `events`, and comments through the shared `_ImPersistentLane` machinery. Delta lanes (Telegram, WeChat, Feishu) also carry `previous_block`; WhatsApp is snapshot/no-previous-block because the producer sends the current bounded conversation window per event. Telegram additionally carries full out-of-window reply targets under `referenced_messages`. For built-in email it emits `_meta.agent_meta.notifications.persistent.email` with `email_ids` plus full unread email bodies for the current unread snapshot (ordinary sends are capped at 50,000 characters so the notification layer does not truncate)
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
   side effects. Email is the built-in mirror example: unread mail state is rendered into `.notification/email.json`, model-visible content is projected into `_meta.agent_meta.notifications.persistent.email`, and read/dismiss/reply actions live on the email tool (`src/lingtai/kernel/base_agent/messaging.py:60-130`).

## Connections

The flow is:

```text
MCP/server state
  -> LICC event file (.mcp_inbox/<mcp>/<event>.json)
  -> MCPInboxPoller coalesced notification (.notification/mcp.<mcp>.json)
  -> BaseAgent notification sync (IDLE synthetic notification check, or ACTIVE
     sparse _meta attachment)
  -> _meta.agent_meta.notifications.attention high-attention hook
  -> optional _meta.agent_meta.notifications.persistent communication context
  -> producer tool read/reply/dismiss for exact data and state changes
```

The transient and persistent lanes have different jobs:

| Lane | Job | Must not become |
|---|---|---|
| `.notification/<channel>.json` | Producer-owned live mirror/hook that wakes the agent and carries enough structured data for the kernel projection. | A durable arrival log. |
| `_meta.agent_meta.notifications.attention.<channel>` | High-attention hook saying "this producer needs handling" and carrying only the minimal identity needed to correlate/dismiss. | A second copy of chat/mail content when a persistent lane exists. |
| `_meta.agent_meta.notifications.persistent...` | Context lane for conversation content, routing hooks, and continuity comments that should survive sparse notification movement. | A dismiss/action channel or producer source of truth. |
| Producer tool/store | Exact read/reply/dismiss state and external side effects. | A passive mirror inferred from `_meta`. |

### New human-message LICC channel acceptance gate

A new human-message LICC channel is not complete until its PR defines both
model-visible surfaces and the producer authority boundary:

1. **Transient attention hook** (`_meta.agent_meta.notifications.attention.mcp.<channel>`): wakes the
   agent and carries only identity needed to correlate, reply, read, or dismiss
   (for IM channels, `data.message_ids` or an equivalent stable event-id list,
   plus generic scaffolding such as `header`, `priority`, and `published_at`). It
   must not carry message bodies, summaries, sender/subject, conversation refs,
   platform/routing refs, counts, or `content_moved_to` breadcrumbs once a
   conventional persistent path exists.
2. **Persistent context lane** (`_meta.agent_meta.notifications.persistent...`): carries
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

Curated producers may additionally attach an additive per-update `event_id`
(Telegram: `<account>:update:<update_id>`) in LICC event metadata. The consumer
copies it into `data.previews[*]` alongside the other scalar routing refs, and
the kernel carries it into persistent `events[]` and uses it (falling back to
the compound `id`) for persistent message de-duplication and delivery
tracking — so repeated events that share one compound message id (e.g. two
callback presses on the same inline keyboard) never collapse into one
persistent entry. For a merged edited record the structured `event_id` is the
producer envelope's additive `current_event_id` (the last-applied edit's
identity, advanced on every matched edit while the immutable root `event_id`
stays inside `telegram`) — so an edit arriving after the original was already
delivered into a warm persistent context is re-delivered together with its
append-only raw edit evidence instead of being delta-filtered away.

### 2. Content has one model-visible home

When a producer has a persistent context lane, message content and routing context
MUST be moved there, not duplicated into `_meta.agent_meta.notifications.attention`. For Telegram that
means:

```json
"_meta": {
  "agent_meta": {
    "notifications": {
      "attention": {
        "mcp.telegram": {
          "header": "Telegram event",
          "data": {"message_ids": ["mimo-1:6859932159:6281"]},
          "instructions": "High-attention Telegram hook: use the persistent notification context for content; when handled, dismiss this notification."
        }
      },
      "persistent": {
        "mcp": {
          "telegram": {"messages": [...], "events": [...], "previous_block": {...}}
        }
      }
    }
  }
}
```

WeChat follows the same shape at `mcp.wechat`, with `data.message_ids` carrying
the producer's landed local message ids and content/context living in
`_meta.agent_meta.notifications.persistent.mcp.wechat`.

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

When the LICC consumer replaces an oversize/unserializable curated structured
family with an explicit `licc_structured_omitted` marker (reason, size, and the
producer's recovery handle), the kernel MUST carry that marker into the
persistent lane under `structured_omitted: [...]` rather than treating it as a
message candidate or silently dropping it; the bounded preview fallback message
still applies when no structured messages survive.

### 4. Persistent blocks are context, not unread state

`_meta.agent_meta.notifications.persistent` can remain in history after the transient hook is
handled. It is a communication-context lane, not a notification action channel.
Do not dismiss it, do not mutate producer state from it, and do not treat its
presence as proof that there is still an unhandled event. Telegram persistent
blocks must keep `previous_block` hooks so later deltas can point at earlier
context without re-sending all history.

### 5. Producer tools own side effects and clearing

The producer tool remains the source of truth for exact reads, replies, and
state-clearing side effects. Generic notification dismissal only clears the
notification mirror/hook; it must not be used to pretend producer state changed.
For source-of-truth mirrors (email today), prefer producer-specific
`read`/`dismiss` verbs. For content-free LICC hooks whose event was handled via
the producer tool, generic `notification.dismiss_channel("mcp.<name>")` may clear
the high-attention hook.

### 6. Secret and volume boundaries are part of the contract

Producer metadata copied into `.notification` or `_meta.agent_meta.notifications.persistent`
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
- Chat history/tool results — where `_meta.agent_meta.notifications.attention` and
  `_meta.agent_meta.notifications.persistent` blocks are recorded for the model and replay.

In-memory state involved in this contract:

- `agent._notification_live_holder` and `_notification_payload_signature` control
  sparse movement of the transient notification payload.
- `agent._notification_persistent_telegram_message_ids` /
  `_notification_persistent_telegram_last_tool_id`, the WeChat counterparts
  `agent._notification_persistent_wechat_message_ids` /
  `_notification_persistent_wechat_last_tool_id`, and the Feishu counterparts
  `agent._notification_persistent_feishu_message_ids` /
  `_notification_persistent_feishu_last_tool_id` track per-channel delivery into
  the current provider context (reset on molt), keyed by per-update
  `event_id` when present and by compound message id otherwise. WhatsApp is snapshot-only and
  keeps no agent-side delivery tracker.

## Review triggers

Re-check this contract whenever a change touches any of these areas:

- LICC schema, validation, atomic write, or dead-letter handling:
  `src/lingtai/services/mcp_inbox.py`, `src/lingtai/services/mcp_licc.py`.
- `.notification` helper semantics, channel allowlists, publish/clear/dismiss,
  or generic-dismiss guards: `src/lingtai/kernel/notifications.py` and
  `src/lingtai/tools/notification/`.
- Notification injection, sparse live-holder movement, `notification_guidance`,
  `_meta.agent_meta.notifications.attention`, `_meta.agent_meta.notifications.persistent`, or sanitizer logic:
  `src/lingtai/kernel/meta_block.py`, `src/lingtai/kernel/base_agent/__init__.py`,
  and `src/lingtai/kernel/base_agent/turn.py`.
- Built-in producers that create notification mirrors or human-message metadata:
  `src/lingtai/kernel/base_agent/messaging.py`, `src/lingtai/tools/email/`,
  and curated messaging MCP managers under `src/lingtai/mcp_servers/`.
- Tests that lock notification shape, curated IM persistent context, MCP inbox
  delivery, or email notification behavior.

## Current implementation status

- **Telegram:** compliant with the content split. Transient
  `_meta.agent_meta.notifications.attention.mcp.telegram` is an identity-only high-attention hook;
  content/context lives in `_meta.agent_meta.notifications.persistent.mcp.telegram`.
- **WeChat:** compliant with the content split. The producer attaches
  structured `recent_messages`/`latest_incoming` plus generic routing keys to
  its LICC events; transient `_meta.agent_meta.notifications.attention.mcp.wechat` is an
  identity-only high-attention hook; content/context lives in
  `_meta.agent_meta.notifications.persistent.mcp.wechat`. WeChat inbox/sent records and
  `wechat.read` remain source of truth.
- **Feishu:** compliant with the content split. The producer attaches bounded
  `recent_messages`/`latest_incoming` plus generic routing keys; transient
  `_meta.agent_meta.notifications.attention.mcp.feishu` is identity-only; content/context lives in
  the Feishu delta lane at `_meta.agent_meta.notifications.persistent.mcp.feishu`.
- **WhatsApp:** compliant with the content split. The producer attaches bounded
  current conversation context plus generic routing keys; transient
  `_meta.agent_meta.notifications.attention.mcp.whatsapp` is identity-only; content/context lives in
  the WhatsApp snapshot lane at `_meta.agent_meta.notifications.persistent.mcp.whatsapp`
  with no `previous_block` or delivery tracker.
- **Generic LICC/MCP:** still publishes bounded previews into the raw
  `.notification/mcp.<name>.json` mirror. That is allowed until the producer has
  a persistent context lane.
- **Email:** migrated to the same attention/context split. Transient `_meta.agent_meta.notifications.attention.email` is an identity-only high-attention hook carrying `email_ids`; unread context lives in `_meta.agent_meta.notifications.persistent.email`; the email tool/store remains source of truth.

## Notes

This contract deliberately separates **attention** from **context** from
**authority**. The agent needs an attention hook to wake and decide what to do,
context to understand human messages without re-reading every time, and producer
tools to perform real state changes. Mixing those three was the source of the
Telegram transient-content regressions that PRs #700, #704, and #705 resolved.
