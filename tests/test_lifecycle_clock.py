"""Observable behavior and composition tests for the lifecycle-clock Port."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lingtai.adapters.lifecycle_clock import SystemLifecycleClockAdapter
from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.base_agent import identity as identity_mod
from lingtai.kernel.base_agent import lifecycle as lifecycle_mod
from lingtai.kernel.lifecycle_clock import LifecycleClockPort
from lingtai.kernel.state import AgentState
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

from tests._agent_presence_helpers import make_test_presence_store
from tests._lifecycle_clock_helpers import (
    FakeLifecycleClock,
    ScriptedLifecycleClock,
    make_test_lifecycle_clock,
)
from tests._notification_store_helpers import notification_store_for
from tests._service_helpers import make_tool_result_mock_service as make_mock_service
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._workdir_lease_helpers import make_test_lease


def _make_agent(tmp_path, *, lifecycle_clock=None, agent_presence=None):
    workdir = tmp_path / "clock_agent"
    return BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        agent_presence=(
            agent_presence if agent_presence is not None else make_test_presence_store()
        ),
        lifecycle_clock=(
            lifecycle_clock
            if lifecycle_clock is not None
            else make_test_lifecycle_clock()
        ),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
    )


def test_port_is_two_readings_and_abstract() -> None:
    assert LifecycleClockPort.__abstractmethods__ == frozenset(
        {"wall_seconds", "monotonic_seconds"}
    )
    with pytest.raises(TypeError):
        LifecycleClockPort()


def test_system_adapter_forwards_uncached_wall_and_monotonic_reads() -> None:
    adapter = SystemLifecycleClockAdapter()
    wall_values = iter([111.5, 112.5])
    mono_values = iter([222.25, 223.25])
    with patch(
        "lingtai.adapters.lifecycle_clock.time.time",
        side_effect=lambda: next(wall_values),
    ) as wall, patch(
        "lingtai.adapters.lifecycle_clock.time.monotonic",
        side_effect=lambda: next(mono_values),
    ) as monotonic:
        assert adapter.wall_seconds() == 111.5
        assert adapter.wall_seconds() == 112.5
        assert adapter.monotonic_seconds() == 222.25
        assert adapter.monotonic_seconds() == 223.25
    assert wall.call_count == monotonic.call_count == 2


def test_fake_advances_wall_and_monotonic_independently() -> None:
    clock = FakeLifecycleClock(wall=1000.0, monotonic=0.0)
    assert (clock.wall_seconds(), clock.monotonic_seconds()) == (1000.0, 0.0)
    clock.advance_wall(50.0)
    assert clock.wall_seconds() == 1050.0
    assert clock.monotonic_seconds() == 0.0
    clock.advance_monotonic(5.0)
    assert clock.monotonic_seconds() == 5.0
    clock.set_wall(900.0)
    assert clock.wall_seconds() == 900.0
    assert clock.monotonic_seconds() == 5.0


def test_baseagent_requires_lifecycle_clock_at_construction(tmp_path):
    workdir = tmp_path / "no_clock"
    with pytest.raises(TypeError):
        BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=make_mock_service(),
            working_dir=workdir,
            workdir_lease=make_test_lease(),
            agent_presence=make_test_presence_store(),
            snapshot_port=make_test_snapshot_port(),
            source_revision_port=make_test_source_revision_port(),
            notification_store=notification_store_for(workdir),
        )


def test_baseagent_binds_injected_clock(tmp_path):
    clock = make_test_lifecycle_clock()
    assert _make_agent(tmp_path, lifecycle_clock=clock)._lifecycle_clock is clock


def _capture_baseagent_lifecycle_clock(monkeypatch):
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
    workdir = tmp_path / "cap_agent"
    return dict(
        service=make_mock_service(),
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        agent_presence=make_test_presence_store(),
        snapshot_port=make_test_snapshot_port(),
        source_revision_port=make_test_source_revision_port(),
        notification_store=notification_store_for(workdir),
        event_journal=None,
    )


def test_agent_default_composes_system_adapter_into_core(monkeypatch, tmp_path):
    from lingtai.agent import Agent

    captured, sentinel = _capture_baseagent_lifecycle_clock(monkeypatch)
    with pytest.raises(sentinel):
        Agent(**_agent_ports_for_capture(tmp_path))
    assert isinstance(captured["lifecycle_clock"], SystemLifecycleClockAdapter)


def test_agent_explicit_clock_override_wins(monkeypatch, tmp_path):
    from lingtai.agent import Agent

    captured, sentinel = _capture_baseagent_lifecycle_clock(monkeypatch)
    injected = make_test_lifecycle_clock()
    ports = _agent_ports_for_capture(tmp_path)
    ports["lifecycle_clock"] = injected
    with pytest.raises(sentinel):
        Agent(**ports)
    assert captured["lifecycle_clock"] is injected


def test_cli_build_agent_captures_and_injects_system_adapter(tmp_path):
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
    workdir.mkdir()
    with _patch("lingtai.cli.LLMService"), _patch(
        "lingtai.cli.PosixFilesystemMailAdapter"
    ), _patch("lingtai.cli.Agent") as mock_agent:
        cli.build_agent(data, workdir)
    assert isinstance(
        mock_agent.call_args.kwargs["lifecycle_clock"], SystemLifecycleClockAdapter
    )


def test_heartbeat_publishes_wall_value_through_presence_store(tmp_path):
    presence = make_test_presence_store()
    clock = FakeLifecycleClock(wall=4242.0, monotonic=7.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock, agent_presence=presence)
    with patch.object(agent, "_write_status_snapshot"):
        lifecycle_mod._write_heartbeat_tick(agent)
    assert agent._heartbeat == 4242.0
    assert presence.published_values == [4242.0]
    clock.advance_monotonic(100.0)
    clock.set_wall(5000.0)
    with patch.object(agent, "_write_status_snapshot"):
        lifecycle_mod._write_heartbeat_tick(agent)
    assert presence.published_values == [4242.0, 5000.0]


def test_event_journal_ts_is_the_wall_reading(tmp_path):
    clock = FakeLifecycleClock(wall=33_000.0, monotonic=12.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)

    class CapturingJournal:
        def __init__(self):
            self.entries = []

        def append(self, entry):
            self.entries.append(entry)

    journal = CapturingJournal()
    agent._event_journal = journal
    agent._log("custom_diagnostic_event", note="x")
    assert journal.entries[-1]["ts"] == 33_000.0
    clock.advance_monotonic(1000.0)
    clock.set_wall(34_000.0)
    agent._log("custom_diagnostic_event", note="y")
    assert journal.entries[-1]["ts"] == 34_000.0


def test_deferred_oldest_at_is_seeded_from_wall(tmp_path):
    clock = FakeLifecycleClock(wall=50_000.0, monotonic=8.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    agent._note_notification_deferred_active(("f.json", 1, "x"), sources=["email"])
    assert agent._deferred_notifications_oldest_at == 50_000.0
    clock.set_wall(60_000.0)
    clock.advance_monotonic(100.0)
    agent._note_notification_deferred_active(("f.json", 2, "y"), sources=["email"])
    assert agent._deferred_notifications_oldest_at == 50_000.0


def test_progress_event_bumps_last_progress_from_wall(tmp_path):
    clock = FakeLifecycleClock(wall=40_000.0, monotonic=5.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    clock.set_wall(40_500.0)
    clock.set_monotonic(9_999.0)
    agent._log("llm_call")
    assert agent._last_progress_at == agent._active_turn_started_at == 40_500.0


def test_constructor_uses_one_shared_wall_sample(tmp_path):
    clock = ScriptedLifecycleClock(
        wall_start=8000.0, wall_step=1.0, monotonic_start=3.0, monotonic_step=1.0
    )
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    assert agent._state_changed_at == agent._last_progress_at == 8000.0
    assert agent._idle_since_monotonic == 3.0
    assert clock.wall_reads == clock.monotonic_reads == 1


def test_state_transition_uses_wall_and_idle_anchor_uses_monotonic(tmp_path):
    clock = FakeLifecycleClock(wall=8000.0, monotonic=3.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    clock.set_wall(9000.0)
    clock.set_monotonic(50.0)
    agent._set_state(AgentState.ACTIVE, reason="test")
    assert agent._state_changed_at == agent._last_progress_at == 9000.0
    clock.set_wall(9100.0)
    clock.set_monotonic(60.0)
    agent._set_state(AgentState.IDLE, reason="test")
    assert agent._idle_since_monotonic == 60.0


def test_active_transition_shares_one_wall_sample(tmp_path):
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
    assert clock.wall_reads == wall_reads_before + 1


def test_status_ages_consume_wall_and_uptime_consumes_monotonic(tmp_path):
    clock = FakeLifecycleClock(wall=10_000.0, monotonic=100.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    agent._last_progress_at = 10_000.0
    agent._heartbeat = 9_990.0
    agent._uptime_anchor = 100.0
    clock.set_wall(10_030.0)
    clock.set_monotonic(160.0)
    status = identity_mod._status(agent)
    assert status["runtime"]["no_progress_seconds"] == 30.0
    assert status["runtime"]["heartbeat_age_seconds"] == 40.0
    assert status["runtime"]["uptime_seconds"] == 60.0
    clock.set_wall(0.0)
    assert identity_mod._status(agent)["runtime"]["uptime_seconds"] == 60.0


def test_status_active_turn_and_deferred_ages_consume_wall(tmp_path):
    clock = FakeLifecycleClock(wall=20_000.0, monotonic=500.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    agent._active_turn_kind = "llm_call"
    agent._active_turn_id = "c1"
    agent._active_turn_started_at = 20_000.0
    agent._deferred_notifications_count = 1
    agent._deferred_notifications_oldest_at = 19_990.0
    clock.set_wall(20_050.0)
    clock.set_monotonic(9_999.0)
    status = identity_mod._status(agent)
    assert status["active_turn"]["elapsed_seconds"] == 50.0
    assert status["active_turn"]["started_at"] == 20_000.0
    assert status["deferred_notifications"]["oldest_at"] == 19_990.0


def test_idle_timeout_uses_monotonic_or_explicit_seam(tmp_path):
    from lingtai.kernel.config import IDLE_SLEEP_TIMEOUT_SECONDS

    clock = FakeLifecycleClock(wall=10_000.0, monotonic=0.0)
    agent = _make_agent(tmp_path, lifecycle_clock=clock)
    with patch.object(agent, "_save_chat_history"):
        agent._idle_since_monotonic = 0.0
        clock.set_monotonic(IDLE_SLEEP_TIMEOUT_SECONDS - 1.0)
        lifecycle_mod._maybe_sleep_after_idle_timeout(agent)
        assert agent._state == AgentState.IDLE
        clock.set_monotonic(IDLE_SLEEP_TIMEOUT_SECONDS)
        lifecycle_mod._maybe_sleep_after_idle_timeout(agent)
        assert agent._state == AgentState.ASLEEP

    agent = _make_agent(tmp_path / "explicit", lifecycle_clock=clock)
    with patch.object(agent, "_save_chat_history"):
        agent._idle_since_monotonic = 100.0
        lifecycle_mod._maybe_sleep_after_idle_timeout(
            agent, now_mono=100.0 + IDLE_SLEEP_TIMEOUT_SECONDS
        )
        assert agent._state == AgentState.ASLEEP


def _one_tick_heartbeat_agent(clock, *, state, snapshot_interval, snapshot_port=None, tmp_path):
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


def test_aed_uses_monotonic_and_strict_threshold(tmp_path):
    clock = FakeLifecycleClock(wall=5_000.0, monotonic=10.0)
    agent = _one_tick_heartbeat_agent(
        clock, state=AgentState.STUCK, snapshot_interval=None, tmp_path=tmp_path
    )
    _run_one_tick(agent)
    assert agent._aed_start == 10.0
    agent._heartbeat_thread = object()
    clock.set_monotonic(agent._aed_start + agent._config.aed_timeout)
    _run_one_tick(agent)
    agent._set_state.assert_not_called()
    agent._heartbeat_thread = object()
    clock.set_monotonic(agent._aed_start + agent._config.aed_timeout + 0.5)
    _run_one_tick(agent)
    agent._set_state.assert_called_once_with(AgentState.ASLEEP, reason="AED timeout")


def test_active_watchdog_no_progress_age_is_wall(tmp_path):
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    agent = _one_tick_heartbeat_agent(
        clock, state=AgentState.ACTIVE, snapshot_interval=None, tmp_path=tmp_path
    )
    agent._last_progress_at = 1_000.0
    agent._heartbeat_thread = object()
    clock.advance_monotonic(10_000.0)
    _run_one_tick(agent)
    assert agent._active_stuck_logged is False
    agent._heartbeat_thread = object()
    clock.set_wall(11_000.0)
    _run_one_tick(agent)
    assert agent._active_stuck_logged is True
    agent._write_status_snapshot.assert_called_once_with()
