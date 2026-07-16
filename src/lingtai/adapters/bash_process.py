"""Fail-loud Bash asynchronous process composition selector."""
from __future__ import annotations

import os

from lingtai.tools.bash._async_process import BashAsyncProcessPort
from .posix.bash_process import PosixBashAsyncProcessAdapter


def select_bash_async_process() -> BashAsyncProcessPort:
    if os.name == "posix":
        return PosixBashAsyncProcessAdapter()
    raise NotImplementedError(f"Bash async process supervision is unsupported on {os.name!r}")
