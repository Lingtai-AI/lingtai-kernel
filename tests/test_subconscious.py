"""Tests for the subconscious engine (Architecture B: inline injection).

Tests the shared engine extraction, config hard-gating, JSON parsing,
timer lifecycle, in-memory insight storage, and IDLE-gated soul flow.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingtai_kernel.config import AgentConfig
from lingtai_kernel.state import AgentState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_service():
    svc = MagicMock()
    svc.model = "test-model"
    svc.provider = "test-provider"
    return svc


def _make_agent(tmp_path, **config_kw):
    from lingtai_kernel import BaseAgent
    return BaseAgent(
        service=_make_mock_service(),
        agent_name="test",
        working_dir=tmp_path / "test_agent",
        config=AgentConfig(**config_kw),
    )


# ---------------------------------------------------------------------------
# Config hard-gating
# ---------------------------------------------------------------------------


class TestSubconsciousConfigHardGating:
    """enabling requires both provider and model to be explicitly set."""

    def test_enable_without_provider_fails(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.config import _handle_config
        result = _handle_config(agent, {
            "subconscious_enabled": True,
            "subconscious_model": "cheap-model",
        })
        assert "error" in result
        assert "subconscious_provider" in result["error"]
        assert not agent._config.subconscious_enabled

    def test_enable_without_model_fails(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.config import _handle_config
        result = _handle_config(agent, {
            "subconscious_enabled": True,
            "subconscious_provider": "mimo",
        })
        assert "error" in result
        assert "subconscious_model" in result["error"]
        assert not agent._config.subconscious_enabled

    def test_enable_with_both_succeeds(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.config import _handle_config
        result = _handle_config(agent, {
            "subconscious_enabled": True,
            "subconscious_provider": "mimo",
            "subconscious_model": "mimo-2-cheap",
        })
        assert result["status"] == "ok"
        assert result["new"]["subconscious_enabled"] is True
        assert agent._config.subconscious_enabled is True
        assert agent._config.subconscious_provider == "mimo"
        assert agent._config.subconscious_model == "mimo-2-cheap"

    def test_set_provider_model_before_enable(self, tmp_path):
        """Provider and model can be set in separate config calls."""
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.config import _handle_config

        # Set provider first.
        r1 = _handle_config(agent, {"subconscious_provider": "mimo"})
        assert r1["status"] == "ok"

        # Set model.
        r2 = _handle_config(agent, {"subconscious_model": "cheap"})
        assert r2["status"] == "ok"

        # Now enable — both are set.
        r3 = _handle_config(agent, {"subconscious_enabled": True})
        assert r3["status"] == "ok"
        assert agent._config.subconscious_enabled is True

    def test_disable_succeeds_without_provider_model(self, tmp_path):
        """Disabling doesn't require provider/model."""
        agent = _make_agent(tmp_path, subconscious_enabled=True)
        agent._config.subconscious_provider = None
        agent._config.subconscious_model = None
        from lingtai_kernel.intrinsics.soul.config import _handle_config
        result = _handle_config(agent, {"subconscious_enabled": False})
        assert result["status"] == "ok"
        assert agent._config.subconscious_enabled is False


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


class TestSubconsciousConfigPersistence:
    """subconscious config round-trips through init.json."""

    def test_persist_subconscious_config(self, tmp_path):
        agent = _make_agent(tmp_path)
        init_path = agent._working_dir / "init.json"
        init_path.parent.mkdir(parents=True, exist_ok=True)
        init_path.write_text(json.dumps({
            "manifest": {"llm": {}}
        }), encoding="utf-8")

        from lingtai_kernel.intrinsics.soul.config import _persist_soul_config
        _persist_soul_config(agent, {
            "subconscious_enabled": True,
            "subconscious_provider": "mimo",
            "subconscious_model": "cheap",
        })

        data = json.loads(init_path.read_text())
        sub = data["manifest"]["soul"]["subconscious"]
        assert sub["enabled"] is True
        assert sub["provider"] == "mimo"
        assert sub["model"] == "cheap"


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestSubconsciousJsonParsing:
    """Parse structured and unstructured LLM responses."""

    def test_parse_valid_json(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response(
            '{"insight": "pattern X matches Y", "confidence": 0.8, "source_memory": "snapshot_001"}'
        )
        assert result is not None
        assert result["insight"] == "pattern X matches Y"
        assert result["confidence"] == 0.8
        assert result["source_memory"] == "snapshot_001"

    def test_parse_null_insight_returns_none(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response('{"insight": null}')
        assert result is None

    def test_parse_empty_insight_returns_none(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response('{"insight": ""}')
        assert result is None

    def test_parse_markdown_wrapped_json(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response(
            '```json\n{"insight": "found it", "confidence": 0.9}\n```'
        )
        assert result is not None
        assert result["insight"] == "found it"

    def test_parse_unstructured_text(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response("This reminds me of something.")
        assert result is not None
        assert result["insight"] == "This reminds me of something."
        assert result["confidence"] == 0.5
        assert result["source_memory"] == "unstructured"

    def test_confidence_clamped(self):
        from lingtai_kernel.intrinsics.soul.subconscious import _parse_subconscious_response
        result = _parse_subconscious_response(
            '{"insight": "test", "confidence": 1.5}'
        )
        assert result["confidence"] == 1.0

        result = _parse_subconscious_response(
            '{"insight": "test", "confidence": -0.5}'
        )
        assert result["confidence"] == 0.0


# ---------------------------------------------------------------------------
# In-memory insight storage (replaces JSONL tests)
# ---------------------------------------------------------------------------


class TestSubconsciousInMemoryStorage:
    """Store, read, and clear subconscious insights in-memory."""

    def test_store_and_read(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import (
            _store_subconscious_insight,
            _get_subconscious_insights,
            _clear_subconscious_insights,
        )

        _store_subconscious_insight(agent, {
            "ts": time.time(),
            "fire_id": "test",
            "insight": "pattern found",
            "confidence": 0.7,
            "source_memory": "snap",
            "model_used": "cheap",
        })

        insights = _get_subconscious_insights(agent)
        assert len(insights) == 1
        assert insights[0]["insight"] == "pattern found"
        assert insights[0]["confidence"] == 0.7

        _clear_subconscious_insights(agent)
        insights = _get_subconscious_insights(agent)
        assert len(insights) == 0

    def test_multiple_insights_order(self, tmp_path):
        """Insights are stored in order."""
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import (
            _store_subconscious_insight,
            _get_subconscious_insights,
        )

        _store_subconscious_insight(agent, {
            "ts": time.time() - 10,
            "fire_id": "f1",
            "insight": "first insight",
            "confidence": 0.5,
            "source_memory": "s1",
            "model_used": "m",
        })
        _store_subconscious_insight(agent, {
            "ts": time.time(),
            "fire_id": "f2",
            "insight": "second insight",
            "confidence": 0.8,
            "source_memory": "s2",
            "model_used": "m",
        })

        insights = _get_subconscious_insights(agent)
        assert len(insights) == 2
        assert insights[0]["insight"] == "first insight"
        assert insights[1]["insight"] == "second insight"

    def test_clear_is_idempotent(self, tmp_path):
        """Clearing empty insights is safe."""
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import _clear_subconscious_insights
        _clear_subconscious_insights(agent)  # No error
        _clear_subconscious_insights(agent)  # Still no error

    def test_thread_safety(self, tmp_path):
        """Concurrent stores are thread-safe."""
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import (
            _store_subconscious_insight,
            _get_subconscious_insights,
        )

        def store_worker(i):
            _store_subconscious_insight(agent, {
                "ts": time.time(),
                "fire_id": f"f{i}",
                "insight": f"insight {i}",
                "confidence": 0.5,
                "source_memory": "s",
                "model_used": "m",
            })

        threads = [threading.Thread(target=store_worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        insights = _get_subconscious_insights(agent)
        assert len(insights) == 10


# ---------------------------------------------------------------------------
# Timer lifecycle
# ---------------------------------------------------------------------------


class TestSubconsciousTimerLifecycle:
    """Timer starts on turn start, cancels on state transition."""

    def test_timer_not_started_when_disabled(self, tmp_path):
        agent = _make_agent(tmp_path, subconscious_enabled=False)
        from lingtai_kernel.intrinsics.soul.subconscious import _start_subconscious_timer
        _start_subconscious_timer(agent)
        assert getattr(agent, "_subconscious_timer", None) is None

    def test_timer_started_when_enabled(self, tmp_path):
        agent = _make_agent(tmp_path, subconscious_enabled=True)
        from lingtai_kernel.intrinsics.soul.subconscious import (
            _start_subconscious_timer,
            _cancel_subconscious_timer,
        )
        _start_subconscious_timer(agent)
        assert agent._subconscious_timer is not None
        assert agent._subconscious_timer.is_alive()
        _cancel_subconscious_timer(agent)
        assert agent._subconscious_timer is None

    def test_timer_interval_is_30s(self, tmp_path):
        """Architecture B uses 30s interval (not 60s)."""
        from lingtai_kernel.intrinsics.soul.subconscious import _SUBCONSCIOUS_FIRE_INTERVAL
        assert _SUBCONSCIOUS_FIRE_INTERVAL == 30.0

    def test_state_transition_cancels_timer(self, tmp_path):
        agent = _make_agent(tmp_path, subconscious_enabled=True)
        from lingtai_kernel.intrinsics.soul.subconscious import _start_subconscious_timer
        _start_subconscious_timer(agent)
        assert agent._subconscious_timer is not None

        # Transition away from ACTIVE cancels the timer.
        agent._state = AgentState.ACTIVE
        agent._set_state(AgentState.IDLE, reason="turn done")
        assert agent._subconscious_timer is None


# ---------------------------------------------------------------------------
# Shared engine
# ---------------------------------------------------------------------------


class TestSharedConsultationEngine:
    """_run_consultation_voice respects allow_tool_recommendations."""

    def test_no_tools_when_disabled(self, tmp_path):
        """allow_tool_recommendations=False passes tools=None."""
        from lingtai_kernel.intrinsics.soul.consultation import _run_consultation_voice
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock

        agent = _make_agent(tmp_path)
        agent._config.model = "test-model"

        iface = ChatInterface()
        iface.add_user_message("hello")

        mock_response = MagicMock()
        mock_response.text = "response text"
        mock_response.tool_calls = []
        mock_response.thoughts = []
        mock_response.usage = MagicMock(
            input_tokens=0, output_tokens=0,
            thinking_tokens=0, cached_tokens=0,
        )

        mock_session = MagicMock()
        mock_session.interface = MagicMock()
        mock_session.interface.entries = [MagicMock(role="assistant", content=[TextBlock(text="response text")])]
        agent.service.create_session.return_value = mock_session

        with patch(
            "lingtai_kernel.intrinsics.soul.consultation._send_with_timeout",
            return_value=mock_response,
        ):
            result = _run_consultation_voice(
                agent, iface, "test",
                system_prompt="test prompt",
                spark="test spark",
                allow_tool_recommendations=False,
            )

        # Verify tools=None was passed.
        call_kwargs = agent.service.create_session.call_args
        assert call_kwargs.kwargs.get("tools") is None or call_kwargs[1].get("tools") is None

    def test_tools_passed_when_enabled(self, tmp_path):
        """allow_tool_recommendations=True passes tool schemas."""
        from lingtai_kernel.intrinsics.soul.consultation import _run_consultation_voice
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock

        agent = _make_agent(tmp_path)
        agent._config.model = "test-model"
        agent._session = MagicMock()
        agent._session._build_tool_schemas_fn.return_value = [{"name": "test"}]

        iface = ChatInterface()
        iface.add_user_message("hello")

        mock_response = MagicMock()
        mock_response.text = "done"
        mock_response.tool_calls = []
        mock_response.thoughts = []
        mock_response.usage = MagicMock(
            input_tokens=0, output_tokens=0,
            thinking_tokens=0, cached_tokens=0,
        )

        mock_session = MagicMock()
        mock_session.interface = MagicMock()
        mock_session.interface.entries = [MagicMock(role="assistant", content=[TextBlock(text="done")])]
        agent.service.create_session.return_value = mock_session

        with patch(
            "lingtai_kernel.intrinsics.soul.consultation._send_with_timeout",
            return_value=mock_response,
        ):
            result = _run_consultation_voice(
                agent, iface, "test",
                system_prompt="test prompt",
                spark="test spark",
                allow_tool_recommendations=True,
            )

        # Verify tools were passed (not None).
        call_kwargs = agent.service.create_session.call_args
        tools = call_kwargs.kwargs.get("tools") or call_kwargs[1].get("tools")
        assert tools is not None


# ---------------------------------------------------------------------------
# Session overrides
# ---------------------------------------------------------------------------


class TestSessionOverrides:
    """Session overrides are passed through to create_session."""

    def test_model_override(self, tmp_path):
        from lingtai_kernel.intrinsics.soul.subconscious import _build_session_overrides

        agent = _make_agent(tmp_path,
                            subconscious_provider="mimo",
                            subconscious_model="cheap-model",
                            subconscious_base_url="http://localhost:8080")

        overrides = _build_session_overrides(agent)
        assert overrides["provider"] == "mimo"
        assert overrides["model"] == "cheap-model"
        assert overrides["base_url"] == "http://localhost:8080"

    def test_empty_overrides(self, tmp_path):
        from lingtai_kernel.intrinsics.soul.subconscious import _build_session_overrides
        agent = _make_agent(tmp_path)
        overrides = _build_session_overrides(agent)
        assert overrides.get("provider") is None or "provider" not in overrides


# ---------------------------------------------------------------------------
# IDLE-gated soul flow
# ---------------------------------------------------------------------------


class TestIdleGatedSoulFlow:
    """Soul flow fires only on IDLE, not ACTIVE."""

    def test_soul_fire_allowed_idle(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._state = AgentState.IDLE
        from lingtai_kernel.intrinsics.soul.flow import _soul_fire_allowed
        assert _soul_fire_allowed(agent) is True

    def test_soul_fire_not_allowed_active(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._state = AgentState.ACTIVE
        from lingtai_kernel.intrinsics.soul.flow import _soul_fire_allowed
        assert _soul_fire_allowed(agent) is False

    def test_soul_fire_not_allowed_asleep(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._state = AgentState.ASLEEP
        from lingtai_kernel.intrinsics.soul.flow import _soul_fire_allowed
        assert _soul_fire_allowed(agent) is False


# ---------------------------------------------------------------------------
# Current context building
# ---------------------------------------------------------------------------


class TestCurrentContextBuilding:
    """_build_current_context clones the current chat interface."""

    def test_no_chat_returns_none(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent._chat = None
        from lingtai_kernel.intrinsics.soul.subconscious import _build_current_context
        result = _build_current_context(agent)
        assert result is None

    def test_clones_interface(self, tmp_path):
        from lingtai_kernel.intrinsics.soul.subconscious import _build_current_context
        from lingtai_kernel.llm.interface import ChatInterface

        agent = _make_agent(tmp_path)
        mock_iface = ChatInterface()
        mock_iface.add_user_message("test message")
        agent._chat = MagicMock()
        agent._chat.interface = mock_iface

        result = _build_current_context(agent)
        assert result is not None
        assert len(result.entries) == 1
        # Verify it's a clone (not the same object)
        assert result is not mock_iface


# ---------------------------------------------------------------------------
# Notification injection
# ---------------------------------------------------------------------------


class TestNotificationInjection:
    """_inject_subconscious_inline publishes to the notification system."""

    def test_no_insights_is_noop(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import _inject_subconscious_inline
        # Should not raise
        _inject_subconscious_inline(agent)

    def test_publishes_latest_insight(self, tmp_path):
        agent = _make_agent(tmp_path)
        from lingtai_kernel.intrinsics.soul.subconscious import (
            _store_subconscious_insight,
            _inject_subconscious_inline,
        )

        _store_subconscious_insight(agent, {
            "ts": time.time(),
            "fire_id": "test",
            "insight": "check your email",
            "confidence": 0.9,
            "source_memory": "past_snapshot",
            "model_used": "cheap",
        })

        with patch(
            "lingtai_kernel.intrinsics.system.publish_notification"
        ) as mock_publish:
            _inject_subconscious_inline(agent)
            mock_publish.assert_called_once()
            # Verify channel name and data
            call_args = mock_publish.call_args
            assert call_args[0][1] == "subconscious"
            assert call_args.kwargs["data"]["insight"] == "check your email"
            assert call_args.kwargs["data"]["confidence"] == 0.9
