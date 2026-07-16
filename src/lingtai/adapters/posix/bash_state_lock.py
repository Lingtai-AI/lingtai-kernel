"""POSIX adapter for Bash async state serialization."""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path


class PosixBashStateLockAdapter:
    @contextlib.contextmanager
    def exclusive(self, job_dir: Path):
        handle = open(job_dir / ".state.lock", "a", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
