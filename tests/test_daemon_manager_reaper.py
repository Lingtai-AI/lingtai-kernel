"""The suite's daemon-manager reaper kills exactly the managers it is scoped to.

``tests/conftest.py`` relies on ``tests/_daemon_manager_reaper.py`` to stop the
resident POSIX daemon managers that real ``emanate`` tests spawn. Because it
sends signals on a developer machine that also runs real LingTai agents, its
selection must be exact: the launch argv shape, the current uid, and an agent
directory under a caller-named root (or a provably dead pytest session).
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
from tests import _daemon_manager_reaper as reaper

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="the central daemon manager and its reaper are POSIX-only"
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

    assert [p.pid for p in reaper.find_daemon_managers(under=[inside])] == [101]
    assert [p.pid for p in reaper.find_daemon_managers(dead_pytest_sessions=True)] == [105]
    assert reaper.find_daemon_managers() == []


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

        assert reaper.reap_daemon_managers_under(inside.parent) == [inside_pid]

        assert reaper.process_exited(inside_pid)
        assert not reaper.process_exited(outside_pid)
        assert all(decoy.poll() is None for decoy in decoys)
    finally:
        for decoy in decoys:
            os.killpg(decoy.pid, signal.SIGKILL)
            decoy.wait()
        reaper.reap_daemon_managers_under(tmp_path)


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

        assert reaper.reap_daemon_managers_under(tmp_path, grace_s=0.3) == [stubborn.pid]

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
        recycled = reaper.ManagerProcess(
            bystander.pid, bystander.pid, str(tmp_path / "agent")
        )

        reaper.reap_daemon_managers([recycled], grace_s=0.3)

        assert bystander.poll() is None
    finally:
        os.killpg(bystander.pid, signal.SIGKILL)
        bystander.wait()
