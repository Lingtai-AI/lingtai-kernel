"""Windows production adapter for the avatar launcher Port.

``WindowsAvatarLauncherAdapter`` is the Windows sibling of
``PosixAvatarLauncherAdapter``: it disconnects stdin/stdout, owns a
binary-write stderr file closed in the parent after launch, and returns a PID
plus the opaque ``Popen`` handle. Detachment uses
``creationflags=_win32.DETACHED_CREATIONFLAGS`` (new process group + no window)
and ``close_fds=True`` instead of the POSIX ``start_new_session=True``.

Honest termination tier (owner decision U7): on Windows both
``subprocess.Popen.terminate()`` and ``.kill()`` call ``TerminateProcess`` —
there is no graceful signal to send. This adapter therefore does **not** pretend
a graceful tier exists: ``terminate`` and ``force_terminate`` are both forceful,
immediate termination of exactly the owned process (never a process tree).
"""
from __future__ import annotations

import subprocess
from typing import Any

from lingtai.adapters.windows import _win32
from lingtai.tools.avatar._launcher import AvatarLaunchReceipt, AvatarLaunchRequest


class WindowsAvatarLauncherAdapter:
    def launch(self, request: AvatarLaunchRequest) -> AvatarLaunchReceipt:
        request.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_fh = request.stderr_path.open("wb")
        try:
            process = subprocess.Popen(
                list(request.argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=stderr_fh, creationflags=_win32.DETACHED_CREATIONFLAGS,
                close_fds=True,
            )
        finally:
            stderr_fh.close()
        return AvatarLaunchReceipt(process.pid, process)

    @staticmethod
    def poll(handle: Any) -> int | None:
        return handle.poll()

    @staticmethod
    def terminate(handle: Any) -> None:
        """Forceful immediate termination. On Windows this maps to
        ``TerminateProcess`` — identical to ``force_terminate``; there is no
        graceful tier to offer."""
        handle.terminate()

    @staticmethod
    def force_terminate(handle: Any) -> None:
        """Forceful immediate termination of exactly the owned process, never a
        tree kill. On Windows ``Popen.kill`` and ``Popen.terminate`` both call
        ``TerminateProcess``."""
        handle.kill()

    @staticmethod
    def release(handle: Any) -> None:
        try:
            handle.poll()
        except (OSError, ValueError):
            # Releasing the observation handle must not change spawn policy.
            pass


__all__ = ["WindowsAvatarLauncherAdapter"]
