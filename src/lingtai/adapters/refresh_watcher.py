"""Outer platform selector for the RefreshWatcher capability.

Composition roots call this selector instead of constructing the POSIX handoff
adapter directly.  This slice has one real implementation; unsupported
platforms fail before importing it rather than receiving a no-op watcher or a
misleading default.
"""
from __future__ import annotations

import os
import sys

from lingtai.kernel.refresh_watcher import RefreshWatcherPort


def select_refresh_watcher() -> RefreshWatcherPort:
    """Return the production refresh-watcher Port for this platform.

    The current vertical slice intentionally ships only the POSIX adapter.  The
    selector is the capability-level registration point for a future genuine
    platform implementation and owns the fail-loud unsupported-platform
    behavior.
    """
    if sys.platform == "win32":
        from lingtai.adapters.windows.refresh_watcher import WindowsRefreshWatcherAdapter

        return WindowsRefreshWatcherAdapter()
    if os.name != "posix":
        raise NotImplementedError(
            f"No production refresh-watcher adapter for platform {sys.platform!r} "
            f"(os.name={os.name!r}). This vertical slice ships POSIX and Windows "
            "adapters; other non-POSIX platforms are out of scope."
        )
    from lingtai.adapters.posix.refresh_watcher import PosixRefreshWatcherAdapter

    return PosixRefreshWatcherAdapter()


__all__ = ["select_refresh_watcher"]
