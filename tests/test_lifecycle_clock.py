"""Behavior and conformance locks for the lifecycle-clock Port, adapter, and wiring.

Covers the S7b slice: the Core-owned two-reading ``LifecycleClockPort``, its one
portable ``SystemLifecycleClockAdapter``, required ``BaseAgent`` construction
injection (no default), ``Agent``/CLI composition, and the wall-vs-monotonic
domain split across heartbeat/state/progress/status/uptime/idle/AED/snapshot.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.base_agent import identity as identity_mod
from lingtai.kernel.base_agent import lifecycle as lifecycle_mod
from lingtai.kernel.lifecycle_clock import LifecycleClockPort
from lingtai.adapters.lifecycle_clock import SystemLifecycleClockAdapter
from lingtai.kernel.state import AgentState

from tests._service_helpers import make_tool_result_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store
from tests._lifecycle_clock_helpers import (
    FakeLifecycleClock,
    ScriptedLifecycleClock,
    make_test_lifecycle_clock,
)


# ---------------------------------------------------------------------------
# Test agent construction
# ---------------------------------------------------------------------------


def _make_agent(tmp_path, *, lifecycle_clock=None, agent_presence=None):
    """Construct a real bare BaseAgent with injectable clock / presence store."""
    workdir = tmp_path / "clock_agent"
    return BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        agent_presence=agent_presence if agent_presence is not None else make_test_presence_store(),
        lifecycle_clock=lifecycle_clock if lifecycle_clock is not None else make_test_lifecycle_clock(),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
    )


# ---------------------------------------------------------------------------
# Port shape
# ---------------------------------------------------------------------------


def test_port_has_exactly_two_abstract_methods_and_no_concrete_tech():
    import lingtai.kernel.lifecycle_clock as port_pkg

    src = inspect.getsource(port_pkg)
    # Exactly the two zero-argument readings — no third op, no wait/sleep/deadline.
    assert src.count("@abstractmethod") == 2
    assert "def wall_seconds(self) -> float" in src
    assert "def monotonic_seconds(self) -> float" in src
    assert LifecycleClockPort.__abstractmethods__ == frozenset(
        {"wall_seconds", "monotonic_seconds"}
    )
    # Core Port names no concrete time/scheduler/filesystem mechanism.
    for banned in (
        "import time",
        "import threading",
        "from datetime",
        "import datetime",
        "from pathlib",
        "def sleep",
        "def wait",
        "def deadline",
        "def now",
        "lingtai.adapters",
        "SystemLifecycleClockAdapter",
    ):
        assert banned not in src, banned


def test_bare_port_cannot_be_instantiated():
    with pytest.raises(TypeError):
        LifecycleClockPort()  # abstract


# ---------------------------------------------------------------------------
# Production adapter
# ---------------------------------------------------------------------------


def test_system_adapter_forwards_wall_and_monotonic_independently():
    adapter = SystemLifecycleClockAdapter()
    assert isinstance(adapter, LifecycleClockPort)

    with patch("lingtai.adapters.lifecycle_clock.time.time", return_value=111.5) as wall, patch(
        "lingtai.adapters.lifecycle_clock.time.monotonic", return_value=222.25
    ) as mono:
        assert adapter.wall_seconds() == 111.5
        assert adapter.monotonic_seconds() == 222.25

    # Each reading forwards to exactly its own source and returns it unmodified.
    wall.assert_called_once_with()
    mono.assert_called_once_with()


def test_system_adapter_is_uncached():
    adapter = SystemLifecycleClockAdapter()
    values = iter([1.0, 2.0, 3.0])
    with patch(
        "lingtai.adapters.lifecycle_clock.time.time", side_effect=lambda: next(values)
    ):
        assert adapter.wall_seconds() == 1.0
        assert adapter.wall_seconds() == 2.0
        assert adapter.wall_seconds() == 3.0


def test_system_adapter_monotonic_is_uncached():
    # SF5 — monotonic forwarding is independent and uncached, matching the
    # repeated wall proof above: each read re-samples ``time.monotonic()`` and
    # the wall source is not touched.
    adapter = SystemLifecycleClockAdapter()
    values = iter([10.0, 20.0, 30.0])
    with patch(
        "lingtai.adapters.lifecycle_clock.time.monotonic",
        side_effect=lambda: next(values),
    ), patch("lingtai.adapters.lifecycle_clock.time.time") as wall:
        assert adapter.monotonic_seconds() == 10.0
        assert adapter.monotonic_seconds() == 20.0
        assert adapter.monotonic_seconds() == 30.0
    wall.assert_not_called()


# ---------------------------------------------------------------------------
# Deterministic fake
# ---------------------------------------------------------------------------


def test_fake_advances_wall_and_monotonic_independently():
    clock = FakeLifecycleClock(wall=1000.0, monotonic=0.0)
    assert clock.wall_seconds() == 1000.0
    assert clock.monotonic_seconds() == 0.0

    clock.advance_wall(50.0)
    assert clock.wall_seconds() == 1050.0
    assert clock.monotonic_seconds() == 0.0  # monotonic untouched by a wall move

    clock.advance_monotonic(5.0)
    assert clock.monotonic_seconds() == 5.0
    assert clock.wall_seconds() == 1050.0  # wall untouched by a monotonic move

    # Wall may move backward (jump); monotonic is advanced independently.
    clock.set_wall(900.0)
    assert clock.wall_seconds() == 900.0
    assert clock.monotonic_seconds() == 5.0


# ---------------------------------------------------------------------------
# Required construction injection (no default)
# ---------------------------------------------------------------------------


def test_baseagent_requires_lifecycle_clock_at_construction(tmp_path):
    workdir = tmp_path / "no_clock"
    with pytest.raises(TypeError):
        BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=make_mock_service(),
            working_dir=workdir,
            workdir_lease=make_test_lease(),
            agent_presence=make_test_presence_store(),
            # lifecycle_clock deliberately omitted — must fail loudly.
            snapshot_port=make_test_snapshot_port(),
            source_revision_port=make_test_source_revision_port(),
            notification_store=notification_store_for(workdir),
        )


def test_baseagent_has_no_hidden_clock_default():
    # The constructor signature carries no default for lifecycle_clock, and Core
    # never imports the concrete adapter.
    sig = inspect.signature(BaseAgent.__init__)
    assert sig.parameters["lifecycle_clock"].default is inspect.Parameter.empty
    src = inspect.getsource(BaseAgent.__init__)
    assert "SystemLifecycleClockAdapter" not in src
    assert "lingtai.adapters" not in src


def test_baseagent_binds_injected_clock(tmp_path):
    clock = make_test_lifecycle_clock()
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    assert agent._lifecycle_clock is clock


# ---------------------------------------------------------------------------
# Composition roots
# ---------------------------------------------------------------------------


def test_agent_composes_system_adapter_when_lifecycle_clock_absent():
    # lingtai.Agent is the outer composition root for direct wrapper callers: it
    # constructs the portable system adapter (no working_dir needed) only when no
    # clock was injected, and does so before super().__init__.
    from lingtai import agent as agent_mod

    src = inspect.getsource(agent_mod.Agent.__init__)
    assert 'if "lifecycle_clock" not in kwargs:' in src
    assert "SystemLifecycleClockAdapter()" in src
    assert 'kwargs["lifecycle_clock"] = SystemLifecycleClockAdapter()' in src
    # The fallback is constructed before super().__init__ (an injected clock wins
    # because the guard only fills the key when absent).
    assert src.index("SystemLifecycleClockAdapter()") < src.index("super().__init__")


def test_cli_build_agent_injects_system_adapter():
    # cli.build_agent constructs SystemLifecycleClockAdapter() explicitly
    # (secondary source-shape tripwire; the runtime capture below is primary).
    from lingtai import cli

    src = inspect.getsource(cli.build_agent)
    assert "lifecycle_clock=SystemLifecycleClockAdapter()" in src


def _capture_baseagent_lifecycle_clock(monkeypatch):
    """Patch BaseAgent.__init__ to record the ``lifecycle_clock`` Core received.

    Raises a sentinel after capture so the composition root's clock decision is
    observed without booting a full agent (LLM/session/filesystem side effects).
    """
    captured: dict = {}

    class _Captured(Exception):
        pass

    def fake_init(self, *args, **kwargs):
        captured["lifecycle_clock"] = kwargs.get("lifecycle_clock")
        raise _Captured

    import lingtai.kernel.base_agent as base_agent_mod

    monkeypatch.setattr(base_agent_mod.BaseAgent, "__init__", fake_init)
    return captured, _Captured


def _agent_ports_for_capture(tmp_path):
    """Non-clock required ports so only ``lifecycle_clock`` exercises the default."""
    workdir = tmp_path / "cap_agent"
    return dict(
        service=make_mock_service(),
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        agent_presence=make_test_presence_store(),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
        event_journal=None,  # honest opt-out; skip the owned journal adapter
    )


def test_agent_default_composes_system_adapter_into_core(monkeypatch, tmp_path):
    # SF4 runtime — with no clock injected, lingtai.Agent constructs and passes a
    # real SystemLifecycleClockAdapter into Core.
    from lingtai.agent import Agent

    captured, sentinel = _capture_baseagent_lifecycle_clock(monkeypatch)
    with pytest.raises(sentinel):
        Agent(**_agent_ports_for_capture(tmp_path))
    assert isinstance(captured["lifecycle_clock"], SystemLifecycleClockAdapter)


def test_agent_explicit_clock_override_wins(monkeypatch, tmp_path):
    # SF4 runtime — an explicitly injected clock reaches Core unchanged; the
    # composition root does not override it with a system adapter.
    from lingtai.agent import Agent

    captured, sentinel = _capture_baseagent_lifecycle_clock(monkeypatch)
    injected = make_test_lifecycle_clock()
    ports = _agent_ports_for_capture(tmp_path)
    ports["lifecycle_clock"] = injected
    with pytest.raises(sentinel):
        Agent(**ports)
    assert captured["lifecycle_clock"] is injected


def test_cli_build_agent_captures_and_injects_system_adapter(tmp_path):
    # SF4 runtime — cli.build_agent composes a real SystemLifecycleClockAdapter
    # and hands it to the Agent it builds. Mock the heavy LLMService/mail/Agent
    # pieces (matching the established test_cli.py pattern) so the assertion
    # isolates the clock that build_agent actually injects into Agent(...).
    from unittest.mock import patch as _patch

    from lingtai import cli

    data = {
        "manifest": {
            "agent_name": "cap",
            "language": "en",
            "llm": {"provider": "openai", "model": "gpt-4o", "api_key": "k"},
        },
    }
    workdir = tmp_path / "cli_cap"
    workdir.mkdir(parents=True, exist_ok=True)

    with _patch("lingtai.cli.LLMService"), _patch(
        "lingtai.cli.PosixFilesystemMailAdapter"
    ), _patch("lingtai.cli.Agent") as mock_agent:
        cli.build_agent(data, workdir)

    injected = mock_agent.call_args.kwargs["lifecycle_clock"]
    assert isinstance(injected, SystemLifecycleClockAdapter)


# ---------------------------------------------------------------------------
# Wall domain — heartbeat publication and status/state timestamps
# ---------------------------------------------------------------------------


def test_heartbeat_publishes_wall_value_through_presence_store(tmp_path):
    presence = make_test_presence_store()
    clock = FakeLifecycleClock(wall=4242.0, monotonic=7.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock, agent_presence=presence)

    with patch.object(agent, "_write_status_snapshot"):
        lifecycle_mod._write_heartbeat_tick(agent)

    # The wall reading — not monotonic — becomes the heartbeat and is published
    # unchanged to the presence store.
    assert agent._heartbeat == 4242.0
    assert presence.published_values == [4242.0]

    # A monotonic move does not change the next heartbeat; a wall move does.
    clock.advance_monotonic(100.0)
    clock.set_wall(5000.0)
    with patch.object(agent, "_write_status_snapshot"):
        lifecycle_mod._write_heartbeat_tick(agent)
    assert agent._heartbeat == 5000.0
    assert presence.published_values == [4242.0, 5000.0]


def test_event_journal_ts_is_the_wall_reading(tmp_path):
    # SF2 — BaseAgent event-journal ``ts`` is stamped from the wall reading, not
    # monotonic. Use a non-progress event so the only clock read on this path is
    # the ``ts`` stamp itself.
    clock = FakeLifecycleClock(wall=33_000.0, monotonic=12.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)

    class _CapturingJournal:
        def __init__(self):
            self.entries = []

        def append(self, entry):
            self.entries.append(entry)

    journal = _CapturingJournal()
    agent._event_journal = journal
    agent._log("custom_diagnostic_event", note="x")

    assert journal.entries[-1]["ts"] == 33_000.0
    # A monotonic move must not change the wall-domain ``ts``.
    clock.advance_monotonic(1000.0)
    clock.set_wall(34_000.0)
    agent._log("custom_diagnostic_event", note="y")
    assert journal.entries[-1]["ts"] == 34_000.0


def test_deferred_oldest_at_is_seeded_from_wall(tmp_path):
    # SF2 — the deferred-notification oldest timestamp is seeded from the wall
    # reading on the first ACTIVE deferral and is not disturbed by monotonic.
    clock = FakeLifecycleClock(wall=50_000.0, monotonic=8.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    assert agent._deferred_notifications_oldest_at is None
    agent._note_notification_deferred_active(("f.json", 1, "x"), sources=["email"])
    assert agent._deferred_notifications_oldest_at == 50_000.0
    # A later deferral (monotonic moved) does not overwrite the oldest wall stamp.
    clock.set_wall(60_000.0)
    clock.advance_monotonic(100.0)
    agent._note_notification_deferred_active(("f.json", 2, "y"), sources=["email"])
    assert agent._deferred_notifications_oldest_at == 50_000.0


def test_progress_event_bumps_last_progress_from_wall(tmp_path):
    # SF2 — a progress event (via ``_log``) refreshes ``_last_progress_at`` and
    # the ACTIVE-turn start from the wall reading, not monotonic.
    clock = FakeLifecycleClock(wall=40_000.0, monotonic=5.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    clock.set_wall(40_500.0)
    clock.set_monotonic(9_999.0)  # a monotonic move must not seed the progress ts
    agent._log("llm_call")
    assert agent._last_progress_at == 40_500.0
    assert agent._active_turn_started_at == 40_500.0


def test_constructor_seeds_state_and_progress_from_shared_wall_sample(tmp_path):
    clock = FakeLifecycleClock(wall=8000.0, monotonic=3.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    # Both watchdog fields seed off the same wall sample.
    assert agent._state_changed_at == 8000.0
    assert agent._last_progress_at == 8000.0
    # The initial IDLE anchor is monotonic, not wall.
    assert agent._idle_since_monotonic == 3.0


def test_constructor_state_and_progress_are_one_shared_wall_read(tmp_path):
    # SF3 — a changing-per-read clock: if ``_state_changed_at`` and
    # ``_last_progress_at`` are equal they must have come from a *single* shared
    # wall read (a second read would differ). The idle anchor is the single
    # monotonic read taken before the wall sample.
    clock = ScriptedLifecycleClock(
        wall_start=8000.0, wall_step=1.0, monotonic_start=3.0, monotonic_step=1.0
    )
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    assert agent._state_changed_at == agent._last_progress_at == 8000.0
    assert agent._idle_since_monotonic == 3.0
    # Exactly one shared wall sample for the two watchdog fields, and one
    # monotonic sample for the idle anchor, during construction.
    assert clock.wall_reads == 1
    assert clock.monotonic_reads == 1


def test_state_transition_uses_wall_and_idle_anchor_uses_monotonic(tmp_path):
    clock = FakeLifecycleClock(wall=8000.0, monotonic=3.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)

    clock.set_wall(9000.0)
    clock.set_monotonic(50.0)
    agent._set_state(AgentState.ACTIVE, reason="test")
    assert agent._state_changed_at == 9000.0
    assert agent._last_progress_at == 9000.0
    assert agent._active_turn_started_at == 9000.0

    clock.set_wall(9100.0)
    clock.set_monotonic(60.0)
    agent._set_state(AgentState.IDLE, reason="test")
    # Leaving/entering IDLE re-anchors the monotonic idle clock.
    assert agent._idle_since_monotonic == 60.0


def test_active_transition_shares_one_wall_sample_across_three_fields(tmp_path):
    # SF3 — the ACTIVE transition must seed ``_state_changed_at``,
    # ``_last_progress_at``, and ``_active_turn_started_at`` from ONE wall read.
    # With a changing-per-read clock, all three being equal proves the single
    # shared sample; the transition also takes exactly one monotonic read (the
    # idle anchor is cleared, not re-sampled, when leaving IDLE for ACTIVE).
    clock = ScriptedLifecycleClock(
        wall_start=9000.0, wall_step=100.0, monotonic_start=50.0, monotonic_step=10.0
    )
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    wall_reads_before = clock.wall_reads
    agent._set_state(AgentState.ACTIVE, reason="test")
    assert (
        agent._state_changed_at
        == agent._last_progress_at
        == agent._active_turn_started_at
    )
    # Only one additional wall read happened for the whole transition.
    assert clock.wall_reads == wall_reads_before + 1


def test_status_ages_consume_wall(tmp_path):
    clock = FakeLifecycleClock(wall=10_000.0, monotonic=100.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    # A bare (unstarted) agent already carries the state _status reads; freeze
    # known wall anchors, then advance wall only.
    agent._last_progress_at = 10_000.0
    agent._heartbeat = 9_990.0
    agent._uptime_anchor = 100.0
    clock.set_wall(10_030.0)
    status = identity_mod._status(agent)
    runtime = status["runtime"]
    # no_progress and heartbeat ages are wall - wall.
    assert runtime["no_progress_seconds"] == 30.0
    assert runtime["heartbeat_age_seconds"] == 40.0


def test_status_active_turn_and_deferred_ages_consume_wall(tmp_path):
    # SF2 — the status active-turn elapsed age and the deferred-notification
    # oldest_at echo are wall-domain (identity._status): a wall move changes the
    # elapsed age, a monotonic move does not.
    clock = FakeLifecycleClock(wall=20_000.0, monotonic=500.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    agent._active_turn_kind = "llm_call"
    agent._active_turn_id = "c1"
    agent._active_turn_started_at = 20_000.0
    agent._deferred_notifications_count = 1
    agent._deferred_notifications_oldest_at = 19_990.0

    clock.set_wall(20_050.0)
    clock.set_monotonic(9_999.0)  # a monotonic move must not affect wall ages
    status = identity_mod._status(agent)

    assert status["active_turn"]["elapsed_seconds"] == 50.0
    assert status["active_turn"]["started_at"] == 20_000.0
    # The deferred block echoes the raw wall timestamp unchanged.
    assert status["deferred_notifications"]["oldest_at"] == 19_990.0


# ---------------------------------------------------------------------------
# Monotonic domain — uptime, idle timeout, AED
# ---------------------------------------------------------------------------


def test_uptime_consumes_monotonic_and_ignores_wall_jumps(tmp_path):
    clock = FakeLifecycleClock(wall=10_000.0, monotonic=100.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    # Anchor uptime at monotonic 100.0 (as start()/reset would), without threads.
    agent._uptime_anchor = 100.0
    agent._last_progress_at = clock.wall_seconds()
    clock.set_monotonic(160.0)
    assert identity_mod._status(agent)["runtime"]["uptime_seconds"] == 60.0

    # A wall jump (forward or backward) must not change monotonic uptime.
    clock.set_wall(0.0)
    assert identity_mod._status(agent)["runtime"]["uptime_seconds"] == 60.0

    clock.set_wall(1_000_000.0)
    assert identity_mod._status(agent)["runtime"]["uptime_seconds"] == 60.0


def test_idle_timeout_default_reads_monotonic(tmp_path):
    from lingtai.kernel.config import IDLE_SLEEP_TIMEOUT_SECONDS

    clock = FakeLifecycleClock(wall=10_000.0, monotonic=0.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    with patch.object(agent, "_save_chat_history"):
        agent._idle_since_monotonic = 0.0
        # Below the timeout — stays IDLE. Default now reads the monotonic clock.
        clock.set_monotonic(IDLE_SLEEP_TIMEOUT_SECONDS - 1.0)
        lifecycle_mod._maybe_sleep_after_idle_timeout(agent)
        assert agent._state == AgentState.IDLE

        # At the timeout — sleeps.
        clock.set_monotonic(IDLE_SLEEP_TIMEOUT_SECONDS)
        lifecycle_mod._maybe_sleep_after_idle_timeout(agent)
        assert agent._state == AgentState.ASLEEP


def test_idle_timeout_explicit_now_mono_seam_preserved(tmp_path):
    from lingtai.kernel.config import IDLE_SLEEP_TIMEOUT_SECONDS

    clock = FakeLifecycleClock(wall=10_000.0, monotonic=0.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    with patch.object(agent, "_save_chat_history"):
        agent._idle_since_monotonic = 100.0
        # Explicit now_mono wins over the clock (the preserved test seam).
        lifecycle_mod._maybe_sleep_after_idle_timeout(
            agent, now_mono=100.0 + IDLE_SLEEP_TIMEOUT_SECONDS
        )
        assert agent._state == AgentState.ASLEEP


# ---------------------------------------------------------------------------
# Heartbeat-loop AED / watchdog / snapshot-GC domain and shared-sample proofs
# ---------------------------------------------------------------------------


def _one_tick_heartbeat_agent(clock, *, state, snapshot_interval, snapshot_port=None, tmp_path):
    """A minimal SimpleNamespace agent for a single ``_heartbeat_loop`` tick.

    Only the fields the AED, ACTIVE-watchdog, and snapshot/GC branches read are
    populated; the clock is the real injected Port under test so the branches'
    wall/monotonic reads are exercised for real.
    """
    stop = Mock()

    def stop_after_one(_seconds):
        agent._heartbeat_thread = None

    stop.wait.side_effect = stop_after_one
    shutdown = Mock()
    shutdown.is_set.return_value = False
    agent = SimpleNamespace(
        agent_name="clock-tick",
        _heartbeat_thread=object(),
        _heartbeat_stop=stop,
        _shutdown=shutdown,
        _heartbeat_runtime_ready=True,
        _lifecycle_clock=clock,
        _state=state,
        _config=SimpleNamespace(snapshot_interval=snapshot_interval, aed_timeout=100.0),
        _snapshot_port=snapshot_port,
        _last_snapshot=0.0,
        _last_gc=0.0,
        _aed_start=None,
        _active_stuck_logged=False,
        _last_progress_at=0.0,
        _state_changed_at=0.0,
        _active_turn_kind=None,
        _active_turn_id=None,
        _deferred_notifications_count=0,
        _deferred_notifications_oldest_at=None,
        _working_dir=tmp_path,
        _save_chat_history=Mock(),
        _asleep=Mock(),
        _set_state=Mock(),
        _write_status_snapshot=Mock(),
        _sync_notifications=Mock(),
        _setup_telegram_task_card=Mock(),
        _log=Mock(),
    )
    return agent


def _run_one_tick(agent):
    with patch.object(lifecycle_mod, "_write_heartbeat_tick"), patch.object(
        lifecycle_mod, "_check_rules_file"
    ), patch.object(lifecycle_mod, "_maybe_sleep_after_idle_timeout"), patch(
        "lingtai.kernel.nudge.run_checks"
    ):
        lifecycle_mod._heartbeat_loop(agent)


def test_aed_uses_monotonic_and_preserves_strict_operator(tmp_path):
    # SF2/SF3 — the STUCK AED comparison reads the monotonic domain and keeps
    # its strict ``>`` boundary. First tick anchors ``_aed_start`` at the
    # monotonic reading; a later monotonic value exactly at the timeout does NOT
    # trip (strict), one past it does.
    clock = FakeLifecycleClock(wall=5_000.0, monotonic=10.0)
    agent = _one_tick_heartbeat_agent(
        clock, state=AgentState.STUCK, snapshot_interval=None, tmp_path=tmp_path
    )
    _run_one_tick(agent)
    # Anchored from monotonic, not wall.
    assert agent._aed_start == 10.0

    # Exactly at the timeout — strict ``>`` does not fire.
    agent._heartbeat_thread = object()
    clock.set_monotonic(10.0 + agent._config.aed_timeout)
    _run_one_tick(agent)
    agent._set_state.assert_not_called()

    # One past the timeout — fires, moving to ASLEEP.
    agent._heartbeat_thread = object()
    clock.set_monotonic(10.0 + agent._config.aed_timeout + 0.5)
    _run_one_tick(agent)
    agent._set_state.assert_called_once_with(AgentState.ASLEEP, reason="AED timeout")
    # A wall move never affected the AED decision.
    assert agent._aed_start == 10.0


def test_active_watchdog_no_progress_age_is_wall(tmp_path):
    # SF2 — the Issue #164 ACTIVE-without-progress watchdog measures
    # ``wall_seconds() - _last_progress_at``. Move only wall past the threshold
    # and confirm it fires; a monotonic-only move must not.
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    agent = _one_tick_heartbeat_agent(
        clock, state=AgentState.ACTIVE, snapshot_interval=None, tmp_path=tmp_path
    )
    agent._last_progress_at = 1_000.0

    # Monotonic-only move: no wall age accrues, watchdog stays silent.
    agent._heartbeat_thread = object()
    clock.advance_monotonic(10_000.0)
    _run_one_tick(agent)
    assert agent._active_stuck_logged is False

    # Wall move well past the 600s floor threshold: watchdog latches once.
    agent._heartbeat_thread = object()
    clock.set_wall(1_000.0 + 10_000.0)
    _run_one_tick(agent)
    assert agent._active_stuck_logged is True
    agent._write_status_snapshot.assert_called_once_with()


def test_snapshot_and_gc_share_one_monotonic_tick(tmp_path):
    # SF3 — with snapshots enabled, one heartbeat tick takes exactly ONE
    # monotonic read shared by both the snapshot-interval and GC checks. A
    # changing-per-read clock would give snapshot and GC different values if the
    # tick were not shared; here both stamps are equal to the single read.
    class _RecordingSnapshotPort:
        def __init__(self):
            self.snapshot_calls = 0
            self.collect_garbage_calls = 0

        def snapshot(self):
            self.snapshot_calls += 1

        def collect_garbage(self):
            self.collect_garbage_calls += 1

    port = _RecordingSnapshotPort()
    clock = ScriptedLifecycleClock(
        monotonic_start=90_000.0, monotonic_step=1_000.0, wall_start=0.0, wall_step=1.0
    )
    agent = _one_tick_heartbeat_agent(
        clock,
        state=AgentState.IDLE,
        snapshot_interval=10,
        snapshot_port=port,
        tmp_path=tmp_path,
    )
    _run_one_tick(agent)

    # Both first-eligible checks fired and both recorded the SAME monotonic
    # sample — proving a single shared tick, not two independent reads.
    assert port.snapshot_calls == 1
    assert port.collect_garbage_calls == 1
    assert agent._last_snapshot == agent._last_gc == 90_000.0
    assert clock.monotonic_reads == 1


# ---------------------------------------------------------------------------
# Cadence primitive is not the clock
# ---------------------------------------------------------------------------


def test_heartbeat_cadence_uses_event_wait_not_the_port():
    src = inspect.getsource(lifecycle_mod._heartbeat_loop)
    # Both loop branches keep the interruptible Event.wait(1.0) cadence.
    assert src.count("_heartbeat_stop.wait(1.0)") == 2
    # The Port owns no wait/sleep/deadline the loop could call instead.
    port_src = inspect.getsource(LifecycleClockPort)
    for banned in ("def wait", "def sleep", "def deadline"):
        assert banned not in port_src
