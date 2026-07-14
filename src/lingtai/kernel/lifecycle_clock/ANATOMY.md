---
related_files:
  - src/lingtai/kernel/lifecycle_clock/CONTRACT.md
  - src/lingtai/kernel/lifecycle_clock/__init__.py
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/adapters/lifecycle_clock.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/identity.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - docs/references/lifecycle-clock.md
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Lifecycle Clock Port Anatomy

This folder is the Core-owned lifecycle-time boundary: the technology-neutral
Port that lets Core read wall-clock and monotonic seconds for its agent-lifecycle
policy without knowing the concrete time mechanism. The production portable
adapter that implements it lives outside Core; its promises are defined in the
paired [`CONTRACT.md`](CONTRACT.md) and the capability is taught by the manual
[`docs/references/lifecycle-clock.md`](../../../../docs/references/lifecycle-clock.md).

## Components

- `LifecycleClockPort` — abstract outbound Port with exactly `wall_seconds` and
  `monotonic_seconds` (`src/lingtai/kernel/lifecycle_clock/__init__.py:19-60`).

## Connections

- Core receives a `LifecycleClockPort` as the required `lifecycle_clock`
  constructor argument of `BaseAgent`, stores it as `self._lifecycle_clock` before
  the first monotonic/wall sample, and reads only its two methods
  (`src/lingtai/kernel/base_agent/__init__.py`).
- Wall-domain readers: constructor and state-transition timestamp seeding, the
  ACTIVE active-turn start, deferred-notification and event-journal timestamps,
  and the ACTIVE no-progress watchdog age in
  `src/lingtai/kernel/base_agent/__init__.py` and
  `src/lingtai/kernel/base_agent/lifecycle.py`; the status no-progress/active-turn
  ages and the heartbeat age in `src/lingtai/kernel/base_agent/identity.py`; and
  the own-heartbeat value published through `AgentPresenceStorePort` in
  `src/lingtai/kernel/base_agent/lifecycle.py`.
- Monotonic-domain readers: the initial/state-transition idle anchor in
  `src/lingtai/kernel/base_agent/__init__.py`; the uptime start/reset anchor, the
  AED start/check, the one shared snapshot/GC tick, and the hidden idle-timeout
  default in `src/lingtai/kernel/base_agent/lifecycle.py`; and status uptime in
  `src/lingtai/kernel/base_agent/identity.py`.
- The only production adapter is `SystemLifecycleClockAdapter`
  (`src/lingtai/adapters/lifecycle_clock.py`), mapped structurally by the source
  root [`src/lingtai/ANATOMY.md`](../../ANATOMY.md). The composition roots
  `src/lingtai/agent.py` and `src/lingtai/cli.py` construct and inject it.

## Composition

- **Parent:** `src/lingtai/kernel/` (see [`ANATOMY.md`](../ANATOMY.md)).
- **Paired contract:** [`CONTRACT.md`](CONTRACT.md) owns the Port's behavioral
  promises and lists the adapter, composition roots, and contract tests.
- **Sibling relationship:** the agent-presence store consumes the injected wall
  clock for its heartbeat value; see
  [`src/lingtai/kernel/agent_presence/CONTRACT.md`](../agent_presence/CONTRACT.md).

## State

The Port itself owns no state; it is an abstract boundary. It reads live clock
values on demand and holds nothing. Callers own the derived anchors
(`_uptime_anchor`, `_idle_since_monotonic`, `_aed_start`, `_last_snapshot`,
`_last_gc` are process-local monotonic anchors; `_state_changed_at`,
`_last_progress_at`, `_heartbeat`, and event/status ages are wall values); the
concrete time mechanism lives entirely in the outside adapter.

## Notes

This is a navigation-only Port anatomy; the two-domain semantics, sampling and
comparison rules, composition roots, and exclusions are normative in the paired
[`CONTRACT.md`](CONTRACT.md). There is no second concrete clock mechanism and no
dedicated anatomy for the one-file portable system adapter — its structure is
owned by this governed pair and the source-root composition map. The excluded
clocks (heartbeat `Event.wait(1.0)` cadence, notification/event identity, Agent
CPR, refresh-watcher subprocess, retention/nudge/Task Card/`time_veil`/display,
and LLM/session/tool clocks) are deliberately not routed through this Port.
