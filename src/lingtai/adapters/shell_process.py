"""Composition selector for the canonical shell async process Port."""
from __future__ import annotations

import os

from lingtai.tools.bash._async_process import BashAsyncProcessPort
from .posix.bash_process import PosixBashAsyncProcessAdapter


def select_shell_async_process() -> BashAsyncProcessPort:
    if os.name == "posix":
        return PosixBashAsyncProcessAdapter()
    if os.name == "nt":
        from .windows.powershell_process import WindowsShellAsyncProcessAdapter
        return WindowsShellAsyncProcessAdapter()
    raise NotImplementedError(f"shell async process supervision is unsupported on {os.name!r}")
