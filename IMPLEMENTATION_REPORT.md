# Phase 1 Daemon RAM Capping Implementation Report

## Files Changed

- `src/lingtai/tools/daemon/__init__.py` (8881 lines total; delta +89/-9): added default-safe manager config/env resolution and high-concurrency routing while preserving the direct detached supervisor path by default.
- `src/lingtai/adapters/posix/daemon_manager.py` (346 lines): added the thin resident POSIX central daemon manager, durable queue/journal, restart recovery, bounded Phase 1 execution assignment, and terminal notification handoff.
- `src/lingtai/adapters/posix/daemon_manager_entrypoint.py` (22 lines): added the `python -m` entrypoint for the resident manager process.
- `tests/test_daemon_central_manager.py` (189 lines): added focused manager tests for completion, notification, recovery, timeout, queue draining, and default-disabled routing.

## Design Decisions

- Phase 1 only: the manager still spawns one execution child per active run via the existing detached-supervisor runtime helpers. No persistent reusable LLM worker pool was implemented.
- Default-safe configuration:
  - `manager_pool_size` defaults to `0`, so the central manager is disabled unless explicitly configured.
  - `manager_threshold` defaults to `50`.
  - Environment overrides are `LINGTAI_DAEMON_MANAGER_POOL_SIZE` and `LINGTAI_DAEMON_MANAGER_THRESHOLD`.
- Low-concurrency behavior remains on the existing `select_daemon_supervisor_adapter().spawn_detached(...)` path. The manager path is selected only on POSIX when `manager_pool_size > 0` and `batch_count > manager_threshold`.
- The resident manager is per agent working directory under `<agent>/daemon/manager`, with private `0700` directories and `0600` queue/journal JSON files.
- The manager is intentionally thin: it handles intake, journal, assignment, process wait/deadline/control through existing supervisor helpers, exact termination on recovery, terminal truth, and notification. It does not import the daemon tool manager or LLM runtime at process startup.
- Manager restart recovery marks previously active journal entries failed with explicit evidence and publishes the terminal notification through the existing idempotency gate. Queued jobs remain queued.

## Tests Run

Using local worktree venv `.venv` created with `/opt/homebrew/bin/python3.11` because the provided rollout venv lacked `pytest`, and system `python3` was Python 3.14 with an incompatible installed `mcp` package.

- `.venv/bin/python -m pytest tests/test_daemon_central_manager.py tests/test_daemon.py::test_daemon_load_config_coercion tests/test_daemon.py::test_daemon_default_max_emanations_is_100 tests/test_daemon_detached_supervisor.py::test_terminal_notification_published_once_across_supervisor_and_reconcile -q`
  - Result: `8 passed in 9.63s`
- `.venv/bin/python -m pytest tests/test_daemon_central_manager.py tests/test_daemon_detached_supervisor.py tests/test_daemon.py::test_daemon_default_max_emanations_is_100 tests/test_daemon.py::test_daemon_max_emanations_override_reaches_manager tests/test_daemon.py::test_daemon_default_max_turns_is_5000_without_config tests/test_daemon.py::test_daemon_config_max_turns_overrides_default tests/test_daemon.py::test_daemon_explicit_max_turns_beats_config_file tests/test_daemon.py::test_daemon_invalid_config_max_turns_falls_back_to_5000 tests/test_daemon.py::test_daemon_load_config_coercion -q`
  - Result: `54 passed in 35.22s`
- `.venv/bin/python -m pytest tests/test_daemon_central_manager.py tests/test_daemon.py -q`
  - Result: `138 passed in 23.09s`
- `.venv/bin/python -m pytest tests/test_daemon_detached_supervisor.py tests/test_daemon_central_manager.py -q`
  - Result: `47 passed in 34.30s`
- `.venv/bin/python -m py_compile src/lingtai/adapters/posix/daemon_manager.py src/lingtai/adapters/posix/daemon_manager_entrypoint.py src/lingtai/tools/daemon/__init__.py tests/test_daemon_central_manager.py`
  - Result: passed with no output

## Review-Fixes

- P0 central-manager enqueue: removed the caller-side `_await_supervisor_startup(...)`
  wait from the central-manager path. The LingTai and CLI backend routing sites both
  flow through `_enqueue_central_daemon_manager_run(...)`, so queued jobs now return
  to `emanate` after durable enqueue; the resident manager owns later assignment and
  pid/state updates.
- P1 credential/capsule durability: terminal manager journal records now scrub the
  runtime capsule and retain only non-secret identity/status fields with
  `capsule_scrubbed=true`. Active journal and queue entries still carry the capsule
  while a job is restartable/queued; they remain private `0600` files. A follow-up
  design decision is still needed for avoiding or encrypting queued restart capsules.
- P1 manager pid identity: manager pidfile liveness now requires the saved
  `manager_start_identity` to match the observed process identity, avoiding PID-only
  false positives after PID reuse. Manager startup also carries a per-spawn token in
  the private pidfile for traceability.
- P1 queue ordering: queued jobs dispatch FIFO by `enqueued_at`, with filename as the
  stable tie-breaker.
- P1 tests: added production routing coverage for `manager_pool_size=1` and
  `manager_threshold=0` with two slow jobs, asserting `emanate` returns dispatched ids
  before the second job has a pid and while it remains queued. Added default-disabled
  routing coverage proving the legacy spawn adapter is still used.
- P2 recovery/queue evidence: recovery errors now include source path and exception
  type/message. Malformed top-level queue JSON is converted into a terminal
  `failed_malformed_queue_job` journal record and removed from the queue.

Review-fix validation:

- `.venv/bin/python -m pytest tests/test_daemon_central_manager.py -q`
  - Result: `11 passed in 19.05s`
- `.venv/bin/python -m pytest tests/test_daemon_central_manager.py tests/test_daemon.py -q`
  - Result: `144 passed in 38.08s`
- `.venv/bin/python -m pytest tests/test_daemon_detached_supervisor.py tests/test_daemon_central_manager.py -q`
  - Result: `53 passed in 46.96s`
- `.venv/bin/python -m py_compile src/lingtai/adapters/posix/daemon_manager.py src/lingtai/adapters/posix/daemon_manager_entrypoint.py src/lingtai/tools/daemon/__init__.py tests/test_daemon_central_manager.py`
  - Result: passed with no output

## Deviations From Spec

- Queued and active manager records store the one-shot runtime capsule needed to execute queued jobs after a restart. Files are private (`0600`) and outside the public run-dir layout, and terminal journal records scrub the capsule after completion/recovery. This is still more durable secret exposure while jobs are queued/active than the previous pure pipe capsule path. A future hardening pass should replace this with encrypted local storage or a credential re-resolution protocol.
- Phase 1 does not make queued state visible as a distinct public daemon status. Existing `daemon.json` remains `running` until the manager starts the execution child or reaches terminal recovery.

## Risks / Known Limitations

- The manager pidfile now records and verifies a process start identity before treating a live PID as the resident manager.
- The manager is POSIX-only. Windows remains on the existing path.
- This does not reduce memory for active execution children; it caps active concurrency only when explicitly enabled. True reusable worker-pool memory savings remain Phase 2.
- Queue ordering is FIFO by `enqueued_at`, with run-id filename as the stable tie-breaker.

## Deferred Items

- Phase 2 reusable execution-worker pool with surface-keyed reuse/reset/scrub.
- Memory benchmark at 10/100/500/1000 in-flight after Phase 2.
- Queue-state visibility in `daemon(action="list")`.
- Secure queued/active capsule persistence.
