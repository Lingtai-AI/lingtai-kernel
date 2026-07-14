"""Portable system lifecycle-clock adapter.

``SystemLifecycleClockAdapter`` implements the Core-owned
``lingtai.kernel.lifecycle_clock.LifecycleClockPort`` by delegating directly to
Python's standard wall and monotonic clocks. It is the sole production adapter
for that Port.

This adapter is portable, not POSIX-specific: ``time.time()`` and
``time.monotonic()`` are the concrete mechanism, but there is no filesystem,
``fcntl``, symlink, or platform-selection behavior here — so it lives at the
top of the ``lingtai.adapters`` package rather than under
``lingtai.adapters.posix``. Its structure is mapped by ``src/lingtai/ANATOMY.md``
and owned by the lifecycle-clock governed twins
(``src/lingtai/kernel/lifecycle_clock/CONTRACT.md`` and its paired ``ANATOMY.md``);
it has no dedicated anatomy of its own.
"""
from __future__ import annotations

import time

from lingtai.kernel.lifecycle_clock import LifecycleClockPort


class SystemLifecycleClockAdapter(LifecycleClockPort):
    """Delegate the two lifecycle-clock readings to Python's system clocks.

    Each call forwards straight to the underlying source and returns the raw
    ``float`` seconds unchanged: no caching, clamping, rounding, UTC formatting,
    exception translation, or policy. Wall and monotonic sources are read
    independently.
    """

    def wall_seconds(self) -> float:
        return time.time()

    def monotonic_seconds(self) -> float:
        return time.monotonic()


__all__ = ["SystemLifecycleClockAdapter"]
