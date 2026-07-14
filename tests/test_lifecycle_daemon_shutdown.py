"""Regression tests for daemon resources during agent teardown.

Production incident 2026-06-04: refresh stopped heartbeat/released lock while
CLI daemon executor workers still kept the old Python process alive. The next
watcher relaunch then hit the duplicate-process guard. These tests pin the
contract that daemon-owned pools/process groups are reclaimed before parent
liveness is withdrawn.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import Future
from types import SimpleNamespace


def test_daemon_shutdown_for_agent_stop_reclaims_pools_and_cli_processes(tmp_path, monkeypatch):
    from lingtai.tools import daemon as daemon_module

    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=tmp_path / "agent",
        _log=lambda *args, **kwargs: None,
    )
    mgr = daemon_module.DaemonManager(agent)

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
    assert report["cli_processes_killed"] == 1
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
    import lingtai.tools.soul.flow as soul_flow

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
    # _stop() now calls agent._cancel_soul_timer() (BaseAgent delegates to the
    # soul flow hook); mirror that so the monkeypatched cancel still records.
    agent._cancel_soul_timer = lambda: soul_flow._cancel_soul_timer(agent)

    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda a: order.append(("soul", None)))
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
    import lingtai.tools.soul.flow as soul_flow

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
    agent._cancel_soul_timer = lambda: None
    monkeypatch.setattr(soul_flow, "_cancel_soul_timer", lambda a: None)

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


# ---------------------------------------------------------------------------
# Same-agent refresh survival — _shutdown_daemon_runtime's PREPARE branch.
# ``.refresh.taken`` existing is the ONE signal that distinguishes a real
# refresh-triggered stop from an ordinary user stop/sleep/suspend, since both
# funnel through the exact same _stop()/_shutdown_daemon_runtime path.
# ---------------------------------------------------------------------------


def test_shutdown_daemon_runtime_enters_drain_instead_of_ordinary_shutdown_on_real_refresh_handoff(
    tmp_path, monkeypatch,
):
    """When ``.refresh.taken`` exists (a real refresh handoff in progress)
    and the daemon manager has a real nonterminal run, _shutdown_daemon_runtime
    must call the REAL _prepare_refresh_host (committing a real marker) and
    enter the drain loop on a background thread — NOT call
    shutdown_for_agent_stop, which would kill the exact in-flight work PREPARE
    just committed to keep running."""
    from lingtai.kernel.base_agent import lifecycle
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    (agent._working_dir / ".refresh.taken").touch()

    import lingtai.tools.daemon.refresh_host as refresh_host_module
    monkeypatch.setattr(
        refresh_host_module, "_read_cmdline",
        lambda pid: f"python -m lingtai run {agent._working_dir}" if pid == os.getpid() else None,
    )

    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)  # default Future() is not done()

    shutdown_calls = []
    monkeypatch.setattr(mgr, "shutdown_for_agent_stop", lambda **kw: shutdown_calls.append(kw))

    drain_calls = []
    real_run_drain_loop = mgr._run_drain_loop

    def _bounded_drain_loop(marker, **kwargs):
        drain_calls.append(marker)
        # Bound the real drain loop so the background thread this test
        # spawns actually terminates instead of polling forever — the run
        # never reaches terminal state in this test, so max_ticks=1 is the
        # only way to prove the REAL _run_drain_loop was entered without
        # hanging the test suite.
        return real_run_drain_loop(marker, poll_interval=0.01, max_ticks=1)

    monkeypatch.setattr(mgr, "_run_drain_loop", _bounded_drain_loop)

    lifecycle._shutdown_daemon_runtime(agent, reason="agent_stop")

    assert shutdown_calls == [], (
        "shutdown_for_agent_stop must NOT be called when a real refresh "
        "handoff marker was committed — calling it would kill the exact "
        "in-flight daemon work the marker just claimed to protect"
    )
    assert len(drain_calls) == 1
    marker = drain_calls[0]
    assert marker.owned_run_ids == ("em-live",)

    # The drain thread runs in the background; give it a moment to finish
    # its bounded max_ticks=1 loop before asserting on its side effects.
    for t in threading.enumerate():
        if t.name == "daemon-refresh-drain-host":
            t.join(timeout=5)

    # The real marker was durably committed to disk — proving this is the
    # genuine PREPARE path, not a stand-in.
    from lingtai.tools.daemon.refresh_host import refresh_hosts_dir
    committed_markers = list((refresh_hosts_dir(agent._working_dir)).glob("*.json"))
    assert len(committed_markers) == 1


def test_shutdown_daemon_runtime_falls_back_to_ordinary_shutdown_without_refresh_taken(tmp_path, monkeypatch):
    """Without ``.refresh.taken`` (an ordinary user stop/sleep/suspend — NOT
    a refresh), _shutdown_daemon_runtime must behave exactly as before this
    stage: call shutdown_for_agent_stop unchanged, never attempt PREPARE.
    Draining only makes sense for a refresh — an ordinary stop has no
    successor process that will ever arrive to route control requests to."""
    from lingtai.kernel.base_agent import lifecycle
    from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry

    agent = make_daemon_agent(tmp_path)
    assert not (agent._working_dir / ".refresh.taken").exists()

    mgr = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, em_id="em-live")
    register_daemon_entry(mgr, "em-live", run_dir)

    shutdown_calls = []
    monkeypatch.setattr(mgr, "shutdown_for_agent_stop", lambda **kw: shutdown_calls.append(kw))
    prepare_calls = []
    monkeypatch.setattr(mgr, "_prepare_refresh_host", lambda: prepare_calls.append(1) or None)

    lifecycle._shutdown_daemon_runtime(agent, reason="agent_stop")

    assert prepare_calls == [], "PREPARE must never be attempted for a non-refresh stop"
    assert len(shutdown_calls) == 1
    assert shutdown_calls[0].get("reason") == "agent_stop"


def test_v7_shutdown_daemon_runtime_ambiguous_prepare_does_not_fall_back_to_ordinary_shutdown(
    tmp_path, monkeypatch,
):
    """v7 isolated counterexample: the parent's exact mechanical proof
    (ambiguous_prepare_fell_back_to_ordinary_shutdown=["agent_stop"] against
    pre-v7 code). Pre-v7, `except Exception` at the PREPARE call site caught
    CommitAmbiguousError/OwnerTaggingAmbiguousError indistinguishably from
    an ordinary precommit failure and fell through to
    shutdown_for_agent_stop — potentially killing in-flight daemon work
    whose true durable marker/tag state this process can no longer prove is
    safe to discard. This test uses the REAL exception class (not a
    same-named stand-in) to prove the fix's class-name recognition actually
    matches production exceptions, not merely a test double shaped like
    one."""
    from lingtai.kernel.base_agent import lifecycle
    from lingtai.tools.daemon.refresh_host import CommitAmbiguousError

    agent = SimpleNamespace(
        _working_dir=tmp_path / "agent",
        _log=lambda *a, **k: None,
    )
    (tmp_path / "agent").mkdir()
    (agent._working_dir / ".refresh.taken").touch()

    shutdown_calls = []
    drain_calls = []

    class FakeMgr:
        def _prepare_refresh_host(self):
            raise CommitAmbiguousError(
                tmp_path / "agent" / "daemons" / ".refresh-hosts" / "g1.json",
                rollback_attempted=True, rollback_succeeded=False,
                cause=OSError("simulated durability failure"),
            )

        def _run_drain_loop(self, marker, **kw):
            drain_calls.append(marker)

        def shutdown_for_agent_stop(self, **kw):
            shutdown_calls.append(kw)

    agent.get_capability = lambda name: FakeMgr() if name == "daemon" else None

    lifecycle._shutdown_daemon_runtime(agent, reason="agent_stop")

    assert shutdown_calls == [], (
        "an ambiguous CommitAmbiguousError from _prepare_refresh_host must "
        "NEVER fall back to ordinary shutdown_for_agent_stop — the true "
        "on-disk marker/tag state is unknown, so calling ordinary shutdown "
        "risks killing in-flight work that may already be durably owned "
        "by a marker this process cannot prove does or does not exist"
    )
    assert drain_calls == [], (
        "an ambiguous failure also must not start a drain loop — there is "
        "no confirmed committed marker to drain for"
    )


def test_v7_shutdown_daemon_runtime_ordinary_prepare_failure_still_falls_back_to_ordinary_shutdown(
    tmp_path, monkeypatch,
):
    """Regression guard: an ORDINARY (non-ambiguous) precommit failure from
    _prepare_refresh_host — e.g. a plain MarkerValidationError, already
    unfrozen/re-raised by that method with provably clean on-disk state —
    must still fall through to shutdown_for_agent_stop exactly as before
    this fix. The v7 fail-closed behavior is scoped ONLY to the two
    genuinely ambiguous exception types, never to ordinary failures."""
    from lingtai.kernel.base_agent import lifecycle

    agent = SimpleNamespace(
        _working_dir=tmp_path / "agent",
        _log=lambda *a, **k: None,
    )
    (tmp_path / "agent").mkdir()
    (agent._working_dir / ".refresh.taken").touch()

    shutdown_calls = []

    class FakeMgr:
        def _prepare_refresh_host(self):
            raise RuntimeError("ordinary precommit failure, e.g. plain OSError")

        def shutdown_for_agent_stop(self, **kw):
            shutdown_calls.append(kw)

    agent.get_capability = lambda name: FakeMgr() if name == "daemon" else None

    lifecycle._shutdown_daemon_runtime(agent, reason="agent_stop")

    assert len(shutdown_calls) == 1, (
        "an ordinary (non-ambiguous) precommit failure must still fall "
        "back to shutdown_for_agent_stop — only CommitAmbiguousError/"
        "OwnerTaggingAmbiguousError trigger the fail-closed no-shutdown "
        "outcome"
    )
    assert shutdown_calls[0].get("reason") == "agent_stop"


def test_shutdown_daemon_runtime_falls_back_to_ordinary_shutdown_when_no_working_dir_attr():
    """A test-double agent lacking _working_dir entirely (as several other
    fixtures in this file legitimately use) must fail closed to the
    ordinary shutdown path — never assume a refresh handoff when the
    working directory can't even be determined."""
    from lingtai.kernel.base_agent import lifecycle

    order = []

    class FakeDaemon:
        def shutdown_for_agent_stop(self, *, reason):
            order.append(("daemon", reason))

    agent = SimpleNamespace(
        _log=lambda *a, **k: None,
        get_capability=lambda name: FakeDaemon() if name == "daemon" else None,
    )
    lifecycle._shutdown_daemon_runtime(agent, reason="agent_stop")
    assert order == [("daemon", "agent_stop")]
