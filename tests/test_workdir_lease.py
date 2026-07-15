"""Port behavior, POSIX lock safety, and composition tests for workdir leases."""
from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

import pytest

from lingtai.adapters.posix.workdir_lease import PosixWorkdirLeaseAdapter
from lingtai.adapters.workdir_lease import select_workdir_lease
from lingtai.kernel.workdir_lease import WorkdirLeasePort

from ._workdir_lease_helpers import FakeWorkdirLease, RecordingWorkdirLease
from tests._agent_presence_helpers import make_test_presence_store
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port


def test_port_exposes_two_operations_with_neutral_signature():
    assert WorkdirLeasePort.__abstractmethods__ == frozenset({"acquire", "release"})
    signature = inspect.signature(WorkdirLeasePort.acquire)
    assert list(signature.parameters) == ["self", "timeout_seconds"]
    assert signature.parameters["timeout_seconds"].default == 0
    assert issubclass(PosixWorkdirLeaseAdapter, WorkdirLeasePort)
    assert issubclass(FakeWorkdirLease, WorkdirLeasePort)


@pytest.fixture(params=("fake", "posix"), ids=("fake", "posix-adapter"))
def lease_pair(request, tmp_path):
    if request.param == "fake":
        key = str(tmp_path / "agent")
        yield (lambda: FakeWorkdirLease(key)), (lambda: FakeWorkdirLease(key))
        return
    target = tmp_path / "agent"
    target.mkdir()
    yield (lambda: PosixWorkdirLeaseAdapter(target)), (lambda: PosixWorkdirLeaseAdapter(target))


def test_contract_collision_zero_timeout_raises_immediately(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    try:
        b = make_b()
        start = time.monotonic()
        with pytest.raises(RuntimeError, match="already in use"):
            b.acquire(0)
        assert time.monotonic() - start < 0.5
    finally:
        a.release()


def test_contract_release_allows_reacquire(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    a.release()
    b = make_b()
    b.acquire(0)
    b.release()


def test_contract_delayed_release_succeeds_before_timeout(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    acquired = threading.Event()

    def try_acquire():
        b = make_b()
        b.acquire(5.0)
        acquired.set()
        b.release()

    thread = threading.Thread(target=try_acquire)
    thread.start()
    time.sleep(0.3)
    assert not acquired.is_set()
    a.release()
    thread.join(timeout=5.0)
    assert acquired.is_set()


def test_contract_expiry_raises_when_never_released(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    try:
        with pytest.raises(RuntimeError, match="already in use"):
            make_b().acquire(0.5)
    finally:
        a.release()


def test_contract_release_is_idempotent(lease_pair):
    make_a, _ = lease_pair
    a = make_a()
    a.acquire(0)
    a.release()
    a.release()
    make_a().release()


def test_posix_collision_error_text_is_exact(tmp_path):
    directory = tmp_path / "agent"
    directory.mkdir()
    first = PosixWorkdirLeaseAdapter(directory)
    first.acquire(0)
    try:
        with pytest.raises(RuntimeError) as exc:
            PosixWorkdirLeaseAdapter(directory).acquire(0)
        assert str(exc.value) == (
            f"Working directory '{directory}' is already in use by another agent. "
            "Each agent needs its own directory."
        )
    finally:
        first.release()


def test_posix_release_closes_before_unlink_and_can_reacquire(tmp_path, monkeypatch):
    directory = tmp_path / "agent"
    directory.mkdir()
    lease = PosixWorkdirLeaseAdapter(directory)
    lease.acquire(0)
    lock_path = directory / ".agent.lock"
    lock_file = lease._lock_file
    assert lock_file is not None and lock_path.exists()
    original_unlink = Path.unlink

    def closed_then_unlink(path: Path, *args: object, **kwargs: object) -> None:
        assert lock_file.closed
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", closed_then_unlink)
    lease.release()
    assert not lock_path.exists()
    lease.acquire(0)
    lease.release()


def test_posix_release_closes_when_unlock_fails(tmp_path, monkeypatch):
    import fcntl

    directory = tmp_path / "agent"
    directory.mkdir()
    lease = PosixWorkdirLeaseAdapter(directory)
    lease.acquire(0)
    lock_file = lease._lock_file
    assert lock_file is not None and not lock_file.closed
    real_flock = fcntl.flock

    def flock_raises_on_unlock(fd, op):
        if op == fcntl.LOCK_UN:
            raise OSError("simulated LOCK_UN failure")
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", flock_raises_on_unlock)
    closed_at_unlink: list[bool] = []
    original_unlink = Path.unlink

    def record_close(path: Path, *args: object, **kwargs: object) -> None:
        closed_at_unlink.append(lock_file.closed)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", record_close)
    lease.release()
    assert closed_at_unlink == [True]
    assert lock_file.closed and lease._lock_file is None
    lease.release()
    monkeypatch.setattr(fcntl, "flock", real_flock)
    second = PosixWorkdirLeaseAdapter(directory)
    second.acquire(0)
    second.release()


def test_posix_release_keeps_inode_when_unlock_and_close_fail(tmp_path, monkeypatch):
    import fcntl

    directory = tmp_path / "agent"
    directory.mkdir()
    lease = PosixWorkdirLeaseAdapter(directory)
    lease.acquire(0)
    real_handle = lease._lock_file
    assert real_handle is not None and not real_handle.closed

    class CloseFails:
        closed = False

        def fileno(self):
            return real_handle.fileno()

        def close(self):
            raise OSError("simulated close failure")

    lease._lock_file = CloseFails()
    real_flock = fcntl.flock

    def flock_raises_on_unlock(fd, op):
        if op == fcntl.LOCK_UN:
            raise OSError("simulated LOCK_UN failure")
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", flock_raises_on_unlock)
    lease.release()
    assert lease._lock_file is None and not real_handle.closed
    assert (directory / ".agent.lock").exists()
    with pytest.raises(RuntimeError, match="already in use"):
        PosixWorkdirLeaseAdapter(directory).acquire(0)
    monkeypatch.setattr(fcntl, "flock", real_flock)
    real_flock(real_handle, fcntl.LOCK_UN)
    real_handle.close()


def test_posix_lock_file_existence_is_not_authority(tmp_path):
    directory = tmp_path / "agent"
    directory.mkdir()
    (directory / ".agent.lock").write_text("")
    lease = PosixWorkdirLeaseAdapter(directory)
    lease.acquire(0)
    lease.release()


def test_base_agent_acquires_the_lease_once_with_ten_seconds(tmp_path):
    from unittest.mock import MagicMock
    from lingtai.kernel.base_agent import BaseAgent

    lease = RecordingWorkdirLease()
    workdir = tmp_path / "agent"
    BaseAgent(
        service=MagicMock(),
        working_dir=workdir,
        workdir_lease=lease,
        snapshot_port=make_test_snapshot_port(),
        agent_presence=make_test_presence_store(),
        lifecycle_clock=make_test_lifecycle_clock(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
    )
    assert lease.acquires == [10]
    assert lease.releases == 0


def test_base_agent_releases_the_lease_when_construction_fails(tmp_path):
    from lingtai.kernel.base_agent import BaseAgent

    workdir = tmp_path / "agent"
    workdir.mkdir()
    (workdir / "system").write_text("")
    recording = RecordingWorkdirLease()
    with pytest.raises(FileExistsError):
        BaseAgent(
            service=object(),
            working_dir=workdir,
            workdir_lease=recording,
            snapshot_port=make_test_snapshot_port(),
            agent_presence=make_test_presence_store(),
            lifecycle_clock=make_test_lifecycle_clock(),
            source_revision_port=make_test_source_revision_port(),
            notification_store=notification_store_for(workdir),
        )
    assert recording.acquires == [10]
    assert recording.releases == 1

    real = PosixWorkdirLeaseAdapter(workdir)
    real.acquire(10)
    with pytest.raises(FileExistsError):
        BaseAgent(
            service=object(),
            working_dir=workdir,
            workdir_lease=real,
            snapshot_port=make_test_snapshot_port(),
            agent_presence=make_test_presence_store(),
            lifecycle_clock=make_test_lifecycle_clock(),
            source_revision_port=make_test_source_revision_port(),
            notification_store=notification_store_for(workdir),
        )
    real2 = PosixWorkdirLeaseAdapter(workdir)
    real2.acquire(0)
    real2.release()
    real.release()


def test_old_workingdir_lock_authority_is_retired():
    from lingtai.kernel.workdir import WorkingDir

    assert not hasattr(WorkingDir, "acquire_lock")
    assert not hasattr(WorkingDir, "release_lock")


def test_base_agent_requires_an_explicit_lease():
    from lingtai.kernel.base_agent import BaseAgent

    parameter = inspect.signature(BaseAgent.__init__).parameters["workdir_lease"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind == inspect.Parameter.KEYWORD_ONLY


def test_selector_returns_the_posix_adapter_on_this_platform(tmp_path):
    assert isinstance(select_workdir_lease(tmp_path), PosixWorkdirLeaseAdapter)


def test_cli_build_agent_injects_the_production_lease(tmp_path, monkeypatch):
    import lingtai.cli as cli

    captured: dict = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self._molt_count = 0

        def _setup_from_init(self):
            return None

    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(cli, "LLMService", lambda **kwargs: object())
    monkeypatch.setattr(cli, "PosixJsonlEventJournalAdapter", lambda *a, **k: object())
    monkeypatch.setattr(cli, "PosixFilesystemMailAdapter", lambda *a, **k: object())
    monkeypatch.setattr(cli, "build_provider_defaults_from_manifest_llm", lambda *a, **k: {})
    cli.build_agent(
        {"manifest": {"llm": {"provider": "test", "model": "test-model"}, "agent_name": "cli-agent"}},
        tmp_path,
    )
    assert isinstance(captured["workdir_lease"], PosixWorkdirLeaseAdapter)


def test_cli_log_rebuild_injects_the_production_lease(tmp_path, monkeypatch):
    import lingtai.cli as cli
    import lingtai.kernel.services.logging as services_logging

    captured: dict = {}

    def fake_rebuild(agent_dir, *, workdir_lease, **kwargs):
        captured["workdir_lease"] = workdir_lease
        return {"status": "ok"}

    monkeypatch.setattr(services_logging, "rebuild_sqlite_event_index", fake_rebuild)
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    class Args:
        log_command = "rebuild"
        agent_dir = None

    args = Args()
    args.agent_dir = agent_dir
    cli._handle_log_command(args)
    assert isinstance(captured["workdir_lease"], PosixWorkdirLeaseAdapter)
