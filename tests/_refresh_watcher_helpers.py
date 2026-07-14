"""Shared test helpers for the refresh-watcher Port.

Provides a deterministic in-memory ``FakeRefreshWatcher`` that implements the
Core-owned ``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` without
spawning any real process, plus the ``make_test_refresh_watcher()`` factory
raw ``BaseAgent(...)`` construction tests use to inject a real (but
process-free) watcher.

The fake records every ``spawn_detached`` call (the typed
``RefreshWatcherRequest``) and translates it the same way the production
POSIX adapter does — rendering the watcher program source via
``watcher_program.render_watcher_script`` and the process environment via
the POSIX adapter's ``build_watcher_env`` — so existing tests can keep
asserting on ``last_script``/``last_env`` text without patching
``subprocess.Popen`` directly.
"""
from __future__ import annotations

from lingtai.kernel.refresh_watcher import RefreshWatcherPort, RefreshWatcherRequest
from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script


class FakeRefreshWatcher(RefreshWatcherPort):
    """In-memory refresh watcher that records spawn calls instead of launching."""

    def __init__(self) -> None:
        self.calls: list[tuple[RefreshWatcherRequest, str, dict]] = []

    def spawn_detached(self, request: RefreshWatcherRequest) -> None:
        from lingtai.adapters.posix.refresh_watcher import build_watcher_env

        script = render_watcher_script(request)
        env = build_watcher_env(request)
        self.calls.append((request, script, env))

    @property
    def spawned(self) -> bool:
        return bool(self.calls)

    @property
    def last_request(self) -> RefreshWatcherRequest | None:
        return self.calls[-1][0] if self.calls else None

    @property
    def last_script(self) -> str | None:
        return self.calls[-1][1] if self.calls else None

    @property
    def last_env(self) -> dict | None:
        return self.calls[-1][2] if self.calls else None


def make_test_refresh_watcher() -> FakeRefreshWatcher:
    """Return a fresh deterministic refresh watcher for injecting into ``BaseAgent``."""
    return FakeRefreshWatcher()
