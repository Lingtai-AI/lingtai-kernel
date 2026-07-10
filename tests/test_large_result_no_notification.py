"""Large tool results no longer create ``large_tool_result`` system notifications.

Per the agent-meta-large-result-ranking change, large tool results are ranked
through ``_meta.agent_meta.current_tool_result_chars.top_results`` and digested
via ``system(action="summarize")``.  The kernel no longer publishes or injects
``source="large_tool_result"`` system notifications — neither at tool-execution
time (``_maybe_notify_large_tool_result``) nor at the turn boundary
(``_rescan_large_tool_results``).  These tests pin that contract:

1. The per-result hook produces no large_tool_result event, however large.
2. The turn-boundary rescan never publishes and always returns 0.
3. The same large result is still reported by current_tool_result_chars.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

from lingtai.kernel.llm.interface import (
    ChatInterface,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.kernel.base_agent.messaging import _rescan_large_tool_results
from lingtai.kernel import meta_block


def _make_stub_agent(iface: ChatInterface):
    """Minimal stub agent that records any system notification publish attempt."""

    class _StubChat:
        interface = iface

    agent = MagicMock()
    agent._chat = _StubChat()
    agent._chat.interface = iface
    agent._log = MagicMock()
    agent._summarize_notification_threshold = 5000
    agent._system_notification_lock = threading.Lock()

    published: list[dict] = []

    def _fake_enqueue(*, source, ref_id, body, skip_if_ref_id_exists=False, **kw):
        published.append({"source": source, "ref_id": ref_id})
        return f"evt_{len(published):03d}"

    agent._enqueue_system_notification = _fake_enqueue
    agent._published = published
    return agent


def _add_tool_pair(iface: ChatInterface, call_id: str, tool_name: str, content):
    iface.add_assistant_message([ToolCallBlock(id=call_id, name=tool_name, args={})])
    iface.add_tool_results([ToolResultBlock(id=call_id, name=tool_name, content=content)])


def test_rescan_never_publishes_for_huge_result():
    """A single result well over any old gate must NOT publish a notification."""
    iface = ChatInterface()
    # 80k chars — far above the old 50000-char total-length gate.
    _add_tool_pair(iface, "tc-huge", "bash", {"output": "X" * 80000, "status": "ok"})
    agent = _make_stub_agent(iface)

    count = _rescan_large_tool_results(agent)

    assert count == 0
    assert agent._published == []


def test_rescan_never_publishes_for_many_large_results():
    """Many large results together must still publish nothing."""
    iface = ChatInterface()
    for i in range(8):
        _add_tool_pair(iface, f"tc-{i}", "bash", {"output": "Y" * 9000, "status": "ok"})
    agent = _make_stub_agent(iface)

    count = _rescan_large_tool_results(agent)

    assert count == 0
    assert agent._published == []


def test_maybe_notify_large_tool_result_publishes_nothing():
    """The per-result hook produces no large_tool_result event."""
    from lingtai.kernel.base_agent import BaseAgent

    iface = ChatInterface()
    _add_tool_pair(iface, "tc-big", "bash", {"output": "Z" * 80000, "status": "ok"})
    agent = _make_stub_agent(iface)

    # Call the real method on the stub (unbound, via the class).
    BaseAgent._maybe_notify_large_tool_result(
        agent, "bash", {"output": "Z" * 80000, "status": "ok"}, tool_call_id="tc-big"
    )

    assert agent._published == []


def test_large_result_still_reported_by_current_tool_result_chars():
    """The large result the kernel stopped notifying about is still ranked."""
    iface = ChatInterface()
    _add_tool_pair(iface, "tc-rank", "bash", {"output": "Q" * 40000, "status": "ok"})

    agent = MagicMock()
    agent._session.chat.interface = iface

    summary = meta_block.current_tool_result_chars(agent)

    assert summary["total_chars"] >= 40000
    ids = [r["id"] for r in summary["top_results"]]
    assert "tc-rank" in ids


def test_current_tool_result_chars_reports_threshold_and_over_count():
    """The ranking carries the large-result threshold and how many exceed it.

    This replaces the per-notification threshold messaging: the agent can see
    what counts as "large" and how many results currently qualify, directly in
    the ranked block, so it can decide what to summarize.
    """
    iface = ChatInterface()
    # Two results over the 1000-char top-list floor, one of them over threshold.
    _add_tool_pair(iface, "tc-over", "bash", {"output": "A" * 6000, "status": "ok"})
    _add_tool_pair(iface, "tc-under", "bash", {"output": "B" * 2000, "status": "ok"})

    agent = MagicMock()
    agent._session.chat.interface = iface
    agent._summarize_notification_threshold = 5000

    summary = meta_block.current_tool_result_chars(agent)

    assert summary["threshold"] == 5000
    # Only tc-over (6000 chars) exceeds the 5000-char threshold.
    assert summary["over_threshold_count"] == 1


def test_current_tool_result_chars_threshold_defaults_when_unset():
    """With no configured threshold, the default (3000) is reported."""
    iface = ChatInterface()
    _add_tool_pair(iface, "tc-x", "bash", {"output": "C" * 100, "status": "ok"})

    agent = MagicMock(spec=["_session"])
    agent._session.chat.interface = iface

    summary = meta_block.current_tool_result_chars(agent)

    assert summary["threshold"] == 3000
    assert summary["over_threshold_count"] == 0
