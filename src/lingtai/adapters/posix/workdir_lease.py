"""POSIX filesystem working-directory lease adapter.

``PosixWorkdirLeaseAdapter`` implements the Core-owned
``lingtai.kernel.workdir_lease.WorkdirLeasePort`` by holding an exclusive,
non-blocking ``fcntl.flock`` on ``<workdir>/.agent.lock``. It is the only
production lease adapter; Core never constructs it.

The concrete lock file (``.agent.lock`` via ``WorkdirLayout``), the open file
descriptor, the ``fcntl`` calls, the 250 ms poll cadence, the ``time.monotonic``
deadline, and the best-effort lock-file unlink on release all live here — they
are the POSIX filesystem mechanism, not part of the technology-neutral Port.
This is a faithful move of the exclusion behavior formerly on
``WorkingDir.acquire_lock``/``release_lock``.
"""
from __future__ import annotations

import contextlib
import fcntl
import time
from pathlib import Path
from typing import Any

from lingtai.kernel.workdir import workdir_layout
from lingtai.kernel.workdir_lease import WorkdirLeasePort


class PosixWorkdirLeaseAdapter(WorkdirLeasePort):
    """Exclusive working-directory lease via a POSIX ``flock`` on ``.agent.lock``.

    Address = the agent working directory. Example::

        lease = PosixWorkdirLeaseAdapter(Path("/agents/abc123"))
        lease.acquire(timeout_seconds=10)  # claim, waiting up to 10s
        ...
        lease.release()  # unlock, close, best-effort unlink
    """

    def __init__(self, working_dir: str | Path) -> None:
        self._layout = workdir_layout(Path(working_dir))
        self._path = self._layout.root
        self._lock_file: Any = None

    def acquire(self, timeout_seconds: float = 0) -> None:
        """Acquire an exclusive lock on the working directory.

        ``timeout_seconds`` is the max time to wait for the lock. ``0`` fails
        immediately (one attempt). Polls at 250 ms intervals until a monotonic
        deadline, then raises ``RuntimeError``.
        """
        lock_path = self._layout.agent_lock
        deadline = time.monotonic() + timeout_seconds
        while True:
            self._lock_file = open(lock_path, "w")
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return  # success
            except OSError:
                self._lock_file.close()
                self._lock_file = None
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Working directory '{self._path}' is already in use "
                        f"by another agent. Each agent needs its own directory."
                    )
                time.sleep(0.25)

    def release(self) -> None:
        """Release the lease: attempt unlock/close, then conditionally unlink.

        Idempotent and safe when not held. Close is attempted in a ``finally``
        even if the explicit ``LOCK_UN`` raises — close failure is swallowed. The lock file is unlinked only when the handle
        reports confirmed closed; uncertain closure leaves the inode named so a
        second holder cannot create and lock a fresh inode while the old
        descriptor may still be live. Internal state is reset so repeated calls
        stay safe.
        """
        lock_file = self._lock_file
        if lock_file is None:
            return
        # Reset state up front so a raise anywhere below still leaves the adapter
        # releasable/idempotent and never re-touches this descriptor.
        self._lock_file = None
        lock_path = self._layout.agent_lock
        try:
            with contextlib.suppress(OSError):
                fcntl.flock(lock_file, fcntl.LOCK_UN)
        finally:
            with contextlib.suppress(OSError):
                lock_file.close()
        # A failed ``close`` may leave the old descriptor open and still locked.
        # Keep the named inode in that uncertain state: unlinking it would let a
        # second process create and lock a different inode under the same path.
        if not bool(getattr(lock_file, "closed", False)):
            return
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)
