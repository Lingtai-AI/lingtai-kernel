"""Tests for lingtai.tools.soul.

After the past-self-consultation refactor, this file covers only:
- The agent-callable surface (``handle``): inquiry action, flow rejection,
  unknown-action error.
- The wall-clock soul timer (``_start_soul_timer`` / ``_cancel_soul_timer``)
  that drives consultation cadence.

The legacy diary+mirror-session machinery (``soul_flow``,
``_collect_new_diary``, ``_ensure_soul_session``, ``_save_soul_session``,
``_trim_soul_session``, ``reset_soul_session``, ``enqueue_flow_voice``,
``_soul_history_path``, ``_soul_cursor_path``) has been removed; tests for
it are gone with it. The new mechanism is covered in
``tests/test_soul_consultation.py``.
"""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

import threading
import time
from unittest.mock import MagicMock

import pytest

from lingtai.kernel.config import AgentConfig
from lingtai.tools import soul
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store


def _make_mock_agent():
    """Tiny mock for direct ``handle`` calls — no real LLM, no real chat."""
    agent = MagicMock()
    agent._soul_delay = 120.0
    return agent


def _make_mock_service():
    svc = MagicMock()
    svc.model = "test-model"
    svc.make_tool_result.return_value = {"role": "tool", "content": "ok"}
    return svc


# ---------------------------------------------------------------------------
# soul.handle — agent-callable surface
# ---------------------------------------------------------------------------


class TestSoulHandle:

    @pytest.fixture(autouse=True)
    def _enable_soul_flow(self, monkeypatch):
        # Soul flow is opt-in / disabled by default (issue: env gate).
        # This class exercises the ENABLED flow mechanics; the disabled
        # behavior is covered in TestVoluntaryFlowOptIn. inquiry/unknown
        # tests are unaffected by the gate.
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")

    def test_inquiry_returns_voice(self):
        agent = _make_mock_agent()
        agent._config.retry_timeout = 30.0
        result = soul.handle(agent, {"action": "inquiry", "inquiry": "What am I missing?"})
        assert result["status"] == "ok"
        assert "voice" in result

    def test_inquiry_requires_text(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "inquiry"})
        assert "error" in result

    def test_inquiry_rejects_empty(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "inquiry", "inquiry": "   "})
        assert "error" in result

    def test_flow_action_voluntary_succeeds_when_lock_free(self):
        """Voluntary flow returns ok when no fire is in flight; the real
        consultation runs on a daemon thread and lands later via tc_inbox."""
        agent = _make_mock_agent()
        agent._soul_fire_lock = threading.Lock()
        result = soul.handle(agent, {"action": "flow"})
        assert result.get("status") == "ok"
        assert "soul flow triggered" in result.get("message", "").lower()

    def test_flow_action_rejected_when_fire_in_flight(self):
        """Voluntary flow refuses if another fire (timer or prior voluntary)
        already holds the fire lock."""
        agent = _make_mock_agent()
        lock = threading.Lock()
        lock.acquire()
        agent._soul_fire_lock = lock
        try:
            result = soul.handle(agent, {"action": "flow"})
        finally:
            lock.release()
        assert "error" in result
        assert "ongoing" in result["error"]

    def test_flow_voluntary_waits_for_idle(self, monkeypatch):
        """Voluntary flow daemon thread waits for _idle event before
        calling _run_consultation_fire (race condition fix)."""
        agent = _make_mock_agent()
        agent._soul_fire_lock = threading.Lock()
        agent._soul_delay = 5.0
        idle_event = threading.Event()
        # Simulate ACTIVE — _idle is cleared
        idle_event.clear()
        agent._idle = idle_event

        fire_called = threading.Event()
        original_fire = soul._run_consultation_fire

        def tracking_fire(a):
            fire_called.set()

        monkeypatch.setattr(
            "lingtai.tools.soul.flow._run_consultation_fire",
            tracking_fire,
        )

        result = soul.handle(agent, {"action": "flow"})
        assert result.get("status") == "ok"

        # Fire should NOT have been called yet — still ACTIVE
        assert not fire_called.wait(timeout=0.2)

        # Simulate transition to IDLE
        idle_event.set()
        assert fire_called.wait(timeout=2.0)

    def test_flow_voluntary_timeout_when_never_idle(self, monkeypatch):
        """Voluntary flow gives up if IDLE never arrives within timeout."""
        agent = _make_mock_agent()
        agent._soul_fire_lock = threading.Lock()
        agent._soul_delay = 0.3  # Short timeout for test speed
        idle_event = threading.Event()
        idle_event.clear()
        agent._idle = idle_event

        fire_called = threading.Event()

        def tracking_fire(a):
            fire_called.set()

        monkeypatch.setattr(
            "lingtai.tools.soul.flow._run_consultation_fire",
            tracking_fire,
        )

        result = soul.handle(agent, {"action": "flow"})
        assert result.get("status") == "ok"

        # Wait longer than the timeout — fire should never be called
        time.sleep(0.6)
        assert not fire_called.is_set()

    def test_unknown_action_returns_error(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "on"})
        assert "error" in result

    def test_inquiry_works_with_large_delay(self):
        """Inquiry is independent of soul_delay value — no timer interaction."""
        agent = _make_mock_agent()
        agent._soul_delay = 999999.0
        agent._config.retry_timeout = 30.0
        result = soul.handle(agent, {"action": "inquiry", "inquiry": "Am I stuck?"})
        assert result["status"] == "ok"
        assert "voice" in result


# ---------------------------------------------------------------------------
# soul.get_schema — public schema shape
# ---------------------------------------------------------------------------


class TestSoulSchema:

    def test_schema_exposes_five_actions(self):
        schema = soul.get_schema("en")
        # Five actions are agent-visible: inquiry (manual self-Q&A),
        # flow (mechanical, fires only on the wall clock / turn counter —
        # agent cannot invoke), config (agent adjusts cadence + K
        # at runtime), voice (agent picks/customizes own soul-flow
        # prompt — read or set), and dismiss (clear soul notification).
        assert schema["properties"]["action"]["enum"] == [
            "inquiry", "flow", "config", "voice", "dismiss",
        ]

    def test_schema_inquiry_property_present(self):
        schema = soul.get_schema("en")
        assert "inquiry" in schema["properties"]

    def test_schema_config_properties_present(self):
        # config parameters — delay_seconds (number, min 30s),
        # consultation_past_count (integer, [0, 5]).
        schema = soul.get_schema("en")
        assert "delay_seconds" in schema["properties"]
        assert schema["properties"]["delay_seconds"]["type"] == "number"
        assert schema["properties"]["delay_seconds"]["minimum"] == 30.0
        assert "consultation_interval" not in schema["properties"]
        assert "consultation_past_count" in schema["properties"]
        assert schema["properties"]["consultation_past_count"]["type"] == "integer"

    def test_schema_required_is_action(self):
        assert soul.get_schema("en")["required"] == ["action"]


# ---------------------------------------------------------------------------
# Soul timer — wall-clock cadence that drives _run_consultation_fire
# ---------------------------------------------------------------------------


class TestSoulTimer:

    @pytest.fixture(autouse=True)
    def _enable_soul_flow(self, monkeypatch):
        # Timer arming is gated on the opt-in env var (default off). These
        # tests cover timer mechanics when flow is enabled; the disabled
        # case is covered in TestSoulTimerOptIn.
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")

    def test_soul_attributes_initialized_default(self, tmp_path):
        """BaseAgent with default config has soul_delay=999999999."""
        from lingtai.kernel import BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        assert agent._soul_delay == 999999999.0
        assert agent._soul_timer is None

    def test_soul_timer_lifecycle_follows_idle_state(self, tmp_path):
        """Timer starts on IDLE entry and cancels on IDLE exit.

        _set_state starts a soul timer when entering IDLE and cancels it
        when leaving IDLE (to ACTIVE, STUCK, etc.).
        """
        from lingtai.kernel import AgentState, BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 300.0

        # Going ACTIVE from initial IDLE cancels the timer (if any).
        agent._set_state(AgentState.ACTIVE, reason="test")
        assert agent._soul_timer is None

        # Entering IDLE starts a fresh timer.
        agent._set_state(AgentState.IDLE, reason="done")
        assert agent._soul_timer is not None
        assert agent._soul_timer.is_alive()

        # Leaving IDLE cancels it.
        agent._set_state(AgentState.ACTIVE, reason="new mail")
        assert agent._soul_timer is None

    def test_soul_timer_not_started_when_shutdown(self, tmp_path):
        """_start_soul_timer is a no-op when _shutdown is set."""
        from lingtai.kernel import BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 1.0
        agent._shutdown.set()
        agent._start_soul_timer()
        assert agent._soul_timer is None

    def test_soul_delay_from_config(self, tmp_path):
        """soul_delay in config sets initial _soul_delay."""
        from lingtai.kernel import BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            config=AgentConfig(soul_delay=60.0),
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        assert agent._soul_delay == 60.0

    def test_soul_delay_clamped_to_min(self, tmp_path):
        """soul_delay below 1 is clamped to 1."""
        from lingtai.kernel import BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            config=AgentConfig(soul_delay=-10.0),
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        assert agent._soul_delay == 1.0

    def test_stop_cancels_soul_timer(self, tmp_path):
        from lingtai.kernel import BaseAgent
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 300.0
        agent._start_soul_timer()
        assert agent._soul_timer is not None
        agent.stop()
        assert agent._soul_timer is None


class TestSoulFireAllowed:
    """_soul_fire_allowed compares by string value, not enum identity."""

    def test_allows_idle_state(self):
        from lingtai.tools.soul.flow import _soul_fire_allowed
        from lingtai.kernel.state import AgentState
        agent = MagicMock()
        agent._state = AgentState.IDLE
        assert _soul_fire_allowed(agent) is True

    def test_rejects_active_state(self):
        from lingtai.tools.soul.flow import _soul_fire_allowed
        from lingtai.kernel.state import AgentState
        agent = MagicMock()
        agent._state = AgentState.ACTIVE
        assert _soul_fire_allowed(agent) is False

    def test_allows_foreign_enum_with_idle_value(self):
        """Simulates stale-enum mismatch: a different Enum class whose
        .value is 'idle' should still be accepted."""
        import enum
        from lingtai.tools.soul.flow import _soul_fire_allowed

        class ForeignState(enum.Enum):
            IDLE = "idle"

        agent = MagicMock()
        agent._state = ForeignState.IDLE
        assert _soul_fire_allowed(agent) is True

    def test_rejects_foreign_enum_with_active_value(self):
        import enum
        from lingtai.tools.soul.flow import _soul_fire_allowed

        class ForeignState(enum.Enum):
            ACTIVE = "active"

        agent = MagicMock()
        agent._state = ForeignState.ACTIVE
        assert _soul_fire_allowed(agent) is False


# ---------------------------------------------------------------------------
# Soul flow opt-in gate — LINGTAI_SOUL_FLOW_ENABLED (default disabled)
# ---------------------------------------------------------------------------


class TestSoulFlowEnvParsing:
    """_soul_flow_enabled parses the env var truthy/falsey, default off."""

    def test_unset_is_disabled(self, monkeypatch):
        from lingtai.tools.soul.flow import _soul_flow_enabled
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        assert _soul_flow_enabled() is False

    def test_truthy_values_enable(self, monkeypatch):
        from lingtai.tools.soul.flow import _soul_flow_enabled
        for val in ("1", "true", "TRUE", "Yes", "on", " on ", "ON"):
            monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", val)
            assert _soul_flow_enabled() is True, f"{val!r} should enable"

    def test_falsey_values_disable(self, monkeypatch):
        from lingtai.tools.soul.flow import _soul_flow_enabled
        for val in ("0", "", "false", "no", "off", "disabled", "2", "  "):
            monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", val)
            assert _soul_flow_enabled() is False, f"{val!r} should disable"


class TestSoulTimerOptIn:
    """The wall-clock timer arms only when soul flow is opted in."""

    def test_timer_not_armed_when_disabled(self, tmp_path, monkeypatch):
        from lingtai.kernel import BaseAgent
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 300.0
        agent._start_soul_timer()
        assert agent._soul_timer is None

    def test_timer_armed_when_enabled(self, tmp_path, monkeypatch):
        from lingtai.kernel import BaseAgent
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 300.0
        try:
            agent._start_soul_timer()
            assert agent._soul_timer is not None
            assert agent._soul_timer.is_alive()
        finally:
            agent.stop(timeout=1.0)

    def test_timer_follows_idle_only_when_enabled(self, tmp_path, monkeypatch):
        """_set_state IDLE entry does not arm the timer while disabled."""
        from lingtai.kernel import AgentState, BaseAgent
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        agent._soul_delay = 300.0
        agent._set_state(AgentState.ACTIVE, reason="test")
        agent._set_state(AgentState.IDLE, reason="done")
        # Disabled: entering IDLE must not arm a timer.
        assert agent._soul_timer is None


class TestVoluntaryFlowOptIn:
    """Voluntary soul(action='flow') is gated on the env var."""

    def test_disabled_returns_disabled_payload_no_thread(self, monkeypatch):
        """Disabled voluntary flow returns a stable disabled payload and
        does NOT spawn the fire thread or touch the lock."""
        import threading as _threading
        from lingtai.tools.soul.flow import SOUL_FLOW_ENABLED_ENV
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        agent = _make_mock_agent()
        # A live lock — the gate must return BEFORE any lock interaction.
        agent._soul_fire_lock = _threading.Lock()

        before = _threading.active_count()
        result = soul.handle(agent, {"action": "flow"})
        # No fire thread spawned.
        assert _threading.active_count() == before

        assert result["status"] == "disabled"
        assert result["enabled"] is False
        assert result["env_var"] == SOUL_FLOW_ENABLED_ENV
        assert SOUL_FLOW_ENABLED_ENV in result["message"]
        assert "soul-manual" in result["message"]
        # It is NOT an error — agents must not retry blindly.
        assert "error" not in result
        # Lock was never taken.
        assert agent._soul_fire_lock.acquire(blocking=False) is True
        agent._soul_fire_lock.release()

    def test_enabled_preserves_voluntary_ok(self, monkeypatch):
        """When enabled, voluntary flow behaves as before (status ok)."""
        import threading as _threading
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")
        agent = _make_mock_agent()
        agent._soul_fire_lock = _threading.Lock()
        result = soul.handle(agent, {"action": "flow"})
        assert result.get("status") == "ok"
        assert "soul flow triggered" in result.get("message", "").lower()


class TestConsultationFireOptIn:
    """_run_consultation_fire defensively no-ops when disabled."""

    def test_fire_noops_when_disabled(self, monkeypatch):
        from lingtai.tools.soul import flow
        from lingtai.kernel.state import AgentState
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)

        agent = MagicMock()
        agent._state = AgentState.IDLE
        logs = []
        agent._log.side_effect = lambda ev, **kw: logs.append((ev, kw))

        publish = MagicMock()
        monkeypatch.setattr(
            "lingtai.tools.system.publish_notification",
            publish,
            raising=False,
        )
        monkeypatch.setattr(flow, "_append_soul_flow_record", MagicMock())

        flow._run_consultation_fire(agent)

        publish.assert_not_called()
        assert any(ev == "soul_flow_disabled" for ev, _ in logs)
        # It must not even reach the fire gate check.
        assert not any(ev == "soul_fire_gate_check" for ev, _ in logs)

    def test_fire_runs_when_enabled(self, monkeypatch):
        """Sanity: with env enabled the fire proceeds past the env gate to
        the existing state/lock gates (reaches soul_fire_gate_check)."""
        from lingtai.tools.soul import flow
        from lingtai.kernel.state import AgentState
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")

        agent = MagicMock()
        # ACTIVE so it stops at the existing state gate, not the env gate.
        agent._state = AgentState.ACTIVE
        logs = []
        agent._log.side_effect = lambda ev, **kw: logs.append((ev, kw))
        monkeypatch.setattr(flow, "_append_soul_flow_record", MagicMock())

        flow._run_consultation_fire(agent)

        # Reached the gate check (env gate passed) but stopped on state.
        assert any(ev == "soul_fire_gate_check" for ev, _ in logs)
        assert any(ev == "consultation_skipped_state" for ev, _ in logs)
        assert not any(ev == "soul_flow_disabled" for ev, _ in logs)


class TestNonFlowActionsUnaffectedByOptIn:
    """inquiry/config/voice/dismiss work regardless of the flow env gate."""

    def test_inquiry_works_when_flow_disabled(self, monkeypatch):
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        agent = _make_mock_agent()
        agent._config.retry_timeout = 30.0
        result = soul.handle(agent, {"action": "inquiry", "inquiry": "Am I ok?"})
        assert result["status"] == "ok"
        assert "voice" in result

    def test_config_works_when_flow_disabled_and_notes_state(self, tmp_path, monkeypatch):
        """config succeeds when flow is disabled and appends a disabled note."""
        from lingtai.kernel import BaseAgent
        from lingtai.tools.soul.flow import SOUL_FLOW_ENABLED_ENV
        monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        try:
            result = soul.handle(agent, {"action": "config", "delay_seconds": 300})
            assert result["status"] == "ok"
            assert result["new"]["delay_seconds"] == 300.0
            # Disabled note surfaced; config does not enable flow.
            assert result.get("soul_flow_enabled") is False
            assert SOUL_FLOW_ENABLED_ENV in result.get("note", "")
            # config must NOT arm a timer while flow is disabled.
            assert agent._soul_timer is None
        finally:
            agent.stop(timeout=1.0)

    def test_config_no_note_when_flow_enabled(self, tmp_path, monkeypatch):
        from lingtai.kernel import BaseAgent
        monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")
        agent = BaseAgent(
            intrinsics=_TEST_INTRINSICS,
            service=_make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent", workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(tmp_path / "test_agent"),
        )
        try:
            result = soul.handle(agent, {"action": "config", "delay_seconds": 300})
            assert result["status"] == "ok"
            assert "soul_flow_enabled" not in result
            assert "note" not in result
        finally:
            agent.stop(timeout=1.0)


def test_consultation_fire_discards_late_result_after_state_change(monkeypatch):
    """If the agent becomes STUCK while consultation is running, the late
    result must not enqueue a TC wake into an unsafe interface window.

    The fire starts while IDLE (passes the gate), but the batch callback
    transitions the agent to STUCK mid-flight — the post-batch state
    check must discard the result.
    """
    from lingtai.tools.soul import flow
    from lingtai.kernel.llm.interface import TextBlock
    from lingtai.kernel.state import AgentState

    # Soul flow is opt-in / disabled by default — enable it so this test
    # exercises the fire path past the env gate.
    monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "1")

    agent = MagicMock()
    agent._state = AgentState.IDLE  # Must start IDLE to pass the gate
    agent._logs = []
    agent._tc_inbox.enqueue = MagicMock()

    def log(event_type, **fields):
        agent._logs.append((event_type, fields))
    agent._log.side_effect = log

    monkeypatch.setattr(flow, "_append_soul_flow_record", MagicMock())

    def fake_batch(_agent):
        _agent._state = AgentState.STUCK
        return [{"source": "insights", "blocks": [TextBlock(text="late")]}]

    monkeypatch.setattr(
        "lingtai.tools.soul.consultation._render_current_diary",
        lambda _agent: "diary",
    )
    monkeypatch.setattr(
        "lingtai.tools.soul.consultation._run_consultation_batch",
        fake_batch,
    )
    monkeypatch.setattr(
        "lingtai.tools.soul.consultation.build_consultation_pair",
        MagicMock(),
    )

    flow._run_consultation_fire(agent)

    # Soul flow no longer enqueues a TC wake directly; filesystem
    # notification + heartbeat sync owns injection/wake-up. A mid-flight state
    # change therefore must not touch tc_inbox, and the fire is allowed to
    # publish/log through the notification path without the old
    # consultation_discarded_state event.
    agent._tc_inbox.enqueue.assert_not_called()
    assert any(name == "consultation_fire" for name, _ in agent._logs)
