"""Windows detached-process refresh-watcher adapter.

``WindowsRefreshWatcherAdapter`` implements the Core-owned
``lingtai.kernel.refresh_watcher.RefreshWatcherPort`` by encoding a
``RefreshWatcherRequest`` to its compact deterministic JSON wire form and
launching a new interpreter subprocess against the owned Windows entrypoint
module ``lingtai.adapters.windows.refresh_watcher_entrypoint``, detached with
the shared Windows creation flags so it survives the caller's exit and shares
no console-control fate with it. It is the Windows sibling of
``PosixRefreshWatcherAdapter``; Core never constructs either.

The concrete interpreter path (``sys.executable``), the ``-m`` module
invocation, ``DEVNULL`` stream detachment, the
``CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` creation flags, and the full
watcher environment all live here. The environment policy —
``os.environ`` capture plus the ``env_overwrite`` bit translated to the
concrete ``LINGTAI_REFRESH_ENV_OVERWRITE`` variable, authoritative in both
directions — is platform-neutral by construction, so this adapter reuses the
sibling's ``build_watcher_env`` as the single source of that policy instead of
duplicating it (the POSIX adapter module is import-safe on every platform; its
POSIX-ness is confined to the ``spawn_detached`` mechanism).
"""
from __future__ import annotations

import subprocess
import sys

from lingtai.adapters.posix.refresh_watcher import build_watcher_env
from lingtai.adapters.windows._win32 import DETACHED_CREATIONFLAGS
from lingtai.kernel.refresh_watcher import RefreshWatcherPort, RefreshWatcherRequest, encode_request

ENTRYPOINT_MODULE = "lingtai.adapters.windows.refresh_watcher_entrypoint"


class WindowsRefreshWatcherAdapter(RefreshWatcherPort):
    """Encode the request and spawn the watcher as a detached Windows process.

    The launched process runs the current interpreter against the owned
    Windows entrypoint module (``sys.executable -m
    lingtai.adapters.windows.refresh_watcher_entrypoint <encoded-request>``)
    with ``build_watcher_env(request)`` as its full environment, all three
    standard streams sent to ``DEVNULL``, and the shared detached creation
    flags (new process group, no window). The call returns immediately after
    ``Popen`` starts the process; it does not wait for or track the child.
    """

    def spawn_detached(self, request: RefreshWatcherRequest) -> None:
        payload = encode_request(request)
        env = build_watcher_env(request)
        subprocess.Popen(
            [sys.executable, "-m", ENTRYPOINT_MODULE, payload],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_CREATIONFLAGS,
            close_fds=True,
            env=env,
        )


__all__ = ["WindowsRefreshWatcherAdapter", "ENTRYPOINT_MODULE"]
