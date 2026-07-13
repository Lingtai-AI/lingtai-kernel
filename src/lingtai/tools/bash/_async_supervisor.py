"""Durable implementation details for Bash async jobs.

This is intentionally private to the Bash capability.  The supervisor process
owns ``wait()``; managers only consume the atomically persisted state, so an
agent relaunch never has to infer an exit result from an unrelated PID.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

try:  # Bash async jobs are POSIX-only because cancellation is process-group based.
    import fcntl
except ImportError:  # pragma: no cover - asserted by the Bash POSIX contract
    fcntl = None  # type: ignore[assignment]


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
    if fcntl is None:
        raise RuntimeError("Bash async supervisor requires POSIX file locking")
    lock = open(job_dir / _LOCK_FILE, "a", encoding="utf-8")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


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


def _linux_process_identity(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = raw.rfind(")")
        # Fields after the final ')' begin at process-stat field 3.  starttime is
        # field 22, hence index 19 in this suffix.
        fields = raw[end + 2 :].split()
        start_ticks = fields[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
        return f"linux:{boot_id}:{start_ticks}"
    except (OSError, IndexError, ValueError):
        return None


def _ps_process_identity(pid: int) -> str | None:
    """Fallback for Darwin/BSD, whose procfs form is unavailable.

    ``lstart`` is the operating system's process start timestamp; including the
    parent PID makes an accidental same-second PID reuse even less plausible.
    If it cannot be read, managers deliberately refuse to signal that PID.
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    return f"ps:{line}" if result.returncode == 0 and line else None


def process_identity(pid: int) -> str | None:
    """Capture an OS start-time identity for a live PID, never PID alone."""
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return _linux_process_identity(pid) or _ps_process_identity(pid)


def process_identity_matches(pid: int, saved_identity: object) -> bool:
    """True only when the PID is still the recorded process incarnation."""
    return isinstance(saved_identity, str) and process_identity(pid) == saved_identity


def process_is_alive(pid: int) -> bool:
    """Whether a process can still make progress rather than merely occupy a PID.

    Linux zombies still answer ``kill(pid, 0)`` but cannot write a terminal state;
    treating them as alive would make a dead detached supervisor appear able to
    commit forever.  Other POSIX systems retain the conservative ``kill`` check.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # Permission or observation failures prove neither death nor PID absence.
        return True
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        end = raw.rfind(")")
        return raw[end + 2 :].split(maxsplit=1)[0] != "Z"
    except (OSError, IndexError, ValueError):
        return True


def process_group_exists(process_group: int) -> bool:
    """Conservatively report whether the owned group has a live (non-zombie) member."""
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin reports EPERM for a group containing only an unreaped zombie;
        # fall through to the member-state proof below.
        pass
    except OSError:
        # Other observation failures must never become false cancellation success.
        return True

    proc_root = Path("/proc")
    if proc_root.is_dir():
        try:
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                raw = (entry / "stat").read_text(encoding="utf-8")
                end = raw.rfind(")")
                fields = raw[end + 2 :].split()
                if int(fields[2]) == process_group and fields[0] != "Z":
                    return True
            return False
        except (OSError, IndexError, ValueError):
            return True

    try:
        result = subprocess.run(
            ["ps", "-axo", "pgid=,state="],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2 and fields[0] == str(process_group):
            if not fields[1].lstrip().startswith("Z"):
                return True
    return False


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


_CANCEL_POLL_SECONDS = 0.05
_CANCEL_TERM_GRACE_SECONDS = 0.5
_CANCEL_GROUP_QUIESCE_SECONDS = 1.0


def _cancel_requested(job_dir: Path) -> bool:
    state = load_state(job_dir)
    return bool(state and state.get("cancel_requested_at"))


def _wait_with_cancellation(
    job_dir: Path, proc: subprocess.Popen,
) -> tuple[int, str | None]:
    """Return the direct wait status plus proof of any whole-group cancellation.

    Once TERM is sent, the direct shell remains unreaped for the entire grace.  Its
    PID therefore cannot be recycled while KILL targets the original process group,
    even if that shell exits promptly and an ignored-TERM descendant survives.
    """
    while True:
        if _cancel_requested(job_dir):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return proc.wait(), "natural_or_concurrent"
            except OSError:
                return proc.wait(), "unconfirmed"

            grace_deadline = time.monotonic() + _CANCEL_TERM_GRACE_SECONDS
            while True:
                remaining = grace_deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(_CANCEL_POLL_SECONDS, remaining))

            group_absent = False
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                group_absent = True
            except OSError:
                # Darwin can return EPERM when only already-dead/zombie members
                # remain.  Confirm live-member absence before accepting that as
                # quiescence; any live member keeps cancellation unconfirmed.
                group_absent = not process_group_exists(proc.pid)
                if not group_absent:
                    return proc.wait(), "unconfirmed"

            returncode = proc.wait()
            if not group_absent:
                quiesce_deadline = time.monotonic() + _CANCEL_GROUP_QUIESCE_SECONDS
                while process_group_exists(proc.pid):
                    if time.monotonic() >= quiesce_deadline:
                        return returncode, "unconfirmed"
                    time.sleep(_CANCEL_POLL_SECONDS)
            return returncode, "group_cancelled"
        try:
            return proc.wait(timeout=_CANCEL_POLL_SECONDS), None
        except subprocess.TimeoutExpired:
            continue


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

    supervisor_pid = os.getpid()
    supervisor_identity = process_identity(supervisor_pid)
    if not _claim_supervisor_start(
        job_dir, start_token, supervisor_pid, supervisor_identity
    ):
        return 2

    try:
        stdout = open(job_dir / "stdout.log", "x", encoding="utf-8")
        stderr = open(job_dir / "stderr.log", "x", encoding="utf-8")
    except OSError as exc:
        _mark_launch_failure(job_dir, f"cannot open async log: {exc}")
        return 2

    proc: subprocess.Popen | None = None
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
                    proc = subprocess.Popen(
                        command,
                        shell=True,
                        stdout=stdout,
                        stderr=stderr,
                        cwd=cwd,
                        start_new_session=True,
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
                    identity = process_identity(proc.pid)
                    current.update({
                        "status": "running",
                        "pid": proc.pid,
                        "pid_identity": identity,
                        "pid_start_time": identity,
                        "started_at": started,
                        "process_group": proc.pid,
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

    if proc is None:
        stdout.close()
        stderr.close()
        return 2

    try:
        returncode, cancellation_outcome = _wait_with_cancellation(job_dir, proc)
    except Exception as exc:  # pragma: no cover - defensive: wait normally cannot fail
        stdout.close()
        stderr.close()
        _mark_launch_failure(job_dir, f"supervisor wait failed: {exc}")
        return 2
    stdout.close()
    stderr.close()

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
