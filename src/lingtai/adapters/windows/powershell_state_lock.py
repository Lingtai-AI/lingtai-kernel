"""Native cross-process state lock for Windows shell jobs."""
from __future__ import annotations

import contextlib
import time
from pathlib import Path


class WindowsShellStateLockAdapter:
    """Byte-range lock backed by Windows ``msvcrt.locking``.

    The lock file is shared by the manager and detached supervisor processes;
    unlike ``threading.Lock`` this serializes state transitions across process
    boundaries.  Atomic state replacement remains owned by the shared policy.
    """

    @contextlib.contextmanager
    def exclusive(self, job_dir: Path):
        if __import__("os").name != "nt":
            raise OSError("Windows shell state lock requires Windows")
        import msvcrt

        lock_path = job_dir / ".state.lock"
        handle = open(lock_path, "a+b")
        try:
            handle.seek(0)
            if handle.tell() == 0 and lock_path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            # LK_NBLCK is explicit and retryable, avoiding a hidden indefinite
            # CRT timeout while preserving cross-process exclusion.
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            yield
        finally:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                handle.close()


__all__ = ["WindowsShellStateLockAdapter"]
