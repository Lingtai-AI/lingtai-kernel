"""Exact-argument pin for the Windows shell state-lock byte range.

``WindowsShellStateLockAdapter.exclusive`` serializes shell state transitions
across the manager and detached supervisor processes with an ``msvcrt``
byte-range lock. This runs the real adapter code with only the ``msvcrt``
mechanism module replaced by a recorder, so the exact lock/unlock arguments —
``LK_NBLCK``/``LK_UNLCK``, length 1, at file position 0 on
``<job_dir>/.state.lock`` — are pinned even on POSIX CI. The native serialization
behavior is proven on Windows by ``tests/test_shell_windows_native.py``.
"""
from __future__ import annotations

import os
import sys
import types

from lingtai.adapters.windows.powershell_state_lock import WindowsShellStateLockAdapter


def test_exclusive_locks_and_unlocks_byte_zero_length_one(tmp_path, monkeypatch):
    calls: list[tuple[str, int, int]] = []
    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)

    def locking(fd: int, mode: int, nbytes: int) -> None:
        calls.append(
            (
                "lock" if mode == fake_msvcrt.LK_NBLCK else "unlock",
                os.lseek(fd, 0, os.SEEK_CUR),  # OS file position at call time
                nbytes,
            )
        )

    fake_msvcrt.locking = locking

    # Construct the job_dir Path BEFORE patching os.name so pathlib dispatches
    # its concrete class on the real platform; the adapter reads os.name at
    # exclusive() time, not construction.
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    lock_path = job_dir / ".state.lock"

    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(os, "name", "nt")

    adapter = WindowsShellStateLockAdapter()
    captured_handle = {}
    with adapter.exclusive(job_dir):
        # Inside the critical section: locked exactly once at position 0,
        # length 1, and the lock file exists with the single seeded byte.
        assert calls == [("lock", 0, 1)]
        assert lock_path.is_file()
        assert lock_path.stat().st_size == 1

    # On exit: unlocked at position 0, length 1, and the handle is closed.
    assert calls == [("lock", 0, 1), ("unlock", 0, 1)]


def test_exclusive_does_not_reseed_a_nonempty_lock_file(tmp_path, monkeypatch):
    """A pre-existing non-empty lock file is not re-seeded; still locks byte 0."""
    calls: list[tuple[str, int, int]] = []
    fake_msvcrt = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0)
    fake_msvcrt.locking = lambda fd, mode, nbytes: calls.append(
        ("lock" if mode == fake_msvcrt.LK_NBLCK else "unlock",
         os.lseek(fd, 0, os.SEEK_CUR), nbytes)
    )

    job_dir = tmp_path / "job"
    job_dir.mkdir()
    lock_path = job_dir / ".state.lock"
    lock_path.write_bytes(b"seeded-payload")  # non-empty already

    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(os, "name", "nt")

    with WindowsShellStateLockAdapter().exclusive(job_dir):
        pass

    # Size unchanged (no re-seed) and the lock/unlock range is still byte 0 len 1.
    assert lock_path.stat().st_size == len(b"seeded-payload")
    assert calls == [("lock", 0, 1), ("unlock", 0, 1)]


def test_exclusive_guards_against_non_windows_use(tmp_path):
    """Off Windows, the adapter fails loudly instead of silently no-op'ing."""
    if os.name == "nt":
        import pytest

        pytest.skip("guard is only observable off Windows")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    import pytest

    with pytest.raises(OSError, match="requires Windows"):
        with WindowsShellStateLockAdapter().exclusive(job_dir):
            pass
