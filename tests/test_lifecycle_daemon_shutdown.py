"""Regression tests for daemon resources during agent teardown.

Production incident 2026-06-04: refresh stopped heartbeat/released lock while
CLI daemon executor workers still kept the old Python process alive. The next
watcher relaunch then hit the duplicate-process guard. These tests pin the
contract that daemon-owned pools/process groups are reclaimed before parent
liveness is withdrawn.
"""
from __future__ import annotations

import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest


def test_daemon_shutdown_for_agent_stop_reclaims_pools_and_cli_processes(tmp_path, monkeypatch):
    from lingtai.tools import daemon as daemon_module

    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=tmp_path / "agent",
        _log=lambda *args, **kwargs: None,
    )

    class RecordingProcessPort:
        def __init__(self):
            self.terminate_all_calls = []

        def terminate_all(self, *, reason=None):
            self.terminate_all_calls.append(reason)
            return 2

    process_port = RecordingProcessPort()
    mgr = daemon_module.DaemonManager(agent, process_port=process_port)

    pending = Future()
    ask_pending = Future()
    mgr._emanations["em-1"] = {
        "future": pending,
        "ask_future": ask_pending,
    }

    class FakePool:
        def __init__(self):
            self.shutdown_calls = []

        def shutdown(self, **kwargs):
            self.shutdown_calls.append(kwargs)

    pool = FakePool()
    cancel = threading.Event()
    mgr._pools.append((pool, cancel))

    killed = []
    monkeypatch.setattr(
        daemon_module,
        "_kill_process_group",
        lambda proc: killed.append(proc.pid),
    )
    proc = SimpleNamespace(pid=4242)
    with mgr._cli_lock:
        mgr._cli_procs.append(proc)

    logs = []
    monkeypatch.setattr(mgr, "_log", lambda event, **fields: logs.append((event, fields)))

    report = mgr.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=0.0)

    assert report["status"] == "shutdown"
    assert report["reason"] == "agent_stop"
    assert report["cancelled"] == 2
    assert report["cli_processes_killed"] == 3
    assert process_port.terminate_all_calls == ["agent_stop"]
    assert report["pools_shutdown"] == 1
    assert report["ask_futures_shutdown"] == 1
    assert killed == [4242]
    assert cancel.is_set()
    assert pool.shutdown_calls == [{"wait": False, "cancel_futures": True}]
    assert mgr._pools == []
    assert mgr._cli_procs == []
    assert mgr._emanations == {}
    assert any(event == "daemon_lifecycle_shutdown" for event, _ in logs)


def test_agent_stop_shuts_down_daemon_before_heartbeat_and_lock(monkeypatch):
    from lingtai.kernel.base_agent import lifecycle

    order = []

    class FakeDaemon:
        def shutdown_for_agent_stop(self, *, reason):
            order.append(("daemon", reason))

    class FakeWorkdir:
        def write_manifest(self, manifest):
            order.append(("manifest", manifest))

    class FakeLease:
        def release(self):
            order.append(("lock", None))

    agent = SimpleNamespace(
        _log=lambda event, **fields: order.append(("log", event)),
        _shutdown=threading.Event(),
        _thread=None,
        _session=SimpleNamespace(close=lambda: order.append(("session", None))),
        _mail_service=None,
        _event_journal=None,
        _workdir=FakeWorkdir(),
        _workdir_lease=FakeLease(),
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: FakeDaemon() if name == "daemon" else None,
    )
    monkeypatch.setattr(lifecycle, "_stop_heartbeat", lambda a: order.append(("heartbeat", None)))

    lifecycle._stop(agent, timeout=0.01)

    assert ("daemon", "agent_stop") in order
    assert order.index(("daemon", "agent_stop")) < order.index(("heartbeat", None))
    assert order.index(("daemon", "agent_stop")) < order.index(("lock", None))
    # Full safety-critical teardown order: manifest → heartbeat → release. The
    # heartbeat-before-release edge is asserted explicitly — without it, swapping
    # the last two operations (release the lease before stopping the heartbeat)
    # would still pass, yet a quick relaunch could race a still-fresh heartbeat
    # into a directory whose lease was already dropped. See the Contract's
    # manifest → heartbeat → release rule.
    manifest_i = order.index(("manifest", {"agent": "test"}))
    heartbeat_i = order.index(("heartbeat", None))
    lock_i = order.index(("lock", None))
    assert manifest_i < heartbeat_i < lock_i


def test_stop_heartbeat_withdraws_through_presence_port(monkeypatch):
    """``_stop_heartbeat`` withdraws own liveness through the presence Port.

    The former direct ``.agent.heartbeat`` unlink is now
    ``agent._agent_presence.withdraw_heartbeat()``. This pins that the
    withdrawal flows through the injected Port (best-effort inside the adapter),
    not a direct filesystem call in Core.
    """
    from lingtai.kernel.base_agent import lifecycle
    from tests._agent_presence_helpers import RecordingAgentPresenceStore

    presence = RecordingAgentPresenceStore()
    agent = SimpleNamespace(
        _heartbeat_thread=None,
        _heartbeat_stop=threading.Event(),
        _heartbeat=123.0,
        _agent_presence=presence,
        _log=lambda *a, **k: None,
    )

    lifecycle._stop_heartbeat(agent)

    assert presence.withdraws == 1


def test_stop_teardown_order_withdraws_via_port_between_manifest_and_release(monkeypatch):
    """Full ``_stop`` order manifest → heartbeat-withdraw(Port) → lease-release.

    Uses a real ``_stop_heartbeat`` (not monkeypatched) so the presence Port's
    ``withdraw_heartbeat`` is the recorded 'heartbeat' step, proving the Port
    withdrawal sits inside the safety-critical teardown window.
    """
    from lingtai.kernel.base_agent import lifecycle

    order = []

    class RecordingPresence:
        def observe_manifest(self):  # pragma: no cover - not used here
            raise AssertionError("not expected")

        def observe_heartbeat(self):  # pragma: no cover - not used here
            raise AssertionError("not expected")

        def publish_heartbeat(self, wall_seconds):  # pragma: no cover
            raise AssertionError("not expected")

        def withdraw_heartbeat(self):
            order.append(("heartbeat", None))

    class FakeWorkdir:
        def write_manifest(self, manifest):
            order.append(("manifest", manifest))

    class FakeLease:
        def release(self):
            order.append(("lock", None))

    agent = SimpleNamespace(
        _log=lambda event, **fields: None,
        _shutdown=threading.Event(),
        _thread=None,
        _heartbeat_thread=None,
        _heartbeat_stop=threading.Event(),
        _heartbeat=1.0,
        _session=SimpleNamespace(close=lambda: None),
        _mail_service=None,
        _event_journal=None,
        _workdir=FakeWorkdir(),
        _workdir_lease=FakeLease(),
        _agent_presence=RecordingPresence(),
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: None,
    )

    lifecycle._stop(agent, timeout=0.01)

    manifest_i = order.index(("manifest", {"agent": "test"}))
    heartbeat_i = order.index(("heartbeat", None))
    lock_i = order.index(("lock", None))
    assert manifest_i < heartbeat_i < lock_i


def test_daemon_shutdown_waits_for_cli_ask_future_before_releasing_liveness(tmp_path, monkeypatch):
    from lingtai.tools import daemon as daemon_module

    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=tmp_path / "agent",
        _log=lambda *args, **kwargs: None,
    )
    # Default construction selects the platform's production process port
    # (POSIX or Windows); this test never spawns, it only pins teardown order.
    mgr = daemon_module.DaemonManager(agent)

    primary_done = Future()
    primary_done.set_result("done")
    ask_done = Future()
    mgr._emanations["em-1"] = {
        "future": primary_done,
        "ask_future": ask_done,
    }

    waits = []

    def fake_wait(futures, timeout):
        waits.append((set(futures), timeout))
        ask_done.set_result("ask done")

    monkeypatch.setattr(daemon_module, "wait", fake_wait)
    report = mgr.shutdown_for_agent_stop(reason="agent_stop", wait_timeout=2.5)

    assert waits == [({primary_done, ask_done}, 2.5)]
    assert report["ask_futures_shutdown"] == 1
    assert report["futures_remaining"] == 0


def test_stop_releases_workdir_lease_when_intermediate_teardown_raises(monkeypatch):
    """Issue #661: an unwrapped teardown failure must not wedge ``.agent.lock``.

    ``_stop`` releases the workdir lease in a ``finally``, so a raise from any
    intermediate step (session close, manifest write, heartbeat stop) still
    leaves the directory acquirable. The exception propagates so the caller
    still sees the teardown failure.
    """
    from lingtai.kernel.base_agent import lifecycle

    released = []

    class FakeLease:
        def release(self):
            released.append(True)

    class ExplodingSession:
        def close(self):
            raise RuntimeError("session close boom")

    agent = SimpleNamespace(
        _log=lambda event, **fields: None,
        _shutdown=threading.Event(),
        _thread=None,
        _session=ExplodingSession(),
        _mail_service=None,
        _event_journal=None,
        _workdir=SimpleNamespace(write_manifest=lambda m: None),
        _workdir_lease=FakeLease(),
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: None,
    )

    with pytest.raises(RuntimeError, match="session close boom"):
        lifecycle._stop(agent, timeout=0.01)

    assert released == [True]


def test_stop_releases_workdir_lease_when_manifest_write_raises(monkeypatch):
    """Issue #661: a raise at the LAST teardown step still releases the lease."""
    from lingtai.kernel.base_agent import lifecycle

    released = []

    class FakeLease:
        def release(self):
            released.append(True)

    class ExplodingWorkdir:
        def write_manifest(self, manifest):
            raise OSError("disk full")

    agent = SimpleNamespace(
        _log=lambda event, **fields: None,
        _shutdown=threading.Event(),
        _thread=None,
        _session=SimpleNamespace(close=lambda: None),
        _mail_service=None,
        _event_journal=None,
        _workdir=ExplodingWorkdir(),
        _workdir_lease=FakeLease(),
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: None,
    )

    with pytest.raises(OSError, match="disk full"):
        lifecycle._stop(agent, timeout=0.01)

    assert released == [True]


def test_stop_releases_workdir_lease_when_agent_service_teardown_raises(monkeypatch):
    """Issue #661 also wraps the post-quiescence subclass service hook."""
    from lingtai.kernel.base_agent import lifecycle

    released = []

    class FakeLease:
        def release(self):
            released.append(True)

    def exploding_services():
        raise RuntimeError("agent service close boom")

    agent = SimpleNamespace(
        _log=lambda event, **fields: None,
        _shutdown=threading.Event(),
        _thread=None,
        _llm_worker_poison_future=None,
        inbox=None,
        _task_card_controller=None,
        _task_card_manager=None,
        _close_agent_owned_services_after_quiescence=exploding_services,
        _session=SimpleNamespace(close=lambda: None),
        _mail_service=None,
        _event_journal=None,
        _workdir=SimpleNamespace(write_manifest=lambda m: None),
        _workdir_lease=FakeLease(),
        _workdir_lease_acquired=True,
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: None,
    )

    with pytest.raises(RuntimeError, match="agent service close boom"):
        lifecycle._stop(agent, timeout=0.01)

    assert released == [True]
    assert not agent._workdir_lease_acquired


def test_stop_timeout_retains_services_heartbeat_and_lease_until_execution_quiesces(
    monkeypatch,
):
    from lingtai.kernel.base_agent import lifecycle

    order: list[str] = []
    release_provider = threading.Event()
    provider_started = threading.Event()
    provider_future = Future()

    class FakeTaskCard:
        def shutdown_for_agent_stop(self, *, reason):
            order.append(f"task_card:{reason}")

    class FakeWorkdir:
        def write_manifest(self, manifest):
            order.append("manifest")

    class FakeLease:
        released = False

        def release(self):
            self.released = True
            order.append("lease")

    lease = FakeLease()

    def provider_run_loop():
        provider_started.set()
        assert release_provider.wait(timeout=5)
        assert not lease.released, "provider must not write after lease release"
        order.append("provider_state_write")
        provider_future.set_result(None)

    run_loop = threading.Thread(target=provider_run_loop, daemon=True)
    run_loop.start()
    assert provider_started.wait(timeout=1)

    agent = SimpleNamespace(
        _log=lambda event, **fields: order.append(event),
        _shutdown=threading.Event(),
        _thread=run_loop,
        _llm_worker_poison_future=provider_future,
        inbox=None,
        _task_card_controller=FakeTaskCard(),
        _task_card_manager=None,
        _close_agent_owned_services_after_quiescence=lambda: order.append("services"),
        _session=SimpleNamespace(close=lambda: order.append("session")),
        _mail_service=None,
        _event_journal=None,
        _workdir=FakeWorkdir(),
        _workdir_lease=lease,
        _build_manifest=lambda: {"agent": "test"},
        get_capability=lambda name: None,
    )
    monkeypatch.setattr(lifecycle, "_stop_heartbeat", lambda a: order.append("heartbeat"))

    timed_out = lifecycle._stop(agent, timeout=0.01)

    assert timed_out.status is lifecycle.StopStatus.TIMED_OUT
    assert timed_out.run_loop_alive
    assert timed_out.provider_worker_alive
    assert not lease.released
    assert not any(
        step in order
        for step in (
            "task_card:agent_stop",
            "services",
            "session",
            "manifest",
            "heartbeat",
            "lease",
        )
    )

    release_provider.set()
    run_loop.join(timeout=1)
    assert not run_loop.is_alive()

    stopped = lifecycle._stop(agent, timeout=1.0)

    assert stopped.status is lifecycle.StopStatus.STOPPED
    assert not stopped.run_loop_alive
    assert not stopped.provider_worker_alive
    assert lease.released
    provider_i = order.index("provider_state_write")
    services_i = order.index("services")
    session_i = order.index("session")
    manifest_i = order.index("manifest")
    heartbeat_i = order.index("heartbeat")
    lease_i = order.index("lease")
    assert provider_i < services_i < session_i < manifest_i < heartbeat_i < lease_i
