---
name: daemon-dispatch-ledger
description: Nested daemon-manual reference for append-order ledger warnings and non-repair diagnostics.
version: 0.2.0
last_changed_at: 2026-09-08T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/CONTRACT.md
- src/lingtai/tools/daemon/ANATOMY.md
- src/lingtai/tools/daemon/dispatch_ledger.py
- src/lingtai/kernel/daemon_dispatch.py
maintenance: |
  Keep this reference aligned with the append-only dispatch ledger and its
  warning codes; route changes from the daemon manual.
---

# Dispatch Ledger Diagnostics

`daemons/.dispatch-ledger.jsonl` records accepted run membership and append
order: `schema`, monotonic `sequence`, `run_id`, and informational
`created_at`. It is not lifecycle truth; read each ledger-selected
`daemon.json` for state, result, and usage. `.dispatch-recovery/` contains only
unresolved running or pending-notification markers.

## Normal operation

- A new or cutover agent may have no ledger; legacy folders are not backfilled.
- `daemon(action="list", input={})` reads the newest 1000 ledger records in
  append order. It does not sort timestamps, scan lifetime folders, or repair an
  index.
- Use exact `daemon(action="check", input={"id": "<run_id>"})` or manual
  filesystem inspection for a known legacy run. An omitted list item is not
  proof of deletion or failure.
- The owning Agent Record refreshes a bounded ledger-selected summary
  asynchronously; heartbeat/liveness does not depend on that snapshot.

## Warnings and write failures

List warnings are advisory and bounded: `dispatch_ledger_empty`,
`dispatch_ledger_invalid_record`,
`dispatch_ledger_sequence_non_monotonic`,
`dispatch_ledger_duplicate_run_id`, and
`dispatch_ledger_daemon_state_unreadable`. They include the checked scope and
never repair files.

A malformed final record is stricter than a read warning: a later acceptance
refuses before launch because the next sequence is unprovable. No runtime path
truncates, repairs, sorts, migrates, or rebuilds this ledger.
