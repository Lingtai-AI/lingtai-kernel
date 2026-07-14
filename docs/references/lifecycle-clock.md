# Lifecycle Clock — capability manual

The **lifecycle clock** is the kernel's Core-owned boundary for reading time in
the agent-lifecycle paths. It gives Core the two time sources its lifecycle
policy needs — cross-process wall-clock seconds and process-local monotonic
seconds — without letting Core name Python's concrete `time.time()` /
`time.monotonic()` clocks.

This manual teaches *what to do* with the capability. The normative promises,
domains, and exclusions live in the paired
[`CONTRACT.md`](../../src/lingtai/kernel/lifecycle_clock/CONTRACT.md); the code
map lives in the paired
[`ANATOMY.md`](../../src/lingtai/kernel/lifecycle_clock/ANATOMY.md).

## Why it exists

Before this slice, the kernel called `time.time()` and `time.monotonic()`
directly in `base_agent/__init__.py`, `identity.py`, and `lifecycle.py`. That
scattered the "what time is it?" mechanism across Core and made lifecycle timing
impossible to drive deterministically in a test without monkeypatching a module
global. It also blurred a real distinction the lifecycle code depends on: some
timestamps are **persisted and compared across processes** (a heartbeat file read
by another process, a status age, an event-journal `ts`), while others are
**process-local elapsed intervals** (uptime, the idle timeout, the AED countdown,
snapshot pacing). Those two needs want two different clocks.

The lifecycle clock names exactly that split as a Core-owned Port with one
portable adapter, so Core reads time through an injected boundary and tests script
it.

## The two domains — never interchange them

`LifecycleClockPort` has exactly two zero-argument readings:

- **`wall_seconds() -> float`** — system wall-clock seconds. Use it for anything
  **persisted or compared across processes**: the heartbeat value, state/progress
  timestamps, the deferred-notification and event-journal timestamps, and the
  status heartbeat/progress/active-turn ages. Wall time may jump forward or
  backward (NTP, manual clock changes); it carries **no monotonicity promise**.
- **`monotonic_seconds() -> float`** — process-local monotonic seconds from an
  arbitrary epoch. Use it **only** for elapsed intervals within one process:
  uptime, the hidden IDLE→ASLEEP idle timeout, the AED STUCK countdown, and the
  snapshot/GC pacing. Only differences between two reads of the **same** clock are
  meaningful. **Never persist a monotonic value and never compare it across
  processes.**

The single most important rule: **a value's domain is fixed.** A persisted /
cross-process timestamp or a status age is always wall; a process-local elapsed
interval is always monotonic. Moving a value from one domain to the other is a
behavior change, not a refactor.

## How to use it

### In Core (`BaseAgent`)

`BaseAgent.__init__` takes a **required** `lifecycle_clock: LifecycleClockPort`
and stores it as `self._lifecycle_clock` before its first time sample. There is
**no default, nullable, or no-op clock** — omitting it fails loudly at
construction. Read time with `self._lifecycle_clock.wall_seconds()` or
`self._lifecycle_clock.monotonic_seconds()` and pick the domain from the rule
above. Core never imports or constructs the concrete adapter.

Preserve the existing sampling structure when you touch these paths:

- The constructor and each state transition take **one** wall sample shared across
  the fields that read it (`_state_changed_at` and `_last_progress_at`, plus the
  ACTIVE active-turn start).
- The heartbeat loop takes **one** shared monotonic tick for both the snapshot and
  GC interval checks.
- The strict (`>`) and inclusive (`>=`) threshold comparisons are unchanged.

### At the composition roots

The one production adapter is `SystemLifecycleClockAdapter`
(`src/lingtai/adapters/lifecycle_clock.py`), which forwards `wall_seconds()` to
`time.time()` and `monotonic_seconds()` to `time.monotonic()` with no caching or
policy. Wire it at the outer edge:

- **`lingtai.Agent`** constructs `SystemLifecycleClockAdapter()` when no
  `lifecycle_clock` was injected (it needs no `working_dir`); an explicitly
  injected clock wins.
- **`lingtai.cli.build_agent`** constructs and injects the same adapter explicitly.

The adapter is portable, not POSIX — it has no filesystem, `fcntl`, or
platform-selection behavior — so it lives at the top of `lingtai.adapters`, not
under `adapters/posix`, and is not registered through any adapter registry.

### In tests

Use the shared `tests/_lifecycle_clock_helpers.py`:

- `make_test_lifecycle_clock()` returns a fresh `FakeLifecycleClock` for the
  common construction case — every real direct `BaseAgent(...)` call passes one.
- `FakeLifecycleClock` holds independent `wall` and `monotonic` scalars with
  `set_wall`/`set_monotonic`/`advance_wall`/`advance_monotonic`/`advance` controls
  and `wall_reads`/`monotonic_reads` counters. Because the two sources move only
  when a test advances them, you can prove that a wall jump does not disturb a
  monotonic elapsed interval (and vice versa), and assert which domain a code path
  consumed. The fake never reads the host clock, never sleeps, and touches no
  filesystem.

To drive lifecycle/status timing deterministically, inject a scripted fake instead
of monkeypatching a module-global `time`.

## What the clock is NOT

The Port owns exactly two readings and nothing else. Do **not** add a third
operation or a wait/sleep/deadline/scheduler/timer method, and do **not** route
these through it:

- the heartbeat cadence primitive `agent._heartbeat_stop.wait(1.0)` (it stays a
  dedicated interruptible `threading.Event`);
- notification / system-event identity clocks (the synthetic `call_id` /
  `event_id`);
- the `Agent` CPR deadline / poll / `time.sleep` clocks (process supervision,
  adjacent to S8);
- the refresh-watcher subprocess clocks embedded in its generated source string;
- retention, nudge, Task Card, `time_veil`/datetime display, and LLM/session/tool
  clocks.

These keep their existing mechanisms even inside a file the migration otherwise
touches. Keeping them out of the Port is what keeps it a lifecycle *clock* rather
than a universal runtime timer framework.
