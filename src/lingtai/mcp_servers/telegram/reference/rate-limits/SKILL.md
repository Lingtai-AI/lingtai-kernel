---
name: telegram-rate-limits
description: |
  Current official Telegram Bot API flood-control guidance: published quotas,
  `retry_after` semantics, documented unknowns, and safe client policy. Read
  before changing Telegram send cadence, programmable Task Card cadence, or 429
  recovery behavior.
version: 1.1.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
  - src/lingtai/mcp_servers/telegram/SKILL.md
  - src/lingtai/mcp_servers/telegram/account.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - tests/test_telegram_rate_limit.py
maintenance: |
  Re-check the two official Telegram sources before changing any quoted quota or
  retry semantics. Keep provider facts distinct from LingTai product policy and
  keep the parent manual as the concise progressive-disclosure entry point.
---

# Telegram Bot API rate limits

This reference owns provider facts and safe one-request policy; it is not a
second Telegram contract. Re-check the official pages before changing cadence or
recovery:

- [`ResponseParameters`](https://core.telegram.org/bots/api#responseparameters)
- [`Bots FAQ — Broadcasting to Users`](https://core.telegram.org/bots/faq#broadcasting-to-users)

Last verified against both pages: **2026-07-29 UTC**.

## Published guidance

- In one chat, avoid more than **one message per second**; continued excess can
  produce HTTP 429.
- A group permits no more than **20 messages per minute**.
- Bulk broadcasts are **about 30 messages per second** without paid broadcasts;
  Telegram recommends spreading large batches over 8–12 hours.
- Eligible paid broadcasts publish a 1000-message-per-second ceiling at the
  documented Stars cost. This is opt-in billing, never an automatic fallback.

These are provider guidelines, not permission to run every source at its limit.
Normal messages and automatic/programmable Task Card edits share the chat and
bot account, so coalesce presentation traffic and leave human communication
headroom.

## `retry_after` and the unknown scope

Telegram defines `ResponseParameters.retry_after` as optional integer seconds
remaining before a flood-controlled request may be repeated. LingTai reports
`retryable=true` only for a valid nonnegative integer and never sleeps, holds the
MCP worker, or schedules a hidden second side effect (`auto_retry=false`). A
missing or malformed value omits both `retry_after` and `retryable`.

Telegram does not document whether a cooldown is scoped to a chat, group,
method, account, or another bucket, nor its penalty formula or whether requests
extend it. Do not invent `retry_scope`, infer a global ban, or claim a cause from
the duration.

## Safe client policy

- A 429 is a failed request; do not persist it as delivered.
- Return the valid cooldown immediately and release the worker. A later new
  action after waiting is caller/orchestrator policy, not automatic retry.
- Do not send a second Telegram notice through the rate-limited route; surface
  the countdown in the result, local UI, or another healthy channel.
- Durable waits, cancellation, coalescing, Task Card pauses, and later retries
  require explicit orchestrator authorization.
- A changed Task Card frame is a real edit/send and consumes quota; unchanged-byte
  diff skipping does not make a churning renderer safe.
