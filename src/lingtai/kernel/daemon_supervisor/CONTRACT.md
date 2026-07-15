---
name: daemon-supervisor
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/daemon_supervisor/ANATOMY.md
  - src/lingtai/kernel/daemon_supervisor/__init__.py
  - src/lingtai/kernel/daemon_supervisor/manifest.py
  - src/lingtai/kernel/daemon_supervisor/control.py
  - src/lingtai/adapters/posix/daemon_supervisor.py
  - src/lingtai/adapters/posix/daemon_execution_child_entrypoint.py
  - src/lingtai/adapters/posix/daemon_resume_owner_entrypoint.py
  - src/lingtai/adapters/posix/process_identity.py
  - src/lingtai/tools/daemon/manual/SKILL.md
maintenance: |
  Keep this Contract paired with its ANATOMY.md and preserve the repository
  Anatomy/Contract maintenance convention. Update the promise and focused tests
  together when the Port, manifest, control, or adapter boundary changes.
---
# Detached daemon supervisor contract

## Core

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
refuses unknown identity; it never falls back to second-resolution `ps`.

## Conformance

Focused tests must cover real detached process launch, parent shutdown survival,
parent interpreter exit, all backend-spec routing through the shared execution
host, completion/MCP/preset/skills reconstruction, run-owned logs, identity
mismatch, timeout/reclaim, control ack/race truth, terminal notification
idempotency, and restrictive manifest mode.
