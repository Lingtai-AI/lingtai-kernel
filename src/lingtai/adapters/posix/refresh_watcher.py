"""POSIX detached-process refresh-watcher adapter.

``PosixRefreshWatcherAdapter`` implements the Core-owned
``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` by launching the
watcher script as a new interpreter subprocess, detached into its own POSIX
session so it survives the caller's exit. It is the only production adapter;
Core never constructs it.

The concrete interpreter path (``sys.executable``), the ``-c`` invocation
mode, ``stdin``/``stdout``/``stderr`` detachment to ``DEVNULL``, and
``start_new_session=True`` (POSIX process-group detachment, not available on
Windows) all live here — they are the concrete process mechanism, not part
of the technology-neutral Port.
"""
from __future__ import annotations

import subprocess
import sys
from typing import Mapping

from lingtai.kernel.refresh_watcher import RefreshWatcherPort


class PosixRefreshWatcherAdapter(RefreshWatcherPort):
    """Spawn the watcher script as a detached POSIX subprocess.

    The launched process runs the current interpreter (``sys.executable -c
    script``) with the given ``env`` as its full environment, all three
    standard streams sent to ``DEVNULL``, and ``start_new_session=True`` so
    it is not a child of the caller's process group. The call returns
    immediately after ``Popen`` starts the process; it does not wait for or
    track the child.
    """

    def spawn_detached(self, script: str, *, env: Mapping[str, str]) -> None:
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=dict(env),
        )


__all__ = ["PosixRefreshWatcherAdapter"]
