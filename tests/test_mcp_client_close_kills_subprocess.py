"""Regression: MCPClient.close() must terminate the subprocess tree.

On POSIX, stopping the asyncio loop closes the stdio pipes and the child
receives EOF on stdin, which exits it. On Windows that is not reliable: a
venv launcher shim spawns a real interpreter as a child that survives,
so every boot/refresh/molt respawn leaked another stdio pair (duplicate
telegram listeners etc). close() now captures the child tree after start
and explicitly terminates it.
"""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from lingtai.services.mcp import (
    MCPClient,
    _kill_process_tree,
    _snapshot_process_table,
)


FAKE_SERVER = r"""
import sys, time
# Simulates a server that does NOT exit on stdin EOF (the Windows leak case).
while True:
    try:
        data = sys.stdin.buffer.read(1)
    except Exception:
        break
    if not data:
        time.sleep(3600)
"""


@pytest.fixture
def fake_server_script(tmp_path):
    path = tmp_path / "fake_server_keepalive.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return str(path)


def _pids_still_alive(pids):
    table = _snapshot_process_table()
    present = {r["pid"] for r in table}
    return [pid for pid in pids if pid in present]


def test_close_terminates_subprocess_tree(fake_server_script):
    marker = "keepalive_marker_%d" % os.getpid()
    client = MCPClient(command=sys.executable, args=[fake_server_script, marker])
    client.start()
    client._capture_child_pids()

    assert client._child_pids, "expected at least one captured child process"
    assert _pids_still_alive(client._child_pids), "child should be alive after start"

    client.close()

    still = _pids_still_alive(client._child_pids)
    assert not still, f"close() leaked subprocesses: {still}"


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL escalation is POSIX-only")
def test_kill_process_tree_reaps_an_owned_root_without_external_sleep():
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3600)"],
        start_new_session=True,
    )
    try:
        assert _kill_process_tree(proc.pid, timeout=0.25) is True
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL escalation is POSIX-only")
def test_kill_process_tree_verifies_every_captured_pid_after_sigkill(monkeypatch):
    import signal

    import lingtai.services.mcp as mcp

    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        mcp,
        "_snapshot_process_table",
        lambda: [
            {"pid": 41001, "ppid": 0, "cmdline": "root"},
            {"pid": 41002, "ppid": 41001, "cmdline": "child"},
            {"pid": 41003, "ppid": 41002, "cmdline": "grandchild"},
        ],
    )
    monkeypatch.setattr(
        mcp.os,
        "killpg",
        lambda pid, sig: events.append(("signal", (pid, sig))),
    )

    def still_present(pids, timeout):
        events.append(("verify", (tuple(pids), timeout)))
        return False

    monkeypatch.setattr(mcp, "_wait_for_processes_exit", still_present)

    assert _kill_process_tree(41001, timeout=0.25) is False
    assert events == [
        ("signal", (41001, signal.SIGTERM)),
        ("verify", ((41001, 41002, 41003), 0.25)),
        ("signal", (41001, signal.SIGKILL)),
        ("verify", ((41001, 41002, 41003), 0.25)),
    ]


def test_windows_process_snapshot_honors_timeout_and_can_fail_closed(monkeypatch):
    import lingtai.services.mcp as mcp

    calls: list[float] = []

    def time_out(*args, timeout, **kwargs):
        calls.append(timeout)
        raise subprocess.TimeoutExpired(args[0], timeout)

    monkeypatch.setattr(mcp, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(subprocess, "check_output", time_out)

    assert mcp._snapshot_process_table(timeout=0.25) == []
    with pytest.raises(subprocess.TimeoutExpired):
        mcp._snapshot_process_table(timeout=0.125, strict=True)
    assert calls == [0.25, 0.125]


def test_windows_wait_snapshot_failure_is_not_exit_evidence(monkeypatch):
    import lingtai.services.mcp as mcp

    calls: list[tuple[float, bool]] = []

    def unavailable(*, timeout, strict):
        calls.append((timeout, strict))
        raise subprocess.TimeoutExpired("powershell", timeout)

    monkeypatch.setattr(mcp, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        mcp,
        "time",
        SimpleNamespace(monotonic=lambda: 10.0, sleep=lambda _: None),
    )
    monkeypatch.setattr(mcp, "_snapshot_process_table", unavailable)
    monkeypatch.setattr(
        mcp,
        "_pid_exited",
        lambda _: pytest.fail("Windows verification must use table snapshots"),
    )

    assert mcp._wait_for_processes_exit([41001, 41002], timeout=0.5) is False
    assert calls == [(0.5, True)]


def test_windows_kill_tree_shares_deadline_and_snapshots_once_per_poll(monkeypatch):
    import lingtai.services.mcp as mcp

    class Clock:
        now = 100.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    snapshot_calls: list[tuple[float, bool]] = []
    tables = iter(
        [
            [
                {"pid": 41001, "ppid": 0, "cmdline": "root"},
                {"pid": 41002, "ppid": 41001, "cmdline": "child"},
                {"pid": 41003, "ppid": 41002, "cmdline": "grandchild"},
            ],
            [{"pid": 41003, "ppid": 41002, "cmdline": "grandchild"}],
            [],
        ]
    )

    def snapshot(*, timeout, strict):
        snapshot_calls.append((timeout, strict))
        clock.now += 0.4
        return next(tables)

    taskkill_calls: list[tuple[list[str], float]] = []

    def taskkill(command, **kwargs):
        taskkill_calls.append((command, kwargs["timeout"]))
        clock.now += 0.6
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(mcp, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        mcp,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep),
    )
    monkeypatch.setattr(mcp, "_snapshot_process_table", snapshot)
    monkeypatch.setattr(subprocess, "run", taskkill)
    monkeypatch.setattr(
        mcp,
        "_pid_exited",
        lambda _: pytest.fail("Windows verification must not snapshot per PID"),
    )

    assert mcp._kill_process_tree(41001, timeout=3.0) is True
    assert taskkill_calls == [
        (["taskkill", "/PID", "41001", "/T", "/F"], pytest.approx(2.6))
    ]
    assert snapshot_calls == [
        (pytest.approx(3.0), True),
        (pytest.approx(2.0), True),
        (pytest.approx(1.55), True),
    ]
    assert clock.now == pytest.approx(101.85)


def test_name_identity_derived_from_command(fake_server_script):
    client = MCPClient(command=sys.executable, args=[fake_server_script])
    assert client.name
    assert "fake_server_keepalive" in client.name


def test_dedup_identity_detects_same_server(fake_server_script):
    c1 = MCPClient(command=sys.executable, args=[fake_server_script, "dedup_a"])
    c2 = MCPClient(command=sys.executable, args=[fake_server_script, "dedup_a"])
    assert c1.name == c2.name
    c1.close()
    c2.close()
