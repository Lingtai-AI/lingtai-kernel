# max-rpm-gating — Concurrency Limit on Daemon Spawns

## What

The daemon manager enforces a hard concurrency cap on emanations. When running + new batch would exceed the limit, the entire `emanate` call is refused — no partial dispatch, no queuing.

## Contract

### Default limit

`DaemonManager.__init__()` accepts `max_emanations: int = 4`. Configurable per agent via `init.json`:

```json
{ "capabilities": { "daemon": { "max_emanations": 8 } } }
```

### Capacity check

Before any I/O, `_handle_emanate()`:

1. **Prunes** completed emanations (futures where `.done()` is `True`).
2. **Prunes** stale pool/cancel-event pairs.
3. **Counts** still-running: `running = len(self._emanations)`.
4. **Compares**: `running + len(tasks) > max_emanations` → error with running/requested/max counts.

### No queuing

No internal queue. Caller must wait for completions or call `daemon(action="reclaim")` to kill all, then retry.

### Per-batch overrides

`max_turns` and `timeout` can be overridden per `emanate` call, capped at the manager's ceilings (`_max_turns`, `_timeout`). The concurrency limit itself has no per-call override.

### ID management

- `_next_id` increments monotonically (`em-1`, `em-2`, ...). Reset to 1 on `reclaim()`.
- Folder names include `<handle>-<YYYYMMDD-HHMMSS>-<hex6>`, so ID reuse is safe.
- `_emanations` dict maps `em_id` → entry dict (future, task, start_time, cancel_event, timeout_event, followup_buffer, followup_lock, run_dir).

### Thread pool

Each `emanate` call creates `ThreadPoolExecutor(max_workers=len(tasks))`. Multiple batches coexist with separate pools and cancel events.

### Watchdog

Each batch spawns a daemon watchdog thread sleeping in 1-second ticks. On timeout: sets `timeout_event` **then** `cancel_event`, so the run loop can call `mark_timeout()` vs `mark_cancelled()`.

## Source

Anchored by function name; re-locate with `grep -n 'def <func>' <file>` if line numbers drift.

- `__init__.py::DaemonManager.__init__()` — `max_emanations` parameter (default 4)
- `__init__.py::_handle_emanate()` — prune + count + capacity check
- `__init__.py::_handle_reclaim()` — cancel all, clear registry, reset `_next_id`
- `__init__.py::_watchdog()` — timeout thread
- `__init__.py::setup()` — wires `max_emanations` from capability kwargs to `DaemonManager`

## Related

- `../verify_daemon_leaves.py` — 11 static checks for this leaf (run against source)
- `pre-send-health` leaf — capacity gate is checked before preset/tool validation
- `daemon-manual` skill — `daemon(action="list")` reports `max_emanations`
