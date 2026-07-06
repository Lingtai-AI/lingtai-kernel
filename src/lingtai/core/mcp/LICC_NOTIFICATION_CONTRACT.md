---
name: licc-notification-contract
description: >
  Contract for LICC/MCP notification events and their model-visible projection
  into _meta.notifications and _meta.notification_persistent.
status: active
contract_version: 1
last_changed_at: "2026-07-06"
related_files:
  - src/lingtai/core/mcp/ANATOMY.md
  - src/lingtai/core/mcp/inbox.py
  - src/lingtai/core/mcp/licc.py
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai_kernel/ANATOMY.md
  - src/lingtai_kernel/base_agent/ANATOMY.md
  - src/lingtai_kernel/base_agent/__init__.py
  - src/lingtai_kernel/base_agent/messaging.py
  - src/lingtai_kernel/base_agent/turn.py
  - src/lingtai_kernel/intrinsics/notification/ANATOMY.md
  - src/lingtai_kernel/intrinsics/notification/__init__.py
  - src/lingtai_kernel/meta_block.py
  - src/lingtai_kernel/notifications.py
  - tests/test_licc_notification_contract_doc.py
  - tests/test_mcp_inbox.py
  - tests/test_meta_block.py
  - tests/test_notification_sync.py
  - tests/test_telegram_notification_read_state.py
  - tests/test_wechat_notification_metadata.py
review_triggers:
  - src/lingtai/core/mcp/inbox.py
  - src/lingtai/core/mcp/licc.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/imap/manager.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai/mcp_servers/whatsapp/manager.py
  - src/lingtai/mcp_servers/cloud_mail/manager.py
  - src/lingtai_kernel/base_agent/__init__.py
  - src/lingtai_kernel/base_agent/messaging.py
  - src/lingtai_kernel/base_agent/turn.py
  - src/lingtai_kernel/intrinsics/email/ANATOMY.md
  - src/lingtai_kernel/intrinsics/email/
  - src/lingtai_kernel/intrinsics/notification/
  - src/lingtai_kernel/meta_block.py
  - src/lingtai_kernel/notifications.py
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
  has one (currently Telegram, WeChat, and built-in email).
- Producer-owned read/reply/dismiss state that remains the source of truth.

Non-scope: the low-level Telegram Bot API, IMAP protocol semantics, frontend UI
rendering, and unrelated notification producers such as `soul` or `goal` except
where they share the `.notification/` filesystem protocol.

## Components

1. **LICC event file (producer -> kernel inbox).** Out-of-process MCPs write one
   JSON event to `<agent_dir>/.mcp_inbox/<mcp_name>/<event_id>.json`; the event
   schema is `licc_version`, `from`, `subject`, `body`, optional `metadata`,
   `wake`, and `received_at` (`src/lingtai/core/mcp/inbox.py:58-116`). The
   canonical producer helper is `push_inbox_event(...)`, which validates the
   payload and writes atomically through `.json.tmp` + `fsync` + `os.replace`
   (`src/lingtai/core/mcp/licc.py:80-145`).
2. **LICC inbox consumer (kernel inbox -> `.notification`).** `MCPInboxPoller`
   validates/coalesces events per MCP, extracts bounded preview metadata, and
   publishes one `.notification/mcp.<name>.json` per sweep
   (`src/lingtai/core/mcp/inbox.py:183-219`,
   `src/lingtai/core/mcp/inbox.py:320-391`).
3. **Notification filesystem envelope.** `notifications.submit` is the standard
   envelope builder (`header`, `icon`, `priority`, `published_at`, optional
   `instructions`, and producer-owned `data`) and `notifications.publish` writes
   it atomically (`src/lingtai_kernel/notifications.py:260-275`,
   `src/lingtai_kernel/notifications.py:1054-1087`).
4. **Model-visible transient hook.** `_meta.notifications` is sparse and
   update-driven. `attach_active_notifications` attaches or moves the canonical
   payload only on first appearance, material change, or deliberate
   `notification(action="check")` (`src/lingtai_kernel/meta_block.py:2734`).
   For IM channels with a persistent lane (Telegram, WeChat), the shared
   `_sanitize_im_notification_after_persistent` (via the per-channel
   `sanitize_telegram_notification_after_persistent` /
   `sanitize_wechat_notification_after_persistent` wrappers) reduces
   `_meta.notifications.mcp.<channel>.data` to stable `message_ids` only; content,
   sender/subject, routing details, counts, and summaries must not remain in the
   transient lane (`src/lingtai_kernel/meta_block.py:2398-2450`).
5. **Model-visible persistent communication context.** When structured IM metadata is available, `build_notification_persistent_payload` emits `_meta.notification_persistent.mcp.<channel>` with `messages`, `events`, `previous_block`, and comments through the shared `_ImPersistentLane` machinery (Telegram additionally carries full out-of-window reply targets under `referenced_messages`). For built-in email it emits `_meta.notification_persistent.email` with `email_ids` plus full unread email bodies for the current unread snapshot (ordinary sends are capped at 50,000 characters so the notification layer does not truncate)
   (`src/lingtai_kernel/meta_block.py:1783-2335`). The Telegram MCP supplies the
   structured `recent_messages`, `latest_incoming`, and `referenced_messages`
   metadata (`src/lingtai/mcp_servers/telegram/manager.py:904-1040`); the WeChat
   MCP supplies `recent_messages` and `latest_incoming` built from its merged
   inbox+sent preview window with per-message text bounded at 500 chars
   (`src/lingtai/mcp_servers/wechat/manager.py:835-956`). Each lane's seed/delta
   boundary matches its producer preview window (Telegram 20, WeChat 10).
6. **Producer source of truth.** Notification files are mirrors/hooks, not the
   authoritative mailbox/chat store. Producer tools own real state changes and
   side effects. Email is the built-in mirror example: unread mail state is rendered into `.notification/email.json`, model-visible content is projected into `_meta.notification_persistent.email`, and read/dismiss/reply actions live on the email tool (`src/lingtai_kernel/base_agent/messaging.py:60-130`).

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
metadata and bounded JSON-safe structured fields (`src/lingtai/core/mcp/inbox.py:183-219`).
Curated MCPs that add new structured metadata must update this contract and tests
before the new field becomes model-visible.

## State

Persistent/on-disk state involved in this contract:

- `.mcp_inbox/<mcp_name>/<event_id>.json` — transient LICC inbox file, consumed
  and deleted by the poller after successful dispatch; invalid files move to
  `.dead/`.
- `.notification/mcp.<name>.json` — producer-owned live mirror. One file per
  channel, overwritten or cleared as current producer state changes.
- Producer stores such as Telegram inbox/sent JSON and email mailbox state —
  authoritative source for exact read/reply/dismiss semantics.
- Chat history/tool results — where `_meta.notifications` and
  `_meta.notification_persistent` blocks are recorded for the model and replay.

In-memory state involved in this contract:

- `agent._notification_live_holder` and `_notification_payload_signature` control
  sparse movement of the transient notification payload.
- `agent._notification_persistent_telegram_message_ids` /
  `_notification_persistent_telegram_last_tool_id` and the WeChat counterparts
  `agent._notification_persistent_wechat_message_ids` /
  `_notification_persistent_wechat_last_tool_id` track per-channel delivery into
  the current provider context (reset on molt).

## Review triggers

Re-check this contract whenever a change touches any of these areas:

- LICC schema, validation, atomic write, or dead-letter handling:
  `src/lingtai/core/mcp/inbox.py`, `src/lingtai/core/mcp/licc.py`.
- `.notification` helper semantics, channel allowlists, publish/clear/dismiss,
  or generic-dismiss guards: `src/lingtai_kernel/notifications.py` and
  `src/lingtai_kernel/intrinsics/notification/`.
- Notification injection, sparse live-holder movement, `notification_guidance`,
  `_meta.notifications`, `_meta.notification_persistent`, or sanitizer logic:
  `src/lingtai_kernel/meta_block.py`, `src/lingtai_kernel/base_agent/__init__.py`,
  and `src/lingtai_kernel/base_agent/turn.py`.
- Built-in producers that create notification mirrors or human-message metadata:
  `src/lingtai_kernel/base_agent/messaging.py`, `src/lingtai_kernel/intrinsics/email/`,
  and curated messaging MCP managers under `src/lingtai/mcp_servers/`.
- Tests that lock notification shape, Telegram/WeChat persistent context, MCP
  inbox delivery, or email notification behavior.

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
- **Generic LICC/MCP:** still publishes bounded previews into the raw
  `.notification/mcp.<name>.json` mirror. That is allowed until the producer has
  a persistent context lane.
- **Email:** migrated to the same attention/context split. Transient `_meta.notifications.email` is an identity-only high-attention hook carrying `email_ids`; unread context lives in `_meta.notification_persistent.email`; the email tool/store remains source of truth.

## Notes

This contract deliberately separates **attention** from **context** from
**authority**. The agent needs an attention hook to wake and decide what to do,
context to understand human messages without re-reading every time, and producer
tools to perform real state changes. Mixing those three was the source of the
Telegram transient-content regressions that PRs #700, #704, and #705 resolved.
