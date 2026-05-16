"""Regression tests for dangling assistant tool-call tail recovery.

A persisted assistant tool-call response without a matching tool result leaves the
canonical chat wire unappendable for strict providers.  The turn engine should
close that tail before returning to IDLE or no-oping wake messages.
"""
from __future__ import annotations

import threading
from pathlib import Path

from lingtai_kernel.base_agent.turn import _handle_tc_wake, _process_response
from lingtai_kernel.llm.base import LLMResponse, ToolCall
from lingtai_kernel.llm.interface import ChatInterface, TextBlock, ToolCallBlock


class _FakeChat:
    def __init__(self) -> None:
        self.interface = ChatInterface()
        self.interface.add_system("system")
        self.interface.add_user_message("start")
        self.interface.add_assistant_message(
            [
                TextBlock("checking"),
                ToolCallBlock(id="call_1", name="email", args={"action": "check"}),
            ]
        )
        self.committed = []

    def commit_tool_results(self, results):
        self.committed.append(list(results))
        self.interface.add_tool_results(results)


class _EmptyTCInbox:
    def __init__(self) -> None:
        self.enqueued = []

    def drain(self):
        return []

    def enqueue(self, item):
        self.enqueued.append(item)


class _Config:
    provider = "test"
    max_turns = 10


class _Service:
    def make_tool_result(self, name, result, **kwargs):  # pragma: no cover
        raise AssertionError("no tool dispatch expected")


class _WakeSession:
    def __init__(self) -> None:
        self.sent = []
        self.chat = None

    def ensure_session(self):  # pragma: no cover
        raise AssertionError("chat already present")

    def send(self, message):
        self.sent.append(message)
        return LLMResponse(text="recovered")


class _FakeAgent:
    def __init__(self) -> None:
        self._chat = _FakeChat()
        self._session = _WakeSession()
        self._tc_inbox = _EmptyTCInbox()
        self._appendix_ids_by_source = {}
        self._config = _Config()
        self._intrinsics = set()
        self._tool_handlers = {}
        self._PARALLEL_SAFE_TOOLS = set()
        self._notification_live_holder = None
        self._cancel_event = threading.Event()
        self._on_tool_result_hook = None
        self._intermediate_text_streamed = True
        self._sent_tracker = object()
        self._working_dir = Path("/nonexistent/lingtai-test-pending-toolcall")
        self.service = _Service()
        self.agent_name = "test-agent"
        self.saved_sources = []
        self.logs = []
        self._last_usage = None
        self._dispatch_tool = lambda tc: {"status": "success"}
        self.heal_calls = []
        self.synced_notifications = 0

    def _heal_pending_tool_calls(self, *, reason: str) -> bool:
        self.heal_calls.append(reason)
        if not self._chat.interface.has_pending_tool_calls():
            return False
        self._chat.interface.close_pending_tool_calls(reason=f"heal:{reason}")
        self._log("heal_pending_tool_calls", reason=reason)
        self._save_chat_history(ledger_source="heal")
        return True

    def _save_chat_history(self, *, ledger_source: str | None = None) -> None:
        self.saved_sources.append(ledger_source)

    def _log(self, event: str, **kwargs) -> None:
        self.logs.append((event, kwargs))
        if getattr(self, "_set_cancel_on_log_event", None) == event:
            self._cancel_event.set()

    def _sync_notifications(self) -> None:
        self.synced_notifications += 1


class _Guard:
    def __init__(self, *, stop_reason=None, invalid_reason=None) -> None:
        self.stop_reason = stop_reason
        self.invalid_reason = invalid_reason

    def check_limit(self, count: int):
        return self.stop_reason

    def check_invalid_tool_limit(self):
        return self.invalid_reason

    def record_calls(self, count: int):  # pragma: no cover
        pass


class _Executor:
    def __init__(self, guard: _Guard) -> None:
        self.guard = guard
        self.calls = []

    def execute(self, tool_calls, **kwargs):
        self.calls.append(tool_calls)
        return [], False, ""


def _tool_response() -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[ToolCall(id="call_1", name="email", args={"action": "check"})],
    )


def test_tc_wake_heals_dangling_tool_call_and_drives_wire():
    agent = _FakeAgent()

    _handle_tc_wake(agent, object())

    assert agent.heal_calls == ["tc_wake_pending_tool_calls"]
    assert not agent._chat.interface.has_pending_tool_calls()
    assert agent._session.sent == [None]
    assert ("tc_wake_healed_pending_tool_calls", {}) in agent.logs
    assert ("tc_wake_continue", {}) in agent.logs


def test_process_response_cancel_before_dispatch_closes_pending_tail():
    agent = _FakeAgent()
    agent._executor = _Executor(_Guard())
    # _process_response clears stale cancellation at entry. Simulate a fresh
    # cancel that arrives after it has seen tool calls but before dispatch.
    agent._set_cancel_on_log_event = "tool_batch_received"

    result = _process_response(agent, _tool_response(), ledger_source="test")

    assert result == {"text": "", "failed": False, "errors": []}
    assert agent._executor.calls == []
    assert agent._session.sent == []
    assert not agent._chat.interface.has_pending_tool_calls()
    healed_entry = agent._chat.interface.entries[-1]
    assert healed_entry.role == "user"
    assert healed_entry.content[0].synthesized is True
    assert "cancel_before_tool_dispatch" in healed_entry.content[0].content


def test_process_response_guard_limit_closes_pending_tail_before_break():
    agent = _FakeAgent()
    agent._executor = _Executor(_Guard(stop_reason="too many tool calls"))

    result = _process_response(agent, _tool_response(), ledger_source="test")

    assert result == {"text": "", "failed": False, "errors": []}
    assert agent._executor.calls == []
    assert not agent._chat.interface.has_pending_tool_calls()
    assert "too many tool calls" in agent._chat.interface.entries[-1].content[0].content


def test_process_response_empty_results_after_cancel_closes_pending_tail():
    agent = _FakeAgent()
    agent._executor = _Executor(_Guard())

    def execute_and_cancel(tool_calls, **kwargs):
        agent._cancel_event.set()
        return [], False, ""

    agent._executor.execute = execute_and_cancel

    result = _process_response(agent, _tool_response(), ledger_source="test")

    assert result == {"text": "", "failed": False, "errors": []}
    assert not agent._chat.interface.has_pending_tool_calls()
    tail = agent._chat.interface.entries[-1]
    assert tail.role == "user"
    assert tail.content[0].synthesized is True
    assert "cancel_after_tool_dispatch_without_results" in tail.content[0].content
