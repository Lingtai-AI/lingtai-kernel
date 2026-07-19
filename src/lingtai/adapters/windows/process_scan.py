"""Windows process-table scan adapter for the duplicate-launch guard.

``WindowsAgentProcessScanAdapter`` implements the Core-owned
``lingtai.kernel.process_scan.AgentProcessScanPort`` with one bounded
PowerShell CIM query over ``Win32_Process``. CIM is the mechanism because the
Win32 snapshot APIs expose executable names but not full command lines, and
the duplicate guard matches on the exact ``… run <workdir>`` argv text via the
canonical Core matcher. JSON transport (``ConvertTo-Json``) keeps command
lines containing spaces, quotes, or newlines lossless.

Any failure — no PowerShell on PATH, query error, unparsable output — yields
nothing per the Port's best-effort contract: the workdir lease remains the
exclusion authority, and scan unavailability must never block an honest boot.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Iterator

from lingtai.kernel.process_scan import AgentProcessScanPort

_CIM_COMMAND = (
    "Get-CimInstance -ClassName Win32_Process | "
    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
)


class WindowsAgentProcessScanAdapter(AgentProcessScanPort):
    """Observe visible processes through one CIM ``Win32_Process`` query."""

    @staticmethod
    def _powershell_executable() -> str | None:
        return shutil.which("pwsh") or shutil.which("powershell")

    def iter_process_commands(self) -> Iterator[tuple[int, str]]:
        shell = self._powershell_executable()
        if shell is None:
            return
        try:
            out = subprocess.check_output(
                [shell, "-NoProfile", "-NonInteractive", "-Command", _CIM_COMMAND],
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return
        try:
            records = json.loads(out)
        except ValueError:
            return
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            pid = record.get("ProcessId")
            command = record.get("CommandLine")
            if isinstance(pid, int) and isinstance(command, str) and command:
                yield pid, command


__all__ = ["WindowsAgentProcessScanAdapter"]
