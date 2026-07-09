"""Regression tests for the WeChat per-account poller lock.

The lock prevents the duplicate-poller race (GH #83) by taking an exclusive
OS lock on a per-account lockfile. It uses ``fcntl.flock`` on POSIX and
``msvcrt.locking`` on native Windows, and must fail loud (never silently
no-op) when neither primitive exists.

These tests run on POSIX. The Windows branch is exercised by injecting a fake
``msvcrt`` module into ``sys.modules`` and hiding ``fcntl``, so no real
Windows host is needed for unit coverage.
"""
from __future__ import annotations

import errno
import os
import types

import pytest

from lingtai.mcp_servers.wechat import lockfile as lockfile_mod
from lingtai.mcp_servers.wechat.lockfile import (
    AccountLock,
    PollerLockBusy,
    UnsupportedPlatformError,
    _WINDOWS_LOCK_BYTES,
)


@pytest.fixture(autouse=True)
def _isolate_lock_dir(tmp_path, monkeypatch):
    """Point the lockfile dir at a per-test tmp dir so runs don't collide."""
    monkeypatch.setattr(
        lockfile_mod, "_lock_dir", lambda: tmp_path, raising=True
    )
    return tmp_path


# --------------------------------------------------------------------------
# POSIX behavior (unchanged) — fcntl.flock is the real primitive here.
# --------------------------------------------------------------------------


def test_posix_acquire_writes_pid_after_lock():
    lock = AccountLock("token-a")
    lock.acquire()
    try:
        assert lock.path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_posix_second_acquire_is_busy_with_pid_and_posix_hints():
    first = AccountLock("token-b")
    first.acquire()
    try:
        second = AccountLock("token-b")
        with pytest.raises(PollerLockBusy) as exc_info:
            second.acquire()
    finally:
        first.release()
    msg = str(exc_info.value)
    assert str(os.getpid()) in msg
    # POSIX remediation hints, not Windows ones.
    assert "kill -TERM" in msg
    assert "ps -p" in msg
    assert "Stop-Process" not in msg


def test_posix_release_allows_reacquire():
    first = AccountLock("token-c")
    first.acquire()
    first.release()
    # A fresh holder must be able to take the lock once released.
    second = AccountLock("token-c")
    second.acquire()
    try:
        assert second.path.read_text().strip()
    finally:
        second.release()


# --------------------------------------------------------------------------
# Windows branch — faked msvcrt, fcntl hidden.
# --------------------------------------------------------------------------


class _FakeMsvcrt(types.ModuleType):
    """Minimal stand-in for the ``msvcrt`` module.

    Models a byte-range lock on the underlying *file* (keyed by inode, like
    the real CRT) rather than the raw fd — two different fds on the same
    lockfile must conflict, which is the whole point of the lock. A second
    LK_NBLCK on an already-locked file raises OSError like the real CRT.
    """

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, busy_error=None):
        super().__init__("msvcrt")
        self._locked: set[int] = set()  # inode numbers
        self.calls: list[tuple[int, int, int]] = []
        self._busy_error = busy_error

    def _inode(self, fd):
        import os as _os

        return _os.fstat(fd).st_ino

    def locking(self, fd, mode, nbytes):
        self.calls.append((fd, mode, nbytes))
        key = self._inode(fd)
        if mode == self.LK_NBLCK:
            if key in self._locked:
                # EDEADLK is the POSIX spelling of the Windows CRT's
                # EDEADLOCK, which is what a lock violation surfaces as.
                raise self._busy_error or OSError(
                    errno.EDEADLK, "lock violation"
                )
            self._locked.add(key)
        elif mode == self.LK_UNLCK:
            self._locked.discard(key)


def _force_windows(monkeypatch, fake_msvcrt):
    """Make the lockfile module take the Windows branch on this POSIX host."""

    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("no fcntl on this fake-Windows host")
        if name == "msvcrt":
            return fake_msvcrt
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)


def test_windows_acquire_takes_msvcrt_lock_and_writes_pid(monkeypatch):
    fake = _FakeMsvcrt()
    _force_windows(monkeypatch, fake)

    lock = AccountLock("win-token")
    lock.acquire()
    try:
        # Actually locked via LK_NBLCK, not a silent no-op.
        assert any(
            mode == fake.LK_NBLCK and nbytes == _WINDOWS_LOCK_BYTES
            for _, mode, nbytes in fake.calls
        )
        assert lock.path.read_text().strip() == str(os.getpid())
    finally:
        lock.release()


def test_windows_second_acquire_is_busy_with_windows_hints(monkeypatch):
    fake = _FakeMsvcrt()
    _force_windows(monkeypatch, fake)

    first = AccountLock("win-busy")
    first.acquire()
    try:
        second = AccountLock("win-busy")
        with pytest.raises(PollerLockBusy) as exc_info:
            second.acquire()
    finally:
        first.release()
    msg = str(exc_info.value)
    assert str(os.getpid()) in msg
    # Windows-specific remediation, not POSIX.
    assert "Stop-Process" in msg
    assert "Get-Process" in msg or "tasklist" in msg
    assert "kill -TERM" not in msg


def test_windows_release_unlocks_and_allows_reacquire(monkeypatch):
    fake = _FakeMsvcrt()
    _force_windows(monkeypatch, fake)

    first = AccountLock("win-release")
    first.acquire()
    first.release()
    # release() must issue LK_UNLCK over the same byte range.
    assert (
        first._fh is None
    )  # handle dropped
    assert any(
        mode == fake.LK_UNLCK and nbytes == _WINDOWS_LOCK_BYTES
        for _, mode, nbytes in fake.calls
    )
    # A fresh acquirer succeeds because the range was actually released.
    second = AccountLock("win-release")
    second.acquire()
    second.release()


def test_windows_sharing_violation_winerror_is_busy(monkeypatch):
    err = OSError()
    err.winerror = 33  # ERROR_LOCK_VIOLATION, no errno set
    fake = _FakeMsvcrt(busy_error=err)
    _force_windows(monkeypatch, fake)

    first = AccountLock("win-winerror")
    first.acquire()
    try:
        second = AccountLock("win-winerror")
        with pytest.raises(PollerLockBusy):
            second.acquire()
    finally:
        first.release()


def test_windows_non_lock_oserror_surfaces_unlabeled(monkeypatch):
    # A permission error (EPERM) is not contention: it must propagate as
    # itself, never be relabeled PollerLockBusy.
    boom = OSError(errno.EPERM, "operation not permitted")
    fake = _FakeMsvcrt(busy_error=boom)
    _force_windows(monkeypatch, fake)

    first = AccountLock("win-boom")
    first.acquire()
    try:
        second = AccountLock("win-boom")
        with pytest.raises(OSError) as exc_info:
            second.acquire()
        assert not isinstance(exc_info.value, PollerLockBusy)
        assert exc_info.value.errno == errno.EPERM
    finally:
        first.release()


def test_neither_primitive_raises_unsupported(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(
        __builtins__, dict
    ) else __builtins__.__import__

    def no_lock_import(name, *args, **kwargs):
        if name in ("fcntl", "msvcrt"):
            raise ImportError(f"no {name} on this fake host")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", no_lock_import)

    lock = AccountLock("no-primitive")
    with pytest.raises(UnsupportedPlatformError) as exc_info:
        lock.acquire()
    # Must be explicit that it did not silently no-op.
    assert "#83" in str(exc_info.value)


def test_classifier_treats_eaccess_and_edeadlock_as_busy():
    assert lockfile_mod._is_windows_lock_busy(OSError(errno.EACCES, "x"))
    assert lockfile_mod._is_windows_lock_busy(OSError(errno.EDEADLK, "x"))
    other = OSError(errno.ENOENT, "x")
    assert not lockfile_mod._is_windows_lock_busy(other)
