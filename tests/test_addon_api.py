"""Tests for the public addon API on BaseAgent."""
from __future__ import annotations

import queue
from pathlib import Path
from unittest.mock import MagicMock

from lingtai_kernel.base_agent import BaseAgent
from lingtai_kernel.message import Message


def _make_agent(tmp_path: Path):
    """Create a minimal BaseAgent for testing addon API."""
    agent = BaseAgent.__new__(BaseAgent)
    agent._working_dir = tmp_path
    agent._nap_wake_reason = ""
    agent._nap_wake = MagicMock()
    agent._log_service = MagicMock()
    agent.agent_name = "test-agent"
    agent.inbox = queue.Queue()
    return agent


def test_working_dir_returns_path(tmp_path):
    agent = _make_agent(tmp_path)
    assert agent.working_dir == tmp_path
    assert isinstance(agent.working_dir, Path)


def test_wake_delegates_to_wake_nap(tmp_path):
    agent = _make_agent(tmp_path)
    agent.wake("mail_arrived")
    agent._nap_wake.set.assert_called_once()
    assert agent._nap_wake_reason == "mail_arrived"


def test_log_delegates_to_log_service(tmp_path):
    agent = _make_agent(tmp_path)
    agent.log("feishu_received", sender="alice", text="hello")
    agent._log_service.log.assert_called_once()
    logged = agent._log_service.log.call_args[0][0]
    assert logged["type"] == "feishu_received"
    assert logged["sender"] == "alice"


def test_log_no_service(tmp_path):
    agent = _make_agent(tmp_path)
    agent._log_service = None
    agent.log("event")  # should not raise


def test_notify_puts_message_in_inbox(tmp_path):
    agent = _make_agent(tmp_path)
    agent.notify("system", "New Feishu message from Alice")
    msg = agent.inbox.get_nowait()
    assert isinstance(msg, Message)
    assert "New Feishu message from Alice" in msg.content
