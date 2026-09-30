"""The suite's process reaper kills exactly the processes it is scoped to.

``tests/conftest.py`` relies on ``tests/_process_reaper.py`` to stop the
resident POSIX daemon managers that real ``emanate`` tests spawn and the
``lingtai run`` agent hosts a failing test leaves behind. Because it sends
signals on a developer machine that also runs real LingTai agents, its
selection must be exact: the launch command-line shape, the current uid, and
an agent directory under a caller-named root (or a provably dead pytest
session).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lingtai.adapters.posix import daemon_manager
from lingtai.adapters.posix.daemon_manager import MANAGER_DIR
from tests import _process_reaper as reaper

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="the reaped process kinds and the reaper are POSIX-only"
)

_MODULE = reaper.ENTRYPOINT_MODULE
_PYTHON = "/opt/python/bin/python3.11"


def test_parse_accepts_only_the_exact_manager_launch_argv() -> None:
    assert reaper.parse_manager_command(
        f"{_PYTHON} -m {_MODULE} /tmp/pytest-of-u/pytest-1/t0/agent 100"
    ) == ("/tmp/pytest-of-u/pytest-1/t0/agent", 100)
    assert reaper.parse_manager_command(
        f"/Apps/My Python/Python -m {_MODULE} /tmp/with space/agent 3"
    ) == ("/tmp/with space/agent", 3)

    for command in (
        f"/bin/zsh -c {_PYTHON} -m {_MODULE} /tmp/a 100",
        f"{_PYTHON} -c import time -m {_MODULE} /tmp/a 100",
        f"grep {_MODULE} pytest-of-",
        f"sh -c 'kill $(pgrep -f {_MODULE})'",
        f"{_PYTHON} -m {_MODULE} relative/agent 100",
        f"{_PYTHON} -m {_MODULE} /tmp/a 100 extra",
        f"{_PYTHON} -m {_MODULE}",
        f"/usr/bin/node -m {_MODULE} /tmp/a 100",
        f"{_PYTHON} -m lingtai.adapters.posix.daemon_supervisor_entrypoint /tmp/a 100",
    ):
        assert reaper.parse_manager_command(command) is None, command


def test_parse_accepts_only_an_agent_run_ending_the_command_line() -> None:
    agent = "/tmp/pytest-of-u/pytest-1/t0/source-project/.lingtai/initiator"
    wrapper = f"{_PYTHON} -c import json\\012run(agent) {agent} /x/move.py -m lingtai run {agent}"
    assert reaper.parse_agent_run_command(f"{_PYTHON} -m lingtai run {agent}") == agent
    assert reaper.parse_agent_run_command(wrapper) == agent
    assert reaper.parse_agent_run_command(
        f"{_PYTHON} -m lingtai run /tmp/target M-iM^[M-* project/.lingtai/a"
    ) == "/tmp/target M-iM^[M-* project/.lingtai/a"
    assert reaper.classify_command(f"{_PYTHON} -m lingtai run {agent}") == (
        reaper.AGENT_RUN, agent,
    )

    for command in (
        f"/bin/zsh -c {_PYTHON} -m lingtai run {agent}",
        f"sh -c 'pgrep -f \" -m lingtai run {agent}\"'",
        f"grep -m lingtai run {agent}",
        f"{_PYTHON} -m lingtai run relative/agent",
        f"{_PYTHON} -m lingtai run",
        f"/usr/bin/node -m lingtai run {agent}",
        f"{_PYTHON} -m lingtai-agent run {agent}",
    ):
        assert reaper.parse_agent_run_command(command) is None, command


def test_find_scopes_by_root_uid_and_dead_pytest_session(tmp_path, monkeypatch) -> None:
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    sessions = tmp_path / "pytest-of-u"
    for number, lock_pid in (("7", finished.pid), ("8", os.getpid()), ("9", None)):
        session = sessions / f"pytest-{number}"
        session.mkdir(parents=True)
        if lock_pid is not None:
            (session / ".lock").write_text(str(lock_pid), encoding="utf-8")
    inside = tmp_path / "scope"
    uid = os.getuid()
    rows = [
        (101, 101, uid, f"{_PYTHON} -m {_MODULE} {inside}/agent 100"),
        (102, 102, uid, f"{_PYTHON} -m {_MODULE} {inside}-sibling/agent 100"),
        (103, 103, uid + 1, f"{_PYTHON} -m {_MODULE} {inside}/other-user 100"),
        (104, 104, uid, f"/bin/sh -c {_PYTHON} -m {_MODULE} {inside}/agent 100"),
        (105, 105, uid, f"{_PYTHON} -m {_MODULE} {sessions}/pytest-7/t0/agent 100"),
        (106, 106, uid, f"{_PYTHON} -m {_MODULE} {sessions}/pytest-8/t0/agent 100"),
        (107, 107, uid, f"{_PYTHON} -m {_MODULE} {sessions}/pytest-9/t0/agent 100"),
        (108, 108, uid, f"{_PYTHON} -m {_MODULE} /Users/someone/project/.lingtai/a 100"),
    ]
    monkeypatch.setattr(reaper, "_process_table", lambda: rows)

    agent_rows = [
        (201, 201, uid, f"{_PYTHON} -m lingtai run {inside}/.lingtai/initiator"),
        (202, 202, uid, f"{_PYTHON} -c import x -m lingtai run {sessions}/pytest-7/t1/a"),
        (203, 203, uid, f"{_PYTHON} -m lingtai run {sessions}/pytest-8/t1/a"),
        (204, 204, uid, f"{_PYTHON} -m lingtai run /Users/someone/project/.lingtai/mimo-1"),
        (205, 205, uid, f"/bin/zsh -c {_PYTHON} -m lingtai run {inside}/a"),
    ]
    monkeypatch.setattr(reaper, "_process_table", lambda: rows + agent_rows)
    managers = (reaper.DAEMON_MANAGER,)

    def pids(**filters) -> list[int]:
        return [proc.pid for proc in reaper.find_leaked_processes(**filters)]

    assert pids(under=[inside], kinds=managers) == [101]
    assert pids(dead_pytest_sessions=True, kinds=managers) == [105]
    assert pids(under=[inside]) == [101, 201]
    assert pids(dead_pytest_sessions=True) == [105, 202]
    assert pids() == []


def test_state_gate_needs_the_manager_lock(tmp_path) -> None:
    assert reaper.daemon_manager_state_present(tmp_path) is False
    (tmp_path / "agent" / "manager.lock").parent.mkdir(parents=True)
    (tmp_path / "agent" / "manager.lock").touch()
    assert reaper.daemon_manager_state_present(tmp_path) is False
    lock = tmp_path / "project" / "agent" / MANAGER_DIR / "manager.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    assert reaper.daemon_manager_state_present(tmp_path) is True


def _spawn_registered_manager(agent_working_dir: Path) -> int:
    daemon_manager._ensure_manager(agent_working_dir, pool_size=1)
    pid_path = agent_working_dir / MANAGER_DIR / "manager.pid"
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            pid = json.loads(pid_path.read_text(encoding="utf-8")).get("pid")
        except (OSError, json.JSONDecodeError):
            pid = None
        if isinstance(pid, int) and not isinstance(pid, bool):
            return pid
        time.sleep(0.05)
    raise AssertionError(f"manager never registered under {agent_working_dir}")


def test_reap_stops_only_the_scoped_real_manager(tmp_path) -> None:
    inside = tmp_path / "inside" / "agent"
    outside = tmp_path / "outside" / "agent"
    decoy_path = inside.parent / "decoy"
    decoys = [
        subprocess.Popen(
            ["/bin/sh", "-c", f"sleep 60; : -m {_MODULE} {decoy_path} 100"],
            start_new_session=True,
        ),
        subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)",
             "-m", _MODULE, str(decoy_path), "100"],
            start_new_session=True,
        ),
    ]
    try:
        inside_pid = _spawn_registered_manager(inside)
        outside_pid = _spawn_registered_manager(outside)

        assert reaper.reap_processes_under(inside.parent) == [inside_pid]

        assert reaper.process_exited(inside_pid)
        assert not reaper.process_exited(outside_pid)
        assert all(decoy.poll() is None for decoy in decoys)
    finally:
        for decoy in decoys:
            os.killpg(decoy.pid, signal.SIGKILL)
            decoy.wait()
        reaper.reap_processes_under(tmp_path)


_IGNORE_SIGTERM = (
    "import os, pathlib, signal, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "pathlib.Path(os.environ['REAPER_TEST_READY']).write_text('1')\n"
    "time.sleep(60)\n"
)


def _wait_ready(path: Path) -> None:
    deadline = time.monotonic() + 15.0
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.02)


def test_reap_escalates_to_sigkill_for_a_manager_ignoring_sigterm(tmp_path) -> None:
    shadow = tmp_path / "shadow"
    package = shadow / "lingtai" / "adapters" / "posix"
    package.mkdir(parents=True)
    for init in (shadow / "lingtai", shadow / "lingtai" / "adapters", package):
        (init / "__init__.py").write_text("", encoding="utf-8")
    (package / "daemon_manager_entrypoint.py").write_text(_IGNORE_SIGTERM, encoding="utf-8")
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    stubborn = subprocess.Popen(
        [sys.executable, "-m", _MODULE, str(agent_dir), "1"],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(shadow),
            "REAPER_TEST_READY": str(tmp_path / "ready"),
        },
        start_new_session=True,
    )
    try:
        _wait_ready(tmp_path / "ready")

        assert reaper.reap_processes_under(tmp_path, grace_s=0.3) == [stubborn.pid]

        assert reaper.process_exited(stubborn.pid)
    finally:
        if not reaper.process_exited(stubborn.pid):
            os.killpg(stubborn.pid, signal.SIGKILL)
            stubborn.wait()


def test_reap_never_escalates_against_a_recycled_pid(tmp_path) -> None:
    """A pid that no longer carries the manager argv is never sent SIGKILL."""
    bystander = subprocess.Popen(
        [sys.executable, "-c", _IGNORE_SIGTERM],
        env={**os.environ, "REAPER_TEST_READY": str(tmp_path / "ready")},
        start_new_session=True,
    )
    try:
        _wait_ready(tmp_path / "ready")
        recycled = reaper.LeakedProcess(
            bystander.pid, bystander.pid, reaper.DAEMON_MANAGER, str(tmp_path / "agent")
        )

        reaper.reap_processes([recycled], grace_s=0.3)

        assert bystander.poll() is None
    finally:
        os.killpg(bystander.pid, signal.SIGKILL)
        bystander.wait()


_AGENT_HOST = (
    "import os, pathlib, subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "pathlib.Path(os.environ['REAPER_TEST_READY']).write_text(str(child.pid))\n"
    "time.sleep(60)\n"
)


def _spawn_agent_host(agent_dir: Path, ready: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", _AGENT_HOST, "-m", "lingtai", "run", str(agent_dir)],
        env={**os.environ, "REAPER_TEST_READY": str(ready)},
        start_new_session=True,
    )


def test_reap_stops_a_scoped_agent_run_with_its_process_group(tmp_path) -> None:
    inside = _spawn_agent_host(tmp_path / "inside" / ".lingtai" / "a", tmp_path / "in.ready")
    outside = _spawn_agent_host(tmp_path / "outside" / ".lingtai" / "a", tmp_path / "out.ready")
    try:
        _wait_ready(tmp_path / "in.ready")
        _wait_ready(tmp_path / "out.ready")
        inside_child = int((tmp_path / "in.ready").read_text(encoding="utf-8"))

        assert reaper.reap_processes_under(
            tmp_path / "inside", kinds=(reaper.AGENT_RUN,)
        ) == [inside.pid]

        assert reaper.process_exited(inside.pid)
        deadline = time.monotonic() + 5.0
        while not reaper.process_exited(inside_child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert reaper.process_exited(inside_child), "the agent's process group must go too"
        assert outside.poll() is None
    finally:
        for host in (inside, outside):
            if host.poll() is None:
                os.killpg(host.pid, signal.SIGKILL)
            host.wait()
