"""Tests for lingtai_kernel.tc_inbox — the involuntary tool-call inbox."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from lingtai_kernel.llm.interface import ToolCallBlock, ToolResultBlock
from lingtai_kernel.tc_inbox import InvoluntaryToolCall, TCInbox


def _make_item(source: str, voice: str = "v", coalesce: bool = False) -> InvoluntaryToolCall:
    tc_id = f"tc_{int(time.time())}_{source}"
    call = ToolCallBlock(id=tc_id, name="soul", args={"action": "flow"})
    result = ToolResultBlock(id=tc_id, name="soul", content={"voice": voice})
    return InvoluntaryToolCall(
        call=call, result=result,
        source=source, enqueued_at=time.time(),
        coalesce=coalesce,
    )


class TestTCInbox:

    def test_enqueue_appends(self):
        inbox = TCInbox()
        inbox.enqueue(_make_item("a"))
        inbox.enqueue(_make_item("b"))
        assert len(inbox) == 2

    def test_enqueue_coalesce_replaces_existing_with_same_source(self):
        inbox = TCInbox()
        inbox.enqueue(_make_item("soul.flow", voice="first", coalesce=True))
        inbox.enqueue(_make_item("soul.flow", voice="second", coalesce=True))
        assert len(inbox) == 1
        items = inbox.drain()
        assert items[0].result.content["voice"] == "second"

    def test_enqueue_no_coalesce_appends_even_with_same_source(self):
        inbox = TCInbox()
        inbox.enqueue(_make_item("x", voice="a", coalesce=False))
        inbox.enqueue(_make_item("x", voice="b", coalesce=False))
        assert len(inbox) == 2

    def test_enqueue_coalesce_only_replaces_same_source(self):
        inbox = TCInbox()
        inbox.enqueue(_make_item("a", coalesce=True))
        inbox.enqueue(_make_item("b", coalesce=True))
        inbox.enqueue(_make_item("a", voice="new", coalesce=True))
        assert len(inbox) == 2
        items = inbox.drain()
        # FIFO — 'a' was first; coalesced 'a' replaces in place; 'b' second.
        assert items[0].source == "a"
        assert items[0].result.content["voice"] == "new"
        assert items[1].source == "b"

    def test_drain_returns_fifo_and_clears(self):
        inbox = TCInbox()
        inbox.enqueue(_make_item("a"))
        inbox.enqueue(_make_item("b"))
        inbox.enqueue(_make_item("c"))
        items = inbox.drain()
        assert [i.source for i in items] == ["a", "b", "c"]
        assert len(inbox) == 0

    def test_drain_empty_returns_empty_list(self):
        inbox = TCInbox()
        assert inbox.drain() == []

    def test_concurrent_enqueue_thread_safe(self):
        inbox = TCInbox()
        N = 200

        def producer(i: int):
            inbox.enqueue(_make_item(f"src_{i}"))

        threads = [threading.Thread(target=producer, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(inbox) == N
        items = inbox.drain()
        assert len(items) == N
        # All sources unique — no losses
        assert len({i.source for i in items}) == N


class TestDrainTCInbox:
    """Tests for BaseAgent._drain_tc_inbox — the wire-chat splice site."""

    def _make_agent(self, tmp_path):
        from lingtai_kernel import BaseAgent
        svc = MagicMock()
        svc.model = "test-model"
        agent = BaseAgent(
            service=svc,
            agent_name="test",
            working_dir=tmp_path / "agent",
        )
        return agent

    def test_drain_skips_when_chat_none(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent._chat = None
        agent._tc_inbox.enqueue(_make_item("soul.flow"))
        # Should not raise, should not consume the queue.
        agent._drain_tc_inbox()
        assert len(agent._tc_inbox) == 1

    def test_drain_splices_pair_into_wire_chat(self, tmp_path):
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock
        agent = self._make_agent(tmp_path)
        iface = ChatInterface()
        iface.add_user_message("hi")
        iface.add_assistant_message([TextBlock(text="hello")])
        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat

        agent._tc_inbox.enqueue(_make_item("soul.flow", voice="my voice"))
        agent._drain_tc_inbox()

        # The last two entries should be the synthetic pair.
        entries = iface.entries
        assert entries[-2].role == "assistant"
        assert entries[-1].role == "user"
        call_block = entries[-2].content[0]
        result_block = entries[-1].content[0]
        assert call_block.name == "soul"
        assert call_block.args == {"action": "flow"}
        assert result_block.id == call_block.id
        assert result_block.content["voice"] == "my voice"
        # Queue is empty after drain.
        assert len(agent._tc_inbox) == 0

    def test_drain_skips_when_pending_tool_calls(self, tmp_path):
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock
        agent = self._make_agent(tmp_path)
        iface = ChatInterface()
        iface.add_user_message("do thing")
        # Assistant turn with an unanswered tool_call — chat is mid-flight.
        iface.add_assistant_message([
            TextBlock(text="let me do it"),
            ToolCallBlock(id="tc_pending", name="some_tool", args={}),
        ])
        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat

        agent._tc_inbox.enqueue(_make_item("soul.flow"))
        agent._drain_tc_inbox()
        # Queue preserved — splice deferred to next safe boundary.
        assert len(agent._tc_inbox) == 1

    def test_drain_noop_when_queue_empty(self, tmp_path):
        from lingtai_kernel.llm.interface import ChatInterface, TextBlock
        agent = self._make_agent(tmp_path)
        iface = ChatInterface()
        iface.add_user_message("hi")
        iface.add_assistant_message([TextBlock(text="hello")])
        mock_chat = MagicMock()
        mock_chat.interface = iface
        agent._chat = mock_chat

        before_count = len(iface.entries)
        agent._drain_tc_inbox()
        # No change to chat state.
        assert len(iface.entries) == before_count
