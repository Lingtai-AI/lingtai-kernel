"""Tests for ChatInterface tool-pairing invariant.

DeepSeek V4 and strict OpenAI reject chat-completions requests where an
assistant message with tool_calls is not immediately followed by matching
tool messages. These tests verify the canonical ChatInterface enforces
that invariant at construction time.
"""
from __future__ import annotations

import pytest

from lingtai_kernel.llm.interface import (
    ChatInterface,
    PendingToolCallsError,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _iface_with_pending_tool_calls() -> ChatInterface:
    """Build an interface whose tail is assistant[tool_calls] with no results."""
    iface = ChatInterface()
    iface.add_system("system prompt")
    iface.add_user_message("hi")
    iface.add_assistant_message(
        [
            TextBlock(text="checking"),
            ToolCallBlock(id="call_A", name="noop", args={}),
        ],
    )
    return iface


class TestHasPendingToolCalls:
    def test_false_on_empty_interface(self):
        iface = ChatInterface()
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_system(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_user(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("hi")
        assert iface.has_pending_tool_calls() is False

    def test_false_when_tail_is_plain_assistant(self):
        iface = ChatInterface()
        iface.add_system("prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message([TextBlock(text="hello")])
        assert iface.has_pending_tool_calls() is False

    def test_true_when_tail_is_assistant_with_tool_calls(self):
        iface = _iface_with_pending_tool_calls()
        assert iface.has_pending_tool_calls() is True

    def test_false_after_tool_results_appended(self):
        iface = _iface_with_pending_tool_calls()
        iface.add_tool_results([ToolResultBlock(id="call_A", name="noop", content="done")])
        assert iface.has_pending_tool_calls() is False
