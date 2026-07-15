"""Deterministic fakes for the workdir-lease Port."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from lingtai.kernel.workdir_lease import WorkdirLeasePort

_HELD: set[str] = set()
_REGISTRY_LOCK = threading.Lock()


class FakeWorkdirLease(WorkdirLeasePort):
    """Process-local exclusive lease keyed by an optional logical directory."""

    def __init__(self, key: str | Path | None = None) -> None:
        self._key = str(key) if key is not None else None
        self._held = False

    def acquire(self, timeout_seconds: float = 0) -> None:
        if self._key is None:
            self._held = True
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
                    "by another agent. Each agent needs its own directory."
                )
            time.sleep(0.01)

    def release(self) -> None:
        if self._held:
            if self._key is not None:
                with _REGISTRY_LOCK:
                    _HELD.discard(self._key)
            self._held = False


def make_test_lease(key: str | Path | None = None) -> FakeWorkdirLease:
    return FakeWorkdirLease(key)


class RecordingWorkdirLease(WorkdirLeasePort):
    """Record consumer acquire timeouts and release calls."""

    def __init__(self) -> None:
        self.acquires: list[float] = []
        self.releases = 0
        self.held = False

    def acquire(self, timeout_seconds: float = 0) -> None:
        self.acquires.append(timeout_seconds)
        self.held = True

    def release(self) -> None:
        self.releases += 1
        self.held = False
