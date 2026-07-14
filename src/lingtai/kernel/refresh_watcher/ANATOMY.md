---
related_files:
  - src/lingtai/kernel/refresh_watcher/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/kernel/base_agent/lifecycle.py
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

This folder is the Core-owned detached-process-supervision boundary: the
technology-neutral Port that lets Core hand the generated relaunch-watcher
script off to a process supervisor without knowing the concrete process
mechanism. The production POSIX adapter that implements it lives outside
Core; its promises are defined in the paired [`CONTRACT.md`](CONTRACT.md).

## Components

- `RefreshWatcherPort` — abstract outbound Port with exactly
  `spawn_detached(script, *, env)`
  (`src/lingtai/kernel/refresh_watcher/__init__.py:19-45`).

## Connections

- Core receives a `RefreshWatcherPort` as the optional, defaulted-`None`
  `refresh_watcher` constructor argument of `BaseAgent` and uses only
  `spawn_detached`. `_perform_refresh`
  (`src/lingtai/kernel/base_agent/lifecycle.py`) raises `RuntimeError` if
  `agent._refresh_watcher is None` once `_build_launch_cmd()` has produced a
  real command, checked before any handshake/shutdown mutation; when the Port
  is present it calls
  `agent._refresh_watcher.spawn_detached(relaunch_script, env=watcher_env)`
  once the `.refresh`/`.refresh.taken` handshake has established the ACK
  invariant, then sets `agent._cancel_event` / `agent._shutdown` so the
  watcher's lock-release phase completes. The no-launch-cmd path never
  reaches this check.
- The only production adapter is `PosixRefreshWatcherAdapter`
  (`src/lingtai/adapters/posix/refresh_watcher.py`), mapped structurally by
  [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
- The composition roots `src/lingtai/agent.py` and `src/lingtai/cli.py`
  construct and inject the adapter.

## Composition

- **Parent:** `src/lingtai/kernel/` (see [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the Port's behavioral
  promises and lists the adapter and contract tests.
- **Adapter package:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).

## State

The Port itself owns no state; it is an abstract boundary. The spawned
process's identity, lifetime, and stdio are owned by the POSIX adapter and
described in its docstring and the paired contract. The Port promises only
that the process was started detached, not any fact about its later state.

## Notes

This is a navigation-only Port anatomy; the concrete `subprocess`/POSIX
detachment mechanism is normative in the paired `CONTRACT.md`. The generated
watcher script's own internal behavior (handshake deadlines, relaunch retry,
stale-duplicate cleanup, redaction) is unaffected by this Port and stays
documented in `base_agent/ANATOMY.md`'s `lifecycle.py` entry — this Port
governs only the hand-off of that script to a detached process.
