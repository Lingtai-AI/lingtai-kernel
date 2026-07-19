"""Outer platform selector for the RefreshWatcher capability.

Composition roots call this selector instead of constructing a platform
handoff adapter directly.  Two production adapters exist — the POSIX
session-detached adapter and the Windows detached-process adapter — and each
composes its own platform entrypoint and watcher-local process mechanism.
Unsupported platforms fail before importing either rather than receiving a
no-op watcher or a misleading default.
"""
from __future__ import annotations

import os
import sys

from lingtai.kernel.refresh_watcher import RefreshWatcherPort


def select_refresh_watcher() -> RefreshWatcherPort:
    """Return the production refresh-watcher Port for this platform.

    The selector is the capability-level registration point for platform
    implementations and owns the fail-loud unsupported-platform behavior.
    """
    if sys.platform == "win32":
        from lingtai.adapters.windows.refresh_watcher import WindowsRefreshWatcherAdapter

        return WindowsRefreshWatcherAdapter()
    if os.name != "posix":
        raise NotImplementedError(
            f"No production refresh-watcher adapter for platform {sys.platform!r} "
            f"(os.name={os.name!r}). Production adapters exist for POSIX and "
            "Windows only; any other platform needs its own adapter with its "
            "own conformance evidence."
        )
    from lingtai.adapters.posix.refresh_watcher import PosixRefreshWatcherAdapter

    return PosixRefreshWatcherAdapter()


__all__ = ["select_refresh_watcher"]
