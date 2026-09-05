#!/usr/bin/env python3
"""Suspend, rename, and resume one POSIX LingTai agent workdir."""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


HEARTBEAT_MAX_AGE = 5
DEFAULT_TIMEOUT = 30
_NAME = re.compile(r"[\w-]{1,64}\Z", re.UNICODE)
_TERMINAL_DAEMON_STATES = frozenset({"done", "failed", "cancelled", "timeout"})
_DAEMON_NON_RUN_DIRS = frozenset({".dispatch-recovery", "_task_files"})


class ChangeNameError(RuntimeError):
    pass


@dataclass(frozen=True)
class Plan:
    old: Path
    new: Path
    agent_id: str
    agent_name: str
    init: dict
    runtime: Path
    resumed_runtime: Path


def _under(value: str, root: Path) -> bool:
    return value == str(root) or value.startswith(str(root) + os.sep)


def _identity(path: Path) -> tuple[dict, str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agent_id, agent_name = data["agent_id"], data["agent_name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ChangeNameError(f"cannot read identity from {path}: {exc}") from exc
    if not isinstance(agent_id, str) or not agent_id or not isinstance(agent_name, str) or not agent_name:
        raise ChangeNameError(".agent.json must contain non-empty agent_id and agent_name")
    return data, agent_id, agent_name


def _probe(runtime: Path, cwd: Path) -> None:
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ChangeNameError(f"configured venv has no executable Python: {runtime}")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        subprocess.run(
            [str(runtime), "-c", "import lingtai"], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=3, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChangeNameError(f"configured runtime cannot import lingtai without writing: {runtime}") from exc


def _process_rows() -> list[tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True,
            timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChangeNameError(f"process scan failed; refusing to infer absence: {exc}") from exc
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise ChangeNameError("process scan returned an unparseable row")
        rows.append((int(fields[0]), fields[1]))
    return rows


def _processes(root: Path) -> list[int]:
    suffix = f" -m lingtai run {root}"
    return [pid for pid, command in _process_rows() if command.endswith(suffix)]


def _assert_no_known_path_writer(root: Path, *, agent_pids: list[int]) -> None:
    """Refuse another visible local process that still carries the old path.

    A process table cannot prove that arbitrary external writers are absent.  It
    can, however, identify a known surviving command line that still references
    this exact path.  The target agent and this helper's immediate launcher are
    expected during preflight and are excluded; every other match is unsafe.
    """
    excluded = set(agent_pids)
    excluded.update({os.getpid(), os.getppid()})
    writers = [
        (pid, command)
        for pid, command in _process_rows()
        if pid not in excluded and str(root) in command
    ]
    if writers:
        raise ChangeNameError(
            "known external writer still references the old workdir: "
            + ", ".join(str(pid) for pid, _command in writers)
        )


def _assert_no_active_daemon(root: Path) -> None:
    """Refuse unresolved or active detached daemon state without repairing it."""
    daemons = root / "daemons"
    if daemons.is_symlink():
        raise ChangeNameError("daemon state directory must not be a symlink")
    if not daemons.exists():
        return
    if not daemons.is_dir():
        raise ChangeNameError("daemon state path is not a directory")
    try:
        entries = list(daemons.iterdir())
    except OSError as exc:
        raise ChangeNameError(f"cannot inspect daemon state: {exc}") from exc
    for entry in entries:
        if entry.is_symlink():
            raise ChangeNameError("daemon state contains a symlink")
        if not entry.is_dir():
            continue
        if entry.name == ".dispatch-recovery":
            try:
                if any(entry.iterdir()):
                    raise ChangeNameError("daemon recovery marker remains; terminal state is uncertain")
            except OSError as exc:
                raise ChangeNameError(f"cannot inspect daemon recovery state: {exc}") from exc
            continue
        if entry.name in _DAEMON_NON_RUN_DIRS:
            continue
        state_path = entry / "daemon.json"
        if state_path.is_symlink() or not state_path.is_file():
            raise ChangeNameError("daemon run state is missing or unsafe")
        try:
            state_data = json.loads(state_path.read_text(encoding="utf-8"))
            state = state_data["state"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ChangeNameError("daemon run state is unreadable or malformed") from exc
        if state not in _TERMINAL_DAEMON_STATES:
            raise ChangeNameError(f"detached daemon is not terminal: {entry.name}")


def _require_no_replace_support() -> None:
    """Fail before suspend when this POSIX host lacks the native move primitive."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise ChangeNameError("cannot load the native no-replace rename primitive") from exc
    probe = b".lingtai-rename-no-replace-probe-does-not-exist"
    if sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise ChangeNameError("native no-replace rename primitive is unavailable: renameat2")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, probe, -100, probe, 1)  # RENAME_NOREPLACE; source is absent
    elif sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise ChangeNameError("native no-replace rename primitive is unavailable: renamex_np")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(probe, probe, 0x00000004)  # RENAME_EXCL; source is absent
    else:
        raise ChangeNameError(f"no no-replace rename support on {sys.platform}")
    if result and ctypes.get_errno() == errno.ENOSYS:
        raise ChangeNameError("native no-replace rename primitive is unavailable")


def _fresh(root: Path) -> bool:
    try:
        return time.time() - (root / ".agent.heartbeat").stat().st_mtime < HEARTBEAT_MAX_AGE
    except OSError:
        return False


def _try_lock(root: Path):
    try:
        stream = (root / ".agent.lock").open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except (OSError, BlockingIOError):
        try:
            stream.close()
        except UnboundLocalError:
            pass
        return None


def _lock_held(root: Path) -> bool:
    path = root / ".agent.lock"
    if not path.is_file():
        return False
    stream = _try_lock(root)
    if stream is None:
        return True
    stream.close()
    return False


def preflight(
    old_arg: str | Path,
    new_name: str,
    *,
    no_known_external_writers: bool = False,
) -> Plan:
    if os.name != "posix":
        raise ChangeNameError("POSIX only")
    _require_no_replace_support()
    if not no_known_external_writers:
        raise ChangeNameError(
            "operator must confirm that no known external writer still targets the old workdir"
        )
    old = Path(old_arg)
    if not old.is_absolute() or old.is_symlink() or not old.is_dir() or old != old.resolve():
        raise ChangeNameError("old workdir must be an existing canonical absolute directory")
    if not _NAME.fullmatch(new_name) or new_name.startswith("."):
        raise ChangeNameError("new name must be one non-dot letters/digits/underscore/hyphen segment (max 64)")
    new = old.parent / new_name
    if new == old or new.exists() or new.is_symlink():
        raise ChangeNameError(f"destination must be absent: {new}")
    for filename in (".agent.json", "init.json", ".agent.heartbeat", ".agent.lock"):
        if (old / filename).is_symlink():
            raise ChangeNameError(f"agent state file must not be a symlink: {filename}")

    manifest, agent_id, agent_name = _identity(old / ".agent.json")
    if manifest.get("address") != old.name:
        raise ChangeNameError(".agent.json address does not match the old basename")
    try:
        init = json.loads((old / "init.json").read_text(encoding="utf-8"))
        venv = init["venv_path"]
        configured_name = init["manifest"]["agent_name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ChangeNameError("v1 requires strict JSON init.json with venv_path and manifest.agent_name") from exc
    if not isinstance(venv, str) or not os.path.isabs(venv):
        raise ChangeNameError("v1 requires an absolute init.json venv_path")
    if configured_name != agent_name:
        raise ChangeNameError("init.json manifest.agent_name disagrees with .agent.json")

    runtime = Path(venv) / "bin" / "python"
    _probe(runtime, old)
    agent_pids = _processes(old)
    if not _fresh(old) or len(agent_pids) != 1 or not _lock_held(old):
        raise ChangeNameError("target is not one live agent with a fresh heartbeat and held lease")
    _assert_no_active_daemon(old)
    _assert_no_known_path_writer(old, agent_pids=agent_pids)

    if _under(venv, old):
        rebased_venv = str(new) + venv[len(str(old)):]
        init["venv_path"] = rebased_venv
    else:
        rebased_venv = venv
    return Plan(old, new, agent_id, agent_name, init, runtime, Path(rebased_venv) / "bin" / "python")


def _wait_stopped(plan: Plan, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes = _processes(plan.old)
        lock = _try_lock(plan.old)
        if not processes and not _fresh(plan.old) and lock is not None:
            return lock
        if lock is not None:
            lock.close()
        time.sleep(0.1)
    raise ChangeNameError("suspend did not release process, heartbeat, and lease before timeout")


def _rename_no_replace(old: Path, new: Path) -> None:
    _require_no_replace_support()
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renamex_np unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(old), os.fsencode(new), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renameat2 unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(old), -100, os.fsencode(new), 1)  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, f"no no-replace rename on {sys.platform}")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(new))


def _write_json(path: Path, data: dict) -> None:
    fd, temp = tempfile.mkstemp(prefix=".change-name-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def _resume(plan: Plan, timeout: float) -> int:
    _write_json(plan.new / "init.json", plan.init)
    manifest, agent_id, agent_name = _identity(plan.new / ".agent.json")
    if (agent_id, agent_name) != (plan.agent_id, plan.agent_name):
        raise ChangeNameError("identity changed during suspend; new directory retained")
    manifest["address"] = plan.new.name
    _write_json(plan.new / ".agent.json", manifest)
    (plan.new / ".suspend").unlink(missing_ok=True)

    log = plan.new / "logs" / "change-name.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log.open("a", encoding="utf-8") as stream:
        child = subprocess.Popen(
            [str(plan.resumed_runtime), "-m", "lingtai", "run", str(plan.new)],
            cwd=plan.new, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise ChangeNameError(f"resumed agent exited ({child.returncode}); inspect {log}")
        try:
            manifest, agent_id, agent_name = _identity(plan.new / ".agent.json")
            heartbeat_after_launch = (plan.new / ".agent.heartbeat").stat().st_mtime >= started
        except OSError:
            heartbeat_after_launch = False
        if (
            child.pid in _processes(plan.new)
            and _fresh(plan.new)
            and heartbeat_after_launch
            and (agent_id, agent_name, manifest.get("address"))
            == (plan.agent_id, plan.agent_name, plan.new.name)
        ):
            return child.pid
        time.sleep(0.1)
    raise ChangeNameError(f"resumed agent did not prove liveness and identity; inspect {log}")


def supervise(
    old: str | Path,
    new_name: str,
    timeout: float,
    *,
    no_known_external_writers: bool = False,
) -> int:
    renamed = False
    try:
        plan = preflight(
            old,
            new_name,
            no_known_external_writers=no_known_external_writers,
        )
        (plan.old / ".suspend").touch()
        lease = _wait_stopped(plan, timeout)
        try:
            _rename_no_replace(plan.old, plan.new)
            renamed = True
        finally:
            lease.close()
        pid = _resume(plan, timeout)
        print(f"name change complete: {plan.old} -> {plan.new}; resumed pid {pid}")
        return 0
    except (ChangeNameError, OSError) as exc:
        state = "new directory retained; repair it there" if renamed else "old directory remains; restart it if suspend completed, then retry"
        print(f"name change failed: {exc}\n{state}", file=sys.stderr)
        return 1


def handoff(
    old: str,
    new_name: str,
    timeout: float,
    *,
    no_known_external_writers: bool = False,
) -> int:
    try:
        plan = preflight(
            old,
            new_name,
            no_known_external_writers=no_known_external_writers,
        )
        log = plan.old / "logs" / "change-name.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            child = subprocess.Popen(
                [
                    str(plan.runtime), str(Path(__file__).resolve()), "--_supervise",
                    str(plan.old), new_name, "--timeout", str(timeout),
                    "--no-known-external-writers",
                ],
                cwd=plan.old, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print(f"rename supervisor started ({child.pid}); log moves to {plan.new / 'logs' / 'change-name.log'}")
        return 0
    except (ChangeNameError, OSError) as exc:
        print(f"name change did not start: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_dir")
    parser.add_argument("new_basename")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument(
        "--no-known-external-writers",
        action="store_true",
        help=(
            "confirm that separately launched MCP/LICC or other external writers "
            "for the old workdir have been stopped"
        ),
    )
    parser.add_argument("--_supervise", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.foreground or args._supervise:
        return supervise(
            args.old_dir,
            args.new_basename,
            args.timeout,
            no_known_external_writers=args.no_known_external_writers,
        )
    return handoff(
        args.old_dir,
        args.new_basename,
        args.timeout,
        no_known_external_writers=args.no_known_external_writers,
    )


if __name__ == "__main__":
    raise SystemExit(main())
