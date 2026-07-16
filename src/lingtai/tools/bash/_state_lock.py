"""Bash-local cross-process state-lock Port."""
from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol


class BashStateLockPort(Protocol):
    def exclusive(self, job_dir: Path) -> AbstractContextManager[None]: ...


# Canonical Port name; retained for the PR1 internal package compatibility seam.
ShellStateLockPort = BashStateLockPort
