"""Compatibility selector for the retained internal Bash implementation."""
from __future__ import annotations

import os

from .posix.bash import PosixBashDialect
from lingtai.tools.bash._shell_dialect import ShellDialect


def select_bash_shell_dialect() -> ShellDialect:
    if os.name == "posix":
        return PosixBashDialect()
    if os.name == "nt":
        from .windows.powershell import PowerShellDialect
        return PowerShellDialect()
    raise NotImplementedError(
        f"Bash shell dialect is unsupported on platform {os.name!r}"
    )
