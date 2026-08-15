---
related_files:
  - src/lingtai/kernel/execution_policy/CONTRACT.md
  - src/lingtai/kernel/execution_policy/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/execution_policy/__init__.py
  - src/lingtai/execution_policy/configured.py
  - src/lingtai/execution_policy/registry.py
  - src/lingtai/tools/daemon/__init__.py
  - tests/test_execution_policy.py
  - tests/test_daemon_execution_policy.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth; update this anatomy with Port, value, adapter, or composition changes.
---
# Execution policy

The execution-policy Core owns immutable workload requests, route decisions,
health values, errors, and the versioned `ExecutionPolicyPort`. It contains no
provider/model names, file parsing, Agent identity policy, or daemon mechanics.

## Components

- `__init__.py` — API version 1, immutable request/decision/health values,
  compatibility and availability errors, the Port, and pass-through policy.
- `lingtai/execution_policy/registry.py` — explicit adapter factory registry.
- `lingtai/execution_policy/configured.py` — first-party strict JSON adapter.
- `lingtai/tools/daemon/__init__.py` — composition and decision consumer.

## Connections

Daemon setup reads the optional `manifest.execution_policy` declaration and
loads one adapter. Before backend/preset side effects, `emanate` sends the exact
task workload, explicit caller choices, parent identity/authority, and preset
allowlist through the Port. The returned preset/backend then enters the existing
authorization, connectivity, capability, and detached-run gates.

## Composition

Core depends only on standard-library values. The wrapper registry and configured
file adapter live outside Core. An absent declaration composes pass-through
behavior, preserving legacy daemon semantics.

## State

Core is stateless. The configured adapter reads one immutable policy config at
construction and a current health snapshot at every decision. Workload and
route ID are persisted in the daemon run's non-secret call parameters.

## Notes

Workloads are exact strings. Agent identity never implies task responsibility;
the dispatching Agent declares responsibility per task.
