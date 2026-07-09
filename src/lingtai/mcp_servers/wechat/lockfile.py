"""Per-account poller lockfile for the WeChat addon.

iLink's getUpdates is a single-consumer long-poll: when two processes hold
the same bot_token and both call getUpdates, each call may receive a
different subset of messages, and there is no way for either consumer to
know it is racing. The practical symptom is "inbound messages appear flaky"
— see GH issue #83. This module prevents that by taking an exclusive
fcntl.flock on a per-account lockfile in the user's runtime directory.

The lock key hashes the bot_token (which is the only stable identifier of
the iLink account from the addon's perspective). The lockfile path is
deterministic across processes/working-dirs on the same machine, so a
second poller for the same account on the same host is reliably refused.

Platform note: the lock primitive differs by OS but the safety model does
not. On POSIX we take an exclusive ``fcntl.flock``; on native Windows (where
``fcntl`` is absent) we take a non-blocking exclusive ``msvcrt.locking``
byte-range lock over the same lockfile. Either way a second poller for the
same account on the same host is reliably refused. ``acquire()`` only raises
``UnsupportedPlatformError`` when *neither* primitive is available, since a
silent no-op would leave issue #83 unresolved while pretending the lock had
been taken.
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import IO

log = logging.getLogger(__name__)

# msvcrt.locking locks/unlocks a byte range starting at the current file
# position. We lock a single byte at offset 0 — the range need not contain
# data, and locking one byte is enough to serialize acquirers. Kept as a
# module constant so acquire()/release() agree on the range.
_WINDOWS_LOCK_BYTES = 1


class PollerLockBusy(RuntimeError):
    """Raised when another lingtai-wechat poller already holds this account."""


class UnsupportedPlatformError(RuntimeError):
    """Raised when the poller lock cannot be implemented on this OS."""


def _lock_dir() -> Path:
    """Where lockfiles live. ~/.lingtai-wechat/locks/ on POSIX."""
    base = Path.home() / ".lingtai-wechat" / "locks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _account_key(bot_token: str) -> str:
    return hashlib.sha256(bot_token.encode("utf-8")).hexdigest()[:16]


def lock_path(bot_token: str) -> Path:
    return _lock_dir() / f"poller-{_account_key(bot_token)}.lock"


class AccountLock:
    """Exclusive OS lock per iLink account.

    On POSIX the primitive is ``fcntl.flock``; on native Windows it is a
    non-blocking ``msvcrt.locking`` byte-range lock. Held for the lifetime of
    the poller. Both primitives release automatically when the process exits
    (the kernel drops the lock), so a hard kill leaves no stale state
    requiring cleanup.
    """

    def __init__(self, bot_token: str) -> None:
        self._path = lock_path(bot_token)
        self._fh: IO[str] | None = None
        # Which primitive currently holds the lock, so release() unlocks with
        # the matching call. None until acquire() succeeds.
        self._primitive: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> None:
        """Take the exclusive lock.

        Raises:
            PollerLockBusy: if another process already holds the lock.
            UnsupportedPlatformError: if neither ``fcntl`` (POSIX) nor
                ``msvcrt`` (Windows) is available.
        """
        # Open without truncating: a losing contender used to wipe the
        # holder's PID entry between holder-write and contender-read, which
        # made the PollerLockBusy diagnostic unreliable. Create-if-missing
        # via os.open with O_RDWR|O_CREAT, then wrap in a Python file.
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        fh = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            primitive = self._take_lock(fh)
        except Exception:
            fh.close()
            raise

        # Only write the PID *after* the lock is acquired, so contenders
        # never observe a half-truncated empty file.
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        os.fsync(fh.fileno())
        self._fh = fh
        self._primitive = primitive
        log.info("Acquired WeChat poller lock for account at %s", self._path)

    def _take_lock(self, fh: IO[str]) -> str:
        """Take the OS lock on ``fh``; return the primitive name used.

        The file is left open on success (the caller keeps it) and closed by
        the caller on any raise.
        """
        try:
            import fcntl
        except ImportError:
            fcntl = None  # type: ignore[assignment]
        if fcntl is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise self._busy(fh, posix=True) from exc
            return "fcntl"

        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover — neither POSIX nor NT
            raise UnsupportedPlatformError(
                "lingtai-wechat's poller lock requires fcntl (POSIX) or "
                "msvcrt (Windows); neither is available on this platform. "
                "Running without a lock would silently re-introduce the "
                "duplicate-poller race (GH #83). Please open an issue."
            ) from exc

        # msvcrt.locking locks a byte range starting at the current file
        # position, so seek to the stable offset first. LK_NBLCK is the
        # non-blocking exclusive variant; it raises OSError on contention.
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, _WINDOWS_LOCK_BYTES)
        except OSError as exc:
            if _is_windows_lock_busy(exc):
                raise self._busy(fh, posix=False) from exc
            # A different OS error (permissions, bad handle, …) must surface
            # as itself rather than be mislabeled as a duplicate poller.
            raise
        return "msvcrt"

    def _busy(self, fh: IO[str], *, posix: bool) -> PollerLockBusy:
        """Build the PollerLockBusy for a losing contender and close ``fh``.

        The contender reads the holder's PID from its own already-open handle
        (no re-open race). If the OS denies that read — Windows may refuse a
        read on a range the holder locked — we fall back to ``unknown``.
        """
        existing_pid = _read_existing_pid_fh(fh)
        fh.close()
        pid_str = existing_pid or "unknown"
        # Concrete remediation hints — after an upgrade, the most common
        # reason this fires is that another LingTai project still has its old
        # lingtai-wechat MCP running and we can't tell the user which project
        # it belongs to from this side of the lock.
        remediation: list[str] = []
        if existing_pid and existing_pid.isdigit():
            if posix:
                remediation.append(
                    f"  Inspect the holder:  ps -p {existing_pid} -o pid,command"
                )
                remediation.append(
                    f"  Find its workdir:    lsof -p {existing_pid} 2>/dev/null | grep cwd"
                )
                remediation.append(
                    f"  Stop it gracefully:  kill -TERM {existing_pid}"
                )
            else:
                remediation.append(
                    f"  Inspect the holder:  Get-Process -Id {existing_pid}   "
                    f"(or: tasklist /FI \"PID eq {existing_pid}\")"
                )
                remediation.append(
                    f"  Stop it gracefully:  Stop-Process -Id {existing_pid}"
                )
        else:
            if posix:
                remediation.append(
                    "  Find pollers:   pgrep -af 'lingtai-wechat|lingtai.mcp_servers.wechat'"
                )
            else:
                remediation.append(
                    "  Find pollers:   Get-CimInstance Win32_Process -Filter "
                    '"Name = \'python.exe\'" | Select-Object ProcessId,CommandLine'
                )
            remediation.append(
                "  Lockfile is held but no PID recorded — most likely a "
                "pre-upgrade poller that predates the lockfile. Stop it "
                "from the project that launched it."
            )
        return PollerLockBusy(
            f"Another lingtai-wechat poller is already running for this "
            f"iLink account.\n"
            f"  Lockfile:    {self._path}\n"
            f"  Holder PID:  {pid_str}\n"
            f"Stop the other poller before starting this one:\n"
            + "\n".join(remediation)
            + "\n(See lingtai-wechat README → Troubleshooting → "
            "\"multiple pollers after upgrade\".)"
        )

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if self._primitive == "fcntl":
                import fcntl
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            elif self._primitive == "msvcrt":
                import msvcrt
                self._fh.seek(0)
                msvcrt.locking(
                    self._fh.fileno(), msvcrt.LK_UNLCK, _WINDOWS_LOCK_BYTES
                )
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None
        self._primitive = None
        # Leave the lockfile on disk (its presence + lock state is what
        # matters); removing it would race with a concurrent acquire().


def _is_windows_lock_busy(exc: OSError) -> bool:
    """Whether an ``msvcrt.locking`` OSError means "already locked".

    On a lock violation ``msvcrt.locking`` raises OSError with ``errno`` set
    to ``EACCES`` (13) or ``EDEADLOCK`` (36) — the two values the Windows CRT
    maps a lock contention to. When present, the Windows ``winerror`` is
    ``ERROR_LOCK_VIOLATION`` (33) or ``ERROR_SHARING_VIOLATION`` (32). Any
    other error (bad handle, permission, disk) is *not* contention and must
    surface as itself so we never mislabel a real failure as a duplicate
    poller.

    The errno constant is named ``EDEADLOCK`` on the Windows Python build but
    ``EDEADLK`` elsewhere, so we accept either spelling to keep this classifier
    testable on POSIX. ``winerror`` only exists on Windows OSErrors.
    """
    import errno

    busy_errnos = {errno.EACCES}
    for name in ("EDEADLOCK", "EDEADLK"):
        value = getattr(errno, name, None)
        if value is not None:
            busy_errnos.add(value)
    if exc.errno in busy_errnos:
        return True
    winerror = getattr(exc, "winerror", None)
    # 32 = ERROR_SHARING_VIOLATION, 33 = ERROR_LOCK_VIOLATION.
    return winerror in (32, 33)


def _read_existing_pid_fh(fh: IO[str]) -> str | None:
    """Read PID from an already-open lockfile handle (no re-open race)."""
    try:
        fh.seek(0)
        return fh.read().strip() or None
    except OSError:
        return None
