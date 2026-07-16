"""Compatibility selector for the retained internal Bash package."""
from __future__ import annotations

from lingtai.tools.bash._async_process import BashAsyncProcessPort
from .shell_process import select_shell_async_process


def select_bash_async_process() -> BashAsyncProcessPort:
    return select_shell_async_process()
