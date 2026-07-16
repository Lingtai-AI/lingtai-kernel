"""Durable implementation details for Bash async jobs.

This is intentionally private to the Bash capability.  The supervisor process
owns ``wait()``; managers only consume the atomically persisted state, so an
agent relaunch never has to infer an exit result from an unrelated PID.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from ._shell_dialect import ShellInvocation
from ._async_process import ProcessRef


_STATE_FILE = "state.json"
_LOCK_FILE = ".state.lock"


def state_path(job_dir: Path) -> Path:
    return job_dir / _STATE_FILE


def load_state(job_dir: Path) -> dict[str, Any] | None:
    """Return a durable job state, or ``None`` for a legacy/broken job directory."""
    try:
        value = json.loads(state_path(job_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


@contextlib.contextmanager
def _state_lock(job_dir: Path):
    """Serialize read-modify-write state transitions across managers/runner."""
    from lingtai.adapters.shell_state_lock import select_shell_state_lock
    with select_shell_state_lock().exclusive(job_dir):
        yield


def _write_state_atomic(job_dir: Path, value: dict[str, Any]) -> None:
    """Durably replace the one authoritative state document."""
    fd, temporary = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=job_dir)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(value, handle, sort_keys=True, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, state_path(job_dir))
    directory_fd = os.open(job_dir, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def write_initial_state(job_dir: Path, value: dict[str, Any]) -> None:
    """Create the first state document before a supervisor is launched."""
    with _state_lock(job_dir):
        _write_state_atomic(job_dir, value)


def update_state(
    job_dir: Path, mutate: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> dict[str, Any] | None:
    """Atomically apply ``mutate`` and return the resulting state.

    ``None`` means this is not a readable durable job.  Mutators receive the
    latest state under the same lock the independent supervisor uses.
    """
    with _state_lock(job_dir):
        current = load_state(job_dir)
        if current is None:
            return None
        updated = mutate(current)
        if updated is None:
            return current
        _write_state_atomic(job_dir, updated)
        return updated


def publish_reminder_if_claimed(
    job_dir: Path, claim_token: str, publish: Callable[[], object],
) -> bool:
    """Publish one reminder while holding its durable claim/suppression lock.

    The external sink write and the ``publishing -> published`` acknowledgement
    intentionally share the job-state lock.  A terminal consumer which wins the
    same lock writes ``suppressed`` first, so a stale timer can no longer publish
    after suppression.  A crash after the sink write but before this state write
    remains possible and is deliberately recovered as a retry; bounded sinks do
    not provide a global exactly-once acknowledgement.
    """
    with _state_lock(job_dir):
        current = load_state(job_dir)
        if current is None:
            return False
        reminder = current.get("reminder")
        if not (
            isinstance(reminder, dict)
            and reminder.get("state") == "publishing"
            and reminder.get("claim_token") == claim_token
        ):
            return False
        try:
            published = publish()
        except Exception:
            return False
        if published is False:
            return False
        reminder["state"] = "published"
        reminder["published_at"] = time.time()
        _write_state_atomic(job_dir, current)
        return True


def _lease_deadline(lease: object) -> float | None:
    if not isinstance(lease, dict):
        return None
    value = lease.get("deadline_at")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _claim_supervisor_start(
    job_dir: Path,
    start_token: str,
    supervisor_pid: int,
    supervisor_identity: str | None,
) -> bool:
    """Claim the matching unexpired lease before any command process can exist."""
    claimed = False

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal claimed
        now = time.time()
        lease = state.get("supervisor_start_lease")
        deadline = _lease_deadline(lease)
        if (
            state.get("status") in {"completed", "unrecoverable"}
            or not isinstance(lease, dict)
            or lease.get("token") != start_token
            or lease.get("state") != "pending"
        ):
            return state
        if deadline is None or now >= deadline:
            state.update({
                "status": "unrecoverable",
                "exit_status_known": False,
                "exit_code": None,
                "finished_at": now,
                "supervisor_error": "supervisor start lease expired before claim",
            })
            return state
        state["supervisor_pid"] = supervisor_pid
        if supervisor_identity is not None:
            state["supervisor_identity"] = supervisor_identity
            state["supervisor_process"] = ProcessRef(
                supervisor_pid, supervisor_identity
            ).to_dict()
        lease.update({
            "state": "claimed",
            "claimed_at": now,
            "supervisor_pid": supervisor_pid,
        })
        claimed = True
        return state

    update_state(job_dir, mutate)
    return claimed


def _mark_launch_failure(job_dir: Path, message: str) -> None:
    finished = time.time()

    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        state.update({
            "status": "unrecoverable",
            "exit_status_known": False,
            "exit_code": None,
            "finished_at": finished,
            "supervisor_error": message,
        })
        return state

    update_state(job_dir, mutate)


def _invocation_from_state(state: dict[str, Any], command: str) -> ShellInvocation:
    """Resolve durable invocation state without weakening legacy compatibility."""
    has_dialect = "shell_dialect" in state
    has_invocation = "invocation" in state
    if not has_dialect and not has_invocation:
        if os.name != "posix":
            raise ValueError(
                "legacy durable shell state has no dialect/invocation; refusing to reinterpret it on a non-POSIX host"
            )
        return ShellInvocation(script=command)
    dialect = state.get("shell_dialect")
    if not isinstance(dialect, str) or not dialect.strip():
        raise ValueError("durable state has an invalid shell_dialect")
    if not has_invocation:
        raise ValueError("durable state is missing invocation")
    invocation = ShellInvocation.from_dict(state.get("invocation"))
    if invocation is None:
        raise ValueError("durable state has an invalid invocation")
    return invocation


def _cancel_requested(job_dir: Path) -> bool:
    state = load_state(job_dir)
    return bool(state and state.get("cancel_requested_at"))


def supervise(job_dir: Path, start_token: str) -> int:
    """Claim a bounded start lease, then persist one command's terminal truth."""
    state = load_state(job_dir)
    if state is None:
        return 2
    command = state.get("command")
    cwd = state.get("cwd")
    if not isinstance(command, str) or not isinstance(cwd, str):
        _mark_launch_failure(job_dir, "durable state is missing command or cwd")
        return 2
    try:
        invocation = _invocation_from_state(state, command)
    except ValueError as exc:
        _mark_launch_failure(job_dir, str(exc))
        return 2

    lease = state.get("supervisor_start_lease")
    if state.get("status") in {"completed", "unrecoverable"} or not isinstance(lease, dict):
        return 2
    deadline = lease.get("deadline_at")
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or time.time() >= float(deadline):
        if isinstance(deadline, (int, float)) and time.time() >= float(deadline):
            _mark_launch_failure(job_dir, "supervisor start lease expired before claim")
        return 2
    from lingtai.adapters.shell_process import select_shell_async_process
    process_port = select_shell_async_process()
    supervisor_ref = process_port.identify_current_process()
    supervisor_pid = supervisor_ref.public_id if supervisor_ref else os.getpid()
    supervisor_identity = supervisor_ref.incarnation if supervisor_ref else None
    if not _claim_supervisor_start(
        job_dir, start_token, supervisor_pid, supervisor_identity
    ):
        return 2

    owned = None
    command_ref = None
    with _state_lock(job_dir):
        current = load_state(job_dir)
        if current is not None:
            lease = current.get("supervisor_start_lease")
            deadline = _lease_deadline(lease)
            valid = (
                current.get("status") not in {"completed", "unrecoverable"}
                and isinstance(lease, dict)
                and lease.get("token") == start_token
                and lease.get("state") == "claimed"
                and lease.get("supervisor_pid") == supervisor_pid
                and deadline is not None
                and time.time() < deadline
            )
            if valid:
                try:
                    command_ref, owned = process_port.spawn(
                        invocation, cwd, job_dir / "stdout.log", job_dir / "stderr.log"
                    )
                except Exception as exc:
                    current.update({
                        "status": "unrecoverable",
                        "exit_status_known": False,
                        "exit_code": None,
                        "finished_at": time.time(),
                        "supervisor_error": f"cannot start async command: {exc}",
                    })
                else:
                    started = time.time()
                    identity = command_ref.incarnation
                    current.update({
                        "status": "running", "pid": command_ref.public_id,
                        "pid_identity": identity,
                        "pid_start_time": identity,
                        "started_at": started,
                        "command_process": command_ref.to_dict(),
                    })
                    lease.update({"state": "started", "started_at": started})
                _write_state_atomic(job_dir, current)
            elif (
                current.get("status") not in {"completed", "unrecoverable"}
                and isinstance(lease, dict)
                and lease.get("token") == start_token
                and deadline is not None
                and time.time() >= deadline
            ):
                current.update({
                    "status": "unrecoverable",
                    "exit_status_known": False,
                    "exit_code": None,
                    "finished_at": time.time(),
                    "supervisor_error": "supervisor start lease expired before command spawn",
                })
                _write_state_atomic(job_dir, current)

    if owned is None:
        return 2

    try:
        completion = process_port.wait(owned, lambda: _cancel_requested(job_dir))
        returncode, cancellation_outcome = completion.exit_code, completion.cancellation_outcome
    except Exception as exc:  # pragma: no cover - defensive: wait normally cannot fail
        _mark_launch_failure(job_dir, f"supervisor wait failed: {exc}")
        return 2

    finished = time.time()

    def mark_completed(current: dict[str, Any]) -> dict[str, Any]:
        # The held Popen's exact wait status is authoritative, including after a
        # cancellation request.  Managers never synthesize a replacement result.
        current.update({
            "status": "completed",
            "exit_status_known": True,
            "exit_code": returncode,
            "finished_at": finished,
        })
        if cancellation_outcome is not None:
            current["cancellation_outcome"] = cancellation_outcome
        # Completion owns the wake-up once exact terminal truth exists.  Serialize
        # this state transition with reminder publication so a watchdog cannot
        # later claim that an already-completed job may still be running.
        reminder = current.get("reminder")
        if isinstance(reminder, dict) and reminder.get("state") in {
            "pending", "publishing", "suppressing"
        }:
            reminder.update({"state": "suppressed", "suppressed_at": finished})
            reminder.pop("claim_token", None)
            reminder.pop("suppressing_at", None)
            reminder.pop("suppressing_until", None)
        return current

    update_state(job_dir, mark_completed)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        return 2
    return supervise(Path(args[0]), args[1])


if __name__ == "__main__":  # pragma: no cover - invoked in the supervisor child
    raise SystemExit(main())
