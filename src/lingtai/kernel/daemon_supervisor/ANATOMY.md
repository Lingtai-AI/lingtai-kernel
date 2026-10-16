---
related_files:
  - src/lingtai/kernel/daemon_supervisor/BEHAVIORS.md
  - src/lingtai/kernel/daemon_supervisor/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/daemon_supervisor/__init__.py
  - src/lingtai/kernel/daemon_supervisor/manifest.py
  - src/lingtai/kernel/daemon_supervisor/control.py
  - src/lingtai/kernel/daemon_supervisor/agent_stub.py
  - src/lingtai/adapters/posix/daemon_supervisor.py
  - src/lingtai/adapters/posix/daemon_capsule.py
  - src/lingtai/adapters/posix/daemon_manager.py
  - src/lingtai/adapters/posix/daemon_mach.py
  - tests/test_daemon_mach.py
  - src/lingtai/adapters/windows/daemon_supervisor.py
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/execution_host.py
  - src/lingtai/tools/daemon/shell_prompt_events.py
  - tests/test_daemon_detached_supervisor.py
  - src/lingtai/tools/daemon/supervisor_runtime.py
  - src/lingtai/adapters/posix/process_identity.py
  - src/lingtai/tools/daemon/manual/SKILL.md
  - ENVIRONMENT_VARIABLES.md
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
# Detached daemon supervisor

The supervisor Port and durable run schemas define the narrow process boundary; runtime composition remains in the tools layer.

## Components

- `__init__.py:34-134` — immutable request wire schema and spawn Port.
- `manifest.py:44-190` — secret-free manifest build/write/read and identity validation.
- `control.py:12-96` — UUID request spool with schema/run identity and ack markers.
- `agent_stub.py:1-58` — the minimal agent surface a detached run composes against, so Core owns no live Agent.
- `adapters/windows/daemon_supervisor.py:117-247` — the Windows launch adapter for the same Port.
- `tools/daemon/supervisor_runtime.py:77-138` — detached startup identity, run attachment, and terminal dispatch.
- `tools/daemon/supervisor_runtime.py:219-394` — execution-child ownership plus the exact control/deadline watcher.
- `adapters/posix/daemon_supervisor.py:94-267` (`PosixDaemonSupervisorAdapter`) — concrete interpreter/session/log launch adapter.
- `adapters/posix/daemon_capsule.py` — bounded capsule encoding plus optional
  one-descriptor `SCM_RIGHTS` transfer and explicit receiver ownership.
- `tools/daemon/execution_host.py:24-224` — composition root that reuses manager setup and every `_BackendSpec` runner; selected Shell alone invokes its private composer for the run-local `shell_prompt_events.py` NotificationPort adapter and `<run>/shell-jobs` namespace, rather than the stub's absent Agent route or shared parent jobs.

- `adapters/posix/daemon_mach.py` — Darwin file-free rendezvous for manager
  socketpair endpoints, kernel audit identity gates and native right cleanup;
  [DS002](BEHAVIORS.md#behavior-ds002) guards the local transport boundary.

## Connections

The parent manager writes the manifest, invokes the Port adapter, and later reads run state or writes control requests. The private POSIX manager socket can pair the in-memory capsule with one received descriptor, which is then transferred to the supervisor and its execution child. The POSIX entrypoint decodes the request and calls the supervisor. The supervisor attaches one `DaemonRunDir`, composes `DetachedDaemonExecutionHost`, and publishes terminal truth without a parent Agent. Selected Shell reminder/completion events stay inside that same live run as provider-boundary prompt guidance; they are not supervisor terminal notifications.

## Composition

Core schemas depend only on standard-library value types. Concrete process launch is an adapter. Backend setup and execution are composed outside Core through `tools/daemon/execution_host.py`; no supervisor-specific backend parser is owned here.

## State

Persistent state is the per-run manifest, restrictive supervisor logs, control request/ack files, `daemon.json` (including bounded pending/delivered Shell prompt-event refs when selected), result/artifact files, and terminal notification receipt. Ephemeral state is one watcher, one host, exact backend child groups, and at most one adopted child descriptor per run. That descriptor is never journaled; replacement, cancellation, malformed work, failed transfer, or process exit closes the current owner.

## Notes

A supervisor is never adopted or restarted. Parent refresh is ordinary and cannot terminate it; explicit reclaim and timeout are the only cancellation paths.
