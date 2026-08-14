"""Thin resident POSIX manager for queued detached daemon runs.

The manager is deliberately narrower than the daemon tool: it owns only
filesystem intake, a durable run journal, execution-child spawning through the
existing supervisor runtime helpers, timeout/control polling, terminal truth,
and notification publishing. The default daemon path never imports or starts
this module unless the caller explicitly enables a manager pool.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from lingtai.kernel._fsutil import atomic_write_json, read_json
from lingtai.kernel.daemon_supervisor import (
    DaemonSupervisorRequest,
    decode_request,
    encode_request,
)


MANAGER_DIR = Path("daemon") / "manager"
ENTRYPOINT_MODULE = "lingtai.adapters.posix.daemon_manager_entrypoint"
_POLL_INTERVAL_S = 0.05
_PID_STALE_AFTER_S = 2.0


def _manager_dir(agent_working_dir: Path) -> Path:
    return Path(agent_working_dir) / MANAGER_DIR


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload, ensure_ascii=False, indent=2)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _manager_env() -> dict[str, str]:
    env = dict(os.environ)
    parts = [str(_source_root())]
    parts.extend(p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def _ensure_manager(agent_working_dir: Path, *, pool_size: int) -> None:
    root = _manager_dir(agent_working_dir)
    _private_dir(root)
    pid_path = root / "manager.pid"
    try:
        info = read_json(pid_path, default={}, expect=dict)
    except TypeError:
        info = {}
    pid = info.get("pid") if isinstance(info, dict) else None
    started_at = info.get("started_at") if isinstance(info, dict) else None
    start_identity = info.get("manager_start_identity") if isinstance(info, dict) else None
    if (
        isinstance(pid, int)
        and not isinstance(pid, bool)
        and _pid_alive(pid)
        and _process_identity_matches(pid, start_identity)
    ):
        return
    if isinstance(started_at, (int, float)) and time.time() - started_at < _PID_STALE_AFTER_S:
        return
    token = uuid.uuid4().hex
    _write_private_json(
        pid_path,
        {
            "pid": None,
            "started_at": time.time(),
            "pool_size": pool_size,
            "state": "starting",
            "manager_token": token,
        },
    )
    env = _manager_env()
    env["LINGTAI_DAEMON_MANAGER_TOKEN"] = token
    subprocess.Popen(
        [sys.executable, "-m", ENTRYPOINT_MODULE, str(agent_working_dir), str(pool_size)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=str(agent_working_dir),
        start_new_session=True,
        close_fds=True,
    )


def enqueue_manager_run(
    *,
    agent_working_dir: Path,
    request: DaemonSupervisorRequest,
    capsule: dict | None,
    pool_size: int,
) -> None:
    """Durably enqueue one run and lazily start the resident manager."""
    if os.name != "posix":
        raise NotImplementedError("central daemon manager is POSIX-only")
    if pool_size <= 0:
        raise ValueError("central daemon manager requires a positive pool size")
    root = _manager_dir(Path(agent_working_dir))
    queue_dir = root / "queue"
    _private_dir(queue_dir)
    _private_dir(root / "journal")
    payload = {
        "schema": "lingtai.daemon_manager_job.v1",
        "run_id": request.run_id,
        "request": encode_request(request),
        "capsule": capsule or {},
        "enqueued_at": time.time(),
    }
    _write_private_json(queue_dir / f"{request.run_id}.json", payload)
    _ensure_manager(Path(agent_working_dir), pool_size=pool_size)


def run_manager(agent_working_dir: Path, *, pool_size: int) -> None:
    """Run the resident queue manager until idle for a short grace period."""
    if pool_size <= 0:
        raise ValueError("pool_size must be positive")
    root = _manager_dir(Path(agent_working_dir))
    queue_dir = root / "queue"
    journal_dir = root / "journal"
    for path in (root, queue_dir, journal_dir):
        _private_dir(path)
    _write_private_json(
        root / "manager.pid",
        {
            "pid": os.getpid(),
            "started_at": time.time(),
            "pool_size": pool_size,
            "state": "running",
            "manager_start_identity": _process_identity(os.getpid()),
            "manager_token": os.environ.get("LINGTAI_DAEMON_MANAGER_TOKEN"),
        },
    )
    manager = _DaemonManagerProcess(queue_dir, journal_dir, pool_size=pool_size)
    manager.recover_interrupted_active_runs()
    manager.run(idle_exit_s=None)


class _DaemonManagerProcess:
    def __init__(self, queue_dir: Path, journal_dir: Path, *, pool_size: int) -> None:
        self.queue_dir = queue_dir
        self.journal_dir = journal_dir
        self.pool_size = pool_size
        self.active: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self.last_activity = time.monotonic()

    def recover_interrupted_active_runs(self) -> None:
        """Mark manager-owned active runs from a prior crash failed."""
        for path in sorted(self.journal_dir.glob("*.json")):
            record = read_json(path, default={}, expect=dict)
            if not isinstance(record, dict) or record.get("state") != "active":
                continue
            run_id = str(record.get("run_id") or path.stem)
            try:
                request = decode_request(str(record["request"]))
                manifest = _read_manifest_for_request(request)
                run_dir = _attach_run_dir(manifest)
                state = run_dir.read_state_from_disk(run_dir.path)
                if state.get("state") not in {"done", "failed", "cancelled", "timeout"}:
                    run_dir.update_state(
                        owner="manager",
                        manager_recovered_by=os.getpid(),
                    )
                    _terminate_exact_children(run_dir)
                    run_dir.mark_failed(RuntimeError(
                        "central daemon manager recovered an active journal "
                        "entry after manager restart; prior manager died before "
                        "committing terminal truth"
                    ))
                _publish_terminal(run_dir, manifest)
                self._journal(run_id, "failed_recovered", request, record.get("capsule") or {})
            except Exception as exc:
                self._journal_raw(path.stem, {
                    "state": "recovery_error",
                    "run_id": run_id,
                    "source": str(path),
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                    "updated_at": time.time(),
                })

    def run(self, *, idle_exit_s: float | None = 2.0) -> None:
        idle_since: float | None = None
        while True:
            self._reap_finished_threads()
            self._start_queued_jobs()
            if self.active or list(self.queue_dir.glob("*.json")):
                idle_since = None
                self.last_activity = time.monotonic()
            else:
                if idle_exit_s is None:
                    time.sleep(_POLL_INTERVAL_S)
                    continue
                if idle_since is None:
                    idle_since = time.monotonic()
                elif time.monotonic() - idle_since > idle_exit_s:
                    return
            time.sleep(_POLL_INTERVAL_S)

    def _reap_finished_threads(self) -> None:
        with self.lock:
            finished = [run_id for run_id, thread in self.active.items() if not thread.is_alive()]
            for run_id in finished:
                self.active.pop(run_id, None)

    def _start_queued_jobs(self) -> None:
        with self.lock:
            capacity = self.pool_size - len(self.active)
        if capacity <= 0:
            return
        jobs = sorted(
            self.queue_dir.glob("*.json"),
            key=lambda path: (*self._queue_sort_key(path), path.name),
        )
        for job_path in jobs[:capacity]:
            job = read_json(job_path, default={}, expect=dict)
            if not isinstance(job, dict):
                self._quarantine_malformed_job(
                    job_path,
                    ValueError("manager queue job JSON must be an object"),
                )
                continue
            try:
                if "request" not in job:
                    raise ValueError("manager queue job missing request")
                request = decode_request(str(job["request"]))
            except Exception as exc:
                self._quarantine_malformed_job(job_path, exc)
                continue
            capsule = job.get("capsule") if isinstance(job.get("capsule"), dict) else {}
            try:
                job_path.unlink()
            except OSError:
                continue
            self._journal(request.run_id, "active", request, capsule)
            thread = threading.Thread(
                target=self._run_job,
                args=(request, capsule),
                name=f"daemon-manager-{request.run_id}",
                daemon=True,
            )
            with self.lock:
                self.active[request.run_id] = thread
            thread.start()

    def _queue_sort_key(self, job_path: Path) -> tuple[float, str]:
        try:
            job = read_json(job_path, default={}, expect=dict)
        except Exception:
            return (float("inf"), job_path.name)
        enqueued_at = job.get("enqueued_at") if isinstance(job, dict) else None
        if isinstance(enqueued_at, (int, float)) and not isinstance(enqueued_at, bool):
            return (float(enqueued_at), job_path.name)
        return (float("inf"), job_path.name)

    def _quarantine_malformed_job(self, job_path: Path, exc: Exception) -> None:
        self._journal_raw(job_path.stem, {
            "schema": "lingtai.daemon_manager_journal.v1",
            "run_id": job_path.stem,
            "state": "failed_malformed_queue_job",
            "source": str(job_path),
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
            "manager_pid": os.getpid(),
            "updated_at": time.time(),
        })
        try:
            job_path.unlink()
        except OSError:
            pass

    def _run_job(self, request: DaemonSupervisorRequest, capsule: dict) -> None:
        try:
            manifest = _read_manifest_for_request(request)
            run_dir = _attach_run_dir(manifest)
            if request.run_id != manifest.get("run_id"):
                run_dir.update_state(owner="manager", supervisor_pid=os.getpid())
                run_dir.mark_failed(ValueError("manager request/manifest run_id mismatch"))
                _publish_terminal(run_dir, manifest)
                return
            run_dir.update_state(
                owner="manager",
                supervisor_pid=os.getpid(),
                supervisor_start_identity=_process_identity(os.getpid()),
                supervisor_manifest_path=str(Path(request.manifest_path).resolve()),
                manager_pid=os.getpid(),
            )
            _run_one_emanation(run_dir, manifest, capsule)
        except Exception as exc:
            try:
                manifest = _read_manifest_for_request(request)
                run_dir = _attach_run_dir(manifest)
                run_dir.mark_failed(exc)
                _publish_terminal(run_dir, manifest)
            except Exception:
                pass
        finally:
            try:
                manifest = _read_manifest_for_request(request)
                run_dir = _attach_run_dir(manifest)
                _publish_terminal(run_dir, manifest)
            except Exception:
                pass
            self._journal(request.run_id, "terminal", request, capsule)

    def _journal(
        self,
        run_id: str,
        state: str,
        request: DaemonSupervisorRequest,
        capsule: dict,
    ) -> None:
        payload = {
            "schema": "lingtai.daemon_manager_journal.v1",
            "run_id": run_id,
            "request": encode_request(request),
            "state": state,
            "manager_pid": os.getpid(),
            "updated_at": time.time(),
        }
        if state == "active":
            payload["capsule"] = capsule
        else:
            payload["capsule_scrubbed"] = True
        self._journal_raw(run_id, payload)

    def _journal_raw(self, run_id: str, payload: dict[str, Any]) -> None:
        _write_private_json(self.journal_dir / f"{run_id}.json", payload)


def _read_manifest_for_request(request: DaemonSupervisorRequest) -> dict:
    from lingtai.kernel.daemon_supervisor.manifest import read_manifest

    return read_manifest(Path(request.manifest_path))


def _attach_run_dir(manifest: dict):
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    return DaemonRunDir.attach(Path(manifest["run_dir"]))


def _process_identity(pid: int) -> str | None:
    from lingtai.adapters.posix.process_identity import process_identity

    return process_identity(pid)


def _process_identity_matches(pid: int, saved_identity: object) -> bool:
    from lingtai.adapters.posix.process_identity import process_identity_matches

    return process_identity_matches(pid, saved_identity)


def _run_one_emanation(run_dir, manifest: dict, capsule: dict) -> None:
    from lingtai.tools.daemon.supervisor_runtime import _run_one_emanation

    _run_one_emanation(run_dir, manifest, capsule)


def _publish_terminal(run_dir, manifest: dict) -> None:
    from lingtai.tools.daemon.supervisor_runtime import _publish_terminal_notification_if_needed

    _publish_terminal_notification_if_needed(run_dir, manifest)


def _terminate_exact_children(run_dir) -> None:
    from lingtai.tools.daemon.supervisor_runtime import _terminate_exact_run_children

    _terminate_exact_run_children(run_dir, owned_procs=())
