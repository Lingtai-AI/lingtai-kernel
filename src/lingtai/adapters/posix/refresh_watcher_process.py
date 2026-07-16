"""POSIX process-mechanism adapter for the Core refresh-watcher policy.

The outer ``PosixRefreshWatcherAdapter`` still owns the first detached handoff
that starts the watcher entrypoint.  This adapter owns only the process
operations the already-running watcher policy needs while supervising the
replacement agent: command-line observation, liveness, detached relaunch, and
graceful/forced termination.  It is deliberately watcher-local rather than a
generic process-supervision framework.
"""
from __future__ import annotations

import os
import signal
import subprocess
from typing import Sequence

from lingtai.kernel.refresh_watcher import (
    RefreshWatcherProcessHandle,
    RefreshWatcherProcessObservation,
    RefreshWatcherProcessPort,
)


class PosixRefreshWatcherProcessAdapter(RefreshWatcherProcessPort):
    """Implement the watcher-local process Port with POSIX primitives."""

    @staticmethod
    def _pid(
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> int:
        return process.pid

    def observe(self, pid: int) -> RefreshWatcherProcessObservation | None:
        """Read one candidate command line using the POSIX process table."""
        try:
            command_line = subprocess.check_output(
                ["ps", "-p", str(pid), "-o", "command="],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            return None
        return RefreshWatcherProcessObservation(pid=pid, command_line=command_line)

    def is_alive(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> bool:
        """Use the POSIX existence probe for an observed process identity."""
        try:
            os.kill(self._pid(process), 0)
        except OSError:
            return False
        return True

    def start_agent(
        self,
        cmd: Sequence[str],
        stderr_log: str,
    ) -> RefreshWatcherProcessHandle:
        """Launch a replacement agent in a detached POSIX session."""
        with open(stderr_log, "a") as serr:
            process = subprocess.Popen(
                list(cmd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=serr,
                start_new_session=True,
            )
        return RefreshWatcherProcessHandle(pid=process.pid)

    def graceful_stop(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> None:
        """Request the adapter's graceful POSIX termination operation."""
        os.kill(self._pid(process), signal.SIGTERM)

    def force_stop(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> None:
        """Force-stop a process after the graceful grace period expires."""
        os.kill(self._pid(process), signal.SIGKILL)


__all__ = ["PosixRefreshWatcherProcessAdapter"]
