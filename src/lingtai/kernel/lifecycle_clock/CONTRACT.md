---
name: lifecycle-clock
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/lifecycle_clock/ANATOMY.md
  - src/lingtai/kernel/base_agent/CONTRACT.md
  - src/lingtai/kernel/lifecycle_clock/__init__.py
  - src/lingtai/adapters/lifecycle_clock.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/kernel/base_agent/identity.py
  - src/lingtai/kernel/base_agent/lifecycle.py
  - src/lingtai/agent.py
  - src/lingtai/cli.py
  - src/lingtai/kernel/agent_presence/CONTRACT.md
  - docs/references/lifecycle-clock.md
  - tests/_lifecycle_clock_helpers.py
  - tests/test_lifecycle_clock.py
  - tests/test_snapshot.py
  - tests/test_agent_presence.py
  - tests/test_architecture_documents.py
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Lifecycle Clock

## Purpose

The lifecycle clock is Core's outbound boundary for reading the two time sources
its agent-lifecycle policy depends on, without knowing the concrete time
mechanism. It separates two distinct time domains — cross-process wall-clock
seconds and process-local monotonic seconds — from Python's concrete
`time.time()` / `time.monotonic()` clocks. Core reads the current time through
this Port for persisted/cross-process timestamps and ages (state/progress
bookkeeping, deferred-notification and event-journal timestamps, heartbeat
publication, and status heartbeat/progress/active-turn ages) and for process-local
elapsed intervals (uptime, idle-timeout, AED, and snapshot/GC pacing). The
capability is taught by [`docs/references/lifecycle-clock.md`](../../../../docs/references/lifecycle-clock.md).
The clock does not own waiting, sleeping, scheduling, datetime rendering, event
identity, the heartbeat cadence primitive, or process-supervision (CPR /
refresh-watcher) clocks.

## Behavior

Runtime and coding agents MUST read lifecycle time through the injected Port,
never through direct `time.time()` / `time.monotonic()` calls in the migrated
lifecycle paths. They MUST keep the two domains distinct: wall-clock readings
feed persisted/cross-process timestamps and ages and MAY jump forward or backward;
monotonic readings feed process-local elapsed intervals only and MUST NOT be
persisted or compared across processes. A consumer that receives a lifecycle
clock receives a real time source: there is NO disabled, nullable, no-op, or
default clock, and `BaseAgent` fails loudly at construction/signature time when
none is supplied. Agents MUST preserve the existing sampling structure and
comparison operators: constructor and state-transition wall samples stay shared
across the fields that read them, the heartbeat loop takes one shared monotonic
tick for both the snapshot and GC interval checks, and the strict/inclusive
threshold comparisons are unchanged. Own-heartbeat publication MUST pass the raw
wall float unchanged to `AgentPresenceStorePort.publish_heartbeat`; exact
`str(value)`-no-newline byte conformance belongs to the Agent Presence Contract
and `tests/test_agent_presence.py`, not lifecycle-clock test evidence. Preserve
the manifest-persist → heartbeat-withdraw → workdir-lease-release teardown order.
Agents MUST NOT add a
third clock operation, a wait/sleep/deadline/scheduler/timer method, `Path`,
datetime, timezone, or filesystem/POSIX vocabulary to this Port; MUST NOT route
`Event.wait(1.0)` heartbeat cadence, notification/event identity clocks, Agent
CPR clocks, the refresh-watcher subprocess clocks, retention/nudge/Task
Card/display/`time_veil` clocks, or LLM/session/tool clocks through it; and MUST
NOT construct the concrete adapter inside Core.

## Port

`LifecycleClockPort` is bound at construction and exposes exactly two
zero-argument readings:

1. `wall_seconds() -> float` — system wall-clock seconds suitable for persisted or
   cross-process comparison. The value may jump forward or backward and carries no
   monotonicity promise.
2. `monotonic_seconds() -> float` — process-local monotonic seconds from an
   arbitrary epoch. Only differences between values from the same runtime clock are
   meaningful; the value MUST NOT be persisted or compared across processes.

The Port names no `time`, datetime, timezone, `Path`, filesystem, POSIX, thread,
subprocess, scheduler, or wait/sleep/deadline vocabulary. There is no third
operation and no argument on either method.

## Adapters

`SystemLifecycleClockAdapter` (`src/lingtai/adapters/lifecycle_clock.py`) is the
one production adapter. It delegates `wall_seconds()` directly to Python
`time.time()` and `monotonic_seconds()` directly to `time.monotonic()`, reading
the two sources independently and returning the raw floats with no caching,
clamping, rounding, UTC formatting, policy, or exception translation. It is
portable, not POSIX-specific: Python's system clocks are the concrete mechanism,
but there is no filesystem, `fcntl`, symlink, or platform-selection behavior, so it
lives at the top of `lingtai.adapters` rather than under `adapters/posix` and is
not registered through an adapter registry. A deterministic in-memory
`FakeLifecycleClock` in `tests/_lifecycle_clock_helpers.py` implements the same
Port — with independently controlled wall and monotonic values and read counters —
to prove substitutability. Core never imports the adapter package.

## Contract rules

1. `BaseAgent.__init__` accepts a required `lifecycle_clock` Port — never a path,
   optional, default, or no-op adapter — stored as `self._lifecycle_clock` before
   the first monotonic/wall sample, and reads only its two methods. Core neither
   imports nor constructs `SystemLifecycleClockAdapter`.
2. `lingtai.Agent` is the outer composition root for direct wrapper callers: when
   no `lifecycle_clock` is injected it constructs `SystemLifecycleClockAdapter()`
   before `super().__init__`; an explicitly injected clock wins.
   `lingtai.cli.build_agent` explicitly constructs and injects the same adapter.
3. Wall-domain reads feed persisted/cross-process timestamps and ages:
   `_state_changed_at`/`_last_progress_at` seeding and state-transition
   bookkeeping, the ACTIVE `_active_turn_started_at`, the deferred-notification
   `_deferred_notifications_oldest_at`, the event-journal `ts`, the ACTIVE
   no-progress watchdog age, the own-heartbeat value, and the status
   no-progress/active-turn/heartbeat ages. The constructor and state-transition
   samples stay shared across the fields that read them.
4. Monotonic-domain reads feed process-local elapsed intervals only:
   `_idle_since_monotonic` at construction and state transition, `_uptime_anchor`
   at start/reset and status uptime, the AED `_aed_start` comparison, the one shared
   snapshot/GC tick, and the hidden IDLE→ASLEEP idle-timeout default (`now_mono`
   test seam preserved). No monotonic value becomes persisted state.
5. Own-heartbeat publication passes the raw wall float unchanged to the injected
   `AgentPresenceStorePort`. Exact `str(value)`-no-newline byte conformance is
   owned by the Agent Presence Contract and `tests/test_agent_presence.py`, not
   by lifecycle-clock test evidence. Preserve the strict liveness comparison,
   ASLEEP heartbeat continuity, and the manifest → heartbeat-withdraw →
   lease-release teardown order.
6. Both heartbeat-loop branches keep calling `agent._heartbeat_stop.wait(1.0)` on
   the dedicated stop `Event`; the Port gains no wait/sleep/deadline method and the
   Event is not turned into a generic timer.
7. Excluded clocks stay on their existing mechanisms even in migrated files: the
   synthetic notification/system-event `call_id`/`event_id` clocks, the Agent CPR
   deadline/poll/`time.sleep` clocks, the refresh-watcher subprocess source-string
   clocks, and the `datetime`/`time_veil`/display/retention/nudge/Task
   Card/LLM/session/tool clocks are not routed through this Port.

## Contract tests

`tests/test_lifecycle_clock.py` proves the Port exposes exactly the two abstract
methods with no concrete-technology imports; the production adapter forwards wall
and monotonic sources independently and returns the unmodified floats; the fake is
substitutable and advances wall independently from monotonic; a bare `BaseAgent`
omission fails at construction/signature time (no Core default/no-op adapter);
`lingtai.Agent` and `lingtai.cli.build_agent` compose `SystemLifecycleClockAdapter`
while an explicitly injected clock wins; heartbeat publishes the fake raw wall
float unchanged through `AgentPresenceStorePort`; constructor and ACTIVE-transition
changing-read cases prove the shared wall sample; uptime, idle timeout, and AED
consume monotonic values; snapshot and GC use one shared monotonic tick and preserve
the existing boundary operators. Exact daily GC boundary evidence belongs to
`tests/test_snapshot.py`; exact `str(value)`-no-newline presence-byte conformance
belongs to the Agent Presence Contract and `tests/test_agent_presence.py`. Wall
jumps do not alter monotonic elapsed behavior and monotonic movement does not alter
wall-age behavior. Direct mechanical evidence covers the two-operation Port shape
and `Event.wait(1.0)`; remaining exclusions are maintenance/source-review
constraints, not a claim that one lifecycle test directly locks every exclusion.
`tests/test_architecture_documents.py` enforces the governed twin, heading order,
canonical maintenance, adapter-outside-Core rule, the exact two-operation Port
shape, and reciprocal links.

## Maintenance

Read the paired Anatomy for locations and composition. Port, adapter, Core
callers, shared conformance tests, and this contract change together. Breaking
Port or semantic changes bump `contract_version`; implementation drift is a
defect, not permission to weaken this contract.
