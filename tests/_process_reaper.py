"""Find and stop long-lived LingTai processes that tests left behind.

Two process kinds deliberately outlive whatever launched them, so a test that
starts one and then fails (or is interrupted) leaves it running after pytest
exits:

* the POSIX central daemon manager (``lingtai.adapters.posix.daemon_manager``),
  launched with ``start_new_session=True`` and resident while idle, because in
  production it outlives its agent; and
* a ``lingtai run`` agent host, which runs until it is suspended.

A process is identified *only* by its exact launch command line, owned by the
current uid, and selected *only* when the agent directory in that command line
lies under a root the caller names (a test's ``tmp_path`` or this session's
pytest basetemp), or under a numbered pytest session directory whose ``.lock``
proves that session's pytest process is dead. The recognized shapes are::

    <python> -m lingtai.adapters.posix.daemon_manager_entrypoint <agent-dir> <pool>
    <python> ... -m lingtai run <agent-dir>

The second is the same module form ``lingtai.kernel.process_match`` treats as
an agent run, anchored at the end of the command line. Nothing here matches a
substring of an arbitrary command line, so shells, editors, and real LingTai
agents (whose directories are never under a pytest temp root) are never
touched.
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
DAEMON_MANAGER = "daemon-manager"
AGENT_RUN = "agent-run"
ALL_KINDS = (DAEMON_MANAGER, AGENT_RUN)
_MANAGER_MARKER = f" -m {ENTRYPOINT_MODULE} "
_AGENT_RUN_MARKER = " -m lingtai run "
_MANAGER_LOCK_PARTS = ("daemon", "manager", "manager.lock")
_PYTEST_SESSION_DIR = re.compile(r"^(?P<session>/.*/pytest-of-[^/]+/pytest-\d+)(?:/|$)")


class LeakedProcess(NamedTuple):
    pid: int
    pgid: int
    kind: str
    agent_dir: str


def _is_python(executable: str) -> bool:
    return Path(executable).name.lower().startswith("python")


def parse_manager_command(command: str) -> tuple[str, int] | None:
    """Return ``(agent_dir, pool_size)`` when ``command`` is a manager launch.

    The text before ``-m`` must be a bare Python executable path (no flags,
    so ``sh -c ...`` or ``python -c ...`` wrappers never qualify), and the text
    after the module must be exactly an absolute directory and a pool size.
    """
    head, sep, tail = command.partition(_MANAGER_MARKER)
    if not sep or " -" in head or not _is_python(head):
        return None
    agent_dir, _, pool = tail.strip().rpartition(" ")
    if not agent_dir.startswith("/") or not pool.isdigit():
        return None
    return agent_dir, int(pool)


def parse_agent_run_command(command: str) -> str | None:
    """Return the agent dir when ``command`` ends in ``-m lingtai run <dir>``.

    The executable (the text before the first `` -`` flag) must be a Python,
    so a shell whose ``-c`` script mentions the form never qualifies; the
    remainder after the last marker must be exactly one absolute directory.
    """
    idx = command.rfind(_AGENT_RUN_MARKER)
    if idx < 0:
        return None
    if not _is_python(command[:idx].split(" -", 1)[0]):
        return None
    agent_dir = command[idx + len(_AGENT_RUN_MARKER):].strip()
    return agent_dir if agent_dir.startswith("/") else None


def classify_command(command: str) -> tuple[str, str] | None:
    """Return ``(kind, agent_dir)`` for a recognized launch, else ``None``."""
    manager = parse_manager_command(command)
    if manager is not None:
        return DAEMON_MANAGER, manager[0]
    agent_dir = parse_agent_run_command(command)
    if agent_dir is not None:
        return AGENT_RUN, agent_dir
    return None


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


def find_leaked_processes(
    *,
    under: Iterable[Path] = (),
    dead_pytest_sessions: bool = False,
    kinds: Iterable[str] = ALL_KINDS,
) -> list[LeakedProcess]:
    """List this uid's recognized processes whose agent dir matches a filter."""
    if os.name != "posix":
        return []
    roots = _root_texts(under)
    wanted = set(kinds)
    if not roots and not dead_pytest_sessions:
        return []
    found = []
    uid = os.getuid()
    for pid, pgid, proc_uid, command in _process_table():
        if proc_uid != uid or pid == os.getpid():
            continue
        classified = classify_command(command)
        if classified is None or classified[0] not in wanted:
            continue
        kind, agent_dir = classified
        if any(_is_within(agent_dir, root) for root in roots) or (
            dead_pytest_sessions and _pytest_session_is_dead(agent_dir)
        ):
            found.append(LeakedProcess(pid, pgid, kind, agent_dir))
    return found


def reap_processes(
    processes: Iterable[LeakedProcess], *, grace_s: float = 5.0
) -> list[int]:
    """SIGTERM each process's own process group, then SIGKILL any survivor.

    Managers and relaunched agents are session leaders, so their group holds
    exactly them and the children they started; a process that is not its own
    group leader is signalled alone, and the caller's own group is never
    signalled. ``lingtai run`` maps SIGTERM to its cooperative ``.suspend``
    stop. A survivor is re-identified by its command line before SIGKILL so a
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
        still_running = {
            (pid, classified)
            for pid, _pgid, _uid, command in _process_table()
            if (classified := classify_command(command)) is not None
        }
        for proc, group in pending:
            if (proc.pid, (proc.kind, proc.agent_dir)) not in still_running:
                continue
            _signal(proc.pid, group, signal.SIGKILL)
            kill_deadline = time.monotonic() + 2.0
            while not process_exited(proc.pid) and time.monotonic() < kill_deadline:
                time.sleep(0.05)
    return [proc.pid for proc, _group in targets]


def reap_processes_under(
    root: Path, *, kinds: Iterable[str] = ALL_KINDS, grace_s: float = 5.0
) -> list[int]:
    """Stop every recognized process whose agent dir lies under ``root``."""
    return reap_processes(
        find_leaked_processes(under=[root], kinds=kinds), grace_s=grace_s
    )


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


__all__ = [
    "AGENT_RUN",
    "ALL_KINDS",
    "DAEMON_MANAGER",
    "ENTRYPOINT_MODULE",
    "LeakedProcess",
    "classify_command",
    "daemon_manager_state_present",
    "find_leaked_processes",
    "parse_agent_run_command",
    "parse_manager_command",
    "process_exited",
    "reap_processes",
    "reap_processes_under",
]
