"""Shared test helpers for the refresh-watcher Port.

Provides a deterministic in-memory ``FakeRefreshWatcher`` that implements the
Core-owned ``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` without
spawning any real process, plus the ``make_test_refresh_watcher()`` factory
raw ``BaseAgent(...)`` construction tests use to inject a real (but
process-free) watcher.

The fake records every ``spawn_detached`` call (script, env) so a test can
assert the generated relaunch script and environment without patching
``subprocess.Popen`` directly.
"""
from __future__ import annotations

from typing import Mapping

from lingtai.kernel.refresh_watcher import RefreshWatcherPort


class FakeRefreshWatcher(RefreshWatcherPort):
    """In-memory refresh watcher that records spawn calls instead of launching."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def spawn_detached(self, script: str, *, env: Mapping[str, str]) -> None:
        self.calls.append((script, dict(env)))

    @property
    def spawned(self) -> bool:
        return bool(self.calls)

    @property
    def last_script(self) -> str | None:
        return self.calls[-1][0] if self.calls else None

    @property
    def last_env(self) -> dict | None:
        return self.calls[-1][1] if self.calls else None


def make_test_refresh_watcher() -> FakeRefreshWatcher:
    """Return a fresh deterministic refresh watcher for injecting into ``BaseAgent``."""
    return FakeRefreshWatcher()
