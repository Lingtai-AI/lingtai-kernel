"""POSIX process-table scan adapter for the duplicate-launch guard.

``PosixAgentProcessScanAdapter`` implements the Core-owned
``lingtai.kernel.process_scan.AgentProcessScanPort`` with one bounded
``ps -eo pid=,command=`` invocation — a faithful move of the mechanism the CLI
host previously performed inline. Any failure to run or parse the process
table yields nothing (the Port's best-effort contract): the workdir lease is
the real exclusion authority, and ``ps`` being unavailable must never block an
honest boot.
"""
from __future__ import annotations

import subprocess
from typing import Iterator

from lingtai.kernel.process_scan import AgentProcessScanPort


class PosixAgentProcessScanAdapter(AgentProcessScanPort):
    """Observe visible processes through the POSIX process table."""

    def iter_process_commands(self) -> Iterator[tuple[int, str]]:
        try:
            out = subprocess.check_output(
                ["ps", "-eo", "pid=,command="], stderr=subprocess.DEVNULL, text=True
            )
        except Exception:
            return
        for line in out.splitlines():
            trimmed = line.strip()
            if not trimmed:
                continue
            parts = trimmed.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, command = parts
            if not pid_str.isdigit():
                continue
            yield int(pid_str), command


__all__ = ["PosixAgentProcessScanAdapter"]
