---
name: daemon-supervisor
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - tests/test_tool_decision_phase.py
  - src/lingtai/kernel/daemon_supervisor/ANATOMY.md
  - src/lingtai/kernel/daemon_supervisor/__init__.py
  - src/lingtai/kernel/daemon_supervisor/manifest.py
  - src/lingtai/kernel/daemon_supervisor/control.py
  - src/lingtai/adapters/posix/daemon_capsule.py
  - src/lingtai/adapters/posix/daemon_manager.py
  - src/lingtai/adapters/posix/daemon_supervisor.py
  - src/lingtai/adapters/posix/daemon_execution_child_entrypoint.py
  - src/lingtai/adapters/posix/daemon_resume_owner_entrypoint.py
  - src/lingtai/adapters/posix/process_identity.py
  - src/lingtai/adapters/windows/daemon_supervisor.py
  - src/lingtai/adapters/windows/daemon_supervisor_entrypoint.py
  - src/lingtai/adapters/windows/daemon_execution_child_entrypoint.py
  - src/lingtai/adapters/windows/daemon_resume_owner_entrypoint.py
  - src/lingtai/adapters/windows/process_identity.py
  - tests/test_daemon_windows_supervisor.py
  - src/lingtai/tools/daemon/manual/SKILL.md
  - src/lingtai/tools/daemon/execution_host.py
  - src/lingtai/tools/daemon/shell_prompt_events.py
  - tests/test_daemon_detached_supervisor.py
maintenance: |
  Keep this Contract paired with its ANATOMY.md and preserve the repository
  Anatomy/Contract maintenance convention. Update the promise and focused tests
  together when the Port, manifest, control, or adapter boundary changes.
---
# Detached daemon supervisor contract

## Core
Guarded by: [DS001](BEHAVIORS.md#behavior-ds001)


The Core owns immutable request, manifest, and control schemas plus pure
validation. It does not import `DaemonManager`, concrete backend runners, or
POSIX process APIs.

## Ports

`DaemonSupervisorPort.spawn_detached(request)` accepts only a validated
`DaemonSupervisorRequest` carrying run ID, manifest path, and interpreter. The
Port returns after launch and never exposes a process handle, future, or parent
Agent object.

## Adapters

The POSIX adapter translates the Port to detached `python -m` entrypoints in
new sessions, passes the source environment needed by editable runs, and routes
supervisor/child stdout/stderr to restrictive run-owned logs. The supervisor
starts an exact execution child before its watcher; terminal CLI resume uses a
durable single-writer generation and a detached resume owner. No parent future
or process handle is retained, and no broad process matching is performed.

The POSIX runtime extension may carry one already-open child descriptor beside
the bounded capsule. The private central-manager AF_UNIX socket and each
parent/child socketpair use `SCM_RIGHTS`, so transfer duplicates the descriptor
into the receiver. Every API that accepts `adopted_fd` owns it immediately:
the sender closes its copy exactly once after successful transfer and closes it
on every failure before transfer. The receiver owns its duplicate until it
transfers it once or closes it. Replacing a capsule, rolling back a failed ACK,
cancelling queued work, quarantining malformed work, failing before execution,
or exiting the manager must release the descriptor before discarding the
corresponding decision.

The Windows adapter (`WindowsDaemonSupervisorAdapter`) is the `nt` production
sibling behind the same Port: the same encoded request and secret-stripped
environment, launched against Windows-owned entrypoint mirrors
(`lingtai.adapters.windows.daemon_supervisor_entrypoint`, execution child,
resume owner) with `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` creation
flags instead of a POSIX session. The one-shot capsule crosses as an
inherited pipe HANDLE allowed through
`STARTUPINFO.lpAttributeList["handle_list"]` under `close_fds=True`; the
child environment carries only the numeric handle
(`LINGTAI_DAEMON_CAPSULE_HANDLE`), which the entrypoint mirrors convert back
to a CRT fd (`msvcrt.open_osfhandle`) and republish on the shared fd wire so
the POSIX modules' mechanism-free read loops run unchanged. Capsule bytes are
still written after `Popen`, bounded at 4 MiB, never on disk/argv/env, and
consumed exactly once. Platform selection is
`select_daemon_supervisor_adapter` in the tools-layer supervisor runtime;
other platforms fail loudly. Windows records `execution_pgid=None` (no POSIX
process group exists) and identity is the shared
`windows:<creation_filetime>` incarnation token, so ownership checks stay
fail-closed on missing/mismatched identity.

## Runtime promise

One supervisor owns one run from birth through terminal state. It validates
request/manifest/run-directory identity, records PID/start identity, reconstructs
runtime inputs through the tools-layer execution host, enforces deadline/control,
terminates only its exact execution group and any exact nested CLI group, writes
terminal truth, and publishes one idempotent notification. Supported terminal
CLI ask/resume creates one durable generation claim whose owner is identified
by PID plus stable process-incarnation identity; a bounded pending-launch lease
also blocks a successor until the exact generation/nonce promotes or the lease
expires. The resume owner persists follow-up result state and releases exactly
that generation. Parent stop/refresh is not a cancellation operation.

## Durable and secret boundary

Manifest, control, and log files contain no resolved credentials. API keys are
represented by environment/config references; MCP env/header values and auth-
shaped CLI arguments are redacted. Raw child secrets may exist only in the
inherited one-shot capsule and final child spawn arguments; credential-shaped
environment values are restored only in the dedicated execution child. Every
durable command rendering uses the shared auth-shaped redaction policy. On
Darwin, ownership control uses the libproc birth second/usecond token and
refuses unknown identity; it never falls back to second-resolution `ps`. On
Windows, the capsule descriptor in the child environment is a handle number
only — never capsule content — and ownership control uses the
creation-filetime incarnation token with the same refuse-unknown-identity
rule.

An adopted child descriptor is equally ephemeral: only the live manager,
supervisor, and execution-child processes may own it. It is never serialized to
the manifest, queue, journal, argv, or an environment value; the environment
contains only the inherited capsule-socket locator. Manager restart therefore
invalidates the descriptor and the durable queued job follows the existing
missing-capsule terminal failure path. Windows closes and rejects any attempted
descriptor handoff until an equivalent transport exists.

This transport is policy-inert. The execution host merely retains the received
descriptor for a future runtime consumer and closes it if unconsumed; this
slice does not compose Driver authority or remove the existing fail-closed
dispatch gate for Driver-issued daemon leases.

## Conformance

Focused tests must cover real detached process launch, parent shutdown survival,
parent interpreter exit, all backend-spec routing through the shared execution
host, completion/MCP/preset/skills reconstruction, run-owned logs, identity
mismatch, timeout/reclaim, control ack/race truth, terminal notification
idempotency, and restrictive manifest mode.
Descriptor tests additionally cover successful one-time transfer, prevention
of double-close after descriptor-number reuse, and every discard/failure class
listed above using the descriptor actually handed to the system.


## Selected Shell composition boundary

The supervisor Core neither owns Shell policy nor reconstructs an Agent. Its
execution child may ask `DetachedDaemonExecutionHost` to invoke Shell's private
detached composer with a `DaemonRunDir`-local `NotificationPort` adapter and
`<run>/shell-jobs` state namespace. That adapter is tools layer composition, not
a supervisor port or parent notification route: it writes bounded prompt-event
state only while the run is live, retries an unacknowledged full-queue publication
with capped backoff, and never calls the stub's notification method/store, writes
`.notification`, or emits another terminal receipt. Command cwd remains the
granted task workdir, while activation and rehydration cannot enter parent or
sibling job namespaces. The supervisor continues to own exactly one terminal
daemon receipt; it does not wait for, join, cancel, or restart a daemon around a
Shell job.

## Native review manifest option

`manifest.py:build_manifest` / `read_manifest` carry optional boolean
`tool_decision_enabled` (omitted means false). The native daemon host writes its
own setup flag, not the parent Agent flag; `DetachedDaemonExecutionHost` restores
it for the shared post-response review phase. Non-booleans are invalid. This
additive value does not change supervisor identity, launch, cancellation or
resume ownership. See the [daemon owner contract](../../tools/daemon/CONTRACT.md#native-tool-decision-phase),
[manual](../../tools/daemon/manual/SKILL.md#native-tool-decision-phase) and
[D012](../../tools/daemon/BEHAVIORS.md#behavior-d012); the offline manifest and
native-loop regressions live in `tests/test_tool_decision_phase.py`.
