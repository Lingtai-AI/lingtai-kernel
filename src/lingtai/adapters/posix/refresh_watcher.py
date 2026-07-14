"""POSIX detached-process refresh-watcher adapter.

``PosixRefreshWatcherAdapter`` implements the Core-owned
``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` by rendering the
Core-owned watcher program source from a ``RefreshWatcherRequest`` and
launching it as a new interpreter subprocess, detached into its own POSIX
session so it survives the caller's exit. It is the only production adapter;
Core never constructs it.

The concrete interpreter path (``sys.executable``), the ``-c`` invocation
mode, ``stdin``/``stdout``/``stderr`` detachment to ``DEVNULL``,
``start_new_session=True`` (POSIX process-group detachment, not available on
Windows), and full-environment construction (``os.environ`` capture plus the
``env_overwrite`` policy bit from the request, translated to the concrete
``ENV_OVERWRITE_VAR`` environment-variable name) all live here — they are the
concrete process mechanism, not part of the technology-neutral Port. Core's
``watcher_program`` module never defines or imports ``ENV_OVERWRITE_VAR``; it
knows only the boolean ``request.env_overwrite`` policy bit.
"""
from __future__ import annotations

import os
import subprocess
import sys

from lingtai.kernel.refresh_watcher import RefreshWatcherPort, RefreshWatcherRequest
from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script

ENV_OVERWRITE_VAR = "LINGTAI_REFRESH_ENV_OVERWRITE"


def build_watcher_env(request: RefreshWatcherRequest) -> dict[str, str]:
    """Build the watcher process's full environment from ``request`` policy.

    Base environment inheritance (``os.environ``) and the concrete env-var
    name used to signal env-file overwrite are POSIX process mechanism, so
    they live in this adapter rather than in Core. The parent process's own
    ``os.environ`` is only read here (via ``dict(os.environ)``), never
    mutated — this function returns a fresh copy for the watcher process.

    ``request.env_overwrite`` is authoritative in both directions: when
    ``True`` the marker is set to ``"1"``; when ``False`` the marker is
    explicitly removed from the copied environment, even if the *parent*
    process happens to already have ``LINGTAI_REFRESH_ENV_OVERWRITE`` set
    (e.g. because this process was itself launched as a prior watcher's
    relaunch target). Without the explicit removal, a `False` request would
    silently inherit a stale `True` from the parent's environment — `False`
    would not actually mean `False`.
    """
    env = dict(os.environ)
    if request.env_overwrite:
        env[ENV_OVERWRITE_VAR] = "1"
    else:
        env.pop(ENV_OVERWRITE_VAR, None)
    return env


class PosixRefreshWatcherAdapter(RefreshWatcherPort):
    """Render and spawn the watcher program as a detached POSIX subprocess.

    The launched process runs the current interpreter (``sys.executable -c
    script``) with ``build_watcher_env(request)`` as its full environment,
    all three standard streams sent to ``DEVNULL``, and
    ``start_new_session=True`` so it is not a child of the caller's process
    group. The call returns immediately after ``Popen`` starts the process;
    it does not wait for or track the child.
    """

    def spawn_detached(self, request: RefreshWatcherRequest) -> None:
        script = render_watcher_script(request)
        env = build_watcher_env(request)
        subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )


__all__ = ["PosixRefreshWatcherAdapter", "build_watcher_env"]
