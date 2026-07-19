"""Outer platform selector for the working-directory lease adapter.

This is composition-root wiring: it reads the running platform, selects the
concrete ``WorkdirLeasePort`` adapter, and constructs it. It is deliberately the
only place that branches on the operating system for leasing. Core never imports
this module — the composition roots (``lingtai.agent``, ``lingtai.cli``) call
``select_workdir_lease`` and inject the returned Port into ``BaseAgent`` and the
SQLite rebuild.

Two production adapters exist: the POSIX ``flock`` adapter and the Windows
``msvcrt`` byte-range adapter. On any other platform the selector fails loudly
rather than silently degrading or shipping an unproven mechanism.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from lingtai.kernel.workdir_lease import WorkdirLeasePort


def select_workdir_lease(working_dir: str | Path) -> WorkdirLeasePort:
    """Return the production working-directory lease for the current platform.

    On ``win32`` returns the Windows ``msvcrt`` byte-range adapter; on POSIX
    returns the ``flock`` adapter. Raises ``NotImplementedError`` on every
    platform without a production lease adapter. Each rejection happens
    *before* the concrete adapter module (which needs ``fcntl`` or ``msvcrt``
    mechanisms) is exercised, so an unsupported-platform failure is this
    selector's explicit error rather than a bare mechanism import error. The
    failure is loud and explicit: there is no unlocked or no-op fallback.
    """
    if sys.platform == "win32":
        from lingtai.adapters.windows.workdir_lease import WindowsWorkdirLeaseAdapter

        return WindowsWorkdirLeaseAdapter(working_dir)
    if os.name != "posix":
        raise NotImplementedError(
            f"No production workdir-lease adapter for platform {sys.platform!r} "
            f"(os.name={os.name!r}). Production adapters exist for POSIX "
            "(flock) and Windows (msvcrt byte-range) only; any other platform "
            "needs its own adapter with its own lock conformance suite."
        )
    from lingtai.adapters.posix.workdir_lease import PosixWorkdirLeaseAdapter

    return PosixWorkdirLeaseAdapter(working_dir)


__all__ = ["select_workdir_lease"]
