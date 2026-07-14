"""POSIX adapter for ``.notification/<channel>.json`` mirrors.

It provides atomic file replacement and Store-owned mutation serialization,
both in-process (thread) and across independent processes (POSIX ``flock``
on a persistent per-workdir lock file — see ``_InterprocessMutationLock``).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import threading
from pathlib import Path

from lingtai.kernel._fsutil import atomic_write_json
from lingtai.kernel.notification_store import (
    AllowPredicate,
    CompareUpdateResult,
    ExpectedVersion,
    NotificationStorePort,
    PureAckMutator,
    PureCoreMutator,
    UNCONDITIONAL,
    UpdateAckRefsResult,
    _applied_result,
    _conflict_result,
)

_LARGE_RESULT_ACK_FILE = "large_result_acks.json"
_DOT_NOTIFICATION = ".notification"
# Private, non-JSON name: excluded from snapshot/fingerprint's `*.json` glob
# by construction, so it can never be surfaced as a channel. Never unlinked
# after use — a persistent, stable-inode lock authority is required so two
# independent adapter instances (same process or different processes) always
# flock the SAME inode; unlinking would let a later opener create and lock a
# different inode under the same path (split-inode lock authority).
_MUTATION_LOCK_FILE = ".store.lock"

# Process-wide registry: (pid, canonical workdir) -> one shared threading.Lock.
#
# Durable invariant: same-process instances/threads need exactly one PID-
# scoped thread lock per workdir (plain `flock` does not mutually exclude
# threads of one process once the critical section does real file I/O on
# this platform); every process — including a forked child — needs its own
# distinct open-file description for `flock` (an inherited fd/lock is shared
# with the parent, not exclusive to the child). Keying by `os.getpid()` means
# a forked child never reuses a parent's per-workdir lock OBJECT that might
# already be held. That alone is not sufficient: `_PROCESS_LOCKS_GUARD`
# itself mediates every lookup regardless of PID, so a fork while some
# parent thread holds `_PROCESS_LOCKS_GUARD` would still hand the child an
# inherited, already-locked guard — deadlocking the child's very first
# lookup, before the PID key is ever consulted. `os.register_at_fork`
# resets the guard (and, defensively, the whole registry) in the child so
# it always starts from a fresh, unlocked state.
_PROCESS_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[tuple[int, str], threading.Lock] = {}


def _reset_process_locks_after_fork() -> None:
    global _PROCESS_LOCKS_GUARD, _PROCESS_LOCKS
    _PROCESS_LOCKS_GUARD = threading.Lock()
    _PROCESS_LOCKS = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process_locks_after_fork)


def _process_wide_lock(workdir: Path) -> threading.Lock:
    key = (os.getpid(), str(workdir.resolve()))
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


class _InterprocessMutationLock:
    """One stable-inode POSIX ``flock`` authority per canonical workdir,
    serializing every notification-mutation family (``publish``, ``clear``,
    ``compare_update_channel``, ``update_ack_refs``) across independent
    processes. The lock FILE is never unlinked, so its inode stays stable for
    every opener; the per-process open HANDLE is reopened whenever the owning
    PID changes (see :meth:`_ensure_open`), and otherwise lives for the
    process's lifetime.

    Same-process serialization is handled separately by
    :func:`_process_wide_lock`. Both locks are always acquired together, in
    one consistent order (process-wide lock outermost, then this
    cross-process flock), by every mutating method.
    """

    def __init__(self, workdir: Path) -> None:
        self._workdir = workdir
        self._fh = None
        self._owner_pid = None

    def _ensure_open(self):
        """Return this PID's open handle, reopening across a PID change.

        Precondition (not re-guarded here): every caller must already hold
        the ``(pid, workdir)``-keyed lock from :func:`_process_wide_lock` for
        this exact ``self._workdir`` — :meth:`held` is the only caller and
        always acquires it first. That outer lock already serializes every
        thread/instance that could otherwise race this method for the same
        workdir in the same process, so no separate instance-local guard is
        needed (a redundant one would only invite a false sense of safety
        for a hypothetical direct call outside that outer critical section —
        this method is private precisely because it relies on it).
        """
        current_pid = os.getpid()
        if self._fh is not None and self._owner_pid == current_pid:
            return self._fh
        # Either never opened, or inherited across a fork — the owner PID
        # no longer matches, so a fresh open-file description is required.
        stale_fh = self._fh if self._owner_pid != current_pid else None
        notif_dir = self._workdir / _DOT_NOTIFICATION
        notif_dir.mkdir(exist_ok=True)
        lock_path = notif_dir / _MUTATION_LOCK_FILE
        # 'a' never truncates a concurrently-locked file's content and
        # always succeeds in creating it if absent — the lock authority
        # never depends on the file being empty or any particular content.
        # A fresh open() gives this PID its own open-file description,
        # distinct from any inherited one.
        self._fh = open(lock_path, "a")
        self._owner_pid = current_pid
        if stale_fh is not None:
            # Close only the inherited handle, never unlink the file — the
            # stable inode is the lock authority, not this fd.
            with contextlib.suppress(OSError):
                stale_fh.close()
        return self._fh

    @contextlib.contextmanager
    def held(self):
        """Acquire BOTH the process-wide same-process lock and the
        cross-process flock for the ``with`` block, in that consistent
        order, for every caller regardless of instance.

        Open/acquire failures are NOT swallowed — an unlocked mutation is
        strictly worse than a loud failure, so this never falls back to
        running the mutation without both locks held.
        """
        process_lock = _process_wide_lock(self._workdir)
        with process_lock:
            fh = self._ensure_open()
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _notification_dir(workdir: Path) -> Path:
    """Return the canonical ``.notification/`` directory for a workdir."""
    return workdir / _DOT_NOTIFICATION


def _channel_path(workdir: Path, channel: str) -> Path:
    """Return the canonical path for one channel mirror file."""
    return _notification_dir(workdir) / f"{channel}.json"


def _ack_path(workdir: Path) -> Path:
    return _notification_dir(workdir) / _LARGE_RESULT_ACK_FILE


def _version_entry(path: Path, raw: bytes) -> list:
    """Build one fingerprint entry from bytes already read successfully."""
    return [path.name, len(raw), hashlib.sha256(raw).hexdigest()]


def _safe_version(entry: list | tuple | None) -> list | None:
    """Return a JSON/log-safe fingerprint representation."""
    if entry is None:
        return None
    return list(entry)


class PosixNotificationStoreAdapter(NotificationStorePort):
    """Filesystem implementation of the Core-owned Store Port.

    One composed instance owns serialization; Core supplies channel policy.
    """

    def __init__(self, workdir: Path):
        self._workdir = Path(workdir)
        self._lock = threading.Lock()
        self._interprocess_lock = _InterprocessMutationLock(self._workdir)


    def snapshot(self, allow_channel: AllowPredicate) -> dict[str, object]:
        notif_dir = _notification_dir(self._workdir)
        if not notif_dir.is_dir():
            return {}
        out: dict[str, object] = {}
        for f in sorted(notif_dir.glob("*.json")):
            if f.name == _LARGE_RESULT_ACK_FILE:
                continue
            if not allow_channel(f.stem):
                continue
            try:
                out[f.stem] = json.loads(f.read_bytes())
            except (json.JSONDecodeError, OSError):
                continue
        return out


    def fingerprint(
        self, allow_channel: AllowPredicate
    ) -> tuple[tuple[str, int, str], ...]:
        notif_dir = _notification_dir(self._workdir)
        if not notif_dir.is_dir():
            return ()
        entries: list[tuple[str, int, str]] = []
        for f in notif_dir.iterdir():
            if not (f.is_file() and f.suffix == ".json"):
                continue
            if f.name == _LARGE_RESULT_ACK_FILE:
                continue
            if not allow_channel(f.stem):
                continue
            try:
                data = f.read_bytes()
            except OSError:
                continue
            entries.append((f.name, len(data), hashlib.sha256(data).hexdigest()))
        return tuple(sorted(entries))


    def publish(self, channel: str, payload: dict) -> None:
        notif_dir = _notification_dir(self._workdir)
        notif_dir.mkdir(exist_ok=True)
        target = _channel_path(self._workdir, channel)
        with self._interprocess_lock.held(), self._lock:
            atomic_write_json(target, payload, ensure_ascii=False, indent=None)


    def clear(self, channel: str) -> bool:
        target = _channel_path(self._workdir, channel)
        with self._interprocess_lock.held(), self._lock:
            try:
                target.unlink()
            except FileNotFoundError:
                return False
        return True


    def compare_update_channel(
        self,
        channel: str,
        expected_version: ExpectedVersion,
        pure_core_mutator: PureCoreMutator,
    ) -> CompareUpdateResult:
        target = _channel_path(self._workdir, channel)

        with self._interprocess_lock.held(), self._lock:
            current_payload: dict = {}
            current_version: list | None = None
            try:
                raw_bytes = target.read_bytes()
            except FileNotFoundError:
                raw_bytes = None
            if raw_bytes is not None:
                current_version = _version_entry(target, raw_bytes)
                try:
                    parsed = json.loads(raw_bytes)
                    current_payload = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    current_payload = {}

            if expected_version is not UNCONDITIONAL:
                if expected_version is None:
                    if current_version is not None:
                        return _conflict_result(
                            expected_version=expected_version,
                            current_version=_safe_version(current_version),
                        )
                else:
                    expected_list = _safe_version(expected_version)
                    current_list = _safe_version(current_version)
                    if current_list != expected_list:
                        return _conflict_result(
                            expected_version=expected_list,
                            current_version=current_list,
                        )

            new_payload, new_changed, policy_value = pure_core_mutator(
                dict(current_payload) if isinstance(current_payload, dict) else {}
            )

            if not new_changed:
                return _applied_result(
                    changed=False,
                    cleared=False,
                    value=policy_value,
                    current_version=_safe_version(current_version),
                    previous_version=_safe_version(current_version),
                )

            previous_version = _safe_version(current_version)

            if new_payload is None:
                try:
                    target.unlink()
                    did_clear = True
                except FileNotFoundError:
                    did_clear = False
                return _applied_result(
                    changed=did_clear,
                    cleared=did_clear,
                    value=policy_value,
                    current_version=None,
                    previous_version=previous_version,
                )

            notif_dir = _notification_dir(self._workdir)
            notif_dir.mkdir(exist_ok=True)
            atomic_write_json(
                target, new_payload, ensure_ascii=False, indent=None
            )

            new_raw = target.read_bytes()
            new_version = _version_entry(target, new_raw)
            return _applied_result(
                changed=True,
                cleared=False,
                value=policy_value,
                current_version=_safe_version(new_version),
                previous_version=previous_version,
            )


    def load_ack_refs(self) -> set[str]:
        ack_path = _ack_path(self._workdir)
        try:
            data = json.loads(ack_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {r for r in data if isinstance(r, str)}
        except (json.JSONDecodeError, OSError):
            pass
        return set()

    def update_ack_refs(
        self, pure_core_set_mutator: PureAckMutator
    ) -> UpdateAckRefsResult:
        ack_path = _ack_path(self._workdir)
        with self._interprocess_lock.held(), self._lock:
            current = self.load_ack_refs()
            refs, requested_change, value = pure_core_set_mutator(set(current))
            if not requested_change:
                return UpdateAckRefsResult(changed=False, value=value)
            if not refs:
                try:
                    ack_path.unlink()
                    changed = True
                except OSError:
                    changed = False
                return UpdateAckRefsResult(changed=changed, value=value)
            ack_path.parent.mkdir(exist_ok=True)
            atomic_write_json(
                ack_path, sorted(refs), ensure_ascii=False, indent=None
            )
            return UpdateAckRefsResult(changed=True, value=value)
