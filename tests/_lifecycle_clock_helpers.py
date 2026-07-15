"""Deterministic fakes for the lifecycle-clock Port."""
from __future__ import annotations

from lingtai.kernel.lifecycle_clock import LifecycleClockPort


class FakeLifecycleClock(LifecycleClockPort):
    """Independent, host-clock-free wall and monotonic readings."""

    def __init__(self, *, wall: float = 1_000.0, monotonic: float = 0.0) -> None:
        self._wall = float(wall)
        self._monotonic = float(monotonic)
        self.wall_reads = 0
        self.monotonic_reads = 0

    def wall_seconds(self) -> float:
        self.wall_reads += 1
        return self._wall

    def monotonic_seconds(self) -> float:
        self.monotonic_reads += 1
        return self._monotonic

    def set_wall(self, value: float) -> None:
        self._wall = float(value)

    def set_monotonic(self, value: float) -> None:
        self._monotonic = float(value)

    def advance_wall(self, delta: float) -> None:
        self._wall += float(delta)

    def advance_monotonic(self, delta: float) -> None:
        self._monotonic += float(delta)

    def advance(self, delta: float) -> None:
        self._wall += float(delta)
        self._monotonic += float(delta)


class ScriptedLifecycleClock(LifecycleClockPort):
    """Return a changing value on every read and count both domains."""

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
    return FakeLifecycleClock(wall=wall, monotonic=monotonic)
