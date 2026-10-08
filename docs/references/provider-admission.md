---
related_files:
  - src/lingtai/kernel/base_agent/ANATOMY.md
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/base_agent/BEHAVIORS.md
  - src/lingtai/kernel/provider_admission.py
  - tests/test_provider_admission.py
maintenance: |
  Manual for the Core provider-admission boundary. Keep it reachable from both
  base_agent owner twins and align its one-hop and fail-closed guidance with the
  Port, Contract, LABT, and focused tests.
---

# Provider admission

Provider admission is the Core boundary that decides whether one concrete model
request may reach a provider. It is not a sandbox and it is not a bearer-token
transport: Core carries only private typed parent objects while the driving
adapter owns authentication, current authority, and audit records.

## How to compose it

At the composition root, wrap the live `LLMService` with a
`ProviderCallAdmissionPort`. Bind a `RootProviderAdmission` only after the
driving adapter has admitted the root turn. The wrapper asks the Port again
immediately before every `send`, `send_stream`, or direct `generate`; a
refresh-created provider service must receive the same wrapper before use.

A daemon or avatar launch may receive one `DerivedProviderAdmission` created
from that root. The child class is typed (`DAEMON` or `AVATAR_CHILD`), the
internal handle is not serializable, and a derived parent cannot create another
derived parent. The Driver must independently enforce that same one-hop rule at
its launch boundary.

## Failure handling

Only `GRANTED` permits provider I/O. Missing parent, malformed response, Port
exception, `DENIED`, and `INDETERMINATE` all fail closed. Treat an unconnected
derived adapter as `derived_admission_port_unconnected`; do not install a
permissive fallback or reuse a root decision. A retry requests a new admission
at the next real provider call.

For a resident ACP attach, the Agent's service uses the connection-scoped
router. The correlated attach turn binds its own provider and derived-launch
Ports; ordinary local turns retain their existing path. The router must never
substitute the resident's local grant for an attached turn: missing or denied
connection authority fails before model I/O. The authority is closed when the
ACP connection closes; no turn-start grant is cached.

## Verifying a change

Run:

```bash
python -m pytest -q tests/test_provider_admission.py
```

Read [BA005](../../src/lingtai/kernel/base_agent/BEHAVIORS.md#behavior-ba005)
for the reproducible acceptance procedure. When adding a direct derived-launch
constructor, update the source inventory and its sensitivity test. The inventory
covers documented direct names, imports/re-exports, attributes, and simple
assignment aliases; dynamic factories, registries, and override wrappers still
require focused review and production-path E2E.
