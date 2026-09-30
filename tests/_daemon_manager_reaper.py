"""Find and stop resident daemon-manager processes that tests left behind.

The POSIX central daemon manager (``lingtai.adapters.posix.daemon_manager``)
is launched with ``start_new_session=True`` and stays resident while idle: in
production it deliberately outlives the agent process that spawned it. A test
that drives a real ``emanate`` through the default ``manager_pool_size``
therefore leaves one live manager per agent directory unless something stops
it, and a finished pytest process cannot take it down with it.

A manager is identified *only* by its exact launch argv::

    <python> -m lingtai.adapters.posix.daemon_manager_entrypoint <agent-dir> <pool>

owned by the current uid, and it is selected *only* when ``<agent-dir>`` lies
under a root the caller names (a test's ``tmp_path`` or this session's pytest
basetemp), or under a numbered pytest session directory whose ``.lock`` proves
that session's pytest process is dead. Nothing here matches a substring of an
arbitrary command line, so shells, editors, and real LingTai agents (whose
agent directories are never under a pytest temp root) are never touched.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Iterable, NamedTuple

ENTRYPOINT_MODULE = "lingtai.adapters.posix.daemon_manager_entrypoint"
_ARGV_MARKER = f" -m {ENTRYPOINT_MODULE} "
_MANAGER_LOCK_PARTS = ("daemon", "manager", "manager.lock")
_PYTEST_SESSION_DIR = re.compile(r"^(?P<session>/.*/pytest-of-[^/]+/pytest-\d+)(?:/|$)")


class ManagerProcess(NamedTuple):
    pid: int
    pgid: int
    agent_dir: str


def parse_manager_command(command: str) -> tuple[str, int] | None:
    """Return ``(agent_dir, pool_size)`` when ``command`` is a manager launch.

    The text before ``-m`` must be a bare Python executable path (no flags,
    so ``sh -c ...`` or ``python -c ...`` wrappers never qualify), and the text
    after the module must be exactly an absolute directory and a pool size.
    """
    head, sep, tail = command.partition(_ARGV_MARKER)
    if not sep or " -" in head or not Path(head).name.lower().startswith("python"):
        return None
    agent_dir, _, pool = tail.strip().rpartition(" ")
    if not agent_dir.startswith("/") or not pool.isdigit():
        return None
    return agent_dir, int(pool)


def daemon_manager_state_present(root: Path) -> bool:
    """Cheap gate: whether any ``daemon/manager/manager.lock`` exists under root.

    ``_ensure_manager`` creates that lock file before it can spawn a manager,
    so a tree without one never started a manager and needs no process scan.
    Any error walking the tree answers ``True`` so the caller scans instead.
    """
    try:
        for path in Path(root).rglob(_MANAGER_LOCK_PARTS[-1]):
            if path.parts[-3:] == _MANAGER_LOCK_PARTS:
                return True
    except OSError:
        return True
    return False


def find_daemon_managers(
    *,
    under: Iterable[Path] = (),
    dead_pytest_sessions: bool = False,
) -> list[ManagerProcess]:
    """List this uid's daemon managers whose agent dir matches the filters."""
    if os.name != "posix":
        return []
    roots = _root_texts(under)
    if not roots and not dead_pytest_sessions:
        return []
    found = []
    uid = os.getuid()
    for pid, pgid, proc_uid, command in _process_table():
        if proc_uid != uid or pid == os.getpid():
            continue
        parsed = parse_manager_command(command)
        if parsed is None:
            continue
        agent_dir = parsed[0]
        if any(_is_within(agent_dir, root) for root in roots) or (
            dead_pytest_sessions and _pytest_session_is_dead(agent_dir)
        ):
            found.append(ManagerProcess(pid, pgid, agent_dir))
    return found


def reap_daemon_managers(
    processes: Iterable[ManagerProcess], *, grace_s: float = 5.0
) -> list[int]:
    """SIGTERM each manager's own process group, then SIGKILL any survivor.

    The manager is a session leader, so its group holds exactly the manager
    and the execution children it started; the caller's own group is never
    signalled. A survivor is re-identified by its argv before SIGKILL so a
    recycled pid is never escalated against. Returns the pids signalled.
    """
    if os.name != "posix":
        return []
    own_group = os.getpgrp()
    targets = []
    for proc in processes:
        group = proc.pgid if proc.pgid == proc.pid and proc.pgid != own_group else None
        _signal(proc.pid, group, signal.SIGTERM)
        targets.append((proc, group))
    deadline = time.monotonic() + grace_s
    pending = list(targets)
    while pending:
        pending = [(proc, group) for proc, group in pending if not process_exited(proc.pid)]
        if not pending or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    if pending:
        still_managers = {
            (pid, parsed[0])
            for pid, _pgid, _uid, command in _process_table()
            if (parsed := parse_manager_command(command)) is not None
        }
        for proc, group in pending:
            if (proc.pid, proc.agent_dir) not in still_managers:
                continue
            _signal(proc.pid, group, signal.SIGKILL)
            kill_deadline = time.monotonic() + 2.0
            while not process_exited(proc.pid) and time.monotonic() < kill_deadline:
                time.sleep(0.05)
    return [proc.pid for proc, _group in targets]


def reap_daemon_managers_under(root: Path, *, grace_s: float = 5.0) -> list[int]:
    """Stop every daemon manager whose agent dir lies under ``root``."""
    return reap_daemon_managers(find_daemon_managers(under=[root]), grace_s=grace_s)


def _process_table() -> list[tuple[int, int, int, str]]:
    try:
        raw = subprocess.run(
            ["ps", "-A", "-ww", "-o", "pid=", "-o", "pgid=", "-o", "uid=", "-o", "command="],
            capture_output=True,
            check=False,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in os.fsdecode(raw).splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4 or not all(part.isdigit() for part in parts[:3]):
            continue
        rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3]))
    return rows


def _root_texts(roots: Iterable[Path]) -> list[str]:
    texts: set[str] = set()
    for root in roots:
        for variant in (str(root), os.path.abspath(root), os.path.realpath(root)):
            if variant.startswith("/") and variant != "/":
                texts.add(variant.rstrip("/"))
    return sorted(texts)


def _is_within(path_text: str, root_text: str) -> bool:
    return path_text == root_text or path_text.startswith(root_text + "/")


def _pytest_session_is_dead(agent_dir: str) -> bool:
    """Whether ``agent_dir``'s numbered pytest session proves its owner dead.

    pytest writes its pid into ``pytest-of-<user>/pytest-<N>/.lock`` for the
    session's lifetime and removes it on a normal exit. Only a lock naming a
    pid that no longer exists is proof (a killed or crashed session); a
    missing, unreadable, or live-pid lock leaves the process alone.
    """
    match = _PYTEST_SESSION_DIR.match(agent_dir)
    if match is None:
        return False
    try:
        text = (Path(match["session"]) / ".lock").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return False
    if not text.isdigit():
        return False
    try:
        os.kill(int(text), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _signal(pid: int, group: int | None, signum: int) -> None:
    try:
        if group is not None:
            os.killpg(group, signum)
        else:
            os.kill(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def process_exited(pid: int) -> bool:
    """Whether ``pid`` is gone, reaping it first when it is our own child."""
    try:
        reaped, _status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        return reaped == pid
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


__all__ = [
    "ENTRYPOINT_MODULE",
    "ManagerProcess",
    "daemon_manager_state_present",
    "find_daemon_managers",
    "parse_manager_command",
    "process_exited",
    "reap_daemon_managers",
    "reap_daemon_managers_under",
]
