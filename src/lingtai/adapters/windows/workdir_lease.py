"""Native Windows working-directory lease adapter.

``WindowsWorkdirLeaseAdapter`` implements the Core-owned
``lingtai.kernel.workdir_lease.WorkdirLeasePort`` by holding an exclusive
``msvcrt.locking`` byte-range lock on byte 0, length 1, of
``<workdir>/.agent.lock``. It is the Windows sibling of
``PosixWorkdirLeaseAdapter``; Core never constructs either.

The byte range is an interop invariant, not an implementation accident: the
LingTai TUI's duplicate-launch probe (``tui/internal/duplaunch``, TUI PR #687)
checks byte 0, length 1 of the same ``.agent.lock`` with a non-creating
``LockFileEx`` attempt and maps a conflict to *Block*. ``msvcrt.locking`` and
``LockFileEx`` share one Win32 file-lock namespace, so holding this range is
exactly what makes a live kernel agent visible to the TUI probe. Changing the
file name, offset, or length breaks that cross-repo contract.

Mechanism notes kept out of the Port: the ``"a+b"`` open (no truncation of a
file another process may be probing), the single seeded ``b"\\0"`` byte, the
``seek(0)`` before every lock/unlock, the 250 ms poll cadence, the
``time.monotonic`` deadline, and the close-before-unlink release order. The OS
releases byte-range locks when the holding process dies, which is what makes a
crashed holder recoverable without any lock-file surgery.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Any

from lingtai.kernel.workdir import workdir_layout
from lingtai.kernel.workdir_lease import WorkdirLeasePort


class WindowsWorkdirLeaseAdapter(WorkdirLeasePort):
    """Exclusive working-directory lease via an ``msvcrt`` byte-range lock.

    Address = the agent working directory. Example::

        lease = WindowsWorkdirLeaseAdapter(Path(r"C:\\agents\\abc123"))
        lease.acquire(timeout_seconds=10)  # claim, waiting up to 10s
        ...
        lease.release()  # unlock, close, best-effort unlink
    """

    def __init__(self, working_dir: str | Path) -> None:
        self._layout = workdir_layout(Path(working_dir))
        self._path = self._layout.root
        self._lock_file: Any = None

    def acquire(self, timeout_seconds: float = 0) -> None:
        """Acquire the exclusive byte-0/length-1 lock on ``.agent.lock``.

        ``timeout_seconds`` is the max time to wait for the lock. ``0`` fails
        immediately (one attempt). Polls at 250 ms intervals until a monotonic
        deadline, then raises ``RuntimeError`` with the same contention text as
        the POSIX adapter.
        """
        if os.name != "nt":
            raise OSError("Windows workdir lease requires Windows")
        import msvcrt

        lock_path = self._layout.agent_lock
        deadline = time.monotonic() + timeout_seconds
        while True:
            handle = open(lock_path, "a+b")
            try:
                handle.seek(0)
                if handle.tell() == 0 and lock_path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                handle.close()
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Working directory '{self._path}' is already in use "
                        f"by another agent. Each agent needs its own directory."
                    )
                time.sleep(0.25)
                continue
            self._lock_file = handle
            return

    def release(self) -> None:
        """Release the lease: attempt unlock/close, then conditionally unlink.

        Idempotent and safe when not held. Unlock and close failures are
        swallowed; close is attempted in a ``finally`` even when the explicit
        unlock raises. The lock file is unlinked only when the handle reports
        confirmed closed — an uncertain live descriptor may still hold the
        byte-range lock, and unlinking then would let a second holder create
        and lock a fresh file under the same name. On Windows the unlink also
        fails (and is swallowed) while another process, such as the TUI probe,
        holds the file open without delete sharing; the named file then simply
        remains, and lock-file existence is not authority.
        """
        lock_file = self._lock_file
        if lock_file is None:
            return
        # Reset state up front so a raise anywhere below still leaves the
        # adapter releasable/idempotent and never re-touches this descriptor.
        self._lock_file = None
        lock_path = self._layout.agent_lock
        try:
            with contextlib.suppress(OSError):
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            with contextlib.suppress(OSError):
                lock_file.close()
        if not bool(getattr(lock_file, "closed", False)):
            return
        with contextlib.suppress(OSError):
            lock_path.unlink(missing_ok=True)


__all__ = ["WindowsWorkdirLeaseAdapter"]
