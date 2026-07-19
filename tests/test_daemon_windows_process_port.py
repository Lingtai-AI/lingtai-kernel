"""Windows daemon process port: spawn/termination wiring and native Job proof.

The cross-platform tier runs the real adapter code with the Win32 mechanism
helpers (`_assign_new_job`, `_terminate_job`, ...) and `_win32` observation
faked, plus ``os.name`` patched to pass the construction guard (safe here: no
``Path()`` is constructed inside the adapter's methods — commands are built
before patching, mirroring the workdir-lease exemplar). The native tier proves
real Job Object tree ownership on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from lingtai.tools.daemon import windows_process
from lingtai.tools.daemon.process_port import (
    DaemonProcessCommand,
    DaemonProcessExit,
    DaemonProcessObservation,
    DaemonProcessTerminationScope,
)

windows_mechanism = pytest.mark.skipif(
    os.name != "nt", reason="Job Object mechanism requires native Windows"
)

PRIVATE = DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
INHERITED = DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP


class _FakeStream(list):
    def close(self):
        return None


class _FakeProc:
    def __init__(self, pid, returncode=None):
        self.pid = pid
        self.returncode = returncode
        self.stdout, self.stderr, self.stdin = _FakeStream(), _FakeStream(), None
        self.wait_calls: list[float | None] = []
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self):
        self.kill_calls += 1


class _Mechanism:
    """Recording stand-ins for every module-local Win32 helper."""

    def __init__(self):
        self.events: list[tuple] = []
        self.jobs: list[object] = []

    def install(self, monkeypatch, *, procs_by_job_termination=None):
        def assign_new_job(proc):
            job = object()
            self.jobs.append(job)
            self.events.append(("assign", proc.pid, job))
            return job

        def resume(proc):
            self.events.append(("resume", proc.pid))

        def terminate_job(job):
            self.events.append(("terminate_job", job))
            if procs_by_job_termination is not None:
                proc = procs_by_job_termination.get(id(job))
                if proc is not None:
                    proc.returncode = 1
            return True

        def wait_job_empty(job, timeout):
            self.events.append(("wait_job_empty", job))
            return True

        def close_job(job):
            self.events.append(("close_job", job))

        monkeypatch.setattr(windows_process, "_assign_new_job", assign_new_job)
        monkeypatch.setattr(windows_process, "_resume_suspended_process", resume)
        monkeypatch.setattr(windows_process, "_terminate_job", terminate_job)
        monkeypatch.setattr(windows_process, "_wait_job_empty", wait_job_empty)
        monkeypatch.setattr(windows_process, "_close_job", close_job)

    def names(self):
        return [event[0] for event in self.events]


@pytest.fixture
def nt(monkeypatch):
    """Pass the construction guard and fake the shared identity observation."""
    monkeypatch.setattr(
        windows_process._win32, "process_creation_identity",
        lambda pid: f"windows:{pid}", raising=True,
    )
    monkeypatch.setattr(os, "name", "nt")
    return monkeypatch


def test_windows_adapter_guards_against_non_windows_use():
    if os.name == "nt":
        pytest.skip("guard is only observable off Windows")
    with pytest.raises(RuntimeError, match="unsupported on this platform"):
        windows_process.WindowsDaemonProcessPort()


def test_private_scope_spawn_is_suspended_job_owned_then_resumed(tmp_path, nt):
    command = DaemonProcessCommand(("codex", "exec"), tmp_path, (("X_TEST", "1"),))
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(101, returncode=0)
    popen_kwargs: list[dict] = []

    def fake_popen(*args, **kwargs):
        popen_kwargs.append((args, kwargs))
        return proc

    nt.setattr(windows_process.subprocess, "Popen", fake_popen)
    observations: list[DaemonProcessObservation] = []
    port = windows_process.WindowsDaemonProcessPort(
        observation_callback=observations.append,
    )
    handle = port.spawn(command, group_id="batch")

    args, kwargs = popen_kwargs[0]
    assert args == (("codex", "exec"),)
    assert kwargs["creationflags"] == (
        windows_process._win32.DETACHED_CREATIONFLAGS | windows_process._CREATE_SUSPENDED
    )
    assert kwargs["text"] is True
    assert kwargs["env"] == {"X_TEST": "1"}
    assert kwargs["cwd"] == str(tmp_path)
    # The Job owns the root before its first instruction runs.
    assert mechanism.names() == ["assign", "resume"]
    assert observations == [DaemonProcessObservation(
        pid=101, pgid=None, start_identity="windows:101",
        termination_scope=PRIVATE,
    )]
    assert port.release(handle) is True
    assert mechanism.names()[-1] == "close_job"


def test_inherited_scope_spawn_creates_no_job(tmp_path, nt):
    command = DaemonProcessCommand(("codex",), tmp_path)
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(202, returncode=None)
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)
    observations: list[DaemonProcessObservation] = []
    port = windows_process.WindowsDaemonProcessPort(
        termination_scope=INHERITED, observation_callback=observations.append,
        term_timeout=0.01, kill_timeout=0.01,
    )
    handle = port.spawn(command)
    assert mechanism.events == []  # no Job for supervisor-owned scope
    assert observations[0].termination_scope is INHERITED
    assert observations[0].pgid is None
    # Exact-child force-termination only: Popen.kill (TerminateProcess on the
    # retained handle), never a Job operation.
    proc.returncode = None

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    receipt = port.terminate(handle, reason="reclaim")
    assert receipt == DaemonProcessExit(1, "reclaim")
    assert proc.kill_calls == 1
    assert mechanism.events == []


def test_private_termination_contract_and_first_reason_wins(tmp_path, nt):
    mechanism = _Mechanism()
    procs_by_job: dict[int, _FakeProc] = {}
    mechanism.install(nt, procs_by_job_termination=procs_by_job)
    exited = _FakeProc(301, returncode=0)
    term_ok = _FakeProc(302)
    stubborn = _FakeProc(303)
    ungrouped = _FakeProc(304)
    procs = iter([exited, term_ok, stubborn, ungrouped])
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: next(procs))
    port = windows_process.WindowsDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)

    handle_exited = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    handle_term = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    handle_stubborn = port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id="batch")
    port.spawn(DaemonProcessCommand(("codex",), tmp_path), group_id=None)
    job_of = {
        handle_term: port._handles[handle_term][3],
        handle_stubborn: port._handles[handle_stubborn][3],
    }
    procs_by_job[id(job_of[handle_term])] = term_ok  # job termination reaps it

    # Already-exited child: no job termination is issued.
    before = list(mechanism.names())
    assert port.terminate(handle_exited, reason="timeout").returncode == 0
    assert mechanism.names() == before

    receipt = port.terminate(handle_term, reason="timeout")
    assert receipt == DaemonProcessExit(1, "timeout")
    assert ("terminate_job", job_of[handle_term]) in mechanism.events
    assert ("wait_job_empty", job_of[handle_term]) in mechanism.events

    # Stubborn child: job termination + kill ladder, unreaped => release False.
    receipt = port.terminate(handle_stubborn, reason="reclaim")
    assert receipt == DaemonProcessExit(None, "reclaim")
    assert stubborn.kill_calls >= 1
    assert port.release(handle_stubborn) is False
    assert handle_stubborn in port._handles

    assert port.terminate_group("batch", reason="timeout") == 3
    assert port.terminate_all(reason="agent_stop") == 4
    # The first local cause remains authoritative across later sweeps.
    assert port.terminate(handle_stubborn, reason="agent_stop").reason == "reclaim"


def test_windows_adapter_rejects_unknown_handles_and_observation_failure_cleanup(tmp_path, nt):
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(401)

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)

    def failing_callback(observation):
        raise RuntimeError("observation transaction failed")

    port = windows_process.WindowsDaemonProcessPort(
        observation_callback=failing_callback, term_timeout=0.01, kill_timeout=0.01,
    )
    with pytest.raises(RuntimeError, match="observation transaction failed"):
        port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    assert port._handles == {}
    assert proc.kill_calls >= 1
    assert mechanism.names()[-1] == "close_job"

    fake = object()
    for operation in (
        lambda: port.iter_stdout(fake),
        lambda: port.drain_stderr(fake),
        lambda: port.wait(fake),
        lambda: port.terminate(fake),
    ):
        with pytest.raises(KeyError):
            operation()
    assert port.release(fake) is True


_PRIVATE_TREE_CHILD = textwrap.dedent("""
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print(grandchild.pid, flush=True)
    time.sleep(120)
""")


@windows_mechanism
def test_windows_private_scope_job_reclaims_the_whole_tree(tmp_path):
    """TerminateJobObject kills the spawned child AND its grandchild."""
    from lingtai.adapters.windows import _win32

    port = windows_process.WindowsDaemonProcessPort(term_timeout=10.0, kill_timeout=5.0)
    handle = port.spawn(
        DaemonProcessCommand((sys.executable, "-c", _PRIVATE_TREE_CHILD), tmp_path),
        group_id="native-batch",
    )
    stdout = port.iter_stdout(handle)
    grandchild_pid = int(next(iter(stdout)).strip())
    assert _win32.process_alive(grandchild_pid)
    receipt = port.terminate(handle, reason="reclaim")
    assert receipt.reason == "reclaim"
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _win32.process_alive(grandchild_pid):
        time.sleep(0.1)
    assert not _win32.process_alive(grandchild_pid)
    assert port.release(handle) is True


@windows_mechanism
def test_windows_inherited_scope_terminates_only_the_exact_child(tmp_path):
    """Supervisor-owned scope must never sweep beyond the exact Popen child."""
    from lingtai.adapters.windows import _win32

    port = windows_process.WindowsDaemonProcessPort(
        termination_scope=INHERITED, term_timeout=10.0, kill_timeout=5.0,
    )
    handle = port.spawn(
        DaemonProcessCommand((sys.executable, "-c", _PRIVATE_TREE_CHILD), tmp_path),
    )
    stdout = port.iter_stdout(handle)
    grandchild_pid = int(next(iter(stdout)).strip())
    try:
        receipt = port.terminate(handle, reason="reclaim")
        assert receipt.returncode is not None
        time.sleep(0.5)
        assert _win32.process_alive(grandchild_pid), (
            "inherited scope must not reach beyond the exact child"
        )
    finally:
        _win32.terminate_pid(grandchild_pid)
    assert port.release(handle) is True


@windows_mechanism
def test_windows_native_observation_identity_matches_shared_helper(tmp_path):
    from lingtai.adapters.windows import _win32

    observations: list[DaemonProcessObservation] = []
    port = windows_process.WindowsDaemonProcessPort(
        observation_callback=observations.append,
    )
    handle = port.spawn(
        DaemonProcessCommand(
            (sys.executable, "-c", "import time; time.sleep(30)"), tmp_path,
        ),
    )
    try:
        observation = observations[0]
        assert observation.pgid is None
        assert observation.start_identity == _win32.process_creation_identity(observation.pid)
        assert observation.start_identity.startswith("windows:")
    finally:
        port.terminate(handle, reason="test-cleanup")
        port.release(handle)
