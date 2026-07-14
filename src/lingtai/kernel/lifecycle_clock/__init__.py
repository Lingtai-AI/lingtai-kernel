"""Core-owned outbound Port for the agent lifecycle clock.

This boundary lets Core read the two time sources its lifecycle policy depends
on — wall-clock seconds for persisted or cross-process timestamps and ages, and
process-local monotonic seconds for elapsed intervals — without knowing the
concrete time mechanism. It exposes only the observable ``wall_seconds`` /
``monotonic_seconds`` readings that ``BaseAgent`` construction, state/progress
bookkeeping, heartbeat publication, status ages, uptime, idle timeout, AED, and
snapshot/GC pacing depend on. The concrete clock lives entirely in an outside
adapter that Core never imports or names; this module deliberately carries no
``time``, datetime, filesystem, POSIX, thread, or scheduler vocabulary and no
wait/sleep/deadline operation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LifecycleClockPort(ABC):
    """Two-source lifecycle-time boundary owned by Core.

    An adapter translates a concrete time mechanism into these two
    technology-neutral readings. Core receives an instance and never
    constructs, imports, or names a concrete adapter, and never sees the
    mechanism's identifiers through this Port.

    The two sources are distinct domains and must not be interchanged:

    - ``wall_seconds()`` is system wall-clock time. It is suitable for
      persisted or cross-process comparison, may jump forward or backward, and
      promises no monotonicity.
    - ``monotonic_seconds()`` is process-local monotonic time from an arbitrary
      epoch. Only differences between values from the same runtime clock are
      meaningful; it must never be persisted or compared across processes.

    There is no disabled/no-op/default clock — a consumer that receives a
    lifecycle clock receives a real time source. The Port owns exactly these two
    zero-argument readings and adds no wait, sleep, deadline, scheduler, or timer
    operation.
    """

    @abstractmethod
    def wall_seconds(self) -> float:
        """Return system wall-clock seconds.

        Suitable for persisted or cross-process comparison. The value may jump
        forward or backward and carries no monotonicity promise. Callers that
        need a stable elapsed interval use ``monotonic_seconds`` instead.
        """
        ...

    @abstractmethod
    def monotonic_seconds(self) -> float:
        """Return process-local monotonic seconds from an arbitrary epoch.

        Only differences between values read from the same runtime clock are
        meaningful. The value MUST NOT be persisted or compared across
        processes.
        """
        ...


__all__ = ["LifecycleClockPort"]
