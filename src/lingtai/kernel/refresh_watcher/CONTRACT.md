---
name: refresh-watcher
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/refresh_watcher/ANATOMY.md
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/__init__.py
  - src/lingtai/kernel/refresh_watcher/watcher_program.py
  - src/lingtai/kernel/refresh_watcher/MANUAL.md
  - src/lingtai/kernel/process_match.py
  - src/lingtai/adapters/refresh_watcher.py
  - src/lingtai/adapters/posix/refresh_watcher.py
  - src/lingtai/adapters/posix/refresh_watcher_process.py
  - src/lingtai/adapters/posix/refresh_watcher_entrypoint.py
  - src/lingtai/adapters/windows/refresh_watcher.py
  - src/lingtai/adapters/windows/refresh_watcher_process.py
  - src/lingtai/adapters/windows/refresh_watcher_entrypoint.py
  - tests/test_refresh_watcher_windows.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - tests/_refresh_watcher_helpers.py
  - tests/test_perform_refresh_handshake.py
  - tests/test_process_match.py
  - tests/test_deep_refresh.py
  - tests/test_refresh_watcher_process.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Core Ports,
  every production Adapter, selector, contract tests, and directly relevant
  component contracts belong here. Re-read this contract whenever a linked
  boundary changes. Update the Ports, affected Adapters, selector, contract
  tests, and this contract in the same change; update the paired Anatomy when
  structure or composition also changes; bump contract_version for a breaking
  Port-contract change. If code and contract disagree, treat the disagreement
  as a defect—do not silently rewrite the normative contract to match the
  implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Refresh Watcher

## Purpose

Refresh watcher is the Core boundary for handing a typed relaunch request to a
detached watcher after `_perform_refresh` completes the `.refresh` /
`.refresh.taken` filesystem handshake. The outer `RefreshWatcherPort` owns only
that first hand-off. The watcher program keeps the existing ACK/lock deadlines,
heartbeat health check, retry count/timing, stale-duplicate decision,
canonical matcher, redaction, permanent-failure artifact, notification, and
event policy.

The generated program is still rendered by
`watcher_program.render_watcher_script(request)` and still crosses the existing
compact `encode_request`/`decode_request` wire via each platform's `-m`
entrypoint.
The generated policy receives a second, watcher-local
`RefreshWatcherProcessPort` through the entrypoint's `PROCESS_MECHANISM` global.
That narrow Port performs no policy: it supplies only observation, process
liveness, replacement launch, graceful stop, and forced stop. Core decides when
to call each operation; the concrete POSIX adapter owns all process-table,
signal, and detached-launch mechanics.

See [`MANUAL.md`](MANUAL.md) for the existing capability walkthrough. The
manual's process-mechanism references should be kept aligned with this contract
when that separately scoped document is next maintained.

## Behavior

The observable refresh behavior is unchanged. A successful `_perform_refresh`
constructs one immutable `RefreshWatcherRequest`, waits for/normalizes the
existing handshake, calls `RefreshWatcherPort.spawn_detached` exactly once,
and then signals the existing cancellation/shutdown path. Failed ACK setup does
not spawn. A watcher runs the rendered policy with the exact copied environment
from `build_watcher_env(request)`, including authoritative true/false handling
of `LINGTAI_REFRESH_ENV_OVERWRITE`, detached stdio, and its platform's detached
outer handoff semantics (POSIX session detachment, or the Windows detached
creation flags).

`RefreshWatcherRequest` is frozen and carries only handshake paths, working
directory, a tuple command, identity fields JSON, and the env-overwrite policy
bit. It carries neither generated source nor a caller environment. The
technology-neutral outer Port does not expose process identity, waiting,
observation, signals, or platform vocabulary.

`BaseAgent` keeps the deliberate optional-at-construction behavior for unrelated
raw construction sites: a missing watcher is rejected at `_perform_refresh`
only after a real launch command exists and before handshake/shutdown mutation.
The wrapper `lingtai.Agent` and `lingtai.cli.build_agent` always select and
inject a production watcher when they compose an agent. An explicitly supplied
watcher wins.

## Ports

### Outer hand-off Port

`RefreshWatcherPort` exposes exactly:

- `spawn_detached(request: RefreshWatcherRequest) -> None` — launch the watcher
  and return after start; do not wait for completion or return process identity.

`RefreshWatcherRequest` fields are exactly `taken_path`, `lock_path`,
`events_path`, `stderr_log`, `working_dir`, `cmd: tuple[str, ...]`, `agent_name`,
`address`, `identity_fields_json: str = "{}"`, and
`env_overwrite: bool = True`. `encode_request` is deterministic and
`decode_request` fails loudly on malformed shape, restoring `cmd` to a tuple.

### Watcher-local process-mechanism Port

`RefreshWatcherProcessPort` is intentionally local to the refresh-watcher
capability; it is not a global process framework. Its operations are:

- `observe(pid) -> RefreshWatcherProcessObservation | None` — obtain the
  adapter's command-line observation for a candidate identity.
- `is_alive(process) -> bool` — report liveness of a returned observation or
  launch handle.
- `start_agent(cmd, stderr_log) -> RefreshWatcherProcessHandle` — launch the
  requested replacement and return its handle.
- `graceful_stop(process) -> None` — request the normal termination operation.
- `force_stop(process) -> None` — force termination after the policy's grace
  interval.

`RefreshWatcherProcessHandle(pid)` and
`RefreshWatcherProcessObservation(pid, command_line)` are frozen value objects.
The `pid` is retained only for existing redaction-safe event metadata; Core
never interprets it or performs a process operation directly. The Port has no
shell-language, platform, signal, process-table, stream, or session vocabulary.

## Adapters and composition

`PosixRefreshWatcherAdapter` (`adapters/posix/refresh_watcher.py`) implements
the outer `RefreshWatcherPort` on POSIX. It encodes the request,
builds the full environment, and launches
`lingtai.adapters.posix.refresh_watcher_entrypoint` with detached stdio and
POSIX session semantics.

`WindowsRefreshWatcherAdapter` (`adapters/windows/refresh_watcher.py`) is the
Windows outer implementation: same encode/env/`-m` transport against
`lingtai.adapters.windows.refresh_watcher_entrypoint`, detached with
`CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` creation flags instead of a
POSIX session. It reuses the sibling's platform-neutral `build_watcher_env` as
the single source of the env-overwrite policy translation.

`PosixRefreshWatcherProcessAdapter`
(`adapters/posix/refresh_watcher_process.py`) implements the
watcher-local process Port on POSIX: process-table
command-line observation (`ps`), liveness probing (`os.kill(pid, 0)`),
graceful/forced termination (SIGTERM/SIGKILL), and
detached replacement launch. It does not decide retries, heartbeat health,
duplicate identity, or alerts.

`WindowsRefreshWatcherProcessAdapter`
(`adapters/windows/refresh_watcher_process.py`) is the Windows sibling, bound
at construction to the supervised working directory: observation via a
PowerShell CIM `Win32_Process` command-line query; liveness via
`OpenProcess`/`GetExitCodeProcess` (never `os.kill`, which terminates rather
than probes on Windows); detached replacement launch with the shared creation
flags; graceful stop via the target working directory's `.suspend` cooperative
file channel (the platform's normal termination request for a LingTai agent
process — the agent's heartbeat loop consumes it and performs the ordered
stop); forced stop via `TerminateProcess` on the exact PID, never a tree kill.

Each platform's `refresh_watcher_entrypoint.main` decodes the request, renders
the Core policy, and executes it with `PROCESS_MECHANISM` set to a newly
composed platform process adapter (the Windows entrypoint binds it to
`request.working_dir`). The entrypoints are the only composition sites for the
generated policy's process mechanism; Core never imports the adapters.

`select_refresh_watcher` (`src/lingtai/adapters/refresh_watcher.py`) is the outer
platform selector. It returns the POSIX outer adapter on POSIX and the Windows
outer adapter on `win32`, and raises `NotImplementedError` before importing a
concrete adapter on any other platform. There is no default fake and no no-op
implementation. `lingtai.Agent` and CLI construction route
through this selector.

## Core policy boundary

`watcher_program.py` is a pure renderer. Its generated source may perform the
existing watcher file, time, heartbeat, retry, logging, redaction, and alert
operations, and may import the canonical
`lingtai.kernel.process_match.match_agent_run`. It must not construct or parse
a process-table command, perform process termination, or launch a replacement.
Instead, stale-duplicate cleanup calls the injected process Port in the order
chosen by policy (`observe` → `is_alive` → `graceful_stop` → grace polling →
`force_stop` when needed), and relaunch calls `start_agent` once per retry.

The generated policy must continue to redact bounded stderr/cleanup/relaunch
errors before all three terminal-failure sinks. It must continue to use the
same matcher import and not embed a second matcher implementation.

## Contract rules

1. Outer `spawn_detached` and request wire behavior remain lossless,
   deterministic, immutable, and exactly-once at the successful handshake.
2. The generated watcher keeps the prior ACK/lock deadlines, heartbeat
   threshold, retry count/timing, signal-file cleanup, duplicate decision,
   redaction, artifact, system notification, and event semantics.
3. Core policy never imports or constructs an adapter and never directly owns
   process observation, liveness, launch, or termination. The generated policy
   uses only the injected `RefreshWatcherProcessPort` global.
4. A process mechanism implementation must exercise the typed value objects and
   five Port operations; a fake may be used only in focused policy tests and is
   not a production fallback.
5. The platform process adapters are the only code that owns process-table
   parsing/queries, OS liveness probing, graceful/forced termination, and
   detached replacement launch. Each outer handoff adapter remains responsible
   for its own detached entrypoint launch, and each entrypoint composes only
   its own platform's process mechanism.
6. Selection returns the POSIX adapter on POSIX and the Windows adapter on
   `win32`; any other platform fails loudly with `NotImplementedError`. There
   is no no-op or default-fake watcher on any platform. The Windows graceful
   stop is the supervised working directory's `.suspend` cooperative channel;
   the Windows forced stop terminates exactly one PID and never a tree.
7. The generated stale-duplicate guard imports
   `from lingtai.kernel.process_match import match_agent_run` and contains no
   local matcher definition.
8. Terminal failure metadata is bounded/redacted before artifact,
   `.notification/system.json`, and final event persistence.
9. `build_watcher_env` copies the parent environment and makes the overwrite
   marker authoritative in both directions without mutating the parent.
10. The existing request serialization validation rejects invalid JSON, missing
    or extra fields, and wrong field shapes with `ValueError`.

## Contract tests

The existing handshake, request-wire, entrypoint, matcher, redaction, and
permanent-alert tests remain the behavior evidence in
`tests/test_perform_refresh_handshake.py`, `tests/test_deep_refresh.py`, and
`tests/test_process_match.py`. `tests/test_refresh_watcher_process.py` runs the
rendered Core policy with a small fake process mechanism and asserts policy
selection of observation, liveness, launch, graceful stop, and forced stop
without source-keyword scanning. The real POSIX `-m` smoke remains the evidence
that the POSIX entrypoint composes the production process adapter.
`tests/test_refresh_watcher_windows.py` pins the Windows side: exact detached
spawn shape (Windows entrypoint module, creation flags, env-overwrite policy in
both directions), entrypoint composition of the workdir-bound Windows process
mechanism, the `.suspend` graceful-stop channel, CIM observation shapes with
failure-to-`None` mapping, and — on native Windows — real detached launch,
liveness, forced-stop, and self-observation mechanism truth.

## Maintenance

Follow the canonical maintenance block in frontmatter. Behavioral changes
require synchronized Port, adapter, selector, contract-test, and contract
updates; structural or composition changes also update the paired Anatomy and
reciprocal parent navigation.
