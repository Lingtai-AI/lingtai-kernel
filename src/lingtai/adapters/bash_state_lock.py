"""Fail-loud Bash async state-lock composition selector."""
from __future__ import annotations

import os

from lingtai.tools.bash._state_lock import BashStateLockPort
from .posix.bash_state_lock import PosixBashStateLockAdapter


def select_bash_state_lock() -> BashStateLockPort:
    if os.name == "posix":
        return PosixBashStateLockAdapter()
    raise NotImplementedError(f"Bash async state locking is unsupported on {os.name!r}")
