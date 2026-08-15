---
name: execution-policy
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/CONTRACT.md
  - src/lingtai/kernel/execution_policy/ANATOMY.md
  - src/lingtai/kernel/execution_policy/__init__.py
  - src/lingtai/execution_policy/__init__.py
  - src/lingtai/execution_policy/configured.py
  - src/lingtai/execution_policy/registry.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/_tool_family.py
  - src/lingtai/init_schema.py
  - tests/test_execution_policy.py
  - tests/test_daemon_execution_policy.py
maintenance: |
  Keep this Contract paired with its ANATOMY.md. Change the API version only for
  incompatible Port/declaration/config/health changes, and update focused tests
  and the daemon contract in the same change.
---
# Execution policy contract

## Core and Port

`ExecutionPolicyPort.decide(request)` accepts an immutable `ExecutionRequest`
containing exact workload, caller-selected preset/backend, parent address/admin
flag, and allowed preset references. It returns an immutable
`ExecutionDecision` with preset, backend, and optional non-secret route ID.

Core API version is exactly `1`. Unknown versions fail before execution. The
Core package is technology-neutral and must not name providers or models.

## Configuration adapter

The built-in declaration is:

```json
{"api_version":1,"adapter":"configured","config":"system/execution-policy.json","health":"system/execution-policy-health.json"}
```

Both paths are relative to the Agent working directory and may not escape it.
The policy file contains exact workload keys and ordered `{route_id,preset}`
candidates. The health file contains exact route IDs with boolean availability.
Unknown/missing fields, malformed JSON, unavailable routes, and unsupported API
versions fail loudly.

## Selection promise

An explicit task preset wins unchanged. Otherwise, the first candidate whose
preset is in the parent allowlist and whose route is available wins. A workload
without configuration and a configured workload with no eligible healthy
candidate both fail instead of guessing or silently changing responsibility.

## Daemon boundary

The daemon validates `workload` as a non-empty, whitespace-exact string and uses
`worker` only when omitted. Policy runs before backend/preset side effects. Every
selected preset still passes the existing parent allowlist, preset load,
connectivity, and capability gates. Mixed per-task backends in one batch fail.

## Compatibility

No declaration composes the pass-through policy. Existing explicit presets and
backend behavior remain unchanged. Route configuration changes do not require a
Core change or new Agent identity; model/provider upgrades are data changes.
