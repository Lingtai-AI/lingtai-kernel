"""Compatibility selector for the retained internal Bash package."""
from __future__ import annotations

from lingtai.tools.bash._state_lock import BashStateLockPort
from .shell_state_lock import select_shell_state_lock


def select_bash_state_lock() -> BashStateLockPort:
    return select_shell_state_lock()
