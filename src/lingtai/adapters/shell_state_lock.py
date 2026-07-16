"""Composition selector for the canonical shell state-lock Port."""
from __future__ import annotations

import os

from lingtai.tools.bash._state_lock import BashStateLockPort


def select_shell_state_lock() -> BashStateLockPort:
    if os.name == "posix":
        # Keep the POSIX-only fcntl import behind platform selection so loading
        # the portable selector on Windows reaches its native adapter instead.
        from .posix.bash_state_lock import PosixBashStateLockAdapter
        return PosixBashStateLockAdapter()
    if os.name == "nt":
        from .windows.powershell_state_lock import WindowsShellStateLockAdapter
        return WindowsShellStateLockAdapter()
    raise NotImplementedError(f"shell async state locking is unsupported on {os.name!r}")
