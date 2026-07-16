"""POSIX production adapter for the avatar launcher Port."""
from __future__ import annotations

import subprocess
from typing import Any

from lingtai.tools.avatar._launcher import AvatarLaunchReceipt, AvatarLaunchRequest


class PosixAvatarLauncherAdapter:
    def launch(self, request: AvatarLaunchRequest) -> AvatarLaunchReceipt:
        request.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_fh = request.stderr_path.open("wb")
        try:
            process = subprocess.Popen(
                list(request.argv), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=stderr_fh, start_new_session=True,
            )
        finally:
            stderr_fh.close()
        return AvatarLaunchReceipt(process.pid, process)

    @staticmethod
    def poll(handle: Any) -> int | None:
        return handle.poll()

    @staticmethod
    def terminate(handle: Any) -> None:
        handle.terminate()

    @staticmethod
    def force_terminate(handle: Any) -> None:
        handle.kill()

    @staticmethod
    def release(handle: Any) -> None:
        try:
            handle.poll()
        except (OSError, ValueError):
            # Releasing the observation handle must not change spawn policy.
            pass
