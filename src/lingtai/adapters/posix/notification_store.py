"""POSIX adapter for ``.notification/<channel>.json`` mirrors.

It provides atomic file replacement and Store-owned mutation serialization.
"""

from __future__ import annotations

import hashlib
import json
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
        with self._lock:
            atomic_write_json(target, payload, ensure_ascii=False, indent=None)


    def clear(self, channel: str) -> bool:
        target = _channel_path(self._workdir, channel)
        with self._lock:
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

        with self._lock:
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
        with self._lock:
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
