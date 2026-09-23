"""POSIX production adapter for the avatar launcher Port."""
from __future__ import annotations

import os
import subprocess
from typing import Any

from lingtai.tools.avatar._launcher import AvatarLaunchReceipt, AvatarLaunchRequest


class PosixAvatarLauncherAdapter:
    def launch(self, request: AvatarLaunchRequest) -> AvatarLaunchReceipt:
        request.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_fh = request.stderr_path.open("wb")
        authority_fd = None
        try:
            kwargs = {
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": stderr_fh,
                "start_new_session": True,
                "close_fds": True,
            }
            environment = dict(request.environment or {})
            # A resident ACP endpoint is an opt-in for the parent Agent only.
            # Never pass its refresh marker to an independently launched Avatar.
            acp_marker = "LINGTAI_ACP_SOCKET_AGENT_DIR"
            has_acp_marker = acp_marker in os.environ or acp_marker in environment
            if request.authority_lease is not None:
                from lingtai.adapters.acp.driver_authority import (
                    DRIVER_AUTHORITY_FD_ENV,
                    consume_posix_child_endpoint_lease,
                )

                authority_fd = consume_posix_child_endpoint_lease(request.authority_lease)
                environment[DRIVER_AUTHORITY_FD_ENV] = str(authority_fd)
                kwargs["pass_fds"] = (authority_fd,)
            if environment or has_acp_marker:
                child_environment = {**os.environ, **environment}
                child_environment.pop(acp_marker, None)
                kwargs["env"] = child_environment
            process = subprocess.Popen(list(request.argv), **kwargs)
        finally:
            if authority_fd is not None:
                try:
                    os.close(authority_fd)
                except OSError:
                    pass
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
