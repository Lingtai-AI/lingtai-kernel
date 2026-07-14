"""POSIX detached-process refresh-watcher adapter.

``PosixRefreshWatcherAdapter`` implements the Core-owned
``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` by encoding a
``RefreshWatcherRequest`` to its compact deterministic JSON wire form
(``lingtai.kernel.refresh_watcher.encode_request``) and launching a new
interpreter subprocess against the owned entrypoint module
``lingtai.adapters.posix.refresh_watcher_entrypoint``, detached into its own
POSIX session so it survives the caller's exit. It is the only production
adapter; Core never constructs it.

The concrete interpreter path (``sys.executable``), the ``-m`` module
invocation (rather than putting the ~480-line generated watcher-program
source directly on argv via ``-c``), ``stdin``/``stdout``/``stderr``
detachment to ``DEVNULL``, ``start_new_session=True`` (POSIX process-group
detachment, not available on Windows), and full-environment construction
(``os.environ`` capture plus the ``env_overwrite`` policy bit from the
request, translated to the concrete ``ENV_OVERWRITE_VAR``
environment-variable name) all live here — they are the concrete process
mechanism, not part of the technology-neutral Port. Core's
``watcher_program`` module never defines or imports ``ENV_OVERWRITE_VAR``; it
knows only the boolean ``request.env_overwrite`` policy bit. The entrypoint
module (not this adapter) decodes the request back and renders the actual
watcher-program text via ``watcher_program.render_watcher_script`` — this
adapter never renders or inspects the program text itself, only the request
encoding and the subprocess launch.
"""
from __future__ import annotations

import os
import subprocess
import sys

from lingtai.kernel.refresh_watcher import RefreshWatcherPort, RefreshWatcherRequest, encode_request

ENV_OVERWRITE_VAR = "LINGTAI_REFRESH_ENV_OVERWRITE"

ENTRYPOINT_MODULE = "lingtai.adapters.posix.refresh_watcher_entrypoint"


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
    """Encode the request and spawn the watcher as a detached POSIX subprocess.

    The launched process runs the current interpreter against the owned
    entrypoint module (``sys.executable -m
    lingtai.adapters.posix.refresh_watcher_entrypoint <encoded-request>``)
    with ``build_watcher_env(request)`` as its full environment, all three
    standard streams sent to ``DEVNULL``, and ``start_new_session=True`` so
    it is not a child of the caller's process group. The call returns
    immediately after ``Popen`` starts the process; it does not wait for or
    track the child.
    """

    def spawn_detached(self, request: RefreshWatcherRequest) -> None:
        payload = encode_request(request)
        env = build_watcher_env(request)
        subprocess.Popen(
            [sys.executable, "-m", ENTRYPOINT_MODULE, payload],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )


__all__ = ["PosixRefreshWatcherAdapter", "build_watcher_env", "ENTRYPOINT_MODULE"]
