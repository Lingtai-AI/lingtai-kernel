---
related_files:
  - src/lingtai/kernel/daemon_supervisor/CONTRACT.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/daemon_supervisor/__init__.py
  - src/lingtai/kernel/daemon_supervisor/manifest.py
  - src/lingtai/kernel/daemon_supervisor/control.py
  - src/lingtai/kernel/daemon_supervisor/supervisor.py
  - src/lingtai/adapters/posix/daemon_supervisor.py
  - src/lingtai/tools/daemon/execution_host.py
  - src/lingtai/tools/daemon/supervisor_runtime.py
  - src/lingtai/adapters/posix/process_identity.py
  - src/lingtai/tools/daemon/manual/SKILL.md
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Detached daemon supervisor

The supervisor Port and durable run schemas define the narrow process boundary; runtime composition remains in the tools layer.

## Components

- `__init__.py:34-134` — immutable request wire schema and spawn Port.
- `manifest.py:44-190` — secret-free manifest build/write/read and identity validation.
- `control.py:12-96` — UUID request spool with schema/run identity and ack markers.
- `supervisor.py:1-13` — deliberately empty Core boundary marker; concrete lifetime composition stays outside Core.
- `tools/daemon/supervisor_runtime.py:77-138` — detached startup identity, run attachment, and terminal dispatch.
- `tools/daemon/supervisor_runtime.py:219-394` — execution-child ownership plus the exact control/deadline watcher.
- `adapters/posix/daemon_supervisor.py:27-77` — concrete interpreter/session/log launch adapter.
- `tools/daemon/execution_host.py:24-224` — composition root that reuses manager setup and every `_BackendSpec` runner.

## Connections

The parent manager writes the manifest, invokes the Port adapter, and later reads run state or writes control requests. The POSIX entrypoint decodes the request and calls the supervisor. The supervisor attaches one `DaemonRunDir`, composes `DetachedDaemonExecutionHost`, and publishes terminal truth without a parent Agent.

## Composition

Core schemas depend only on standard-library value types. Concrete process launch is an adapter. Backend setup and execution are composed outside Core through `tools/daemon/execution_host.py`; no supervisor-specific backend parser is owned here.

## State

Persistent state is the per-run manifest, restrictive supervisor logs, control request/ack files, `daemon.json`, result/artifact files, per-generation follow-up result/completion receipts, and terminal/follow-up notification receipts. Detached follow-up claims live with each `resume-claims/resume-<generation>.json`; the supervisor claims before release, retains the claim on sink failure or a receipt crash, and fresh-manager reconciliation retries the same generation-specific idempotency key. Ephemeral state is one watcher, one host, and exact backend child groups per run.

## Notes

A supervisor is never adopted or restarted. Parent refresh is ordinary and cannot terminate it; explicit reclaim and timeout are the only cancellation paths.
