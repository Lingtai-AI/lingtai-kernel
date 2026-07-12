"""Shared contract and production-composition tests for the workdir lease.

The Core-owned Port is ``lingtai.kernel.workdir_lease.WorkdirLeasePort``. Its
only production adapter is
``lingtai.adapters.posix.workdir_lease.PosixWorkdirLeaseAdapter``. These tests
exercise the *observable Port semantics* (``acquire``/``release``, collision,
delayed release, zero-timeout failure, expiry, idempotent release) against both
the production adapter and an independent in-memory fake, and prove the
architecture: Core never names the concrete adapter, the Core Port package
imports no platform lock mechanism, the old ``WorkingDir`` lock authority is
retired, and the composition roots inject the adapter.
"""
from __future__ import annotations

import ast
import inspect
import threading
import time
from pathlib import Path

import pytest

from lingtai.kernel.workdir_lease import WorkdirLeasePort
from lingtai.adapters.posix.workdir_lease import PosixWorkdirLeaseAdapter
from lingtai.adapters.workdir_lease import select_workdir_lease

from ._workdir_lease_helpers import FakeWorkdirLease
from tests._notification_store_helpers import notification_store_for


# --------------------------------------------------------------------------
# Port surface — technology-neutral, no filesystem/platform vocabulary.
# --------------------------------------------------------------------------


def test_port_exposes_only_acquire_release():
    # The abstract surface is exactly the two observable operations.
    assert WorkdirLeasePort.__abstractmethods__ == frozenset({"acquire", "release"})


def test_port_acquire_signature_is_technology_neutral():
    sig = inspect.signature(WorkdirLeasePort.acquire)
    assert list(sig.parameters) == ["self", "timeout_seconds"]
    assert sig.parameters["timeout_seconds"].default == 0
    # The Port module names no filesystem/platform mechanism and never imports a
    # lock library or pathlib — concrete exclusion lives only in the adapter.
    import lingtai.kernel.workdir_lease as port_mod

    text = Path(port_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "fcntl",
        "flock",
        "msvcrt",
        ".agent.lock",
        "import Path",
        "pathlib",
        "open(",
    ):
        assert forbidden not in text, forbidden


def test_production_adapter_is_a_port():
    assert issubclass(PosixWorkdirLeaseAdapter, WorkdirLeasePort)


def test_fake_is_a_port():
    assert issubclass(FakeWorkdirLease, WorkdirLeasePort)


# --------------------------------------------------------------------------
# Shared contract — the SAME assertions run against the production adapter and
# the independent in-memory fake. Each yields (make_a, make_b): two factories
# that build distinct lease instances contending for the SAME directory.
# --------------------------------------------------------------------------


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
        # Zero timeout = one immediate attempt, not a wait.
        assert time.monotonic() - start < 0.5
    finally:
        a.release()


def test_contract_release_allows_reacquire(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    a.release()
    b = make_b()
    b.acquire(0)  # should not raise
    b.release()


def test_contract_delayed_release_succeeds_before_timeout(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)

    acquired = threading.Event()

    def _try():
        b = make_b()
        b.acquire(5.0)
        acquired.set()
        b.release()

    t = threading.Thread(target=_try)
    t.start()
    time.sleep(0.3)
    assert not acquired.is_set()  # still waiting on the held lease

    a.release()
    t.join(timeout=5.0)
    assert acquired.is_set()


def test_contract_expiry_raises_when_never_released(lease_pair):
    make_a, make_b = lease_pair
    a = make_a()
    a.acquire(0)
    try:
        b = make_b()
        with pytest.raises(RuntimeError, match="already in use"):
            b.acquire(0.5)
    finally:
        a.release()


def test_contract_release_is_idempotent(lease_pair):
    make_a, _ = lease_pair
    a = make_a()
    a.acquire(0)
    a.release()
    a.release()  # safe to call twice
    # release before any acquire is also safe
    make_a().release()


# --------------------------------------------------------------------------
# POSIX mechanism specifics — error text, close-before-unlink release order.
# --------------------------------------------------------------------------


def test_posix_collision_error_text_is_exact(tmp_path):
    d = tmp_path / "agent"
    d.mkdir()
    a = PosixWorkdirLeaseAdapter(d)
    a.acquire(0)
    try:
        b = PosixWorkdirLeaseAdapter(d)
        with pytest.raises(RuntimeError) as exc:
            b.acquire(0)
        assert str(exc.value) == (
            f"Working directory '{d}' is already in use by another agent. "
            "Each agent needs its own directory."
        )
    finally:
        a.release()


def test_posix_release_closes_and_unlinks_lock_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    d = tmp_path / "agent"
    d.mkdir()
    a = PosixWorkdirLeaseAdapter(d)
    a.acquire(0)
    lock_path = d / ".agent.lock"
    lock_file = a._lock_file
    assert lock_file is not None
    assert lock_path.exists()

    original_unlink = Path.unlink

    def assert_closed_then_unlink(path: Path, *args: object, **kwargs: object) -> None:
        assert lock_file.closed
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", assert_closed_then_unlink)
    a.release()
    assert not lock_path.exists()
    # Handle reset so a re-acquire works and release stays idempotent.
    a.acquire(0)
    a.release()


def test_posix_release_closes_even_when_unlock_raises(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    """If explicit ``LOCK_UN`` raises, the descriptor is still closed before any
    unlink and state is reset — never unlink a still-open locked inode.

    Reviewer's error-path probe: with the old handle left open at unlink, a second
    adapter could bind a fresh inode to the recreated path while the old
    descriptor still held the OS lock (two authorities). Closing in a ``finally``
    before unlink prevents that.
    """
    import fcntl

    d = tmp_path / "agent"
    d.mkdir()
    a = PosixWorkdirLeaseAdapter(d)
    a.acquire(0)
    lock_file = a._lock_file
    assert lock_file is not None and not lock_file.closed

    real_flock = fcntl.flock

    def flock_raises_on_unlock(fd, op):
        if op == fcntl.LOCK_UN:
            raise OSError("simulated LOCK_UN failure")
        return real_flock(fd, op)

    monkeypatch.setattr(fcntl, "flock", flock_raises_on_unlock)

    handle_closed_at_unlink: list[bool] = []
    original_unlink = Path.unlink

    def record_closed_then_unlink(path: Path, *args: object, **kwargs: object) -> None:
        handle_closed_at_unlink.append(lock_file.closed)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", record_closed_then_unlink)

    a.release()  # must not raise despite the unlock error

    assert handle_closed_at_unlink == [True]  # closed at the unlink boundary
    assert lock_file.closed
    assert a._lock_file is None
    a.release()  # idempotent after the error path

    # With the old descriptor closed, a second adapter can acquire — no lingering
    # second OS-lock authority on a stale open fd.
    monkeypatch.setattr(fcntl, "flock", real_flock)
    b = PosixWorkdirLeaseAdapter(d)
    b.acquire(0)
    b.release()


def test_posix_release_keeps_named_inode_when_unlock_and_close_fail(tmp_path, monkeypatch):
    import fcntl
    d = tmp_path / "agent"
    d.mkdir()
    a = PosixWorkdirLeaseAdapter(d)
    a.acquire(0)
    real_handle = a._lock_file
    assert real_handle is not None and not real_handle.closed

    class CloseFailsWithoutClosing:
        closed = False
        def fileno(self):
            return real_handle.fileno()
        def close(self):
            raise OSError("simulated close failure")

    a._lock_file = CloseFailsWithoutClosing()
    real_flock = fcntl.flock
    def flock_raises_on_unlock(fd, op):
        if op == fcntl.LOCK_UN:
            raise OSError("simulated LOCK_UN failure")
        return real_flock(fd, op)
    monkeypatch.setattr(fcntl, "flock", flock_raises_on_unlock)
    a.release()
    assert a._lock_file is None and not real_handle.closed
    assert (d / ".agent.lock").exists()
    with pytest.raises(RuntimeError, match="already in use"):
        PosixWorkdirLeaseAdapter(d).acquire(0)
    monkeypatch.setattr(fcntl, "flock", real_flock)
    real_flock(real_handle, fcntl.LOCK_UN)
    real_handle.close()


def test_posix_lock_file_existence_is_not_authority(tmp_path):
    """A stale, unlocked ``.agent.lock`` must not block acquisition.

    Authority is holding the OS lock, not the file's presence — so a leftover
    lock file (no live holder) is acquirable.
    """
    d = tmp_path / "agent"
    d.mkdir()
    (d / ".agent.lock").write_text("")  # stale file, nobody holds the flock
    a = PosixWorkdirLeaseAdapter(d)
    a.acquire(0)  # must succeed despite the file already existing
    a.release()


# --------------------------------------------------------------------------
# Architecture — Core is concrete-mechanism-free; single lock authority.
# --------------------------------------------------------------------------


def _module_imports_forbidden(module, forbidden_modules) -> list[str]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if any(node.module == f or node.module.startswith(f + ".") for f in forbidden_modules):
                hits.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if any(alias.name == f or alias.name.startswith(f + ".") for f in forbidden_modules):
                    hits.append(alias.name)
    return hits


def test_core_workdir_lease_package_imports_no_platform_mechanism():
    import lingtai.kernel.workdir_lease as core_port

    # No platform lock library and no concrete adapter under the Core Port.
    assert _module_imports_forbidden(core_port, ["fcntl", "msvcrt", "lingtai.adapters"]) == []
    text = Path(core_port.__file__).read_text(encoding="utf-8")
    assert "flock" not in text
    assert "PosixWorkdirLeaseAdapter" not in text


def test_core_base_agent_never_names_the_concrete_adapter():
    import lingtai.kernel.base_agent as ba

    source = Path(ba.__file__).read_text(encoding="utf-8")
    assert "PosixWorkdirLeaseAdapter" not in source
    assert "select_workdir_lease" not in source
    assert "lingtai.adapters" not in source


def test_base_agent_acquires_the_lease_exactly_once_with_ten_seconds(tmp_path):
    """Behavioral: construction acquires the injected lease once, timeout=10."""
    from unittest.mock import MagicMock

    from lingtai.kernel.base_agent import BaseAgent
    from ._workdir_lease_helpers import RecordingWorkdirLease

    lease = RecordingWorkdirLease()
    BaseAgent(
        service=MagicMock(),
        working_dir=tmp_path / "agent",
        workdir_lease=lease,
        notification_store=notification_store_for(tmp_path / "agent"),
    )
    assert lease.acquires == [10]
    assert lease.releases == 0  # healthy construction never releases


def test_base_agent_releases_the_lease_when_construction_fails(tmp_path):
    """A post-acquire construction fault releases the caller-owned lease once and
    re-raises the original exception, so a retry is not seen as a live collision.

    Reviewer's probe: ``system`` pre-exists as a *file*, so
    ``system_dir.mkdir(exist_ok=True)`` raises ``FileExistsError`` after acquire.
    """
    from lingtai.kernel.base_agent import BaseAgent
    from ._workdir_lease_helpers import RecordingWorkdirLease

    workdir = tmp_path / "agent"
    workdir.mkdir()
    (workdir / "system").write_text("")  # make system_dir.mkdir fail post-acquire

    recording = RecordingWorkdirLease()
    with pytest.raises(FileExistsError):
        BaseAgent(service=object(), working_dir=workdir, workdir_lease=recording, notification_store=notification_store_for(workdir))
    assert recording.acquires == [10]  # original exception (not a release error) propagated
    assert recording.releases == 1  # rolled back exactly once

    # With a real production lease the OS lock is genuinely freed: a fresh adapter
    # can acquire the same directory after the failed construction.
    real = PosixWorkdirLeaseAdapter(workdir)
    real.acquire(10)
    with pytest.raises(FileExistsError):
        BaseAgent(service=object(), working_dir=workdir, workdir_lease=real, notification_store=notification_store_for(workdir))
    real2 = PosixWorkdirLeaseAdapter(workdir)
    real2.acquire(0)  # must not raise — the failed construction released the lock
    real2.release()
    real.release()


def test_agent_cleans_owned_journal_without_hiding_construction_errors(tmp_path, monkeypatch):
    from unittest.mock import Mock
    import lingtai.adapters.posix.event_journal as event_journal_module
    import lingtai.adapters.workdir_lease as lease_selector_module
    from lingtai import Agent
    from ._workdir_lease_helpers import RecordingWorkdirLease

    journal = Mock()
    monkeypatch.setattr(event_journal_module, "PosixJsonlEventJournalAdapter", lambda *a, **k: journal)
    def selection_fails(_working_dir):
        raise NotImplementedError("selector failed")
    monkeypatch.setattr(lease_selector_module, "select_workdir_lease", selection_fails)
    with pytest.raises(NotImplementedError, match="selector failed"):
        Agent(service=object(), working_dir=tmp_path / "selector-agent")
    journal.close.assert_called_once_with()

    journal.reset_mock()
    journal.close.side_effect = OSError("journal close failed")
    workdir = tmp_path / "constructor-agent"
    workdir.mkdir()
    (workdir / "system").write_text("")
    lease = RecordingWorkdirLease()
    with pytest.raises(FileExistsError):
        Agent(service=object(), working_dir=workdir, workdir_lease=lease)
    assert lease.acquires == [10]
    assert lease.releases == 1
    journal.close.assert_called_once_with()


def test_core_consumers_are_adapter_free_and_use_no_retired_lock_authority():
    # Core lifecycle and the SQLite rebuild import no concrete adapter and never
    # call the retired ``WorkingDir`` lock methods. (Their acquire/release
    # behavior is pinned behaviorally elsewhere in this module and in
    # test_services_logging.py, so this test guards only the import boundary and
    # the single-authority invariant.)
    import lingtai.kernel.base_agent.lifecycle as lc
    import lingtai.kernel.services.logging as svc

    for module in (lc, svc):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert _module_imports_forbidden(module, ["lingtai.adapters"]) == []
        assert "acquire_lock" not in source
        assert "release_lock" not in source


def test_old_workingdir_lock_authority_is_retired():
    """Single lock authority: WorkingDir no longer owns acquire/release_lock."""
    from lingtai.kernel.workdir import WorkingDir

    assert not hasattr(WorkingDir, "acquire_lock")
    assert not hasattr(WorkingDir, "release_lock")
    text = Path(inspect.getfile(WorkingDir)).read_text(encoding="utf-8")
    assert "def acquire_lock" not in text
    assert "def release_lock" not in text
    # The retired module-level platform lock branch is gone from Core workdir.
    assert "msvcrt" not in text
    assert "import fcntl" not in text


def test_base_agent_requires_an_explicit_lease():
    """No None/no-op fallback: constructing without a lease fails loudly."""
    from lingtai.kernel.base_agent import BaseAgent

    sig = inspect.signature(BaseAgent.__init__)
    param = sig.parameters["workdir_lease"]
    # Required (no default) and keyword-only.
    assert param.default is inspect.Parameter.empty
    assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_fake_alone_cannot_satisfy_conformance():
    """The shared contract runs against the production adapter, not only the fake
    — the parametrized fixture names both backends and builds a real adapter."""
    src = inspect.getsource(lease_pair)
    assert 'params=("fake", "posix")' in src
    assert "PosixWorkdirLeaseAdapter(target)" in src


# --------------------------------------------------------------------------
# Composition roots inject the production adapter.
# --------------------------------------------------------------------------


def test_selector_returns_the_posix_adapter_on_this_platform(tmp_path):
    lease = select_workdir_lease(tmp_path)
    assert isinstance(lease, PosixWorkdirLeaseAdapter)


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
    monkeypatch.setattr(
        cli, "build_provider_defaults_from_manifest_llm", lambda *a, **k: {}
    )
    data = {
        "manifest": {
            "llm": {"provider": "test", "model": "test-model"},
            "agent_name": "cli-agent",
        }
    }

    cli.build_agent(data, tmp_path)
    lease = captured["workdir_lease"]
    assert isinstance(lease, PosixWorkdirLeaseAdapter)


def test_cli_log_rebuild_injects_the_production_lease(tmp_path, monkeypatch):
    import lingtai.cli as cli

    captured: dict = {}

    def fake_rebuild(agent_dir, *, workdir_lease, **kwargs):
        captured["workdir_lease"] = workdir_lease
        return {"status": "ok"}

    # Patch the symbol at its source so the CLI's late import picks it up.
    import lingtai.kernel.services.logging as svc

    monkeypatch.setattr(svc, "rebuild_sqlite_event_index", fake_rebuild)

    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()

    class Args:
        log_command = "rebuild"
        agent_dir = None

    args = Args()
    args.agent_dir = agent_dir
    cli._handle_log_command(args)
    assert isinstance(captured["workdir_lease"], PosixWorkdirLeaseAdapter)


# --------------------------------------------------------------------------
# Checkout provenance — the code under test comes from this checkout's source.
# --------------------------------------------------------------------------


def test_checkout_source_provenance():
    import lingtai

    resolved = Path(lingtai.__file__).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    expected = (repo_root / "src" / "lingtai" / "__init__.py").resolve()
    assert resolved == expected, f"lingtai imported from {resolved}, expected {expected}"
