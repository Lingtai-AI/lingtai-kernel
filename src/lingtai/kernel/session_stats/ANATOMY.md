---
related_files:
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/session_stats/CONTRACT.md
  - src/lingtai/kernel/session_stats/__init__.py
  - src/lingtai/kernel/daemon_dispatch.py
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/run_dir.py
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - src/lingtai/mcp_servers/task_card/event_projection.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
  - tests/test_session_stats.py
  - tests/test_daemon_dispatch_ledger.py
  - tests/test_provider_admission.py
  - tests/test_telegram_task_card_rows.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# Agent Record / Session Stats

This component owns the agent's atomic, redacted live Agent Record, including
one versioned recent daemon+Shell async-work presentation snapshot. Per-run
daemon and Shell records remain lifecycle truth; this layer only projects their
bounded safe fields and never creates another job engine.

## Components

- `build_agent_record`, `write_agent_record`, and `read_agent_record` own the
  Agent Record projection and atomic I/O; published classifiers own strict safe
  consumer views (`src/lingtai/kernel/session_stats/__init__.py:163-384`).
- `build_async_work_snapshot` owns the fixed 600-second four-category recent
  view, separate daemon/Shell lanes, aggregate arithmetic, and daemon-only
  usage/backend/model details; `query_published_async_work` validates it
  (`src/lingtai/kernel/session_stats/__init__.py:617-760`).
- `aggregate_daemon_records` retains the distinct bounded dispatch-history
  summary used by the existing `daemons` block
  (`src/lingtai/kernel/session_stats/__init__.py:787-831`).
- `RecentAsyncWorkSnapshot` is the one per-agent single-flight owner. Under one
  lock it advances each successfully completed value and returns daemon history
  plus common recent work as one detached in-memory pair
  (`src/lingtai/kernel/session_stats/__init__.py:834-907`).

## Connections

`BaseAgent._write_session_stats_record` schedules the owner without waiting,
reads its last complete pair, and publishes that pair in one atomic
`system/agent_record.json` replacement. The daemon lane reads only
`daemon_dispatch.read_recent_daemon_states`; the Shell lane reads only atomic
`system/jobs/<job-id>/state.json` truth and never probes a process. Telegram
calls `query_published_async_work` and has no fallback state collector.

## Composition

Parent: `src/lingtai/kernel/ANATOMY.md`. Daemon persistence remains owned by
`tools/daemon/`; Shell lifecycle and atomic state writes remain owned by
`tools/bash/`. Telegram's Task Card is a presentation adapter and the shared
event projector is filesystem-neutral.

## State

`BaseAgent` owns the Agent Record write throttle/sequence and one ephemeral
`RecentAsyncWorkSnapshot`. The snapshot has no durable cursor or materialized
history. Durable membership/state remains `daemons/.dispatch-ledger.jsonl`, the
selected `daemon.json` files, and `system/jobs/<job-id>/state.json`. The only
new durable state is the nested `async_work` value inside the existing atomic
Agent Record.

## Notes

The `daemons` block and `async_work.daemon` answer different questions: the
former is a bounded ledger-tail history aggregate; the latter is a fixed recent
presentation window. Missing, malformed, future-dated, or stale published
`async_work` is unavailable, never a fabricated zero snapshot.
