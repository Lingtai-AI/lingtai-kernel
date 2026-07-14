"""Shared test helpers for the lifecycle-clock Port.

Provides a deterministic in-memory ``FakeLifecycleClock`` that implements the
Core-owned ``lingtai.kernel.lifecycle_clock.LifecycleClockPort`` with wall and
monotonic values that are controlled independently, plus the
``make_test_lifecycle_clock()`` factory the ~220 raw ``BaseAgent(...)``
construction tests use to inject a real (but host-clock-free) clock.

The fake never reads the host clock, never sleeps, and touches no filesystem. It
holds two independent scalars so a test can advance wall time without moving
monotonic time (and vice versa) to prove the two domains are separate, and it
counts reads of each source so tests can assert which domain a code path
consumes.

For the Contract's shared-sample seams (a constructor/state-transition wall
sample reused across several fields, and the one heartbeat-tick monotonic sample
shared by the snapshot and GC checks), ``ScriptedLifecycleClock`` returns a
different value on every read; equal fields therefore prove a single shared read.
"""
from __future__ import annotations

from lingtai.kernel.lifecycle_clock import LifecycleClockPort


class FakeLifecycleClock(LifecycleClockPort):
    """In-memory lifecycle clock with independent wall and monotonic values.

    No host clock, sleeps, or filesystem — pure Python — so it proves the Port
    is substitutable and that consumers depend only on the two readings. The two
    sources move only when a test explicitly sets or advances them, so ordering
    and elapsed-interval behavior are fully deterministic.
    """

    def __init__(self, *, wall: float = 1_000.0, monotonic: float = 0.0) -> None:
        self._wall = float(wall)
        self._monotonic = float(monotonic)
        #: Number of times ``wall_seconds`` / ``monotonic_seconds`` were read.
        self.wall_reads = 0
        self.monotonic_reads = 0

    def wall_seconds(self) -> float:
        self.wall_reads += 1
        return self._wall

    def monotonic_seconds(self) -> float:
        self.monotonic_reads += 1
        return self._monotonic

    # -- deterministic test controls (not part of the Port) -----------------

    def set_wall(self, value: float) -> None:
        """Set the absolute wall-clock value (may move forward or backward)."""
        self._wall = float(value)

    def set_monotonic(self, value: float) -> None:
        """Set the absolute monotonic value."""
        self._monotonic = float(value)

    def advance_wall(self, delta: float) -> None:
        """Advance (or, with a negative delta, rewind) wall time only."""
        self._wall += float(delta)

    def advance_monotonic(self, delta: float) -> None:
        """Advance monotonic time only. Monotonic never rewinds in practice."""
        self._monotonic += float(delta)

    def advance(self, delta: float) -> None:
        """Advance both sources by the same delta (steady-time convenience)."""
        self._wall += float(delta)
        self._monotonic += float(delta)


class ScriptedLifecycleClock(LifecycleClockPort):
    """Lifecycle clock whose readings change on every read.

    Each ``wall_seconds()`` / ``monotonic_seconds()`` call returns the current
    value and then advances that source by its fixed step, so consecutive reads
    of the same source always differ. Because no two reads return the same value,
    a consumer field that reads the clock only once carries a *unique* value: if
    two fields end up equal, they were seeded from a single shared read. This is
    the direct proof used for the Contract's shared-sample seams, and it also
    counts reads per source so a test can assert exactly how many samples a path
    takes.
    """

    def __init__(
        self,
        *,
        wall_start: float = 1_000.0,
        wall_step: float = 1.0,
        monotonic_start: float = 0.0,
        monotonic_step: float = 1.0,
    ) -> None:
        self._wall = float(wall_start)
        self._wall_step = float(wall_step)
        self._monotonic = float(monotonic_start)
        self._monotonic_step = float(monotonic_step)
        self.wall_reads = 0
        self.monotonic_reads = 0

    def wall_seconds(self) -> float:
        value = self._wall
        self._wall += self._wall_step
        self.wall_reads += 1
        return value

    def monotonic_seconds(self) -> float:
        value = self._monotonic
        self._monotonic += self._monotonic_step
        self.monotonic_reads += 1
        return value


def make_test_lifecycle_clock(
    *, wall: float = 1_000.0, monotonic: float = 0.0
) -> FakeLifecycleClock:
    """Return a fresh deterministic lifecycle clock for injecting into ``BaseAgent``.

    Defaults give a plausible non-zero wall time and a zero monotonic epoch; the
    common construction-test case ignores the values entirely and only needs a
    real Port instance.
    """
    return FakeLifecycleClock(wall=wall, monotonic=monotonic)
