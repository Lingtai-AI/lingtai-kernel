"""Tests for DeepSeek adapter's reasoning_content placeholder behavior.

DeepSeek V4 thinking mode's actual contract (determined empirically —
the docs understate it):

    Once any assistant turn in the conversation has tool_calls, ALL
    subsequent assistant turns (tool-call AND plain-text) must carry
    reasoning_content on replay. Assistant turns BEFORE the first
    tool_call don't need it.

The server only validates field presence, not content, so the adapter
injects a stable placeholder rather than preserving actual reasoning
across session boundaries.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.llm.deepseek.adapter import DeepSeekAdapter, DeepSeekChatSession
from lingtai_kernel.llm.interface import ChatInterface, ToolResultBlock


def _make_raw_response(*, content=None, reasoning_content=None, tool_calls=None):
    """Build a minimal fake OpenAI ChatCompletion-like object."""
    msg = SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls or [],
    )
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=10),
        ),
    )


def _make_tool_call(id_, name, args_json="{}"):
    return SimpleNamespace(
        id=id_,
        function=SimpleNamespace(name=name, arguments=args_json),
    )


def _build_session(client):
    """Build a DeepSeekChatSession around a mock openai client."""
    iface = ChatInterface()
    iface.add_system("you are a helpful assistant")
    return DeepSeekChatSession(
        client=client,
        model="deepseek-v4-pro",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
    )


class TestReasoningPlaceholder:
    def test_placeholder_set_on_tool_call_turn(self):
        """Assistant turns with tool_calls must carry reasoning_content on replay."""
        client = MagicMock()
        tc = _make_tool_call("call_abc", "email")
        client.chat.completions.create.return_value = _make_raw_response(
            tool_calls=[tc],
        )
        session = _build_session(client)
        session.send("hi")

        # Turn 2: send tool result — the prior assistant turn must now carry
        # reasoning_content on the wire.
        client.chat.completions.create.return_value = _make_raw_response(content="done")
        session.send([ToolResultBlock(id="call_abc", name="email", content="sent")])

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turns = [
            m for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert len(assistant_tool_turns) == 1
        assert "reasoning_content" in assistant_tool_turns[0]
        # Non-empty human-readable placeholder — stripped from response.thoughts
        # on receive by _strip_placeholder_echoes (see dedicated tests below).
        assert assistant_tool_turns[0]["reasoning_content"]

    def test_plain_text_turn_before_any_tool_call_has_no_reasoning(self):
        """Plain-text assistant turns that precede the first tool_call must
        NOT carry reasoning_content — DeepSeek rejects it otherwise."""
        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(
            content="hello there",
        )
        session = _build_session(client)
        session.send("hi")

        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session.send("thanks")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        for m in messages:
            assert "reasoning_content" not in m

    def test_plain_text_turn_AFTER_tool_call_gets_reasoning(self):
        """Plain-text assistant turns that follow any tool_call turn MUST
        carry reasoning_content. The real contract: once thinking mode is
        invoked in the conversation, all subsequent assistant turns need
        reasoning_content on replay — not just the ones with tool_calls.
        This is the case the real-world 400 cascade exposed."""
        from lingtai_kernel.llm.interface import ToolCallBlock, TextBlock

        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        # Prior assistant turn that DID have tool_calls
        iface.add_assistant_message(
            [
                TextBlock(text="checking"),
                ToolCallBlock(id="call_1", name="email", args={"action": "check"}),
            ],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="call_1", name="email", content="no mail"),
        ])
        # Subsequent PLAIN-TEXT assistant turn — no tool_calls of its own.
        iface.add_assistant_message(
            [TextBlock(text="no new mail for you")],
            model="deepseek-v4-pro",
            provider="deepseek",
        )

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = DeepSeekChatSession(
            client=client, model="deepseek-v4-pro", interface=iface,
            tools=None, tool_choice=None, extra_kwargs={}, client_kwargs={},
        )
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        # Two assistant turns in history: the tool-call one AND the plain-text one
        assistant_turns = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_turns) == 2
        # BOTH must carry a non-empty reasoning_content on replay.
        for m in assistant_turns:
            assert "reasoning_content" in m, (
                f"assistant turn missing reasoning_content: {m}"
            )
            assert m["reasoning_content"]

    def test_rehydrated_history_with_trailing_plain_text_still_valid(self):
        """After a session restart, restored history with tool_calls but no
        in-memory reasoning must still carry reasoning_content on replay —
        this is the scenario the real-world 400 cascade came from."""
        from lingtai_kernel.llm.interface import ToolCallBlock, TextBlock

        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        # Simulate a restored assistant turn with tool_calls (no thinking block —
        # matching what chat_history.jsonl actually contains for pre-fix sessions).
        iface.add_assistant_message(
            [
                TextBlock(text="let me check"),
                ToolCallBlock(id="restored_call", name="email", args={"action": "check"}),
            ],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="restored_call", name="email", content="no mail"),
        ])

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = DeepSeekChatSession(
            client=client,
            model="deepseek-v4-pro",
            interface=iface,
            tools=None,
            tool_choice=None,
            extra_kwargs={},
            client_kwargs={},
        )
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turns = [
            m for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert len(assistant_tool_turns) == 1
        assert "reasoning_content" in assistant_tool_turns[0]
        assert assistant_tool_turns[0]["reasoning_content"]


class TestDeepSeekAdapterWiring:
    def test_session_class_override(self):
        assert DeepSeekAdapter._session_class is DeepSeekChatSession

    def test_default_base_url(self):
        adapter = DeepSeekAdapter(api_key="stub")
        assert adapter.base_url == "https://api.deepseek.com"

    def test_base_url_override(self):
        adapter = DeepSeekAdapter(api_key="stub", base_url="https://alt.example/v1")
        assert adapter.base_url == "https://alt.example/v1"


class TestStripPlaceholderEchoes:
    """DeepSeek V4 prepends our placeholder to its own fresh reasoning when
    the placeholder appears on prior assistant turns in context. The filter
    chops the placeholder prefix off so the kernel's 'thinking' log shows
    just the real reasoning tail."""

    def _make_response(self, thoughts):
        return SimpleNamespace(thoughts=list(thoughts), text="reply", tool_calls=[])

    def test_pure_echo_dropped(self):
        """thought == placeholder only → drop entirely (no real reasoning)."""
        from lingtai.llm.deepseek.adapter import (
            _strip_placeholder_echoes, _REASONING_PLACEHOLDER,
        )
        resp = self._make_response([_REASONING_PLACEHOLDER])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == []

    def test_prefix_plus_real_reasoning_stripped_to_tail(self):
        """thought starts with placeholder, has real reasoning after → keep the tail."""
        from lingtai.llm.deepseek.adapter import (
            _strip_placeholder_echoes, _REASONING_PLACEHOLDER,
        )
        resp = self._make_response([
            _REASONING_PLACEHOLDER + "发现 args 检查失败，让我换个方式。",
        ])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == ["发现 args 检查失败，让我换个方式。"]

    def test_prefix_with_leading_whitespace_stripped(self):
        """Placeholder followed by whitespace then real reasoning."""
        from lingtai.llm.deepseek.adapter import (
            _strip_placeholder_echoes, _REASONING_PLACEHOLDER,
        )
        resp = self._make_response([
            _REASONING_PLACEHOLDER + "\n\nThe user is asking...",
        ])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == ["The user is asking..."]

    def test_real_thought_without_prefix_preserved(self):
        from lingtai.llm.deepseek.adapter import _strip_placeholder_echoes
        resp = self._make_response(["The user is asking about X. I should Y."])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == ["The user is asking about X. I should Y."]

    def test_mid_string_placeholder_preserved(self):
        """Placeholder as a substring (not at start) is NOT stripped — only
        prefix matches are treated as echoes."""
        from lingtai.llm.deepseek.adapter import (
            _strip_placeholder_echoes, _REASONING_PLACEHOLDER,
        )
        text = f"I noticed the marker: {_REASONING_PLACEHOLDER} — continuing."
        resp = self._make_response([text])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == [text]

    def test_mixed_list(self):
        from lingtai.llm.deepseek.adapter import (
            _strip_placeholder_echoes, _REASONING_PLACEHOLDER,
        )
        resp = self._make_response([
            _REASONING_PLACEHOLDER,  # pure echo → drop
            _REASONING_PLACEHOLDER + "real part",  # prefix echo → strip
            "Real reasoning content here.",  # untouched
        ])
        _strip_placeholder_echoes(resp)
        assert resp.thoughts == ["real part", "Real reasoning content here."]

    def test_no_thoughts_attribute_noop(self):
        from lingtai.llm.deepseek.adapter import _strip_placeholder_echoes
        resp = SimpleNamespace(text="reply")  # no thoughts attr
        # Should not raise.
        _strip_placeholder_echoes(resp)
