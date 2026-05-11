"""Tests for Mimo (Xiaomi) adapter's reasoning-strip behavior on replay.

Mimo speaks OpenAI Chat Completions but loops when its own prior
``reasoning_content`` is echoed back: the model treats the round-tripped
thinking as authoritative and emits it verbatim again, accumulating
byte-identical thinking blocks in history until the 120s LLM hang
watchdog suspends the agent.

The MimoChatSession strips ``reasoning_content`` from outgoing assistant
turns. The canonical ChatInterface still retains the ThinkingBlock (so
logs, telemetry, and intra-process state are unaffected); only the wire
representation drops the field.

This is the inverse of the DeepSeek contract — DeepSeek *requires* the
round-trip; Mimo is broken by it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.llm.mimo.adapter import MimoAdapter, MimoChatSession
from lingtai_kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _make_raw_response(*, content=None, reasoning_content=None, tool_calls=None):
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


def _build_session(client, iface=None):
    if iface is None:
        iface = ChatInterface()
        iface.add_system("you are a helpful assistant")
    return MimoChatSession(
        client=client,
        model="mimo-v2.5-pro",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
    )


class TestReasoningStrippedOnReplay:
    """No assistant turn should carry reasoning_content on the wire."""

    def test_tool_call_turn_strips_reasoning_on_replay(self):
        client = MagicMock()
        tc = _make_tool_call("call_abc", "email")
        client.chat.completions.create.return_value = _make_raw_response(
            tool_calls=[tc],
            reasoning_content="Let me check the inbox first.",
        )
        session = _build_session(client)
        session.send("hi")

        client.chat.completions.create.return_value = _make_raw_response(
            content="done",
            reasoning_content="Inbox confirmed empty.",
        )
        session.send([ToolResultBlock(id="call_abc", name="email", content="sent")])

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        for m in messages:
            assert "reasoning_content" not in m

    def test_plain_text_turn_strips_reasoning_on_replay(self):
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message(
            [
                ThinkingBlock(text="thinking out loud"),
                TextBlock(text="hello there"),
            ],
            model="mimo-v2.5-pro",
            provider="mimo",
        )

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = _build_session(client, iface=iface)
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_turns = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_turns) == 1
        assert "reasoning_content" not in assistant_turns[0]
        # Content body must still be present.
        assert assistant_turns[0].get("content") == "hello there"

    def test_multiple_prior_thinking_blocks_all_stripped(self):
        """The looping pathology: many assistant turns each carrying a
        ThinkingBlock. None should leak onto the wire."""
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        for i in range(5):
            iface.add_assistant_message(
                [
                    ThinkingBlock(text=f"thought number {i}"),
                    TextBlock(text=f"reply {i}"),
                ],
                model="mimo-v2.5-pro",
                provider="mimo",
            )
            iface.add_user_message(f"follow-up {i}")

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = _build_session(client, iface=iface)
        session.send("final")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        for m in messages:
            assert "reasoning_content" not in m


class TestThinkingStillCapturedInInterface:
    """Stripping is wire-only — the canonical interface keeps the
    ThinkingBlock so in-process logging and telemetry continue to work."""

    def test_thinking_block_lands_in_interface(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(
            content="here's my answer",
            reasoning_content="this is my reasoning",
        )
        session = _build_session(client)
        response = session.send("hi")

        # Response carries the thinking text for the turn loop's _log("thinking").
        assert response.thoughts == ["this is my reasoning"]

        # Interface retains the ThinkingBlock for future replay (which the
        # session will then strip on the wire).
        assistant_entries = [
            e for e in session.interface.entries if e.role == "assistant"
        ]
        assert len(assistant_entries) == 1
        block_types = [type(b).__name__ for b in assistant_entries[0].content]
        assert "ThinkingBlock" in block_types


class TestAdapterRegistration:
    """The Mimo adapter must be wired into the LLMService registry."""

    def test_mimo_resolves_to_mimo_adapter(self):
        from lingtai.llm._register import register_all_adapters
        from lingtai.llm.service import LLMService

        register_all_adapters()
        factory = LLMService._adapter_registry.get("mimo")
        assert factory is not None
        adapter = factory(
            model="mimo-v2.5-pro",
            defaults=None,
            api_key="test",
            base_url="https://api.xiaomimimo.com",
        )
        assert isinstance(adapter, MimoAdapter)
        assert adapter._session_class is MimoChatSession
