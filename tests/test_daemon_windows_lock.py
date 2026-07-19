"""Windows daemon-state lock seam: exact byte range, dispatch, and POSIX parity.

The cross-platform tier runs the real ``run_dir`` seam code with only the
``msvcrt`` mechanism module replaced (the ``test_workdir_lease`` pattern), so
the byte-range arguments and the LK_NBLCK-retry policy are pinned even on
POSIX CI; the native tier proves real cross-process contention on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from lingtai.tools.daemon import run_dir as run_dir_module
from lingtai.tools.daemon.run_dir import DaemonRunDir

posix_mechanism = pytest.mark.skipif(
    os.name == "nt", reason="fcntl flock mechanism is POSIX-only"
)
windows_mechanism = pytest.mark.skipif(
    os.name != "nt", reason="msvcrt byte-range mechanism requires native Windows"
)


def _make_run_dir(tmp_path: Path) -> DaemonRunDir:
    parent = tmp_path / "agent"
    parent.mkdir(parents=True, exist_ok=True)
    return DaemonRunDir(
        parent_working_dir=parent,
        handle="em-lock",
        run_id="em-lock",
        task="t",
        tools=[],
        model="m",
        max_turns=1,
        timeout_s=30.0,
        parent_addr="agent",
        parent_pid=os.getpid(),
        system_prompt="p",
    )


def _recording_msvcrt(calls, *, fail_lock_attempts: int = 0):
    fake = types.SimpleNamespace(LK_NBLCK=2, LK_LOCK=1, LK_UNLCK=0)
    state = {"failures_left": fail_lock_attempts}

    def locking(fd: int, mode: int, nbytes: int) -> None:
        op = {fake.LK_NBLCK: "lock", fake.LK_LOCK: "blocking-lock", fake.LK_UNLCK: "unlock"}[mode]
        calls.append((op, os.lseek(fd, 0, os.SEEK_CUR), nbytes))
        if op == "lock" and state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise OSError(36, "resource deadlock avoided")

    fake.locking = locking
    return fake


def test_windows_state_lock_locks_exactly_byte_zero_length_one(tmp_path, monkeypatch):
    """The Windows mechanism locks byte 0, length 1, position 0 on lock+unlock.

    Calls the module-internal Windows mechanism directly with a recording
    ``msvcrt`` (no ``os.name`` patch is needed: the platform gate lives in the
    ``_exclusive_state_lock`` dispatcher, which has its own test below).
    """
    calls: list[tuple[str, int, int]] = []
    lock_path = tmp_path / ".daemon-state.lock"
    monkeypatch.setitem(sys.modules, "msvcrt", _recording_msvcrt(calls))
    with run_dir_module._windows_exclusive_state_lock(lock_path):
        assert lock_path.is_file() and lock_path.stat().st_size == 1
    assert calls == [("lock", 0, 1), ("unlock", 0, 1)]


def test_windows_state_lock_retries_nonblocking_and_never_lk_lock(tmp_path, monkeypatch):
    """Contention retries stay LK_NBLCK + sleep; the hidden-timeout LK_LOCK
    blocking mode is never used."""
    calls: list[tuple[str, int, int]] = []
    lock_path = tmp_path / ".daemon-state.lock"
    monkeypatch.setitem(sys.modules, "msvcrt", _recording_msvcrt(calls, fail_lock_attempts=3))
    start = time.monotonic()
    with run_dir_module._windows_exclusive_state_lock(lock_path):
        pass
    elapsed = time.monotonic() - start
    assert calls == [("lock", 0, 1)] * 4 + [("unlock", 0, 1)]
    assert all(op != "blocking-lock" for op, _, _ in calls)
    assert elapsed < 5.0  # retried promptly, not a CRT ~1s-per-attempt cadence


def test_exclusive_state_lock_dispatches_to_windows_mechanism_on_nt(tmp_path, monkeypatch):
    entered: list[Path] = []

    @contextmanager
    def fake_windows_lock(lock_path):
        entered.append(lock_path)
        yield

    lock_path = tmp_path / ".daemon-state.lock"  # constructed before patching os.name
    monkeypatch.setattr(run_dir_module, "_windows_exclusive_state_lock", fake_windows_lock)
    monkeypatch.setattr(os, "name", "nt")
    with run_dir_module._exclusive_state_lock(lock_path):
        pass
    assert entered == [lock_path]


def test_state_transaction_and_state_file_lock_share_one_seam(tmp_path, monkeypatch):
    """Both daemon-state lock entry points route through ``_exclusive_state_lock``
    on the same fixed ``.daemon-state.lock`` file."""
    run = _make_run_dir(tmp_path)
    requested: list[Path] = []
    real_seam = run_dir_module._exclusive_state_lock

    @contextmanager
    def recording_seam(lock_path):
        requested.append(lock_path)
        with real_seam(lock_path):
            yield

    monkeypatch.setattr(run_dir_module, "_exclusive_state_lock", recording_seam)
    run.update_state(probe="value")
    with DaemonRunDir.state_file_lock(run.path):
        pass
    assert requested == [run.path / ".daemon-state.lock"] * 2
    assert DaemonRunDir.read_state_from_disk(run.path)["probe"] == "value"


@posix_mechanism
def test_posix_state_lock_mechanism_is_unchanged(tmp_path, monkeypatch):
    """POSIX keeps blocking flock(LOCK_EX) / flock(LOCK_UN) on the same file."""
    import fcntl

    run = _make_run_dir(tmp_path)
    ops: list[int] = []
    real_flock = fcntl.flock

    def recording_flock(fd, op):
        ops.append(op)
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", recording_flock)
    run.update_state(probe="posix")
    with DaemonRunDir.state_file_lock(run.path):
        pass
    assert ops == [fcntl.LOCK_EX, fcntl.LOCK_UN] * 2
    assert run.state_lock_path.exists()


_HOLD_LOCK_SNIPPET = """
import sys, time
from lingtai.tools.daemon.run_dir import DaemonRunDir

with DaemonRunDir.state_file_lock(sys.argv[1]):
    print("HELD", flush=True)
    time.sleep(float(sys.argv[2]))
print("RELEASED", flush=True)
"""


@windows_mechanism
def test_windows_state_lock_serializes_across_real_processes(tmp_path):
    """A second process's transaction blocks until the holder releases."""
    run = _make_run_dir(tmp_path)
    hold_s = 1.5
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SNIPPET, str(run.path), str(hold_s)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "HELD", child.stderr.read()
        start = time.monotonic()
        run.update_state(contended="yes")
        elapsed = time.monotonic() - start
        assert elapsed >= hold_s * 0.5, "update_state did not wait for the cross-process lock"
    finally:
        child.wait(timeout=30)
    assert DaemonRunDir.read_state_from_disk(run.path)["contended"] == "yes"


@windows_mechanism
def test_windows_crashed_lock_holder_never_wedges_the_state_file(tmp_path):
    """The OS releases the byte range when a holder dies mid-transaction."""
    run = _make_run_dir(tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SNIPPET, str(run.path), "60"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().strip() == "HELD", child.stderr.read()
    child.kill()
    child.wait(timeout=30)
    run.update_state(recovered="yes")
    assert DaemonRunDir.read_state_from_disk(run.path)["recovered"] == "yes"
