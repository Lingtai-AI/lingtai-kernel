"""Integration test: lingtai-agent run boots an agent, tests .sleep (asleep) and .suspend (shutdown)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from lingtai.cli import load_init, build_agent
from lingtai.kernel.llm.base import LLMResponse
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.kernel.state import AgentState


def _write_init(tmp_path: Path) -> None:
    """Write a minimal init.json into tmp_path."""
    data = {
        "manifest": {
            "agent_name": "integration-test",
            "language": "en",
            "llm": {
                "provider": "gemini",
                "model": "test-model",
                "api_key": "fake-key",
                "base_url": None,
            },
            "capabilities": {},
            "soul": {"delay": 5},
            "stamina": 10,
            "context_limit": None,
            "molt_pressure": 0.8,
            "molt_prompt": "",
            "max_turns": 5,
            "admin": {},
            "streaming": False,
        },
        "principle": "",
        "covenant": "You are a test agent.",
        "pad": "",
        "lingtai": "",
    }
    (tmp_path / "init.json").write_text(json.dumps(data))


def _make_mock_service():
    """Build a deterministic mock LLMService that satisfies BaseAgent's contract."""
    svc = MagicMock()
    svc.provider = "gemini"
    svc.model = "test-model"
    svc._base_url = None
    svc._provider_defaults = {}
    chat = MagicMock()
    chat.interface = ChatInterface()
    chat.send.return_value = LLMResponse(text="ready")
    chat.get_state.return_value = {}
    chat.static_adapter_comment.return_value = None
    chat.dynamic_adapter_comment.return_value = None
    svc.create_session.return_value = chat
    return svc


@patch("lingtai.agent.LLMService")
@patch("lingtai.cli.LLMService")
def test_sleep_signal_triggers_asleep(mock_llm_cls, mock_agent_llm_cls, tmp_path):
    """Boot agent, touch .sleep, verify ASLEEP (sleep, not shutdown)."""
    _write_init(tmp_path)
    svc = _make_mock_service()
    mock_llm_cls.return_value = svc
    mock_agent_llm_cls.return_value = svc

    data = load_init(tmp_path)
    with patch("lingtai.agent.LLMService", return_value=svc):
        agent = build_agent(data, tmp_path)

    agent.start()

    assert agent.state == AgentState.IDLE
    assert (tmp_path / ".agent.json").is_file()

    # Touch .sleep → ASLEEP (sleep, process stays alive)
    sleep_file = tmp_path / ".sleep"
    event_log = tmp_path / "logs" / "events.jsonl"
    sleep_file.touch()
    deadline = time.time() + 3
    saw_sleep_received = False
    while time.time() < deadline:
        if event_log.exists():
            try:
                saw_sleep_received = any(
                    json.loads(line).get("type") == "sleep_received"
                    for line in event_log.read_text().splitlines()
                    if line.strip()
                )
            except json.JSONDecodeError:
                pass  # Concurrent append may expose a partial final line briefly.
        if saw_sleep_received:
            break
        time.sleep(0.05)

    assert saw_sleep_received, "sleep_received transition should be logged"
    assert not sleep_file.exists(), "signal file should be consumed"
    assert not agent._shutdown.is_set(), ".sleep should NOT set _shutdown"
    # Queued work can legitimately wake ASLEEP through ACTIVE and then IDLE
    # after the recorded transition; none of those states is AED/STUCK.
    assert agent.state in {AgentState.ASLEEP, AgentState.ACTIVE, AgentState.IDLE}

    agent._shutdown.set()  # clean up for test teardown
    agent.stop()


@patch("lingtai.agent.LLMService")
@patch("lingtai.cli.LLMService")
def test_suspend_triggers_shutdown(mock_llm_cls, mock_agent_llm_cls, tmp_path):
    """Boot agent, touch .suspend, verify SUSPENDED (full shutdown)."""
    _write_init(tmp_path)
    svc = _make_mock_service()
    mock_llm_cls.return_value = svc
    mock_agent_llm_cls.return_value = svc

    data = load_init(tmp_path)
    with patch("lingtai.agent.LLMService", return_value=svc):
        agent = build_agent(data, tmp_path)

    agent.start()

    assert agent.state == AgentState.IDLE

    # Touch .suspend → SUSPENDED (process death)
    (tmp_path / ".suspend").touch()
    time.sleep(3)

    assert agent._shutdown.is_set()
    assert agent.state == AgentState.SUSPENDED
    assert not (tmp_path / ".suspend").exists(), "signal file should be deleted"

    agent.stop()


@patch("lingtai.cli.LLMService")
def test_load_init_and_build_agent(mock_llm_cls, tmp_path):
    """load_init + build_agent produce a valid Agent without crashing."""
    _write_init(tmp_path)
    svc = _make_mock_service()
    mock_llm_cls.return_value = svc

    data = load_init(tmp_path)
    with patch("lingtai.agent.LLMService", return_value=svc):
        agent = build_agent(data, tmp_path)

    assert agent.agent_name == "integration-test"
    # ``manifest.max_turns`` is a legacy field and no longer controls the
    # kernel-owned ACTIVE-turn tool-call emergency fuse.
    assert agent._config.max_turns == 50
    assert agent._config.language == "en"
