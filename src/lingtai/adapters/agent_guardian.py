"""Filesystem/process adapter for the shadow-only external agent guardian."""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import math
import os
import stat as stat_module
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from filelock import FileLock, Timeout

from lingtai.kernel.agent_guardian import (
    GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS,
    GuardianAlreadyRunning,
    GuardianLeaseUnavailable,
    LedgerSnapshot,
    LifecycleLedgerCorruption,
    LifecycleLedgerError,
    MAX_LEDGER_BYTES,
    MAX_LEDGER_RECORD_BYTES,
    MAX_LEDGER_RECORDS,
    MAX_PROCESS_ID,
    PresenceSample,
    make_lifecycle_event,
    reduce_lifecycle_events,
    stable_json,
    utc_timestamp,
    validate_lifecycle_event,
)
from lingtai.kernel.agent_presence import ManifestObservation
from lingtai.kernel.process_match import match_agent_run
from lingtai.kernel.session_stats import AGENT_RECORD_SCHEMA, AGENT_RECORD_VERSION, agent_record_path


LEDGER_RELATIVE_PATH = Path("logs/agent_lifecycle.jsonl")
LEDGER_LOCK_RELATIVE_PATH = Path("logs/.agent_lifecycle.lock")
GUARDIAN_LOCK_RELATIVE_PATH = Path("system/.agent_guardian.lock")
_LEDGER_LOCK_TIMEOUT_SECONDS = 10.0
_MAX_GUARDIAN_JSON_BYTES = 1024 * 1024
_RETURN_APPENDED_EVENT = object()


def observe_guardian_manifest(agent_dir: str | Path) -> ManifestObservation:
    """Read `.agent.json` once; any bounded read/parse uncertainty is malformed."""
    try:
        path = Path(agent_dir) / ".agent.json"
        with path.open("rb") as handle:
            if os.fstat(handle.fileno()).st_size > _MAX_GUARDIAN_JSON_BYTES:
                return ManifestObservation.malformed()
            raw = handle.read(_MAX_GUARDIAN_JSON_BYTES + 1)
        if len(raw) > _MAX_GUARDIAN_JSON_BYTES:
            return ManifestObservation.malformed()
        data = json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return ManifestObservation.absent()
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
        MemoryError,
    ):
        return ManifestObservation.malformed()
    if not isinstance(data, dict):
        return ManifestObservation.malformed()
    return ManifestObservation.valid(data)


class FilesystemLifecycleLedgerAdapter:
    """Serialized, file-and-directory-fsync'd lifecycle JSONL adapter."""

    def __init__(
        self,
        agent_dir: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        try:
            self.agent_dir = Path(agent_dir).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise LifecycleLedgerError("ledger_path_unavailable") from exc
        self.path = self.agent_dir / LEDGER_RELATIVE_PATH
        self.lock_path = self.agent_dir / LEDGER_LOCK_RELATIVE_PATH
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._lock = FileLock(str(self.lock_path))

    def _now(self) -> str:
        return utc_timestamp(self._clock())

    def _id(self) -> str:
        return self._id_factory()

    def _require_agent_dir(self) -> None:
        try:
            mode = self.agent_dir.stat().st_mode
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise LifecycleLedgerError("ledger_agent_dir_unavailable") from exc
        except OSError as exc:
            raise LifecycleLedgerError("ledger_agent_dir_unavailable") from exc
        if not stat_module.S_ISDIR(mode):
            raise LifecycleLedgerError("ledger_agent_dir_unavailable")

    def _ensure_parent(self) -> bool:
        """Create only ``logs/`` below an existing agent root and sync its link."""
        self._require_agent_dir()
        try:
            self.path.parent.mkdir()
        except FileExistsError:
            try:
                if not stat_module.S_ISDIR(self.path.parent.stat().st_mode):
                    raise LifecycleLedgerError("ledger_io_error")
            except LifecycleLedgerError:
                raise
            except OSError as exc:
                raise LifecycleLedgerError("ledger_io_error") from exc
            return False
        return True

    def _locked(self, operation, *, create_parent: bool = True):
        try:
            if create_parent:
                self._ensure_parent()
            else:
                self._require_agent_dir()
            with self._lock.acquire(timeout=_LEDGER_LOCK_TIMEOUT_SECONDS):
                if create_parent:
                    # The lock file itself lives below logs/, so mkdir must win
                    # first. Fsync the agent root only after acquiring the shared
                    # ledger lock, and repeat it on every mutation so a retry can
                    # repair a prior post-mkdir fsync failure.
                    self._fsync_directory(self.agent_dir)
                return operation()
        except Timeout as exc:
            raise LifecycleLedgerError("ledger_lock_timeout") from exc
        except LifecycleLedgerError:
            raise
        except OSError as exc:
            raise LifecycleLedgerError("ledger_io_error") from exc

    def _read_handle(self, handle) -> tuple[LedgerSnapshot, int]:
        """Read and reduce one descriptor with physical byte/count bounds."""
        try:
            size = os.fstat(handle.fileno()).st_size
        except OSError as exc:
            raise LifecycleLedgerError("ledger_unreadable") from exc
        if size > MAX_LEDGER_BYTES:
            raise LifecycleLedgerCorruption("ledger_byte_limit_exceeded")

        records: list[dict] = []
        total_bytes = 0
        try:
            handle.seek(0)
            while True:
                raw = handle.readline(MAX_LEDGER_RECORD_BYTES + 1)
                if not raw:
                    final_size = os.fstat(handle.fileno()).st_size
                    if final_size > MAX_LEDGER_BYTES:
                        raise LifecycleLedgerCorruption("ledger_byte_limit_exceeded")
                    if final_size < total_bytes:
                        raise LifecycleLedgerCorruption("ledger_changed_during_read")
                    if final_size > total_bytes:
                        handle.seek(total_bytes)
                        continue
                    break
                total_bytes += len(raw)
                if total_bytes > MAX_LEDGER_BYTES:
                    raise LifecycleLedgerCorruption("ledger_byte_limit_exceeded")
                if len(raw) > MAX_LEDGER_RECORD_BYTES:
                    raise LifecycleLedgerCorruption("ledger_record_limit_exceeded")
                if not raw.endswith(b"\n"):
                    raise LifecycleLedgerCorruption("torn_final_record")
                if len(records) >= MAX_LEDGER_RECORDS:
                    raise LifecycleLedgerCorruption("ledger_record_count_exceeded")
                try:
                    value = json.loads(raw[:-1].decode("utf-8"))
                except (UnicodeError, ValueError, RecursionError) as exc:
                    raise LifecycleLedgerCorruption("malformed_record") from exc
                records.append(validate_lifecycle_event(value))
        except LifecycleLedgerError:
            raise
        except OSError as exc:
            raise LifecycleLedgerError("ledger_unreadable") from exc
        return (
            reduce_lifecycle_events(
                records,
                expected_agent_address=self.agent_dir.name,
            ),
            total_bytes,
        )

    def _read_unlocked(self) -> LedgerSnapshot:
        try:
            with self.path.open("rb") as handle:
                snapshot, _ = self._read_handle(handle)
                return snapshot
        except FileNotFoundError:
            return LedgerSnapshot((), None, None, None, physical_record_count=0)
        except LifecycleLedgerError:
            raise
        except OSError as exc:
            raise LifecycleLedgerError("ledger_unreadable") from exc

    def read_snapshot(self) -> LedgerSnapshot:
        self._require_agent_dir()
        try:
            parent_mode = self.path.parent.stat().st_mode
        except FileNotFoundError:
            return LedgerSnapshot((), None, None, None, physical_record_count=0)
        except OSError as exc:
            raise LifecycleLedgerError("ledger_unreadable") from exc
        if not stat_module.S_ISDIR(parent_mode):
            raise LifecycleLedgerError("ledger_io_error")
        return self._locked(self._read_unlocked, create_parent=False)

    def _fsync_directory(self, directory: Path) -> None:
        if os.name == "nt":
            return  # Python cannot open a directory handle with os.open on Windows.
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _preflight_append(
        self,
        snapshot: LedgerSnapshot,
        current_bytes: int,
        event: dict,
    ) -> tuple[dict | None, bytes]:
        event = validate_lifecycle_event(event)
        if event["agent_address"] != self.agent_dir.name:
            raise LifecycleLedgerCorruption("agent_address_mismatch")
        try:
            encoded = stable_json(event).encode("utf-8") + b"\n"
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise LifecycleLedgerError("invalid_event_encoding") from exc
        for existing in snapshot.records:
            if existing["event_id"] != event["event_id"]:
                continue
            if stable_json(existing) != stable_json(event):
                raise LifecycleLedgerCorruption("event_id_conflict")
            return existing, encoded
        if snapshot.physical_record_count >= MAX_LEDGER_RECORDS:
            raise LifecycleLedgerError("ledger_record_count_exceeded")
        if len(encoded) > MAX_LEDGER_RECORD_BYTES:
            raise LifecycleLedgerError("ledger_record_limit_exceeded")
        if current_bytes + len(encoded) > MAX_LEDGER_BYTES:
            raise LifecycleLedgerError("ledger_byte_limit_exceeded")
        reduce_lifecycle_events(
            [*snapshot.records, event],
            expected_agent_address=self.agent_dir.name,
        )
        return None, encoded

    def _write_append(self, handle, encoded: bytes, *, created: bool) -> None:
        try:
            handle.seek(0, os.SEEK_END)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            if created:
                self._fsync_directory(self.path.parent)
        except OSError as exc:
            raise LifecycleLedgerError("ledger_write_failed") from exc

    def _sync_duplicate(self, handle) -> None:
        """Make a retry durable without changing its already-present bytes."""
        try:
            os.fsync(handle.fileno())
            self._fsync_directory(self.path.parent)
        except OSError as exc:
            raise LifecycleLedgerError("ledger_write_failed") from exc

    def _mutate_unlocked(self, event_factory):
        """Read/preflight/append one existing descriptor, creating only on success."""
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_BINARY", 0)
        while True:
            try:
                descriptor = os.open(self.path, flags)
            except FileNotFoundError:
                snapshot = LedgerSnapshot((), None, None, None, physical_record_count=0)
                event, result = event_factory(snapshot)
                if event is None:
                    return result
                duplicate, encoded = self._preflight_append(snapshot, 0, event)
                if duplicate is not None:
                    return duplicate
                try:
                    descriptor = os.open(
                        self.path,
                        flags | os.O_CREAT | os.O_EXCL,
                        0o666,
                    )
                except FileExistsError:
                    continue
                with os.fdopen(descriptor, "a+b") as handle:
                    snapshot, current_bytes = self._read_handle(handle)
                    duplicate, encoded = self._preflight_append(
                        snapshot,
                        current_bytes,
                        event,
                    )
                    if duplicate is not None:
                        self._sync_duplicate(handle)
                        return duplicate
                    self._write_append(handle, encoded, created=True)
                    return event if result is _RETURN_APPENDED_EVENT else result
            except OSError as exc:
                raise LifecycleLedgerError("ledger_unreadable") from exc

            try:
                with os.fdopen(descriptor, "a+b") as handle:
                    snapshot, current_bytes = self._read_handle(handle)
                    event, result = event_factory(snapshot)
                    if event is None:
                        return result
                    duplicate, encoded = self._preflight_append(
                        snapshot,
                        current_bytes,
                        event,
                    )
                    if duplicate is not None:
                        self._sync_duplicate(handle)
                        return duplicate
                    self._write_append(handle, encoded, created=False)
                    return event if result is _RETURN_APPENDED_EVENT else result
            except LifecycleLedgerError:
                raise
            except OSError as exc:
                raise LifecycleLedgerError("ledger_unreadable") from exc

    def append_event(self, event: dict) -> dict:
        event = validate_lifecycle_event(event)
        return self._locked(
            lambda: self._mutate_unlocked(
                lambda snapshot: (event, _RETURN_APPENDED_EVENT)
            )
        )

    def _event(
        self,
        kind: str,
        *,
        agent_address: str,
        actor_kind: str,
        actor_id: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> dict:
        self._require_agent_address(agent_address)
        return make_lifecycle_event(
            kind,
            event_id=self._id(),
            recorded_at=self._now(),
            agent_address=agent_address,
            actor_kind=actor_kind,
            actor_id=actor_id,
            reason=reason,
            payload=payload,
        )

    def _require_agent_address(self, agent_address: str) -> None:
        if agent_address != self.agent_dir.name:
            raise LifecycleLedgerCorruption("agent_address_mismatch")

    def register_boot(self, *, agent_address: str, working_dir: str) -> dict:
        self._require_agent_address(agent_address)
        try:
            resolved_path = Path(working_dir).resolve()
            resolved_dir = str(resolved_path)
            executable = str(Path(sys.executable).resolve())
        except (OSError, RuntimeError, ValueError) as exc:
            raise LifecycleLedgerError("boot_path_unavailable") from exc
        if resolved_path != self.agent_dir:
            raise LifecycleLedgerCorruption("boot_agent_dir_mismatch")
        pid = os.getpid()
        from lingtai.adapters.posix.process_identity import process_identity

        try:
            identity = process_identity(pid)
        except (OSError, OverflowError, TypeError, ValueError) as exc:
            raise LifecycleLedgerError("process_identity_unavailable") from exc
        if identity is None:
            raise LifecycleLedgerError("process_identity_unavailable")
        event = self._event(
            "boot_registered",
            agent_address=agent_address,
            actor_kind="runtime",
            actor_id=agent_address,
            reason="runtime_boot",
            payload={
                "runtime_id": self._id(),
                "pid": pid,
                "start_identity": identity,
                "working_dir": resolved_dir,
                "executable": executable,
                "command": {
                    "program": Path(sys.argv[0]).name or "unknown",
                    "subcommand": "run",
                    "agent_dir": resolved_dir,
                },
            },
        )

        def operation() -> dict:
            def event_factory(snapshot: LedgerSnapshot):
                if snapshot.active_intent_id is not None:
                    raise LifecycleLedgerError("explicit_suspend_active")
                return event, _RETURN_APPENDED_EVENT

            return self._mutate_unlocked(event_factory)

        return self._locked(operation)

    def request_suspend(self, *, agent_address: str, actor_id: str, reason: str) -> str:
        self._require_agent_address(agent_address)

        def operation() -> str:
            def event_factory(snapshot: LedgerSnapshot):
                if snapshot.active_intent_id is not None:
                    return None, snapshot.active_intent_id
                intent_id = self._id()
                event = self._event(
                    "suspend_requested",
                    agent_address=agent_address,
                    actor_kind="agent",
                    actor_id=actor_id,
                    reason=reason,
                    payload={"intent_id": intent_id},
                )
                return event, intent_id

            return self._mutate_unlocked(event_factory)

        return self._locked(operation)

    def _request_clear(self, kind: str, *, agent_address: str, actor_id: str, reason: str) -> str | None:
        self._require_agent_address(agent_address)

        def operation() -> str | None:
            def event_factory(snapshot: LedgerSnapshot):
                if snapshot.active_intent_id is None:
                    return None, None
                event = self._event(
                    kind,
                    agent_address=agent_address,
                    actor_kind="agent",
                    actor_id=actor_id,
                    reason=reason,
                    payload={"clears_intent_id": snapshot.active_intent_id},
                )
                return event, snapshot.active_intent_id

            return self._mutate_unlocked(event_factory)

        return self._locked(operation)

    def request_cpr(self, *, agent_address: str, actor_id: str, reason: str) -> str | None:
        return self._request_clear("cpr_requested", agent_address=agent_address, actor_id=actor_id, reason=reason)

    def append_guardian_verdict(
        self,
        *,
        agent_address: str,
        actor_id: str,
        reason: str,
        payload: Mapping[str, object],
    ) -> dict:
        event = self._event(
            "guardian_verdict",
            agent_address=agent_address,
            actor_kind="guardian",
            actor_id=actor_id,
            reason=reason,
            payload=payload,
        )

        def operation() -> dict:
            def event_factory(snapshot: LedgerSnapshot):
                actual = "active" if snapshot.active_intent_id else "none"
                if payload.get("intent") != actual:
                    raise LifecycleLedgerError("guardian_intent_changed")
                latest_runtime = (
                    snapshot.latest_boot["payload"]["runtime_id"]
                    if snapshot.latest_boot else None
                )
                if payload.get("runtime_id") != latest_runtime:
                    raise LifecycleLedgerError("guardian_boot_changed")
                return event, _RETURN_APPENDED_EVENT

            return self._mutate_unlocked(event_factory)

        return self._locked(operation)


class LocalAgentGuardianHostAdapter:
    """Local OS observations and the separate lifetime guardian lease."""

    def __init__(
        self,
        agent_dir: str | Path,
        *,
        wall_time: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        try:
            self.agent_dir = Path(agent_dir).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise GuardianLeaseUnavailable("guardian_lease_unavailable") from exc
        self._wall_time = wall_time
        self._sleeper = sleeper
        self._guardian_lock = FileLock(str(self.agent_dir / GUARDIAN_LOCK_RELATIVE_PATH))
        self._guardian_proxy = None

    def acquire_guardian_lease(self) -> None:
        try:
            (self.agent_dir / "system").mkdir(parents=True, exist_ok=True)
            self._guardian_proxy = self._guardian_lock.acquire(timeout=0)
        except Timeout as exc:
            raise GuardianAlreadyRunning("guardian_already_running") from exc
        except (OSError, OverflowError, TypeError, ValueError) as exc:
            raise GuardianLeaseUnavailable("guardian_lease_unavailable") from exc

    def release_guardian_lease(self) -> None:
        if self._guardian_proxy is not None:
            try:
                self._guardian_lock.release()
            except (OSError, OverflowError, TypeError, ValueError) as exc:
                raise GuardianLeaseUnavailable("guardian_lease_unavailable") from exc
            finally:
                self._guardian_proxy = None

    def wall_time(self) -> float:
        return self._wall_time()

    def sleep(self, seconds: float) -> None:
        self._sleeper(seconds)

    def _observe_agent_lease(self) -> str:
        path = self.agent_dir / ".agent.lock"
        try:
            path.stat()
        except FileNotFoundError:
            return "free"
        except OSError:
            return "unknown"
        try:
            handle = path.open("r+b")
        except OSError:
            return "unknown"
        try:
            if os.name == "nt":
                import msvcrt

                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    return "held" if getattr(exc, "winerror", None) in {32, 33, 36} or exc.errno == errno.EACCES else "unknown"
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                return "free"
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                return "held" if exc.errno in {errno.EACCES, errno.EAGAIN} else "unknown"
            fcntl.flock(handle, fcntl.LOCK_UN)
            return "free"
        finally:
            handle.close()

    @staticmethod
    def _pid_existence(pid: int) -> str:
        """Probe only PID existence with literal signal zero; deliver no signal."""
        try:
            os.kill(pid, 0)
        except OSError as exc:
            if exc.errno == errno.ESRCH:
                return "absent"
            if exc.errno in {errno.EPERM, errno.EACCES}:
                return "exists_inaccessible"
            return "unavailable"
        except (OverflowError, ValueError):
            return "unavailable"
        return "exists"

    @classmethod
    def _linux_observation(
        cls,
        pid: int,
        *,
        proc_root: Path = Path("/proc"),
    ) -> tuple[str | None, str | None, str | None, str | None]:
        root = proc_root / str(pid)
        try:
            if not stat_module.S_ISDIR(proc_root.stat().st_mode):
                return None, "?", None, None
            if not stat_module.S_ISDIR(root.stat().st_mode):
                return None, "?", None, None
        except FileNotFoundError:
            try:
                proc_mode = proc_root.stat().st_mode
            except OSError:
                return None, "?", None, None
            if not stat_module.S_ISDIR(proc_mode):
                return None, "?", None, None
            if cls._pid_existence(pid) == "absent":
                return None, None, None, None
            return None, "?", None, None
        except OSError:
            return None, "?", None, None
        from lingtai.adapters.posix.process_identity import _linux_process_identity

        identity_before = _linux_process_identity(pid)
        if identity_before is None:
            return None, "?", None, None
        state = command = executable = None
        try:
            stat = (root / "stat").read_text(encoding="utf-8")
            state = stat[stat.rfind(")") + 2 :].split()[0]
            command = " ".join(part.decode("utf-8", "surrogateescape") for part in (root / "cmdline").read_bytes().split(b"\0") if part)
            executable = str((root / "exe").resolve(strict=True))
        except (OSError, IndexError):
            pass
        identity_after = _linux_process_identity(pid)
        if identity_after is None:
            return identity_before, "?", None, None
        if identity_after != identity_before:
            return identity_after, "!", None, None
        return identity_before, state, command, executable

    @staticmethod
    def _darwin_info(pid: int):
        from lingtai.adapters.posix.process_identity import _DarwinProcBsdInfo

        try:
            lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib", use_errno=True)
            fn = lib.proc_pidinfo
            fn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            fn.restype = ctypes.c_int
            info = _DarwinProcBsdInfo()
            return info if fn(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) >= ctypes.sizeof(info) else None
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            return None

    @staticmethod
    def _darwin_command(pid: int) -> str | None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            sysctl = libc.sysctl
            sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
            mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
            size = ctypes.c_size_t()
            if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0 or size.value < 5:
                return None
            buf = ctypes.create_string_buffer(size.value)
            if sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
                return None
            data = bytes(buf.raw[: size.value])
            argc = int.from_bytes(data[:4], sys.byteorder, signed=True)
            pos = data.find(b"\0", 4) + 1
            while 0 < pos < len(data) and data[pos] == 0:
                pos += 1
            argv: list[str] = []
            while pos > 0 and pos < len(data) and len(argv) < argc:
                end = data.find(b"\0", pos)
                if end < 0:
                    break
                argv.append(data[pos:end].decode("utf-8", "surrogateescape"))
                pos = end + 1
            return " ".join(argv) if len(argv) == argc and argv else None
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            return None

    @classmethod
    def _darwin_observation(cls, pid: int) -> tuple[str | None, str | None, str | None, str | None]:
        from lingtai.adapters.posix.process_identity import _darwin_process_identity

        identity_before = _darwin_process_identity(pid)
        info = cls._darwin_info(pid)
        if info is None:
            if identity_before is None and cls._pid_existence(pid) == "absent":
                return None, None, None, None
            # Signal 0 proved only that the PID exists or could not be safely
            # distinguished. Exact identity still requires libproc evidence.
            return None, "?", None, None
        executable = None
        try:
            lib = ctypes.CDLL(ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib")
            buf = ctypes.create_string_buffer(4096)
            copied = lib.proc_pidpath(pid, buf, len(buf))
            if copied > 0:
                executable = os.fsdecode(buf.value)
        except (AttributeError, OSError, OverflowError, TypeError, ValueError):
            pass
        command = cls._darwin_command(pid)
        identity_after = _darwin_process_identity(pid)
        if identity_before is None or identity_after is None:
            return identity_before or identity_after, "?", None, None
        if identity_after != identity_before:
            return identity_after, "!", None, None
        return identity_before, "T" if info.pbi_status == 4 else "R", command, executable

    @staticmethod
    def _windows_observation(pid: int) -> tuple[str | None, str | None, str | None, str | None]:
        from lingtai.adapters.windows import _win32

        liveness = _win32.process_liveness(pid)
        if liveness == "absent":
            return None, None, None, None
        if liveness != "alive":
            return None, "?", None, None
        identity = _win32.process_creation_identity(pid)
        return identity, None if identity else "?", None, None

    def _process_observation(self, payload: Mapping[str, object]) -> tuple[str, str | None, bool | None, bool | None, tuple[str, ...]]:
        try:
            raw_pid = payload["pid"]
            if (
                isinstance(raw_pid, bool)
                or not isinstance(raw_pid, int)
                or not 0 < raw_pid <= MAX_PROCESS_ID
            ):
                return "unavailable", None, None, None, ("recorded_pid_invalid",)
            pid = raw_pid
            if sys.platform.startswith("linux"):
                identity, state, command, executable = self._linux_observation(pid)
            elif sys.platform == "darwin":
                identity, state, command, executable = self._darwin_observation(pid)
            elif sys.platform == "win32":
                identity, state, command, executable = self._windows_observation(pid)
            else:
                return "unavailable", None, None, None, ("platform_process_observation_unsupported",)
        except (OSError, OverflowError, TypeError, ValueError):
            return "unavailable", None, None, None, ("process_observation_unavailable",)
        if state == "?":
            return "unavailable", None, None, None, ("process_existence_or_identity_unavailable",)
        if state == "!":
            return "identity_mismatch", identity, None, None, ()
        if identity is None:
            return "absent", None, None, None, ()
        if identity != payload["start_identity"]:
            return "identity_mismatch", identity, None, None, ()
        try:
            command_match = match_agent_run(command, str(self.agent_dir)) is not None if command is not None else None
            expected_executable = str(Path(str(payload["executable"])).resolve())
            executable_match = str(Path(executable).resolve()) == expected_executable if executable else None
        except (OSError, OverflowError, TypeError, ValueError):
            return "unavailable", identity, None, None, ("process_observation_unavailable",)
        if command_match is False:
            return "command_mismatch", identity, False, executable_match, ()
        if executable_match is False:
            return "executable_mismatch", identity, command_match, False, ()
        if state is None or command_match is None or executable_match is None:
            return "unavailable", identity, command_match, executable_match, ("exact_process_state_or_command_unavailable",)
        return ("exact_stopped" if state in {"T", "t"} else "exact_running"), identity, True, True, ()

    def _manifest_observation(self) -> str:
        return observe_guardian_manifest(self.agent_dir).kind.value

    def _heartbeat_observation(self, now: float) -> tuple[str, float | None, str | None, tuple[str, ...]]:
        path = agent_record_path(self.agent_dir)
        try:
            with path.open("rb") as handle:
                if os.fstat(handle.fileno()).st_size > _MAX_GUARDIAN_JSON_BYTES:
                    return "unreadable", None, None, ("agent_record_oversized",)
                raw = handle.read(_MAX_GUARDIAN_JSON_BYTES + 1)
            if len(raw) > _MAX_GUARDIAN_JSON_BYTES:
                return "unreadable", None, None, ("agent_record_oversized",)
            record = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return "missing", None, None, ()
        except (OSError, UnicodeError, ValueError, RecursionError, MemoryError):
            return "unreadable", None, None, ("agent_record_unreadable",)
        if not isinstance(record, dict) or record.get("schema") != AGENT_RECORD_SCHEMA or record.get("schema_version") != AGENT_RECORD_VERSION:
            return "unreadable", None, None, ("agent_record_schema_invalid",)
        session = record.get("session")
        state = session.get("state") if isinstance(session, dict) else None
        if not isinstance(state, str) or state not in {
            "active", "idle", "asleep", "stuck", "suspended",
        }:
            return "unreadable", None, None, ("agent_record_state_invalid",)
        health = record.get("health")
        heartbeat = health.get("heartbeat_at") if isinstance(health, dict) else None
        if heartbeat is None:
            return "missing", None, state, ()
        if isinstance(heartbeat, bool) or not isinstance(heartbeat, (int, float)) or not math.isfinite(heartbeat):
            return "unreadable", None, state, ("heartbeat_invalid",)
        age = float(now - heartbeat)
        if not math.isfinite(age) or age < 0:
            return "unreadable", None, state, ("heartbeat_clock_contradiction",)
        freshness = "fresh" if age <= GUARDIAN_HEARTBEAT_THRESHOLD_SECONDS else "stale"
        return freshness, round(age, 3), state, ()

    def sample(self, boot_record: dict | None) -> PresenceSample:
        now = self.wall_time()
        lease = self._observe_agent_lease()
        manifest = self._manifest_observation()
        heartbeat, age, record_state, heartbeat_issues = self._heartbeat_observation(now)
        manifest_issues = () if manifest == "valid" else (f"agent_manifest_{manifest}",)
        if boot_record is None:
            return PresenceSample(now, None, None, None, None, "unavailable", lease, manifest, heartbeat, age, None, None, None, ("boot_registration_missing", *manifest_issues, *heartbeat_issues))
        payload = boot_record["payload"]
        try:
            workdir_match = Path(str(payload["working_dir"])).resolve() == self.agent_dir
        except (OSError, RuntimeError, ValueError):
            workdir_match = False
        if not workdir_match:
            return PresenceSample(now, str(payload["runtime_id"]), int(payload["pid"]), str(payload["start_identity"]), None, "unavailable", lease, manifest, heartbeat, age, None, None, False, ("boot_registration_workdir_mismatch", *manifest_issues, *heartbeat_issues))
        process, observed, command_match, executable_match, process_issues = self._process_observation(payload)
        record_issues = (
            ("agent_record_process_contradiction",)
            if record_state == "suspended" and process in {"exact_running", "exact_stopped"}
            else ()
        )
        return PresenceSample(
            now,
            str(payload["runtime_id"]),
            int(payload["pid"]),
            str(payload["start_identity"]),
            observed,
            process,
            lease,
            manifest,
            heartbeat,
            age,
            command_match,
            executable_match,
            True,
            tuple((*process_issues, *manifest_issues, *heartbeat_issues, *record_issues)),
        )
