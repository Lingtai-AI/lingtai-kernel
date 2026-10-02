---
name: telegram-task-card-projection-notice
description: |
  Shipped retained-legacy/projection notice for Telegram Task Card files. The
  public `task_card` tool is intrinsic and documented at
  src/lingtai/tools/task_card/manual/SKILL.md; Telegram only projects the
  intrinsic taskcard/status + taskcard/taskcard.md artifact read-only. It also
  explains the Telegram-only per-call API token list-price estimate line and
  its since-molt SESSION Cost total.
last_changed_at: 2026-09-30T00:00:00Z
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

The automatic Telegram card shows time and token information on separate
lines, then adds the plain price line, for example:

```text
↻12.4s · ⚡1.2s · 45 tok/s
↓200 (20) ↑900 ◌ 1.0k | 10.0%
```

`⚡` measures actual stream dispatch to first nonempty **text or tool
name/argument payload**. Reasoning, ids, lifecycle, heartbeat, usage and empty
events do not count. `tok/s` uses final provider output (including tools) minus
explicitly reported reasoning tokens, divided by the measured
first-output-to-final-usage interval (180 / 4 = 45 here), not the total API gap.
It is observed output throughput including wire/trailer delay, not server-only
throughput. Generic OpenAI Chat Completions and Responses streaming currently
supply this evidence for text, tool-only and mixed rounds. Missing/estimated
usage, missing explicit reasoning counts, untimed adapters, and
nonstream/forced-SSE fallback omit speed; unknown timing is omitted, not inferred
as zero. Total existing delay, its `↻` and token symbols/counts are retained. The following cost line remains independent:

```text
$0.0084 · ↓$0.0010 ↑$0.0070 | $0.0004
```

Reading it:

- **What it is.** The line is a STANDARD public per-token list-price ESTIMATE in
  USD (LiteLLM prices), not a bill or invoice. It is not the actual subscription/Codex-pool bill, and it
  does not claim the routed tier, batch/priority pricing or discounts. Search,
  grounding and image fixed fees are not included in `total`.
- **Source and basis.** Prices come from LiteLLM's public
  `model_prices_and_context_window.json`, looked up by the EXACT model that
  made that round (no alias or fuzzy match), fetched by this process in the
  background; `stale prices` is appended once the snapshot is older than six
  hours and a refresh has not landed. Above 200k/272k total input the
  catalog's above-threshold rates are used when the model lists them.
- **Mirrors the metrics row.** `↓` is the provider-billable output; `↑` is the
  cache-miss input (total input minus cache read — uncached input plus any
  cache writes, the writes at the catalog's cache-write rate when it lists one,
  otherwise at the input rate); `|` is the cache-hit (cache-read) input,
  the `| hit%` share of `◌`. `↓` is the provider-billable output (thinking included exactly
  once). `<$0.0001` is a nonzero amount that rounds below the display precision.
- **Unknowns are never zero.** When the catalog prices cache writes
  separately but a round has no recorded write count (for example rounds
  logged before the adapter read `input_tokens_details.cache_write_tokens`),
  `↑` shows a lower bound with a trailing `+`: every cache-miss token priced at
  the cheapest applicable rate. A part shows `?` when no safe value exists: a
  count it needs is missing, the catalog lacks or has an invalid rate, or the
  counts are incoherent (for example a 1-hour cache-write part larger than the
  whole write). If any part is a lower bound or unknown, the headline total is
  the known subtotal with a trailing `+` (`$0.0682+`), or `cost ?` when nothing
  is known.
- **Independent from speed.** The price line does not calculate tokens/second.
  The token line above uses only measured compatible stream evidence; the total
  API gap includes waiting/prefill/orchestration and is not its denominator.
- **Async cache, offline, old history.** The card never waits for the network:
  the first render may show `cost loading`, one bounded background refresh (fixed
  URL, 8 MiB cap, per-read timeout plus a total deadline, no credentials)
  fills a process-local snapshot, and a failed fetch is retried no faster than
  every five minutes. Offline, prices stay `cost n/a (prices unavailable)` (or the
  last snapshot marked `stale prices`). Old events written before this feature carry no
  round facts and show `cost n/a (model unknown)`; no price is invented for them.
  Feishu and other channels render exactly as before.

## SESSION Cost row (Telegram only)

Under SESSION the card adds one row with the since-molt total of the same
per-call estimates:

```text
Cost · total ~$0.1234 · in $0.0200 · write $0.0100 · read $0.0034 · out $0.0900
```

Each main-Agent API response since the last molt is counted once (however many
tool calls it made) and priced at the model that made it. A molt starts a new
total. Daemon and other-Agent calls are not included. The total is only shown as
complete (`~`) when this Telegram process has seen every response of the
current session with priced facts. After a restart or refresh it rebuilds only
from the existing bounded event tail, so an older session shows
`total ≥$x · [per-bucket amounts or ?] · partial`; the same lower-bound form covers
responses with missing usage, billing facts, model or price. With nothing
priceable it shows `total ? · in ? · write ? · read ? · out ? · partial`. It is never shown as `$0` for
unknown history, and it is still a list-price estimate, not a bill.

The SESSION line splits ordinary input, cache writes, cache reads and output without double charging. Unknown write counts keep input/write allocation unknown even when the combined total is known.
