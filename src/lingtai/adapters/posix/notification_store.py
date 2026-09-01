"""Filesystem Notification Store adapter over the established JSON layout.

Ordinary JSON channel mutations are serialized only with their own resource.
Daemon aggregate administration is separately linearized by its durable control
record; ordinary per-run appends never rebuild the aggregate report.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from lingtai.adapters.notification_store_lock import select_notification_store_lock
from lingtai.kernel._fsutil import atomic_write_json, atomic_write_text
from lingtai.kernel.notification_store import (
    AllowPredicate,
    CompareUpdateResult,
    ExpectedVersion,
    NotificationStorePort,
    PureAckMutator,
    PureCoreMutator,
    PureHookManifestMutator,
    UNCONDITIONAL,
    UpdateAckRefsResult,
    UpdateHookManifestsResult,
    _applied_result,
    _conflict_result,
)
from lingtai.kernel.notification_store._mutation_lock import (
    NotificationMutationLockPort,
    channel_mutation_scope,
    daemon_run_mutation_scope,
    exclusive_notification_mutation,
    resource_mutation_scope,
)

_LARGE_RESULT_ACK_FILE = "large_result_acks.json"
_HOOK_REGISTRY_FILE = "hooks.json"
_DOT_NOTIFICATION = ".notification"
_DAEMON_CHANNEL = "daemon"
_DAEMON_DIR = "daemon"
_DAEMON_AGGREGATE_FILENAME = "daemon.json"
_DAEMON_TOMBSTONE_FILENAME = ".tombstone"
_DAEMON_CONTROL_VERSION = 1
_DAEMON_CONTROL_SCOPE = resource_mutation_scope("daemon-control")
_ACK_SCOPE = resource_mutation_scope("ack-refs")
_HOOK_SCOPE = resource_mutation_scope("hook-registry")
_DAEMON_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class DaemonControlError(RuntimeError):
    """A corrupt daemon tombstone/control record that must be repaired loudly."""


def _notification_dir(workdir: Path) -> Path:
    return workdir / _DOT_NOTIFICATION


def _channel_path(workdir: Path, channel: str) -> Path:
    return _notification_dir(workdir) / f"{channel}.json"


def _daemon_dir(workdir: Path) -> Path:
    return _notification_dir(workdir) / _DAEMON_DIR


def _daemon_control_path(workdir: Path) -> Path:
    return _daemon_dir(workdir) / _DAEMON_TOMBSTONE_FILENAME


def _daemon_report_path(workdir: Path) -> Path:
    return _notification_dir(workdir) / _DAEMON_AGGREGATE_FILENAME


def _ack_path(workdir: Path) -> Path:
    return _notification_dir(workdir) / _LARGE_RESULT_ACK_FILE


def _hook_registry_path(workdir: Path) -> Path:
    return _notification_dir(workdir) / _HOOK_REGISTRY_FILE


def _validate_daemon_id(daemon_id: str) -> str:
    if not isinstance(daemon_id, str) or _DAEMON_ID_RE.fullmatch(daemon_id) is None:
        raise ValueError("daemon_id must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    if ".." in daemon_id:
        raise ValueError("daemon_id must not contain '..'")
    return daemon_id


def _daemon_path(workdir: Path, daemon_id: str) -> Path:
    return _daemon_dir(workdir) / f"{_validate_daemon_id(daemon_id)}.json"


def _daemon_id_from_payload(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get("daemon_id")
    if isinstance(direct, str) and direct:
        return _validate_daemon_id(direct)
    data = payload.get("data")
    direct = data.get("daemon_id") if isinstance(data, dict) else None
    if isinstance(direct, str) and direct:
        return _validate_daemon_id(direct)
    return None


def _daemon_files(workdir: Path) -> list[Path]:
    directory = _daemon_dir(workdir)
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix == ".json"),
        key=lambda path: path.name,
    )


def _daemon_events(payload: object) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    events = data.get("events") if isinstance(data, dict) else None
    return [event for event in events if isinstance(event, dict)] if isinstance(events, list) else []


def _event_tombstone_key(event: dict) -> str:
    event_id = event.get("event_id")
    if isinstance(event_id, str) and event_id:
        return "event:" + event_id
    idempotency_key = event.get("idempotency_key")
    if isinstance(idempotency_key, str) and idempotency_key:
        return "idempotency:" + idempotency_key
    material = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _valid_batch_state(value: object) -> dict:
    if not isinstance(value, dict):
        raise DaemonControlError("invalid daemon notification tombstone batch state")
    count = value.get("count")
    alarm_fired = value.get("alarm_fired")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise DaemonControlError("invalid daemon notification tombstone batch count")
    if not isinstance(alarm_fired, bool):
        raise DaemonControlError("invalid daemon notification tombstone alarm state")
    return {"count": count, "alarm_fired": alarm_fired}


def _empty_daemon_control() -> dict:
    return {
        "version": _DAEMON_CONTROL_VERSION,
        "epoch": 0,
        "cleared": {},
        "batch_state": {"count": 0, "alarm_fired": False},
        "pending": None,
    }


def _read_daemon_control(workdir: Path) -> dict:
    """Read and validate aggregate authority without ever hiding corruption."""
    path = _daemon_control_path(workdir)
    try:
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        return _empty_daemon_control()
    except (OSError, json.JSONDecodeError) as exc:
        raise DaemonControlError("daemon notification tombstone is unreadable or invalid") from exc
    if not isinstance(value, dict) or value.get("version") != _DAEMON_CONTROL_VERSION:
        raise DaemonControlError("invalid daemon notification tombstone version")
    epoch = value.get("epoch")
    cleared = value.get("cleared")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise DaemonControlError("invalid daemon notification tombstone epoch")
    if not isinstance(cleared, dict):
        raise DaemonControlError("invalid daemon notification tombstone entries")
    normalized: dict[str, dict] = {}
    for filename, entry in cleared.items():
        if not isinstance(filename, str) or not isinstance(entry, dict):
            raise DaemonControlError("invalid daemon notification tombstone entry")
        if not filename.endswith(".json"):
            raise DaemonControlError("invalid daemon notification tombstone entry")
        try:
            _validate_daemon_id(filename[:-5])
        except ValueError as exc:
            raise DaemonControlError("invalid daemon notification tombstone entry") from exc
        raw_sha256 = entry.get("raw_sha256")
        event_keys = entry.get("event_keys")
        if (
            not isinstance(raw_sha256, str)
            or len(raw_sha256) != 64
            or not isinstance(event_keys, list)
            or any(not isinstance(key, str) for key in event_keys)
        ):
            raise DaemonControlError("invalid daemon notification tombstone entry")
        normalized[filename] = {"raw_sha256": raw_sha256, "event_keys": list(event_keys)}
    pending = value.get("pending")
    normalized_pending = None
    if pending is not None:
        if not isinstance(pending, dict):
            raise DaemonControlError("invalid daemon notification tombstone pending append")
        daemon_id = pending.get("daemon_id")
        event_id = pending.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise DaemonControlError("invalid daemon notification tombstone pending event")
        try:
            normalized_daemon_id = _validate_daemon_id(daemon_id)
        except ValueError as exc:
            raise DaemonControlError("invalid daemon notification tombstone pending daemon") from exc
        normalized_pending = {
            "daemon_id": normalized_daemon_id,
            "event_id": event_id,
            "prior_batch_state": _valid_batch_state(pending.get("prior_batch_state")),
            "batch_state": _valid_batch_state(pending.get("batch_state")),
        }
    return {
        "version": _DAEMON_CONTROL_VERSION,
        "epoch": epoch,
        "cleared": normalized,
        "batch_state": _valid_batch_state(value.get("batch_state")),
        "pending": normalized_pending,
    }


def _write_daemon_control(workdir: Path, control: dict, *, fsync: bool = False) -> None:
    path = _daemon_control_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, control, ensure_ascii=False, indent=None, fsync=fsync)


def _pending_is_visible(workdir: Path, pending: dict) -> bool:
    try:
        raw = _daemon_path(workdir, pending["daemon_id"]).read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return any(event.get("event_id") == pending["event_id"] for event in _daemon_events(payload))


def _effective_batch_state(workdir: Path, control: dict) -> dict:
    pending = control.get("pending")
    if isinstance(pending, dict):
        return dict(pending["batch_state"] if _pending_is_visible(workdir, pending) else pending["prior_batch_state"])
    return dict(control["batch_state"])


def _logical_daemon_record(path: Path, physical_raw: bytes, control: dict) -> tuple[Path, bytes, dict | None, bytes] | None:
    """Apply a committed aggregate tombstone to a physical mini-file."""
    entry = control["cleared"].get(path.name)
    if not isinstance(entry, dict) or hashlib.sha256(physical_raw).hexdigest() != entry["raw_sha256"]:
        try:
            parsed = json.loads(physical_raw)
        except json.JSONDecodeError:
            return (path, physical_raw, None, physical_raw)
        return (path, physical_raw, parsed if isinstance(parsed, dict) else None, physical_raw)
    try:
        payload = json.loads(physical_raw)
    except json.JSONDecodeError:
        return (path, physical_raw, None, physical_raw)
    if not isinstance(payload, dict):
        return (path, physical_raw, None, physical_raw)
    remaining = Counter(entry["event_keys"])
    kept: list[dict] = []
    removed = False
    for event in _daemon_events(payload):
        key = _event_tombstone_key(event)
        if remaining[key] > 0:
            remaining[key] -= 1
            removed = True
        else:
            kept.append(event)
    if not removed:
        return (path, physical_raw, payload, physical_raw)
    if not kept:
        return None
    updated = dict(payload)
    data = payload.get("data")
    updated_data = dict(data) if isinstance(data, dict) else {}
    updated_data["events"] = kept
    updated["data"] = updated_data
    updated["header"] = f"{len(kept)} daemon notification{'s' if len(kept) != 1 else ''}"
    raw = json.dumps(updated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return (path, raw, updated, physical_raw)


def _read_daemon_records(workdir: Path, control: dict | None = None) -> list[tuple[Path, bytes, dict | None, bytes]]:
    control = _read_daemon_control(workdir) if control is None else control
    records: list[tuple[Path, bytes, dict | None, bytes]] = []
    for path in _daemon_files(workdir):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        logical = _logical_daemon_record(path, raw, control)
        if logical is not None:
            records.append(logical)
    return records


def _aggregate_daemon_payload(workdir: Path, records: list[tuple[Path, bytes, dict | None, bytes]], control: dict) -> dict | None:
    valid = [(path, payload) for path, _raw, payload, _physical in records if isinstance(payload, dict)]
    if not valid:
        return None
    events: list[dict] = []
    template = valid[0][1]
    for _path, payload in valid:
        events.extend(_daemon_events(payload))
    # The control record only transports Core-computed batch state.  It never
    # reads notification.json or makes an alarm decision.
    data = {"events": events, _DAEMON_CHANNEL: _effective_batch_state(workdir, control)}
    aggregate = dict(template)
    aggregate["header"] = f"{len(events)} daemon notification{'s' if len(events) != 1 else ''}"
    aggregate["data"] = data
    return aggregate


def _daemon_fingerprint(records: list[tuple[Path, bytes, dict | None, bytes]]) -> tuple[str, int, str] | None:
    if not records:
        return None
    material = b"".join(
        ("mini/" + path.name).encode("utf-8") + b"\0" + str(len(raw)).encode("ascii") + b"\0" + raw
        for path, raw, _payload, _physical in records
    )
    return (_DAEMON_AGGREGATE_FILENAME, len(material), hashlib.sha256(material).hexdigest())


def _daemon_control_error_payload() -> dict:
    """Return bounded, content-free daemon visibility when control is broken."""
    return {
        "header": "Daemon notification control error; run lingtai-doctor",
        "icon": "⚠️",
        "priority": "high",
        "data": {
            "events": [],
            _DAEMON_CHANNEL: {"count": 0, "alarm_fired": True},
            "error": {
                "code": "daemon_control_error",
                "action": "run lingtai-doctor",
            },
        },
    }


def _daemon_control_error_fingerprint() -> tuple[str, int, str]:
    raw = json.dumps(
        _daemon_control_error_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (_DAEMON_AGGREGATE_FILENAME, len(raw), hashlib.sha256(raw).hexdigest())


def _daemon_report_payload(workdir: Path, records: list[tuple[Path, bytes, dict | None, bytes]]) -> dict:
    """Build a non-authoritative compatibility report from a captured aggregate."""
    runs: list[dict] = []
    total_events = 0
    active_runs = 0
    terminal_runs = 0
    terminal_states = {"done", "failed", "cancelled", "timeout"}
    for path, _raw, payload, _physical in records:
        if not isinstance(payload, dict):
            continue
        events = _daemon_events(payload)
        data = payload.get("data")
        state = payload.get("state")
        if not isinstance(state, str) and isinstance(data, dict):
            candidate = data.get("state") or data.get("run_state")
            state = candidate if isinstance(candidate, str) else None
        if not isinstance(state, str) and events:
            candidate = events[-1].get("status")
            state = candidate if isinstance(candidate, str) else None
        total_events += len(events)
        if state in terminal_states:
            terminal_runs += 1
        elif state:
            active_runs += 1
        run = {"daemon_id": path.stem, "event_count": len(events)}
        if state:
            run["state"] = state
        runs.append(run)
    report = {"kind": "daemon_report", "version": 1, "derived": True, "stats": {"run_count": len(runs), "event_count": total_events, "active_run_count": active_runs, "terminal_run_count": terminal_runs}, "runs": runs}
    try:
        previous = json.loads(_daemon_report_path(workdir).read_bytes())
    except (OSError, json.JSONDecodeError):
        previous = None
    if isinstance(previous, dict) and previous.get("kind") == "daemon_report":
        migration = previous.get("migration")
        if isinstance(migration, dict):
            report["migration"] = migration
    elif previous is not None:
        report["migration"] = {"legacy_root": previous}
    return report


def _write_daemon_report(workdir: Path, records: list[tuple[Path, bytes, dict | None, bytes]]) -> None:
    path = _daemon_report_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, _daemon_report_payload(workdir, records), ensure_ascii=False, indent=None)


def _version_entry(path: Path, raw: bytes) -> list:
    return [path.name, len(raw), hashlib.sha256(raw).hexdigest()]


def _safe_version(entry: list | tuple | None) -> list | None:
    return list(entry) if entry is not None else None


class PosixNotificationStoreAdapter(NotificationStorePort):
    """Production filesystem adapter retaining the established JSON protocol."""

    def __init__(self, workdir: Path, mutation_lock: NotificationMutationLockPort | None = None):
        self._workdir = Path(workdir)
        self._mutation_lock = mutation_lock or select_notification_store_lock()

    @property
    def mutation_lock(self) -> NotificationMutationLockPort:
        """Return the lock Port composed with this Store instance."""
        return self._mutation_lock

    @contextlib.contextmanager
    def _exclusive_mutation(self, scopes: str | list[str]):
        with exclusive_notification_mutation(self._mutation_lock, _notification_dir(self._workdir), scopes):
            yield

    def snapshot(self, allow_channel: AllowPredicate) -> dict[str, object]:
        notif_dir = _notification_dir(self._workdir)
        if not notif_dir.is_dir():
            return {}
        out: dict[str, object] = {}
        if allow_channel(_DAEMON_CHANNEL):
            try:
                control = _read_daemon_control(self._workdir)
                aggregate = _aggregate_daemon_payload(
                    self._workdir,
                    _read_daemon_records(self._workdir, control),
                    control,
                )
            except DaemonControlError:
                # The control record is daemon-only authority. A bad record is
                # loud to the agent but cannot turn every other channel into an
                # indefinitely unstable coherent read.
                aggregate = _daemon_control_error_payload()
            if aggregate is not None:
                out[_DAEMON_CHANNEL] = aggregate
        for path in sorted(notif_dir.glob("*.json")):
            if path.name in {_LARGE_RESULT_ACK_FILE, _HOOK_REGISTRY_FILE, _DAEMON_AGGREGATE_FILENAME}:
                continue
            if not allow_channel(path.stem):
                continue
            try:
                out[path.stem] = json.loads(path.read_bytes())
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def fingerprint(self, allow_channel: AllowPredicate) -> tuple[tuple[str, int, str], ...]:
        notif_dir = _notification_dir(self._workdir)
        if not notif_dir.is_dir():
            return ()
        entries: list[tuple[str, int, str]] = []
        if allow_channel(_DAEMON_CHANNEL):
            try:
                control = _read_daemon_control(self._workdir)
                daemon_entry = _daemon_fingerprint(
                    _read_daemon_records(self._workdir, control)
                )
            except DaemonControlError:
                # Use the exact synthetic projection's bytes so the coherent
                # snapshot/fingerprint bookends describe the same loud state.
                daemon_entry = _daemon_control_error_fingerprint()
            if daemon_entry is not None:
                entries.append(daemon_entry)
        for path in notif_dir.iterdir():
            if not (path.is_file() and path.suffix == ".json"):
                continue
            if path.name in {_LARGE_RESULT_ACK_FILE, _HOOK_REGISTRY_FILE, _DAEMON_AGGREGATE_FILENAME}:
                continue
            if not allow_channel(path.stem):
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            entries.append((path.name, len(raw), hashlib.sha256(raw).hexdigest()))
        return tuple(sorted(entries))

    def publish(self, channel: str, payload: dict) -> None:
        if channel == _DAEMON_CHANNEL:
            daemon_id = _daemon_id_from_payload(payload)
            if daemon_id is None:
                raise ValueError("daemon event publish requires a daemon_id")
            target = _daemon_path(self._workdir, daemon_id)
            scopes = [daemon_run_mutation_scope(daemon_id), _DAEMON_CONTROL_SCOPE]
        else:
            target = _channel_path(self._workdir, channel)
            scopes = channel_mutation_scope(channel)
        with self._exclusive_mutation(scopes):
            if channel == _DAEMON_CHANNEL:
                # Daemon mini-file writes must not proceed around unreadable
                # aggregate authority. Read paths contain this only for
                # delivery; mutation paths remain fail closed.
                _read_daemon_control(self._workdir)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, payload, ensure_ascii=False, indent=None)

    def clear(self, channel: str) -> bool:
        if channel == _DAEMON_CHANNEL:
            return self._clear_daemon_aggregate()
        target = _channel_path(self._workdir, channel)
        with self._exclusive_mutation(channel_mutation_scope(channel)):
            try:
                target.unlink()
                return True
            except FileNotFoundError:
                return False

    def _normalise_pending_for_writer(self, control: dict) -> dict:
        """Resolve a prior append receipt under a writer lock; readers never do this."""
        if control.get("pending") is None:
            return control
        normalized = dict(control)
        normalized["batch_state"] = _effective_batch_state(self._workdir, control)
        normalized["pending"] = None
        _write_daemon_control(self._workdir, normalized)
        return normalized

    @staticmethod
    def _overlay_batch_state(payload: dict, batch_state: dict) -> dict:
        current = dict(payload)
        data = payload.get("data")
        updated = dict(data) if isinstance(data, dict) else {}
        updated[_DAEMON_CHANNEL] = dict(batch_state)
        current["data"] = updated
        return current

    def _compare_update_daemon_owner(self, owner: str, expected_version: ExpectedVersion, pure_core_mutator: PureCoreMutator) -> CompareUpdateResult:
        if expected_version is not UNCONDITIONAL:
            raise ValueError("daemon owner-scoped mutation requires UNCONDITIONAL")
        daemon_id = _validate_daemon_id(owner)
        target = _daemon_path(self._workdir, daemon_id)
        scopes = [daemon_run_mutation_scope(daemon_id), _DAEMON_CONTROL_SCOPE]
        with self._exclusive_mutation(scopes):
            control = self._normalise_pending_for_writer(_read_daemon_control(self._workdir))
            try:
                physical_raw = target.read_bytes()
            except FileNotFoundError:
                logical = None
            else:
                logical = _logical_daemon_record(target, physical_raw, control)
            current_payload = logical[2] if logical is not None and isinstance(logical[2], dict) else {}
            current_version = _version_entry(target, logical[1]) if logical is not None else None
            prior_batch = _effective_batch_state(self._workdir, control)
            new_payload, requested_change, value = pure_core_mutator(self._overlay_batch_state(current_payload, prior_batch))
            if not requested_change:
                return _applied_result(changed=False, cleared=False, value=value, current_version=_safe_version(current_version), previous_version=_safe_version(current_version))
            previous_version = _safe_version(current_version)
            if new_payload is None:
                try:
                    target.unlink()
                    changed = True
                except FileNotFoundError:
                    changed = False
                return _applied_result(changed=changed, cleared=changed, value=value, current_version=None, previous_version=previous_version)
            routed_owner = _daemon_id_from_payload(new_payload)
            if routed_owner != daemon_id:
                raise ValueError("daemon mutation owner does not match payload daemon_id")
            data = new_payload.get("data") if isinstance(new_payload, dict) else None
            next_batch = _valid_batch_state(data.get(_DAEMON_CHANNEL) if isinstance(data, dict) else None)
            event_id = value if isinstance(value, str) and value else None
            if event_id is None:
                raise ValueError("daemon owner mutation requires a stable event id policy value")
            pending = {"daemon_id": daemon_id, "event_id": event_id, "prior_batch_state": prior_batch, "batch_state": next_batch}
            pending_control = dict(control)
            pending_control["pending"] = pending
            _write_daemon_control(self._workdir, pending_control)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, new_payload, ensure_ascii=False, indent=None)
            committed = dict(pending_control)
            committed["batch_state"] = next_batch
            committed["pending"] = None
            _write_daemon_control(self._workdir, committed)
            raw = target.read_bytes()
            return _applied_result(changed=True, cleared=False, value=value, current_version=_safe_version(_version_entry(target, raw)), previous_version=previous_version)

    @staticmethod
    def _extend_control(
        control: dict,
        removed: list[tuple[Path, bytes, dict | None, bytes]],
        wanted: Counter[str],
        reset_batch: bool,
    ) -> dict:
        cleared = {
            name: {
                "raw_sha256": entry["raw_sha256"],
                "event_keys": list(entry["event_keys"]),
            }
            for name, entry in control["cleared"].items()
        }
        for path, _raw, payload, physical_raw in removed:
            keys: list[str] = []
            if isinstance(payload, dict):
                for event in _daemon_events(payload):
                    key = _event_tombstone_key(event)
                    if wanted[key] > 0:
                        wanted[key] -= 1
                        keys.append(key)
            if keys:
                cleared[path.name] = {
                    "raw_sha256": hashlib.sha256(physical_raw).hexdigest(),
                    "event_keys": keys,
                }
        return {
            "version": _DAEMON_CONTROL_VERSION,
            "epoch": control["epoch"] + 1,
            "cleared": cleared,
            "batch_state": (
                {"count": 0, "alarm_fired": False}
                if reset_batch
                else dict(control["batch_state"])
            ),
            "pending": None,
        }

    def _compact_daemon_tombstone(self) -> None:
        """Physically compact only after the durable visibility cut is committed.

        Each file is re-read under its run plus control scopes.  Do not nest a
        second native flock for the control path: independent file descriptions
        can deadlock against this process's first exclusive flock on POSIX.
        """
        with self._exclusive_mutation(_DAEMON_CONTROL_SCOPE):
            names = tuple(sorted(_read_daemon_control(self._workdir)["cleared"]))
        for filename in names:
            if not filename.endswith(".json"):
                continue
            daemon_id = filename[:-5]
            try:
                _validate_daemon_id(daemon_id)
            except ValueError:
                continue
            target = _daemon_path(self._workdir, daemon_id)
            with self._exclusive_mutation([daemon_run_mutation_scope(daemon_id), _DAEMON_CONTROL_SCOPE]):
                control = _read_daemon_control(self._workdir)
                if filename not in control["cleared"]:
                    continue
                try:
                    physical_raw = target.read_bytes()
                except FileNotFoundError:
                    physical_raw = None
                if physical_raw is None:
                    logical = None
                else:
                    logical = _logical_daemon_record(target, physical_raw, control)
                compacted = dict(control)
                cleared = dict(control["cleared"])
                if logical is None:
                    try:
                        target.unlink()
                    except FileNotFoundError:
                        pass
                    cleared.pop(filename, None)
                elif logical[1] != physical_raw and isinstance(logical[2], dict):
                    atomic_write_text(target, logical[1].decode("utf-8"), encoding="utf-8")
                    cleared.pop(filename, None)
                else:
                    # A SHA mismatch or a tombstone whose keys no longer apply
                    # is inert because its logical bytes equal the physical
                    # bytes. Drop it now so every heartbeat does not keep
                    # parsing an unbounded historical entry.
                    cleared.pop(filename, None)
                compacted["cleared"] = cleared
                _write_daemon_control(self._workdir, compacted)

    def _commit_daemon_removal(
        self,
        control: dict,
        removed: list[tuple[Path, bytes, dict | None, bytes]],
        wanted: Counter[str],
        reset_batch: bool,
    ) -> None:
        # This is the durable aggregate visibility cut. Keep hot append
        # receipts non-fsync; only a committed clear/dismiss pays this cost.
        _write_daemon_control(
            self._workdir,
            self._extend_control(control, removed, wanted, reset_batch),
            fsync=True,
        )

    def _clear_daemon_aggregate(self) -> bool:
        with self._exclusive_mutation(_DAEMON_CONTROL_SCOPE):
            control = self._normalise_pending_for_writer(_read_daemon_control(self._workdir))
            records = _read_daemon_records(self._workdir, control)
            if not records:
                return False
            self._commit_daemon_removal(
                control,
                records,
                Counter(
                    _event_tombstone_key(event)
                    for _path, _raw, payload, _physical in records
                    if isinstance(payload, dict)
                    for event in _daemon_events(payload)
                ),
                reset_batch=True,
            )
        try:
            self._compact_daemon_tombstone()
        except (DaemonControlError, OSError):
            # The visibility cut is already committed. Leave physical cleanup
            # retryable rather than turning a successful clear into a false
            # failure/retry ambiguity.
            pass
        return True

    def _compare_update_daemon_aggregate(self, expected_version: ExpectedVersion, pure_core_mutator: PureCoreMutator) -> CompareUpdateResult:
        compact = False
        with self._exclusive_mutation(_DAEMON_CONTROL_SCOPE):
            control = self._normalise_pending_for_writer(_read_daemon_control(self._workdir))
            records = _read_daemon_records(self._workdir, control)
            current_payload = _aggregate_daemon_payload(self._workdir, records, control) or {}
            current_entry = _daemon_fingerprint(records)
            current_version = _safe_version(current_entry)
            if expected_version is not UNCONDITIONAL and _safe_version(expected_version) != current_version:
                return _conflict_result(expected_version=expected_version, current_version=current_version)
            new_payload, requested_change, value = pure_core_mutator(dict(current_payload))
            if not requested_change:
                return _applied_result(changed=False, cleared=False, value=value, current_version=current_version, previous_version=current_version)
            if new_payload is None:
                removed = records
                wanted = Counter(
                    _event_tombstone_key(event)
                    for _path, _raw, payload, _physical in records
                    if isinstance(payload, dict)
                    for event in _daemon_events(payload)
                )
                reset_batch = True
            else:
                current_events = _daemon_events(current_payload)
                next_events = _daemon_events(new_payload)
                remaining = list(next_events)
                removed_events: list[dict] = []
                for event in current_events:
                    try:
                        remaining.remove(event)
                    except ValueError:
                        removed_events.append(event)
                if not removed_events:
                    raise ValueError("daemon aggregate mutation must clear or remove events")
                wanted = Counter(_event_tombstone_key(event) for event in removed_events)
                removed = []
                for record in records:
                    payload = record[2]
                    if isinstance(payload, dict) and any(wanted[_event_tombstone_key(event)] > 0 for event in _daemon_events(payload)):
                        removed.append(record)
                reset_batch = False
            changed = bool(removed)
            if changed:
                self._commit_daemon_removal(control, removed, wanted, reset_batch)
                compact = True
            # Return the logical post-cut version; compaction must not alter it.
            next_control = _read_daemon_control(self._workdir)
            next_entry = _daemon_fingerprint(_read_daemon_records(self._workdir, next_control))
            result = _applied_result(changed=changed, cleared=next_entry is None, value=value, current_version=_safe_version(next_entry), previous_version=current_version)
        if compact:
            try:
                self._compact_daemon_tombstone()
            except (DaemonControlError, OSError):
                # The logical removal and returned version were committed under
                # daemon control. Compaction remains best effort after that cut.
                pass
        return result

    def compare_update_channel(self, channel: str, expected_version: ExpectedVersion, pure_core_mutator: PureCoreMutator, *, owner: str | None = None) -> CompareUpdateResult:
        if channel == _DAEMON_CHANNEL:
            if owner is not None:
                return self._compare_update_daemon_owner(owner, expected_version, pure_core_mutator)
            return self._compare_update_daemon_aggregate(expected_version, pure_core_mutator)
        if owner is not None:
            raise ValueError("notification mutation owner is only valid for daemon")
        target = _channel_path(self._workdir, channel)
        with self._exclusive_mutation(channel_mutation_scope(channel)):
            current_payload: dict = {}
            current_version: list | None = None
            try:
                raw = target.read_bytes()
            except FileNotFoundError:
                raw = None
            if raw is not None:
                current_version = _version_entry(target, raw)
                try:
                    parsed = json.loads(raw)
                    current_payload = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    current_payload = {}
            if expected_version is not UNCONDITIONAL and _safe_version(expected_version) != _safe_version(current_version):
                return _conflict_result(expected_version=expected_version, current_version=_safe_version(current_version))
            new_payload, requested_change, value = pure_core_mutator(dict(current_payload))
            if not requested_change:
                return _applied_result(changed=False, cleared=False, value=value, current_version=_safe_version(current_version), previous_version=_safe_version(current_version))
            previous_version = _safe_version(current_version)
            if new_payload is None:
                try:
                    target.unlink()
                    cleared = True
                except FileNotFoundError:
                    cleared = False
                return _applied_result(changed=cleared, cleared=cleared, value=value, current_version=None, previous_version=previous_version)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(target, new_payload, ensure_ascii=False, indent=None)
            raw = target.read_bytes()
            return _applied_result(changed=True, cleared=False, value=value, current_version=_safe_version(_version_entry(target, raw)), previous_version=previous_version)

    def load_ack_refs(self) -> set[str]:
        try:
            data = json.loads(_ack_path(self._workdir).read_text(encoding="utf-8"))
            return {value for value in data if isinstance(value, str)} if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def update_ack_refs(self, pure_core_set_mutator: PureAckMutator) -> UpdateAckRefsResult:
        path = _ack_path(self._workdir)
        with self._exclusive_mutation(_ACK_SCOPE):
            refs, requested_change, value = pure_core_set_mutator(self.load_ack_refs())
            if not requested_change:
                return UpdateAckRefsResult(False, value)
            if not refs:
                try:
                    path.unlink()
                    changed = True
                except OSError:
                    changed = False
                return UpdateAckRefsResult(changed, value)
            path.parent.mkdir(exist_ok=True)
            atomic_write_json(path, sorted(refs), ensure_ascii=False, indent=None)
            return UpdateAckRefsResult(True, value)

    def load_hook_manifests(self) -> list[dict]:
        try:
            data = json.loads(_hook_registry_path(self._workdir).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def stat_hook_registry(self) -> tuple[int, int] | None:
        try:
            stat = _hook_registry_path(self._workdir).stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def update_hook_manifests(self, pure_core_manifest_mutator: PureHookManifestMutator) -> UpdateHookManifestsResult:
        path = _hook_registry_path(self._workdir)
        with self._exclusive_mutation(_HOOK_SCOPE):
            manifests, requested_change, value = pure_core_manifest_mutator(self.load_hook_manifests())
            if not requested_change:
                return UpdateHookManifestsResult(False, value)
            if not manifests:
                try:
                    path.unlink()
                    changed = True
                except OSError:
                    changed = False
                return UpdateHookManifestsResult(changed, value)
            path.parent.mkdir(exist_ok=True)
            atomic_write_json(path, manifests, ensure_ascii=False, indent=None)
            return UpdateHookManifestsResult(True, value)
