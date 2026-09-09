---
name: session-stats
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/session_stats/ANATOMY.md
  - src/lingtai/kernel/session_stats/__init__.py
  - src/lingtai/kernel/daemon_dispatch.py
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/tools/daemon/CONTRACT.md
  - src/lingtai/tools/daemon/run_dir.py
  - src/lingtai/tools/bash/CONTRACT.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/task_card/CONTRACT.md
  - tests/test_session_stats.py
  - tests/test_daemon_dispatch_ledger.py
  - tests/test_provider_admission.py
  - tests/test_telegram_task_card_rows.py
  - ENVIRONMENT_VARIABLES.md
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Agent Record / Session Stats

## Purpose

Each Agent publishes one atomic, versioned, redacted Agent Record. Presentation
consumers read that record rather than reconstructing normal live status. This
component additionally owns the provider-neutral recent async-work projection
shared by channel and UI consumers; daemon and Shell owners retain lifecycle
and execution policy.

## Behavior

`build_agent_record`/`write_agent_record` are the only normal Agent Record
writer. They never serialize secrets, environment/config values, prompts,
messages, raw tool payloads, paths, PIDs, Shell commands, or unbounded task
text. The writer is best-effort and remains throttled by the live validated
`LINGTAI_SESSION_STATS_REFRESH_SECONDS` value.

The existing `agent_record.daemons` block remains a bounded dispatch-history
view. `aggregate_daemon_records` reads at most
`LINGTAI_SESSION_STATS_DAEMON_LIMIT` (default 1000) ledger records and only the
`daemon.json` files those records name. It never enumerates, sorts, repairs, or
backfills daemon directories. The unchanged block carries `source`, `present`,
`scanned`, `limit`, `counts_by_state`, `usage`, `checked`, bounded `warnings`,
and `refreshing`: `present` is the number of ledger records in the checked tail,
while `scanned` is only the subset with readable selected state; `checked`
identifies that exact tail/range. Warnings are advisory facts, never authority to
repair, reorder, truncate, clean, or fallback-scan artifacts. The new
`async_work` child neither replaces nor narrows this established block.

The additive `async_work` block is present only after a complete background
collection. Its terminal window is exactly 600 seconds, inclusive at age 600
and exclusive beyond it. Nonterminal work remains visible. Known durable states
map to exactly `running`, `queued`, `done`, or `failed`; unknown/malformed state
is omitted rather than guessed. Daemon cancellation/timeout and Shell
cancellation/nonzero/unrecoverable outcomes are failures. Shell `launching` is
queued. Terminal state without a valid finish time is not counted. An empty
daemon dispatch ledger is an expected empty lane and still permits Shell-only
publication. Any other daemon-reader integrity/unreadable-state warning, or a
non-absence Shell traversal/stat/read I/O failure, makes the new combined
observation unavailable; the owner retains the prior complete child with its
original `generated_at`. Missing Shell state and malformed Shell JSON/object or
lifecycle data may be omitted, as may an unknown/malformed lifecycle field on an
otherwise readable daemon object; none invents a lifecycle.

The heartbeat never awaits collection. `RecentAsyncWorkSnapshot` is the one
single-flight boundary: `schedule()` coalesces while a refresh runs, and
`snapshot()` returns the latest detached daemon-summary/async-work values as one
pair. Each value advances only after its own complete read; a failure retains
that value's predecessor without freezing the other existing publication path.

Published consumer validation is strict. Missing, malformed, future-dated, or
older-than-600-second `async_work` returns unavailable (`None`), never fabricated
zero counts. Aggregate counts must exactly equal the two lanes.

## Port

Agent Record remains `lingtai.agent_record/v1` with root `schema_version: 1`;
`async_work` is an additive independently versioned child:

```json
{
  "schema": "lingtai.async_work/v1",
  "schema_version": 1,
  "generated_at": "<UTC ISO-8601 Z>",
  "window_seconds": 600,
  "running": 0,
  "queued": 0,
  "done": 0,
  "failed": 0,
  "daemon": {
    "running": 0,
    "queued": 0,
    "done": 0,
    "failed": 0,
    "backend_counts": {},
    "usage": {
      "input_tokens": 0,
      "output_tokens": 0,
      "thinking_tokens": 0,
      "cached_tokens": 0,
      "api_calls": 0
    }
  },
  "shell": {"running": 0, "queued": 0, "done": 0, "failed": 0}
}
```

`model_counts` is optional when empty; backend/model maps contain only positive
counts. Every usage/backend/model field is under `daemon`; there is no aggregate
or Shell usage claim. Safe identifiers are bounded ASCII alphanumeric plus
`._:/\\-`. `query_published_async_work(record,
wall_now=...)` returns a detached safe v1 projection or `None`.

## Adapters

`BaseAgent._write_session_stats_record` is the Core publication driver. It
schedules the single-flight owner and atomically publishes its last complete
pair through `kernel._fsutil.atomic_write_json`. Daemon input comes from the
kernel dispatch-ledger reader, bounded by its configured ledger tail. Shell
input is a read-only projection of the Shell owner's atomically replaced
`system/jobs/<job-id>/state.json`; its background pass enumerates every retained
lifetime jobs-directory child and is not count-bounded. The reader never locks,
probes, cancels, polls, or mutates a job. Telegram is a consumer adapter through
`query_published_async_work` and has no private fallback collector.

## Contract rules

1. Agent Record writes remain atomic and best-effort.
2. Foreground heartbeat/turn paths schedule but never await async-work I/O.
3. There is one coalescing snapshot owner, not a scheduler or second job engine.
4. Daemon membership comes only from the bounded dispatch ledger; Shell state
   comes only from atomic state documents under the existing jobs namespace.
5. The 600-second terminal window and four-category mapping are fixed policy,
   not configuration.
6. Aggregate counts equal daemon plus Shell lane counts for every category.
7. Tokens, usage, backend, and model details are daemon-scoped only.
8. Old Agent Records and unavailable/malformed/stale snapshots yield no async
   presentation; consumers do not infer counts or scan fallback stores.
9. No per-run `session_stats.json`, new transport, database, socket, or cleanup
   side effect is introduced.

## Contract tests

`tests/test_session_stats.py` protects atomic publication, redaction, exact
nested schema, mixed arithmetic, daemon-scoped details, 600-second boundary,
malformed-state omission, strict stale/malformed/missing reads, and single-flight
nonblocking behavior. `tests/test_daemon_dispatch_ledger.py` protects ledger
selection and diagnostics. `tests/test_provider_admission.py` audits the one
background thread owner. `tests/test_telegram_task_card_rows.py` proves Telegram
reads the published common snapshot without fallback.

## Maintenance

Read the paired Anatomy for composition. Update the projector, BaseAgent hook,
daemon/Shell owner links, presentation consumer, and focused tests together when
this schema or mapping changes. A breaking nested schema change uses a new
`lingtai.async_work/vN` identity; never reinterpret v1 in place.
