---
related_files:
  - src/lingtai/kernel/refresh_watcher/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - src/lingtai/kernel/refresh_watcher/MANUAL.md
  - src/lingtai/kernel/process_match.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/refresh_watcher.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/refresh_watcher.py
  - src/lingtai/adapters/posix/refresh_watcher_process.py
  - src/lingtai/adapters/posix/refresh_watcher_entrypoint.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/test_refresh_watcher_process.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Refresh Watcher Port Anatomy

This folder maps the Core refresh-watcher capability: the immutable outer
handoff request/Port, the generated refresh policy, and the watcher-local typed
process-mechanism Port used by that policy. Concrete process ownership is
outside Core in the POSIX adapter package. Normative promises live in the paired
[`CONTRACT.md`](CONTRACT.md); [`MANUAL.md`](MANUAL.md) remains the capability
walkthrough.

## Components

- `RefreshWatcherRequest`, `RefreshWatcherPort`, and the deterministic
  `encode_request`/`decode_request` wire functions define the immutable outer
  handoff (`src/lingtai/kernel/refresh_watcher/__init__.py:32-76`, `:148-230`,
  and `:233-264`).
- `RefreshWatcherProcessHandle`, `RefreshWatcherProcessObservation`, and
  `RefreshWatcherProcessPort` define the local observe/liveness/start/graceful
  stop/force-stop boundary (`src/lingtai/kernel/refresh_watcher/__init__.py:79-145`).
- `render_watcher_script(request)` renders the existing ACK/lock, heartbeat,
  retry, matcher, redaction, and alert policy and calls only an injected
  `PROCESS_MECHANISM` global for process operations
  (`src/lingtai/kernel/refresh_watcher/watcher_program.py:53-431`).
- `select_refresh_watcher` is the fail-loud outer selector
  (`src/lingtai/adapters/refresh_watcher.py:14-35`).

## Connections

- `BaseAgent._perform_refresh` builds a `RefreshWatcherRequest` and calls the
  outer Port once after handshake ACK, before shutdown signaling
  (`src/lingtai/kernel/base_agent/lifecycle.py:724-829`).
- `lingtai.Agent` and `lingtai.cli.build_agent` obtain the outer Port through
  `select_refresh_watcher` (`src/lingtai/agent.py:194-204` and
  `src/lingtai/cli.py:15-152`).
- `PosixRefreshWatcherAdapter` owns the initial encoded-request detached
  handoff (`src/lingtai/adapters/posix/refresh_watcher.py:67-90`). Its entrypoint
  decodes/renders the policy and injects
  `PosixRefreshWatcherProcessAdapter` as `PROCESS_MECHANISM`
  (`src/lingtai/adapters/posix/refresh_watcher_entrypoint.py:30-64`).
- `PosixRefreshWatcherProcessAdapter` owns process-table observation, liveness,
  replacement launch, graceful termination, and forced termination
  (`src/lingtai/adapters/posix/refresh_watcher_process.py:26-87`).

## Composition

- **Parent:** [`src/lingtai/kernel/ANATOMY.md`](../ANATOMY.md).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns interface and behavior.
- **Outer selector:** [`src/lingtai/adapters/refresh_watcher.py`](../../adapters/refresh_watcher.py).
- **POSIX adapters:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
- **Canonical matcher:** `src/lingtai/kernel/process_match.py`.

## State

The Core Port and value objects own no runtime process state. The generated
policy owns only transient retry/failure metadata and the existing filesystem
artifacts/events. The POSIX process adapter owns no long-lived supervisor; it
creates observations/handles for one policy run and writes replacement stderr
through the requested log path. The outer adapter owns only its detached
entrypoint handoff.

## Notes

This is a capability-native process boundary, not a shared process framework.
The process adapter's concrete observation, liveness, termination, and detached
launch details must not move back into the renderer. The watcher policy remains
generated source for this slice so existing runtime behavior stays locked; the
fake-process contract test proves the injected calls behaviorally rather than
searching generated text for mechanism words.
