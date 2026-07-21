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
        # Job id -> bool | Exception, consulted by wait_job_empty/terminate_job.
        # Absent entries default to "empty"/"succeeds" so existing tests that
        # never configure this keep their prior behavior.
        self.job_empty: dict[int, bool] = {}
        self.terminate_job_result: dict[int, bool | Exception] = {}

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
            result = self.terminate_job_result.get(id(job), True)
            if isinstance(result, Exception):
                raise result
            if result and procs_by_job_termination is not None:
                proc = procs_by_job_termination.get(id(job))
                if proc is not None:
                    proc.returncode = 1
            return result

        def wait_job_empty(job, timeout):
            self.events.append(("wait_job_empty", job))
            return self.job_empty.get(id(job), True)

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


def test_release_refuses_to_close_job_while_root_exited_descendant_lives(tmp_path, nt):
    """Root exit alone must never be treated as proof the Job is empty.

    A private root can spawn a descendant and exit normally (no terminate()
    call at all — the ordinary wait()-then-release() path). If the Job still
    has an active member at that point, release() must not report success or
    close the only Job handle: the Job has no KILL_ON_JOB_CLOSE, so closing it
    while non-empty silently orphans the descendant with no supervisor sweep
    to recover it.
    """
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(501, returncode=0)  # root already exited
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)
    port = windows_process.WindowsDaemonProcessPort()
    handle = port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = port._handles[handle][3]
    mechanism.job_empty[id(job)] = False  # a descendant is still alive

    assert port.release(handle) is False
    assert handle in port._handles
    assert ("close_job", job) not in mechanism.events

    # Once the descendant is gone, release() succeeds and closes the Job.
    mechanism.job_empty[id(job)] = True
    assert port.release(handle) is True
    assert mechanism.events[-1] == ("close_job", job)


def test_release_refuses_to_close_job_on_wait_job_empty_timeout(tmp_path, nt):
    """terminate() where TerminateJobObject succeeds but the Job never empties
    within the bounded wait must not let a later release() report success."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(502, returncode=None)
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)
    port = windows_process.WindowsDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)
    handle = port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = port._handles[handle][3]
    mechanism.job_empty[id(job)] = False  # TerminateJobObject "succeeds" but never empties

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    receipt = port.terminate(handle, reason="reclaim")
    assert receipt == DaemonProcessExit(1, "reclaim")
    assert ("terminate_job", job) in mechanism.events
    assert ("wait_job_empty", job) in mechanism.events

    # The root was reaped by the exact-child kill fallback, but the Job
    # itself was never confirmed empty — release() must still refuse.
    assert port.release(handle) is False
    assert handle in port._handles
    assert ("close_job", job) not in mechanism.events


def test_release_refuses_to_close_job_when_terminate_job_object_fails(tmp_path, nt):
    """A false TerminateJobObject result must not let an exited root's
    release() silently close a Job that was never actually reclaimed."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(503, returncode=None)
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)
    port = windows_process.WindowsDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)
    handle = port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = port._handles[handle][3]
    mechanism.terminate_job_result[id(job)] = False  # TerminateJobObject fails
    mechanism.job_empty[id(job)] = False  # unreclaimed Job members remain

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    port.terminate(handle, reason="reclaim")
    assert ("terminate_job", job) in mechanism.events
    # A failed TerminateJobObject must not be treated as a green light to
    # wait for/assume emptiness.
    assert ("wait_job_empty", job) not in mechanism.events

    # The exact-child fallback reaped the root, but the Job was never
    # reclaimed — release() must still refuse rather than fake success.
    assert port.release(handle) is False
    assert handle in port._handles
    assert ("close_job", job) not in mechanism.events


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


def test_observation_failure_cleanup_refuses_to_close_job_when_terminate_job_object_fails(
    tmp_path, nt,
):
    """A callback/identity failure after resume must not close the Job when
    TerminateJobObject itself fails — the same no-orphan gate release() and
    terminate() honor, now also honored by the spawn-failure cleanup path."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(601, returncode=None)

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)

    def failing_callback(observation):
        raise RuntimeError("observation transaction failed")

    # Pin TerminateJobObject to fail AND the Job to stay non-empty for the
    # Job this spawn creates, before spawning — install() defaults every job
    # to "succeeds"/"empty", so both must be wired in via the same
    # assign-event-observing wrapper the sibling timeout test below uses.
    # (A failed TerminateJobObject with a Job that is independently confirmed
    # empty is safe to close — the hazard is specifically failed-terminate
    # PLUS non-empty, so both must be pinned to exercise it.)
    original_assign = windows_process._assign_new_job
    def pinned_terminate_fails_assign(proc):
        job = original_assign(proc)
        mechanism.terminate_job_result[id(job)] = False
        mechanism.job_empty[id(job)] = False
        return job
    nt.setattr(windows_process, "_assign_new_job", pinned_terminate_fails_assign)

    port = windows_process.WindowsDaemonProcessPort(
        observation_callback=failing_callback, term_timeout=0.01, kill_timeout=0.01,
    )
    with pytest.raises(RuntimeError, match="observation transaction failed"):
        port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = mechanism.jobs[-1]

    assert ("terminate_job", job) in mechanism.events
    # _terminate()'s own internal wait is gated on TerminateJobObject having
    # succeeded (it did not here); the confirmed-empty check that decides
    # whether to close is a separate, independent query _finalize_job always
    # makes before closing — that is the one that fires here, and it
    # correctly reports non-empty, so the Job is never closed.
    assert ("wait_job_empty", job) in mechanism.events
    assert ("close_job", job) not in mechanism.events
    assert port._handles == {}, "handle must still be forgotten even though the Job is leaked"
    # Only one terminate_job call was issued for this job — proves the outer
    # except in spawn() did not repeat _cleanup_spawn_failure's attempt.
    assert mechanism.events.count(("terminate_job", job)) == 1


def test_observation_failure_cleanup_refuses_to_close_job_on_wait_job_empty_timeout(
    tmp_path, nt,
):
    """A callback/identity failure after resume must not close the Job when
    TerminateJobObject succeeds but the bounded empty-wait times out."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(602, returncode=None)

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
    # Configure emptiness to fail persistently: install() defaults every job
    # to "empty", so the Job created during this spawn must be pinned false
    # via a wrapper that observes the assign event.
    original_assign = windows_process._assign_new_job
    def pinned_not_empty_assign(proc):
        job = original_assign(proc)
        mechanism.job_empty[id(job)] = False
        return job
    nt.setattr(windows_process, "_assign_new_job", pinned_not_empty_assign)

    with pytest.raises(RuntimeError, match="observation transaction failed"):
        port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = mechanism.jobs[-1]

    assert ("terminate_job", job) in mechanism.events
    assert ("wait_job_empty", job) in mechanism.events
    assert ("close_job", job) not in mechanism.events
    assert port._handles == {}, "handle must still be forgotten even though the Job is leaked"


def test_resume_failure_before_registration_closes_confirmed_empty_job_once(tmp_path, nt):
    """A pre-registration failure (resume itself raises) must gate Job close
    on confirmed emptiness, and must not double-close: this path never goes
    through _cleanup_spawn_failure (the handle was never registered), so it
    is the outer except's own responsibility in spawn(). This is the honest-
    success side: the Job is genuinely empty, so it is closed exactly once."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(603, returncode=None)

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)

    def failing_resume(proc):
        mechanism.events.append(("resume", proc.pid))
        raise OSError("NtResumeProcess failed")

    nt.setattr(windows_process, "_resume_suspended_process", failing_resume)
    port = windows_process.WindowsDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)

    with pytest.raises(OSError, match="NtResumeProcess failed"):
        port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = mechanism.jobs[-1]
    assert port._handles == {}
    # _finalize_job consults emptiness (default True in this mechanism) and
    # closes exactly once — proving the pre-registration path also goes
    # through the shared confirmed-empty gate, not an unconditional close.
    assert mechanism.events.count(("close_job", job)) == 1


def test_resume_failure_before_registration_refuses_to_close_nonempty_job(tmp_path, nt):
    """Same pre-registration resume failure, but the Job is confirmed
    non-empty: the outer except in spawn() must leave it open (leaked, not
    closed) rather than orphan a descendant, exactly like the callback-
    failure and release() paths."""
    mechanism = _Mechanism()
    mechanism.install(nt)
    proc = _FakeProc(604, returncode=None)

    def kill_then_exit():
        proc.kill_calls += 1
        proc.returncode = 1

    proc.kill = kill_then_exit
    nt.setattr(windows_process.subprocess, "Popen", lambda *a, **k: proc)

    def failing_resume(proc):
        mechanism.events.append(("resume", proc.pid))
        raise OSError("NtResumeProcess failed")

    nt.setattr(windows_process, "_resume_suspended_process", failing_resume)
    original_assign = windows_process._assign_new_job
    def pinned_not_empty_assign(proc):
        job = original_assign(proc)
        mechanism.job_empty[id(job)] = False
        return job
    nt.setattr(windows_process, "_assign_new_job", pinned_not_empty_assign)
    port = windows_process.WindowsDaemonProcessPort(term_timeout=0.01, kill_timeout=0.01)

    with pytest.raises(OSError, match="NtResumeProcess failed"):
        port.spawn(DaemonProcessCommand(("codex",), tmp_path))
    job = mechanism.jobs[-1]
    assert port._handles == {}, "handle must still be forgotten even though the Job is leaked"
    assert ("close_job", job) not in mechanism.events


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
