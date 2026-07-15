---
related_files:
  - src/lingtai/kernel/refresh_watcher/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - src/lingtai/kernel/refresh_watcher/MANUAL.md
  - src/lingtai/kernel/process_match.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/adapters/posix/refresh_watcher_entrypoint.py
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
technology-neutral Port that lets Core hand a typed relaunch request off to a
process supervisor without knowing the concrete process mechanism, plus the
Core-owned renderer that turns that request into the watcher program's
source text. The production POSIX adapter that implements the Port lives
outside Core; its promises are defined in the paired
[`CONTRACT.md`](CONTRACT.md). See [`MANUAL.md`](MANUAL.md) for the
what/how/why walkthrough.

## Components

- `RefreshWatcherRequest` — immutable (frozen dataclass) value object
  carrying `taken_path`, `lock_path`, `events_path`, `stderr_log`,
  `working_dir`, `cmd: tuple[str, ...]`, `agent_name`, `address`,
  `identity_fields_json: str` (default `"{}"`), and `env_overwrite`. `cmd`
  is a tuple rather than a `list` so it is actually immutable, not just its
  top-level field binding. `identity_fields_json` is a JSON object string
  snapshot rather than a tuple-of-pairs, because the identity payload
  contains a nested mutable dict (`kernel_runtime`) that a shallow container
  would still alias — see `CONTRACT.md`'s Port section for the full
  rationale (`src/lingtai/kernel/refresh_watcher/__init__.py:22-65`).
- `RefreshWatcherPort` — abstract outbound Port with exactly
  `spawn_detached(request: RefreshWatcherRequest)`
  (`src/lingtai/kernel/refresh_watcher/__init__.py:68-99`).
- `_decode_identity_fields(identity_fields_json)` — decodes+validates the
  JSON snapshot back to a `dict`, raising `ValueError` on invalid JSON or a
  non-object top-level value
  (`src/lingtai/kernel/refresh_watcher/watcher_program.py:69-92`).
- `render_watcher_script(request)` — pure Core-owned function rendering the
  complete watcher program source from a `RefreshWatcherRequest` (decoding
  `identity_fields_json` via `_decode_identity_fields` for the rendered
  literal shape); performs no OS calls
  (`src/lingtai/kernel/refresh_watcher/watcher_program.py:95-471`). The
  rendered program's stale same-agent duplicate-process guard
  (`_is_same_agent_run`) imports the canonical Core process-command matcher
  `lingtai.kernel.process_match.match_agent_run`
  (`src/lingtai/kernel/process_match.py`) at runtime rather than embedding a
  second local definition — the same matcher `lingtai.cli._check_duplicate_process`
  uses.
- `encode_request(request)` / `decode_request(payload)` — pure Core-owned,
  technology-neutral functions defining a compact deterministic JSON wire
  shape for a `RefreshWatcherRequest` (fixed field order; `cmd` round-trips
  through a JSON array back to a `tuple`) and validating it on decode,
  failing loudly (`ValueError`) on malformed input. They know nothing about
  how a transport delivers the encoded string
  (`src/lingtai/kernel/refresh_watcher/__init__.py`).

## Connections

- Core receives a `RefreshWatcherPort` as the optional, defaulted-`None`
  `refresh_watcher` constructor argument of `BaseAgent` and uses only
  `spawn_detached`. `_perform_refresh`
  (`src/lingtai/kernel/base_agent/lifecycle.py`) raises `RuntimeError` if
  `agent._refresh_watcher is None` once `_build_launch_cmd()` has produced a
  real command, checked before any handshake/shutdown mutation; when the Port
  is present it builds a `RefreshWatcherRequest` from the handshake paths,
  launch command, and identity fields, and calls
  `agent._refresh_watcher.spawn_detached(request)` once the
  `.refresh`/`.refresh.taken` handshake has established the ACK invariant,
  then sets `agent._cancel_event` / `agent._shutdown` so the watcher's
  lock-release phase completes. The no-launch-cmd path never reaches this
  check.
- The only production adapter is `PosixRefreshWatcherAdapter`
  (`src/lingtai/adapters/posix/refresh_watcher.py`), mapped structurally by
  [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
  It calls `encode_request(request)` for the wire payload and its own
  `build_watcher_env(request)` for the process environment before launching
  `sys.executable -m lingtai.adapters.posix.refresh_watcher_entrypoint
  <payload>`. The owned entrypoint module
  (`src/lingtai/adapters/posix/refresh_watcher_entrypoint.py`, also mapped by
  the POSIX adapter Anatomy) is the process that actually decodes the
  payload via `decode_request` and calls `render_watcher_script(request)` for
  the program text before executing it.
- The composition roots `src/lingtai/agent.py` and `src/lingtai/cli.py`
  construct and inject the adapter.

## Composition

- **Parent:** `src/lingtai/kernel/` (see [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the Port's behavioral
  promises and lists the adapter and contract tests.
- **Adapter package:** [`src/lingtai/adapters/posix/ANATOMY.md`](../../adapters/posix/ANATOMY.md).
- **Manual:** [`MANUAL.md`](MANUAL.md).

## State

The Port itself owns no state; it is an abstract boundary.
`RefreshWatcherRequest` is an immutable value object with no owned state
beyond its fields. The spawned process's identity, lifetime, and stdio are
owned by the POSIX adapter and described in its docstring and the paired
contract. The Port promises only that the process was started detached, not
any fact about its later state.

## Notes

This is a navigation-only Port anatomy; the concrete `subprocess`/POSIX
detachment mechanism, the `-m` entrypoint-module transport, and
full-environment construction are normative in the paired `CONTRACT.md` and
live in the adapter package (`src/lingtai/adapters/posix/`), not here.
For Core-produced requests, `watcher_program.render_watcher_script` preserves
the previously inline `lifecycle.py` script's runtime behavior (handshake
deadlines, relaunch retry, stale-duplicate cleanup, redaction) without claiming
textual byte identity — this Port and its renderer govern only the hand-off to a
detached process, not a redesign of that policy. The one deliberate structural
change since that extraction: the rendered program's stale-duplicate guard now
imports `lingtai.kernel.process_match.match_agent_run`
(`src/lingtai/kernel/process_match.py`) instead of embedding its own copy of
that matching policy, so the watcher and CLI runtime consumers share one
importable Core implementation, while the standalone `lingtai-doctor` bundle
intentionally retains its stdlib-only copy under
`tests/test_process_match.py` parity coverage — this is not a
claim that the watcher's own retry/heartbeat/duplicate-cleanup policy became
independently unit-testable, only that one helper it calls is now imported
rather than duplicated. `base_agent/ANATOMY.md`
still narrates that behavior in its `lifecycle.py` entry for readers
descending from `base_agent/`. The transport that carries a
`RefreshWatcherRequest` across the process boundary (`encode_request`/
`decode_request` plus the POSIX adapter's `-m` invocation of
`refresh_watcher_entrypoint`) replaced the earlier `-c <script>` argv
transport without changing what crosses the Port itself — `spawn_detached`
still takes exactly one `RefreshWatcherRequest` and returns `None`.
