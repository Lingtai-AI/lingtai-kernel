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
  - src/lingtai/mcp_servers/telegram/manager.py
  - tests/test_session_stats.py
  - tests/test_daemon_dispatch_ledger.py
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

This component owns the agent's atomic, redacted live Agent Record. Its daemon
summary is a bounded view over the owning agent's append-only dispatch ledger;
per-run `daemon.json` remains the only lifecycle and usage truth. It never
scans or sorts the lifetime `daemons/` directory and does not create a duplicate
per-turn `session_stats.json` artifact.

## Components

- `build_agent_record`, `write_agent_record`, `read_agent_record`, and the
  published-record classifiers (`__init__.py`) own the versioned Agent Record
  projection and redaction boundary.
- `aggregate_daemon_records` reads only `daemon_dispatch.read_recent_daemon_states`
  for the ledger EOF tail (default 1000). It reports the checked scope and
  bounded warnings; `present` is the number selected in that scope, not a
  lifetime claim.
- `RecentDaemonSnapshot` is the one explicit per-agent single-flight owner.
  `schedule()` coalesces a new ledger/state read while one is running and
  `snapshot()` returns only the last compact detached result to the heartbeat.
- `session_stats_refresh_seconds`, `session_stats_daemon_limit`, and
  `should_refresh_agent_record` retain validated live environment controls and
  the Agent Record write throttle.

## Connections

`BaseAgent._write_session_stats_record` creates the snapshot owner lazily,
schedules it without waiting, and passes its last snapshot to
`build_agent_record`. Consequently a blocked recent-1000 read cannot delay
`.agent.heartbeat` publication. `DaemonRunDir._persist_daemon_state` writes only
`daemon.json` and its small dispatch recovery markers. `TelegramManager` uses
the same ledger-selected bounded states for automatic daemon Task Card facts;
it never reconstructs daemon membership by directory enumeration.

## Composition

Parent: `src/lingtai/kernel/ANATOMY.md`. The ledger primitives live in the
kernel to avoid a kernel-to-tools dependency; `tools/daemon/dispatch_ledger.py`
is a thin owner-facing re-export. The paired Contract owns record shapes,
redaction, bounds, and the nonblocking ownership rule.

## State

`BaseAgent` owns the Agent Record write throttle/sequence and one ephemeral
`RecentDaemonSnapshot`. The snapshot contains no durable cursor or materialized
history. Durable daemon membership is only `daemons/.dispatch-ledger.jsonl`; run
state is only the selected run's `daemon.json`.

## Notes

An absent ledger is a normal cutover condition for an agent with no newly
accepted dispatches. It is reported as a scoped advisory, never repaired,
backfilled from legacy folders, or converted into fake zero-valued daemon rows.
