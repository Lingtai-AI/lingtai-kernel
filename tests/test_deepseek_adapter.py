"""Generic OpenAI reasoning_content round-trip fallback (collapsed from DeepSeek).

DeepSeek V4 thinking mode's actual contract (determined empirically —
the docs understate it):

    Once any assistant turn in the conversation has tool_calls, ALL
    subsequent assistant turns (tool-call AND plain-text) must carry
    reasoning_content on replay. Assistant turns BEFORE the first
    tool_call don't need it.

Real reasoning is now preserved end-to-end: the OpenAI adapter captures
``reasoning_content`` into a ThinkingBlock; ``to_openai`` emits the
ThinkingBlock back as ``reasoning_content`` on replay. The generic
fallback (``OpenAIAdapter(inject_reasoning_fallback=True)``) only injects a
per-turn-unique stub when an assistant turn has no captured ThinkingBlock
(e.g. rehydrated pre-fix history). See lingtai-kernel issue #9 for the
cache-collapse failure mode that drove this design.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai.llm.openai.adapter import (
    OpenAIAdapter,
    OpenAIChatSession,
    OpenAIResponsesSession,
    _fallback_reasoning_for,
    _inject_responses_reasoning_fallback,
)
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from tests._chat_completion_helpers import (
    make_raw_response as _make_raw_response,
    make_tool_call as _make_tool_call,
)


_DEEPSEEK_DEFAULTS = dict(
    inject_reasoning_fallback=True,
    reasoning_effort_vocab="seven_tier",
    prompt_cache_namespace="deepseek",
)


def _build_session(client, iface=None):
    """Build an OpenAIChatSession with the generic fallback enabled."""
    if iface is None:
        iface = ChatInterface()
        iface.add_system("you are a helpful assistant")
    return OpenAIChatSession(
        client=client,
        model="deepseek-v4-pro",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
        inject_reasoning_fallback=True,
    )


class TestRealReasoningPreserved:
    """When the provider returns reasoning_content, it lands in a ThinkingBlock
    and replays as real reasoning_content on the next request — no placeholder."""

    def test_real_reasoning_round_trips_on_tool_call_turn(self):
        client = MagicMock()
        tc = _make_tool_call("call_abc", "email")
        client.chat.completions.create.return_value = _make_raw_response(
            tool_calls=[tc],
            reasoning_content="Let me check the inbox first.",
        )
        session = _build_session(client)
        session.send("hi")

        # Turn 2: send tool result — the prior assistant turn must replay
        # with the REAL reasoning, not a placeholder.
        client.chat.completions.create.return_value = _make_raw_response(
            content="done",
            reasoning_content="Inbox confirmed empty.",
        )
        session.send([ToolResultBlock(id="call_abc", name="email", content="sent")])

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turns = [
            m for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert len(assistant_tool_turns) == 1
        assert assistant_tool_turns[0]["reasoning_content"] == "Let me check the inbox first."

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

    def test_plain_text_turn_after_tool_call_replays_real_reasoning(self):
        """The trailing plain-text reply that closes a tool loop must also
        carry reasoning_content on replay (the contract extends past
        tool-call turns once thinking has been invoked). Real reasoning
        is preserved verbatim."""
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message(
            [
                ThinkingBlock(text="user is asking about inbox"),
                TextBlock(text="checking"),
                ToolCallBlock(id="call_1", name="email", args={"action": "check"}),
            ],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="call_1", name="email", content="no mail"),
        ])
        iface.add_assistant_message(
            [
                ThinkingBlock(text="empty inbox, plain reply"),
                TextBlock(text="no new mail for you"),
            ],
            model="deepseek-v4-pro",
            provider="deepseek",
        )

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = _build_session(client, iface=iface)
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_turns = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_turns) == 2
        assert assistant_turns[0]["reasoning_content"] == "user is asking about inbox"
        assert assistant_turns[1]["reasoning_content"] == "empty inbox, plain reply"


class TestFallbackForRehydratedHistory:
    """Pre-fix chat_history.jsonl entries have no ThinkingBlock. The generic
    fallback must still satisfy DeepSeek's field-presence requirement on replay,
    using a per-turn-unique stub (NOT a constant placeholder — that
    triggered the cache-collapse failure)."""

    def test_rehydrated_tool_call_turn_gets_fallback(self):
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        # Rehydrated turn: TextBlock + ToolCallBlock, no ThinkingBlock.
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
        session = _build_session(client, iface=iface)
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turns = [
            m for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert len(assistant_tool_turns) == 1
        assert assistant_tool_turns[0]["reasoning_content"]
        # Fallback must inline call ids — keeps the stub byte-different per turn.
        assert "restored_call" in assistant_tool_turns[0]["reasoning_content"]

    def test_fallback_disabled_does_not_inject(self):
        """Without inject_reasoning_fallback the generic session is a plain
        OpenAI session: rehydrated turns carry no reasoning_content."""
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message(
            [TextBlock(text="let me check"),
             ToolCallBlock(id="restored_call", name="email", args={"action": "check"})],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="restored_call", name="email", content="no mail"),
        ])

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = OpenAIChatSession(
            client=client,
            model="deepseek-v4-pro",
            interface=iface,
            tools=None,
            tool_choice=None,
            extra_kwargs={},
            client_kwargs={},
            inject_reasoning_fallback=False,
        )
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turns = [
            m for m in messages
            if m.get("role") == "assistant" and m.get("tool_calls")
        ]
        assert len(assistant_tool_turns) == 1
        assert "reasoning_content" not in assistant_tool_turns[0]

    def test_fallbacks_are_unique_across_turns(self):
        """Two consecutive rehydrated tool-call turns must produce
        byte-different reasoning_content (the cache-collapse trigger
        was a constant placeholder repeated across turns)."""
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message(
            [ToolCallBlock(id="call_1", name="email", args={"action": "check"})],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="call_1", name="email", content="no mail"),
        ])
        iface.add_assistant_message(
            [ToolCallBlock(id="call_2", name="search", args={"q": "x"})],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="call_2", name="search", content="no results"),
        ])

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = _build_session(client, iface=iface)
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        rc_values = [
            m["reasoning_content"]
            for m in messages
            if m.get("role") == "assistant" and m.get("reasoning_content")
        ]
        assert len(rc_values) == 2
        assert rc_values[0] != rc_values[1], (
            "Fallback reasoning must be byte-different per turn to defeat "
            "DeepSeek's cache fast-path (issue #9)."
        )

    def test_real_reasoning_preferred_over_fallback(self):
        """When a ThinkingBlock IS present, the fallback must not overwrite
        it with the stub."""
        iface = ChatInterface()
        iface.add_system("system prompt")
        iface.add_user_message("hi")
        iface.add_assistant_message(
            [
                ThinkingBlock(text="real reasoning here"),
                ToolCallBlock(id="call_1", name="email", args={"action": "check"}),
            ],
            model="deepseek-v4-pro",
            provider="deepseek",
        )
        iface.add_tool_results([
            ToolResultBlock(id="call_1", name="email", content="no mail"),
        ])

        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="ok")
        session = _build_session(client, iface=iface)
        session.send("anything else?")

        messages = client.chat.completions.create.call_args.kwargs["messages"]
        assistant_tool_turn = next(
            m for m in messages if m.get("role") == "assistant" and m.get("tool_calls")
        )
        assert assistant_tool_turn["reasoning_content"] == "real reasoning here"


class TestThinkingBlockCaptured:
    """The OpenAI adapter must persist captured reasoning_content as a
    ThinkingBlock on the assistant interface entry — that's the upstream
    half of the round-trip."""

    def test_reasoning_content_lands_in_thinking_block(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(
            content="hi",
            reasoning_content="I should greet the user.",
        )
        session = _build_session(client)
        session.send("hello")

        last_entry = session._interface.entries[-1]
        assert last_entry.role == "assistant"
        thinking_blocks = [b for b in last_entry.content if isinstance(b, ThinkingBlock)]
        assert len(thinking_blocks) == 1
        assert thinking_blocks[0].text == "I should greet the user."

    def test_no_reasoning_no_thinking_block(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _make_raw_response(content="hi")
        session = _build_session(client)
        session.send("hello")

        last_entry = session._interface.entries[-1]
        thinking_blocks = [b for b in last_entry.content if isinstance(b, ThinkingBlock)]
        assert thinking_blocks == []


def test_fallback_reasoning_for_round_trips_tool_identity():
    """Direct unit check of the stub builder: tool names + call ids inline,
    plain-text turns get a content snippet, and the turn index is included."""
    tool_msg = {
        "role": "assistant",
        "content": "let me check",
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "email", "arguments": "{}"}}
        ],
    }
    text_msg = {"role": "assistant", "content": "no new mail for you"}
    assert "email" in _fallback_reasoning_for(tool_msg, 1)
    assert "call_1" in _fallback_reasoning_for(tool_msg, 1)
    assert "(turn 1)" in _fallback_reasoning_for(tool_msg, 1)
    assert "no new mail for you" in _fallback_reasoning_for(text_msg, 2)
    assert "(turn 2)" in _fallback_reasoning_for(text_msg, 2)


class TestOpenAIAdapterWiring:
    def test_session_class_override(self):
        assert OpenAIAdapter._session_class is OpenAIChatSession

    def test_default_base_url(self):
        adapter = OpenAIAdapter(api_key="stub", base_url="https://api.deepseek.com", **_DEEPSEEK_DEFAULTS)
        assert adapter.base_url == "https://api.deepseek.com"

    def test_base_url_override(self):
        adapter = OpenAIAdapter(
            api_key="stub", base_url="https://alt.example/v1", **_DEEPSEEK_DEFAULTS
        )
        assert adapter.base_url == "https://alt.example/v1"


def test_deepseek_inherits_shared_tool_pairing_rejection_after_reasoning_hook():
    """The fallback hook runs, then the shared validator blocks dispatch."""
    class _MalformedSession(OpenAIChatSession):
        def _build_messages(self):
            messages = super()._build_messages()
            messages.append({
                "role": "tool",
                "tool_call_id": "unknown-after-hook",
                "content": "provider-secret",
            })
            return messages

    iface = ChatInterface()
    iface.add_assistant_message([
        ToolCallBlock(id="known-call", name="lookup", args={}),
    ])
    iface.add_tool_results([
        ToolResultBlock(id="known-call", name="lookup", content="known-result"),
    ])
    client = MagicMock()
    session = _MalformedSession(
        client=client,
        model="deepseek-v4-pro",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
        inject_reasoning_fallback=True,
    )

    with pytest.raises(ValueError) as exc_info:
        session.send(None)

    assert "tool pairing" in str(exc_info.value)
    assert "provider-secret" not in str(exc_info.value)
    assert client.chat.completions.create.call_count == 0


def test_deepseek_responses_wire_selection():
    """OpenAIAdapter can opt into the Responses wire via wire_api/use_responses."""
    chat = OpenAIAdapter(api_key="x", **_DEEPSEEK_DEFAULTS)
    assert chat._should_use_responses() is False

    responses = OpenAIAdapter(api_key="x", wire_api="responses", **_DEEPSEEK_DEFAULTS)
    assert responses._should_use_responses() is True

    explicit_chat = OpenAIAdapter(
        api_key="x", wire_api="chat_completions", use_responses=True, **_DEEPSEEK_DEFAULTS
    )
    assert explicit_chat._should_use_responses() is False

    legacy = OpenAIAdapter(
        api_key="x", use_responses=True, force_responses=True, **_DEEPSEEK_DEFAULTS
    )
    assert legacy._should_use_responses() is True


def test_deepseek_responses_defaults_are_stateless_no_compaction():
    """DeepSeek's Responses opt-in kept the old adapter's stateless, no
    context-management defaults on the generic adapter."""
    adapter = OpenAIAdapter(
        api_key="x",
        wire_api="responses",
        compact_threshold=None,
        responses_stateless_replay=True,
        **_DEEPSEEK_DEFAULTS,
    )
    assert adapter._responses_stateless_replay is True
    assert adapter._compact_threshold is None


def test_deepseek_responses_reasoning_fallback_injection():
    """Fallback reasoning item is injected after first function_call in Responses items."""
    items = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "pre-tool reply"},
        {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        {"role": "assistant", "content": "post-tool reply"},
    ]
    out = _inject_responses_reasoning_fallback(items)
    assert out[1]["role"] == "assistant" and out[1].get("content") == "pre-tool reply"
    assert out[-2]["type"] == "reasoning"
    assert out[-2]["summary"][0]["type"] == "summary_text"
    assert "post-tool reply" in out[-2]["summary"][0]["text"]
    assert out[-1]["role"] == "assistant" and out[-1]["content"] == "post-tool reply"


def test_deepseek_responses_keeps_existing_reasoning():
    """Real preserved reasoning items are not duplicated by the fallback."""
    items = [
        {"type": "function_call", "call_id": "c1", "name": "f1", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "ok"},
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "real thinking"}]},
        {"role": "assistant", "content": "answer"},
    ]
    out = _inject_responses_reasoning_fallback(items)
    reasoning_items = [i for i in out if i.get("type") == "reasoning"]
    assert len(reasoning_items) == 1
    assert reasoning_items[0]["summary"][0]["text"] == "real thinking"


def test_deepseek_responses_create_responses_session_uses_generic_session():
    """_create_responses_session builds an OpenAIResponsesSession (stateless)."""
    from lingtai.kernel.llm.interface import ChatInterface

    adapter = OpenAIAdapter(
        api_key="x",
        wire_api="responses",
        responses_stateless_replay=True,
        **_DEEPSEEK_DEFAULTS,
    )
    iface = ChatInterface()
    iface.add_system("sys")
    session = adapter._create_responses_session(
        model="deepseek-v4-flash", system_prompt="sys", interface=iface
    )
    assert isinstance(session, OpenAIResponsesSession)
    assert session._stateless_replay is True


def test_inject_reasoning_fallback_default_true_and_env_controlled():
    """Generic OpenAIAdapter defaults inject_reasoning_fallback to True (per
    Jason 2026-08-09: legacy models are not a concern), and the
    LINGTAI_INJECT_REASONING_FALLBACK env var can disable/enable it."""
    import os

    saved = os.environ.pop("LINGTAI_INJECT_REASONING_FALLBACK", None)
    try:
        a = OpenAIAdapter(api_key="x")
        assert a._inject_reasoning_fallback is True

        os.environ["LINGTAI_INJECT_REASONING_FALLBACK"] = "0"
        a = OpenAIAdapter(api_key="x")
        assert a._inject_reasoning_fallback is False

        os.environ["LINGTAI_INJECT_REASONING_FALLBACK"] = "1"
        a = OpenAIAdapter(api_key="x")
        assert a._inject_reasoning_fallback is True

        # Explicit param wins over env.
        os.environ["LINGTAI_INJECT_REASONING_FALLBACK"] = "0"
        a = OpenAIAdapter(api_key="x", inject_reasoning_fallback=True)
        assert a._inject_reasoning_fallback is True
        a = OpenAIAdapter(api_key="x", inject_reasoning_fallback=False)
        assert a._inject_reasoning_fallback is False
    finally:
        if saved is None:
            os.environ.pop("LINGTAI_INJECT_REASONING_FALLBACK", None)
        else:
            os.environ["LINGTAI_INJECT_REASONING_FALLBACK"] = saved


def test_reset_preserves_inject_reasoning_fallback():
    """fable F1: OpenAIChatSession.reset() rebuilds the session and must not
    drop the inject_reasoning_fallback flag (was lost before the fix because
    reset() forwarded every __init__ param except it, and __dict__.update
    overwrote the live True with the constructor default)."""
    import openai as _openai_mod

    iface = ChatInterface()
    iface.add_system("system prompt")
    iface.add_user_message("hi")
    client = MagicMock()
    session = OpenAIChatSession(
        client=client,
        model="deepseek-v4-pro",
        interface=iface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={"api_key": "sk-test"},
        inject_reasoning_fallback=True,
    )
    assert session._inject_reasoning_fallback is True

    # reset() rebuilds via openai.OpenAI(**client_kwargs); mock the module
    # so no real client is constructed, and restore it afterwards so the
    # mock does not leak into later tests in this pytest process (fable R2 M).
    original_openai = _openai_mod.OpenAI
    try:
        fake_client = MagicMock()
        _openai_mod.OpenAI = MagicMock(return_value=fake_client)
        session.reset()
    finally:
        _openai_mod.OpenAI = original_openai
    assert session._inject_reasoning_fallback is True


def test_provider_defaults_lift_new_reasoning_keys():
    """fable F2: manifest llm defaults for the three new reasoning knobs must
    reach the OpenAIAdapter (previously schema-accepted but dead config).
    Exercise the REAL manifest->defaults builder (not a hand-built defaults
    dict) plus the registered factory, mirroring production."""
    from lingtai.llm._register import register_all_adapters
    from lingtai.llm.service import LLMService, build_provider_defaults_from_manifest_llm

    defaults = build_provider_defaults_from_manifest_llm(
        {
            "provider": "openai",
            "inject_reasoning_fallback": False,
            "reasoning_effort_vocab": "seven_tier",
            "prompt_cache_namespace": "custom-ns",
        },
        max_rpm=0,
    )
    assert defaults is not None
    assert defaults["openai"]["inject_reasoning_fallback"] is False
    assert defaults["openai"]["reasoning_effort_vocab"] == "seven_tier"
    assert defaults["openai"]["prompt_cache_namespace"] == "custom-ns"

    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    a = factory(model="gpt-4o", api_key="sk-test", defaults=defaults["openai"])
    assert a._inject_reasoning_fallback is False
    assert a._reasoning_effort_vocab == "seven_tier"
    assert a._prompt_cache_namespace == "custom-ns"

    # fable R3-L2: the deepseek factory must lift the same keys from
    # defaults (its lift sits ABOVE the setdefault block; a reorder would
    # silently regress manifest opt-out back to True with all tests green).
    ds_defaults = build_provider_defaults_from_manifest_llm(
        {
            "provider": "deepseek",
            "inject_reasoning_fallback": False,
            "reasoning_effort_vocab": "seven_tier",
            "prompt_cache_namespace": "custom-ns",
        },
        max_rpm=0,
    )
    assert ds_defaults is not None
    assert ds_defaults["deepseek"]["inject_reasoning_fallback"] is False
    assert ds_defaults["deepseek"]["reasoning_effort_vocab"] == "seven_tier"
    assert ds_defaults["deepseek"]["prompt_cache_namespace"] == "custom-ns"
    ds_factory = LLMService._adapter_registry["deepseek"]
    dsa = ds_factory(model="deepseek-chat", api_key="sk-test", defaults=ds_defaults["deepseek"])
    assert dsa._inject_reasoning_fallback is False
    assert dsa._reasoning_effort_vocab == "seven_tier"
    assert dsa._prompt_cache_namespace == "custom-ns"
