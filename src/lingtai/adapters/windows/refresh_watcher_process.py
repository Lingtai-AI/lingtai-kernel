"""Windows process-mechanism adapter for the Core refresh-watcher policy.

The outer ``WindowsRefreshWatcherAdapter`` owns the first detached handoff that
starts the watcher entrypoint. This adapter owns only the process operations
the already-running watcher policy needs while supervising the replacement
agent: command-line observation, liveness, detached relaunch, and
graceful/forced termination. It is deliberately watcher-local rather than a
generic process-supervision framework — the Windows sibling of
``PosixRefreshWatcherProcessAdapter``.

Windows mechanism choices, kept out of the technology-neutral Port:

- **Observation** uses a PowerShell CIM query (``Get-CimInstance
  Win32_Process``) for the full command line; the Win32 process snapshot APIs
  expose the executable name but not argv, and the CIM/WMI surface is the same
  one the LingTai TUI uses for process truth on Windows.
- **Liveness** uses ``OpenProcess``/``GetExitCodeProcess`` via the shared
  ``_win32`` surface. It never uses ``os.kill(pid, 0)`` — on Windows that call
  *terminates* the target instead of probing it.
- **Graceful stop** writes the target working directory's ``.suspend`` file.
  Windows has no deliverable SIGTERM for a detached headless process; the
  kernel's own cooperative stop channel *is* the platform's normal termination
  request for a LingTai agent process — the agent's heartbeat loop consumes
  ``.suspend`` within about a second and shuts down in order. The policy only
  ever asks this adapter to stop processes it has matched as agent runs for
  exactly this working directory, so the adapter is constructed bound to that
  directory by the Windows entrypoint.
- **Forced stop** is ``TerminateProcess`` on the exact PID, never a tree kill.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from lingtai.adapters.windows import _win32
from lingtai.kernel.refresh_watcher import (
    RefreshWatcherProcessHandle,
    RefreshWatcherProcessObservation,
    RefreshWatcherProcessPort,
)


class WindowsRefreshWatcherProcessAdapter(RefreshWatcherProcessPort):
    """Implement the watcher-local process Port with native Windows primitives.

    ``working_dir`` binds the adapter to the one agent working directory this
    watcher supervises; the graceful-stop channel is that directory's
    ``.suspend`` file.
    """

    def __init__(self, working_dir: str | Path) -> None:
        self._working_dir = Path(working_dir)

    @staticmethod
    def _pid(
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> int:
        return process.pid

    @staticmethod
    def _powershell_executable() -> str | None:
        return shutil.which("pwsh") or shutil.which("powershell")

    def observe(self, pid: int) -> RefreshWatcherProcessObservation | None:
        """Read one candidate command line from the CIM process table."""
        shell = self._powershell_executable()
        if shell is None:
            return None
        try:
            command_line = subprocess.check_output(
                [
                    shell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "(Get-CimInstance -ClassName Win32_Process "
                    f"-Filter 'ProcessId = {int(pid)}').CommandLine",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            return None
        if not command_line:
            return None
        return RefreshWatcherProcessObservation(pid=pid, command_line=command_line)

    def is_alive(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> bool:
        """Probe liveness via process-handle queries; never signals."""
        return _win32.process_alive(self._pid(process))

    def start_agent(
        self,
        cmd: Sequence[str],
        stderr_log: str,
    ) -> RefreshWatcherProcessHandle:
        """Launch a replacement agent as a detached Windows process."""
        with open(stderr_log, "a") as serr:
            process = subprocess.Popen(
                list(cmd),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=serr,
                creationflags=_win32.DETACHED_CREATIONFLAGS,
                close_fds=True,
            )
        return RefreshWatcherProcessHandle(pid=process.pid)

    def graceful_stop(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> None:
        """Request cooperative shutdown through the workdir ``.suspend`` file.

        The stale duplicate this policy stops is by definition an agent run for
        this adapter's bound working directory; its heartbeat loop consumes
        ``.suspend`` and performs the ordered stop. The PID is not addressable
        for a graceful request on Windows, so the channel is the directory.
        """
        (self._working_dir / ".suspend").touch()

    def force_stop(
        self,
        process: RefreshWatcherProcessHandle | RefreshWatcherProcessObservation,
    ) -> None:
        """Force-stop the exact process after the graceful grace period."""
        _win32.terminate_pid(self._pid(process))


__all__ = ["WindowsRefreshWatcherProcessAdapter"]
