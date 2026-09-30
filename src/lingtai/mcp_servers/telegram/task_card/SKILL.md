---
name: telegram-task-card-projection-notice
description: |
  Shipped retained-legacy/projection notice for Telegram Task Card files. The
  public `task_card` tool is intrinsic and documented at
  src/lingtai/tools/task_card/manual/SKILL.md; Telegram only projects the
  intrinsic taskcard/status + taskcard/taskcard.md artifact read-only. It also
  explains the Telegram-only per-call API token list-price estimate line.
last_changed_at: 2026-09-29T00:00:00Z
related_files:
- src/lingtai/mcp_servers/telegram/SKILL.md
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
- src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
- src/lingtai/mcp_servers/telegram/task_card/api_cost.py
- src/lingtai/mcp_servers/task_card/event_projection.py
- src/lingtai/tools/task_card/manual/SKILL.md
- tests/test_telegram_task_card_api_cost.py
- tests/test_telegram_task_card_event_tail.py
maintenance: |
  Keep this shipped subpackage manual aligned with the intrinsic Task Card
  owner. Do not reintroduce the retired Telegram controller schema, endpoint,
  ceiling, or lifecycle as active behavior.
---

# Telegram Task Card Projection Notice

This shipped file is retained beside the governed projection docs; it is **not**
the active public Task Card manual. The public, channel-neutral owner is the intrinsic Task Card, with packaged
procedures at [`task_card/manual/SKILL.md`](../../../tools/task_card/manual/SKILL.md).
Source-checkout architecture lives at `src/lingtai/tools/task_card/ANATOMY.md`
(not a packaged manual link).
Use that owner for `start | inspect | retry | stop | remove | settings | manual`,
renderer authoring, limits, recovery, and cleanup.

Telegram only projects the producer's `taskcard/status` and
`taskcard/taskcard.md` read-only. Author `taskcard.md` as Markdown: Telegram
renders ATX headings/subheadings, `**bold**`, inline backtick code, and unordered,
ordered, or task-list items inside the complete resident card. Raw HTML and
special characters are escaped; malformed or unmatched Markdown delimiters stay
safe literal text rather than becoming provider markup. Exact `active` plus a
nonempty body projects the programmable slot; exact `inactive` idempotently
excludes only that slot. Missing/unreadable status, active with a missing/blank
body, other status text, and unchanged bytes are no-ops. The resident message,
automatic event-journal slot, authored programmable bytes, and producer files
are not deleted or rewritten.

Do not use this retained package as the old Telegram-owned schema, endpoint,
JSON-card renderer, reverse-MCP route, or refresh-ceiling source.

## API token list-price line (Telegram only)

Under each API-call metrics row (`↻ <delay> ↓out ↑miss ◌ ctx | cache%`) the
automatic Telegram card adds one plain line, for example:

```text
250.0 tok/s · ≈$0.0084 (in $0.0050 · write $0.0020 · read $0.0004 · out $0.0010)
```

Reading it:

- **What it is.** `≈$` marks a STANDARD public per-token list-price ESTIMATE in
  USD (LiteLLM prices), not a bill or invoice. It is not the actual subscription/Codex-pool bill, and it
  does not claim the routed tier, batch/priority pricing or discounts. Search,
  grounding and image fixed fees are not included in `total`.
- **Source and basis.** Prices come from LiteLLM's public
  `model_prices_and_context_window.json`, looked up by the EXACT model that
  made that round (no alias or fuzzy match), fetched by this process in the
  background; `stale prices` is appended once the snapshot is older than six
  hours and a refresh has not landed. Above 200k/272k total input the
  catalog's above-threshold rates are used when the model lists them.
- **Four buckets.** `in` is uncached input (total input minus cache read
  minus cache write); `write` is the cache-write count; `read` is the cache-read
  count; `out` is the provider-billable output (thinking included exactly
  once). `<$0.0001` is a nonzero amount that rounds below the display precision.
- **Unknowns are never zero.** A bucket shows `?` when the provider wire did not
  state its count (for example OpenAI/Gemini/Codex do not report cache-write
  tokens, so `write` and the uncached `input` stay `?`), when the catalog lacks
  that rate, or when the counts are incoherent (for example a 1-hour cache-write
  part larger than the whole write). If any bucket is unknown the total is NOT
  a full sum: the headline is the known subtotal with a trailing `+`
  (`≈$0.0249+`, a lower bound), or `cost ?` when nothing is known.
  Other notes: `cost n/a (model unknown)` (old history, no round facts),
  `cost n/a (model not listed)`, `cost n/a (prices unavailable)`,
  `cost loading`, `cost n/a (estimated tokens)`.
- **`tok/s` vs decode speed.** The leading `<n> tok/s` is billable output tokens
  divided by the existing displayed API gap. That gap may include waiting,
  prefill, streaming and orchestration; it is not an independently measured
  decode interval, and no universal speed comparison is claimed. It is omitted
  when the delay or token count is unknown.
- **Async cache, offline, old history.** The card never waits for the network:
  the first render may show `cost loading`, one bounded background refresh (fixed
  URL, 8 MiB cap, per-read timeout plus a total deadline, no credentials)
  fills a process-local snapshot, and a failed fetch is retried no faster than
  every five minutes. Offline, prices stay `cost n/a (prices unavailable)` (or the
  last snapshot marked `stale prices`). Old events written before this feature carry no
  round facts and show `cost n/a (model unknown)`; no price is invented for them.
  Feishu and other channels render exactly as before.
