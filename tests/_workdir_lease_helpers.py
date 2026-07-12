"""Shared test helpers for the working-directory lease Port.

Provides a deterministic in-memory ``FakeWorkdirLease`` that implements the
Core-owned ``lingtai.kernel.workdir_lease.WorkdirLeasePort`` without any
filesystem, plus small factories the ~230 raw ``BaseAgent(...)`` construction
tests use to inject a real (but directory-free) lease.

The fake models the same observable contract as the production POSIX adapter:
one holder per logical "directory" key, ``acquire(0)`` raises ``RuntimeError`` on
contention, a positive timeout polls until a monotonic deadline, and ``release``
is idempotent. It shares a registry so two fakes on the same key contend exactly
like two adapters on the same working directory.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from lingtai.kernel.workdir_lease import WorkdirLeasePort

# Process-wide registry of currently-held logical directory keys. Guarded by a
# lock so concurrent test threads observe a consistent holder set.
_HELD: set[str] = set()
_REGISTRY_LOCK = threading.Lock()


class FakeWorkdirLease(WorkdirLeasePort):
    """In-memory exclusive lease keyed by an opaque directory string.

    No path, file descriptor, or OS lock — pure Python — so it proves the Port
    is substitutable and that consumers depend only on ``acquire``/``release``.
    """

    def __init__(self, key: str | Path | None = None) -> None:
        # An explicit ``key`` opts into shared-registry exclusion so two fakes on
        # the same key contend (used by the contention conformance tests). A
        # ``None`` key is UNSHARED: each instance is its own uncontended lease —
        # the common construction-test case — so an agent that is never stopped
        # (lease never released) can never falsely block a later fake, and there
        # is no cross-test registry leak.
        self._key = str(key) if key is not None else None
        self._held = False

    def acquire(self, timeout_seconds: float = 0) -> None:
        if self._key is None:
            self._held = True  # unshared: always available
            return
        deadline = time.monotonic() + timeout_seconds
        while True:
            with _REGISTRY_LOCK:
                if self._key not in _HELD:
                    _HELD.add(self._key)
                    self._held = True
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Working directory '{self._key}' is already in use "
                    f"by another agent. Each agent needs its own directory."
                )
            time.sleep(0.01)

    def release(self) -> None:
        if self._held:
            if self._key is not None:
                with _REGISTRY_LOCK:
                    _HELD.discard(self._key)
            self._held = False


def make_test_lease(key: str | Path | None = None) -> FakeWorkdirLease:
    """Return a fresh deterministic lease for injecting into ``BaseAgent``.

    Pass a stable ``key`` (e.g. the working dir) when a test needs two agents to
    contend; omit it for the common single-agent construction case.
    """
    return FakeWorkdirLease(key)


class RecordingWorkdirLease(WorkdirLeasePort):
    """A Port that records each ``acquire`` timeout and counts ``release`` calls.

    Behaviorally pins the acquire/release contract of consumers (``BaseAgent``
    construction, the SQLite rebuild) — the ``acquire`` argument and the exact
    ``release`` count, including on failure paths — without searching source text.
    """

    def __init__(self) -> None:
        self.acquires: list[float] = []
        self.releases: int = 0
        self.held: bool = False

    def acquire(self, timeout_seconds: float = 0) -> None:
        self.acquires.append(timeout_seconds)
        self.held = True

    def release(self) -> None:
        self.releases += 1
        self.held = False
