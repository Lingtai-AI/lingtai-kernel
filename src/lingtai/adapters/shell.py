"""Composition selector for the canonical ``shell`` capability.

The implementation package remains ``lingtai.tools.bash`` for PR1 durable and
packaging compatibility.  Platform identity belongs at this outer selector.
"""
from __future__ import annotations

import os

from lingtai.tools.bash._shell_dialect import ShellDialect
from .posix.bash import PosixBashDialect


def select_shell_dialect() -> ShellDialect:
    if os.name == "posix":
        return PosixBashDialect()
    if os.name == "nt":
        from .windows.powershell import PowerShellDialect
        return PowerShellDialect()
    raise NotImplementedError(f"shell dialect is unsupported on platform {os.name!r}")
