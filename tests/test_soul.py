"""Tests for lingtai_kernel.intrinsics.soul."""
from unittest.mock import MagicMock

from lingtai_kernel.intrinsics import soul


def _make_mock_agent():
    agent = MagicMock()
    agent._soul_delay = 120.0
    agent._soul_prompt = ""
    agent._soul_oneshot = False
    return agent


class TestSoulHandle:

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

    def test_delay_action_updates_delay(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "delay", "delay": 30})
        assert result["status"] == "ok"
        assert result["delay"] == 30.0
        assert agent._soul_delay == 30.0

    def test_delay_must_be_positive(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "delay", "delay": -5})
        assert "error" in result

    def test_delay_allows_very_large_values(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "delay", "delay": 999999})
        assert result["status"] == "ok"
        assert agent._soul_delay == 999999.0

    def test_non_numeric_delay_rejected(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "delay", "delay": "fast"})
        assert "error" in result

    def test_none_delay_rejected(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "delay", "delay": None})
        assert "error" in result

    def test_unknown_action(self):
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "on"})
        assert "error" in result

    def test_flow_action_rejected_manually(self):
        """flow fires mechanically — agent cannot invoke it directly."""
        agent = _make_mock_agent()
        result = soul.handle(agent, {"action": "flow"})
        assert "error" in result
        assert "cannot be invoked manually" in result["error"]

    def test_inquiry_works_with_large_delay(self):
        """Inquiry is independent of soul_delay value."""
        agent = _make_mock_agent()
        agent._soul_delay = 999999.0
        agent._config.retry_timeout = 30.0
        result = soul.handle(agent, {"action": "inquiry", "inquiry": "Am I stuck?"})
        assert result["status"] == "ok"
        assert "voice" in result


from lingtai_kernel.intrinsics.soul import soul_flow, soul_inquiry


class TestSoulFlow:

    def _make_whisper_agent(self, soul_prompt=""):
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock

        agent = MagicMock()
        iface = ChatInterface()
        iface.add_system("You are a test agent.")
        iface.add_user_message("Hello")
        iface.add_assistant_message([TextBlock(text="Hi there!")])

        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat
        agent._build_system_prompt = MagicMock(return_value="You are a test agent.")
        agent._soul_prompt = soul_prompt
        agent._soul_delay = 120.0
        agent._soul_session = None
        agent._soul_cursor = 0

        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "You should check your notes."
        mock_response.thoughts = ["Maybe I should review my earlier findings."]
        mock_session.send.return_value = mock_response
        agent.service.create_session.return_value = mock_session
        agent._config = MagicMock()
        agent._config.language = "en"
        agent._config.provider = None
        agent._config.model = None
        agent._config.retry_timeout = 30.0
        agent.service.model = "test-model"

        return agent, mock_session

    def test_whisper_flow_mode(self):
        """Flow mode: diary is collected and sent to soul session."""
        agent, mock_session = self._make_whisper_agent(soul_prompt="")
        agent._soul_cursor = 0
        result = soul_flow(agent)
        assert result is not None
        assert result["voice"] == "You should check your notes."
        assert mock_session.send.called

    def test_whisper_inquiry_mode(self):
        """Inquiry mode: question as prompt, no timestamp."""
        agent, mock_session = self._make_whisper_agent(soul_prompt="What am I missing?")
        result = soul_inquiry(agent, "What am I missing?")
        assert result is not None
        assert result["voice"] == "You should check your notes."
        assert result["prompt"] == "What am I missing?"

    def test_whisper_returns_none_when_no_diary(self):
        """No new diary entries → soul_flow returns None (no fallback)."""
        agent, mock_session = self._make_whisper_agent(soul_prompt="")
        agent._soul_cursor = 1000  # past all entries
        result = soul_flow(agent)
        assert result is None

    def test_whisper_returns_none_when_no_chat(self):
        agent = MagicMock()
        agent._chat = None
        result = soul_flow(agent)
        assert result is None

    def test_whisper_returns_none_on_empty_interface(self):
        from lingtai_kernel.llm.interface import ChatInterface

        agent = MagicMock()
        iface = ChatInterface()
        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat
        agent._soul_cursor = 0
        result = soul_flow(agent)
        assert result is None

    def test_whisper_returns_none_on_api_error(self):
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock

        agent = MagicMock()
        iface = ChatInterface()
        iface.add_user_message("Hello")
        iface.add_assistant_message([TextBlock(text="Hi")])

        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat
        agent._build_system_prompt = MagicMock(return_value="test")
        agent._soul_prompt = ""
        agent._soul_cursor = 0
        agent._soul_session = None
        agent._config = MagicMock()
        agent._config.language = "en"
        agent._config.provider = None
        agent._config.model = None
        agent._config.retry_timeout = 30.0
        agent.service.model = "test-model"
        agent.service.create_session.side_effect = RuntimeError("API down")

        result = soul_flow(agent)
        assert result is None


import threading
import time

from lingtai_kernel.config import AgentConfig


def make_mock_service():
    svc = MagicMock()
    svc.model = "test-model"
    svc.make_tool_result.return_value = {"role": "tool", "content": "ok"}
    return svc


class TestSoulTimer:

    def test_soul_attributes_initialized_default(self, tmp_path):
        """BaseAgent with default config has soul_delay=120."""
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        assert agent._soul_delay == 120.0
        assert agent._soul_prompt == ""
        assert agent._soul_oneshot is False
        assert agent._soul_timer is None

    def test_soul_timer_runs_perpetual_regardless_of_state(self, tmp_path):
        """Timer is no longer state-gated — runs from boot perpetually.

        Cadence model: soul flow fires every _soul_delay seconds on a wall
        clock, independent of ACTIVE/IDLE transitions. The timer is started
        explicitly via _start_soul_timer() (called by start() at boot, and
        rescheduled in _soul_whisper finally).
        """
        from lingtai_kernel import BaseAgent, AgentState
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        agent._soul_delay = 300.0
        # State transitions must NOT touch the timer anymore.
        agent._set_state(AgentState.ACTIVE, reason="test")
        assert agent._soul_timer is None  # not started by state alone
        agent._set_state(AgentState.IDLE, reason="done")
        assert agent._soul_timer is None  # idle no longer kicks the timer

        # Explicit start (the path normally taken by start() at boot).
        agent._start_soul_timer()
        assert agent._soul_timer is not None
        assert agent._soul_timer.is_alive()

        # Going active does NOT cancel the timer — it keeps cadence.
        agent._set_state(AgentState.ACTIVE, reason="new mail")
        assert agent._soul_timer is not None

        agent._cancel_soul_timer()

    def test_soul_timer_not_started_when_shutdown(self, tmp_path):
        """Timer respects shutdown — _start_soul_timer is a no-op when _shutdown is set."""
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        agent._soul_delay = 1.0
        agent._shutdown.set()
        agent._start_soul_timer()
        assert agent._soul_timer is None

    def test_soul_delay_from_config(self, tmp_path):
        """soul_delay in config sets initial _soul_delay."""
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            config=AgentConfig(soul_delay=60.0),
            working_dir=tmp_path / "test_agent",
        )
        assert agent._soul_delay == 60.0

    def test_soul_delay_clamped_to_min(self, tmp_path):
        """soul_delay below 1 is clamped to 1."""
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            config=AgentConfig(soul_delay=-10.0),
            working_dir=tmp_path / "test_agent",
        )
        assert agent._soul_delay == 1.0


class TestSoulCleanup:

    def test_stop_cancels_soul_timer(self, tmp_path):
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        agent._soul_delay = 300.0
        agent._start_soul_timer()  # explicit start (boot path)
        assert agent._soul_timer is not None
        agent.stop()
        assert agent._soul_timer is None


class TestSoulIntegration:

    def test_flow_enqueues_synthetic_pair_on_tc_inbox(self, tmp_path):
        """Flow whisper fires → synthetic (call, result) pair lands on tc_inbox.

        Replaces the old inbox-injection contract. The voice is no longer a
        plain user-role text message with [soul flow] prefix; it's a real
        soul(action='flow') tool call + result pair queued for splice into
        the wire chat at the next safe boundary.
        """
        from lingtai_kernel import BaseAgent
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock

        svc = make_mock_service()
        agent = BaseAgent(
            service=svc,
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )

        iface = ChatInterface()
        iface.add_system("You are a test agent.")
        iface.add_user_message("Research quantum computing.")
        iface.add_assistant_message([TextBlock(text="I'll start researching.")])

        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat

        mock_soul_session = MagicMock()
        mock_soul_response = MagicMock()
        mock_soul_response.text = "Have you considered the energy implications?"
        mock_soul_response.thoughts = ["The user asked about quantum computing..."]
        # Token usage on the response (consumed by _write_soul_tokens)
        mock_soul_response.usage.input_tokens = 0
        mock_soul_response.usage.output_tokens = 0
        mock_soul_response.usage.thinking_tokens = 0
        mock_soul_response.usage.cached_tokens = 0
        mock_soul_session.send.return_value = mock_soul_response
        # Token estimate on the soul session's interface (consumed by _trim_soul_session).
        # Default soul_context_limit is 200_000 — return well below it so trim is a no-op.
        mock_soul_session.interface.estimate_context_tokens.return_value = 100
        svc.create_session.return_value = mock_soul_session

        agent._soul_delay = 0.1
        agent._start_soul_timer()

        # Wait for the timer to fire and the pair to land on tc_inbox.
        deadline = time.monotonic() + 2.0
        while len(agent._tc_inbox) == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        # Cancel the perpetual timer so it doesn't keep firing during the test.
        agent._cancel_soul_timer()

        assert len(agent._tc_inbox) >= 1
        items = agent._tc_inbox.drain()
        item = items[-1]  # latest (coalesced) survives
        assert item.source == "soul.flow"
        assert item.coalesce is True
        assert item.call.name == "soul"
        assert item.call.args == {"action": "flow"}
        assert item.result.id == item.call.id
        assert item.result.content["voice"] == "Have you considered the energy implications?"

        # Inbox stays clean — soul flow no longer rides the text channel.
        assert agent.inbox.empty()

    def test_empty_whisper_does_not_enqueue(self, tmp_path):
        """No diary → soul_flow returns None → nothing on tc_inbox."""
        from lingtai_kernel import BaseAgent

        svc = make_mock_service()
        agent = BaseAgent(
            service=svc,
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        agent._soul_delay = 0.1
        agent._start_soul_timer()
        time.sleep(0.3)
        agent._cancel_soul_timer()
        assert len(agent._tc_inbox) == 0
        assert agent.inbox.empty()

    def test_soul_timer_not_started_during_shutdown(self, tmp_path):
        """_start_soul_timer is a no-op when _shutdown is set."""
        from lingtai_kernel import BaseAgent
        agent = BaseAgent(
            service=make_mock_service(),
            agent_name="test",
            working_dir=tmp_path / "test_agent",
        )
        agent._soul_delay = 1.0
        agent._shutdown.set()
        agent._start_soul_timer()
        assert agent._soul_timer is None
