"""``send(list[ToolResultBlock])`` converts the canonical block per regime.

This is the heart of the characterization: the kernel builds tool results via
``LLMService.make_tool_result`` -> ``adapter.make_tool_result_message`` (always a
canonical ``ToolResultBlock``) and hands the list to ``ChatSession.send``. For
each *conforming* production regime this test proves the canonical block is
converted into the exact provider wire form the regime declares, AND that the
``send`` returns a real ``LLMResponse`` with concrete usage. The concrete
OpenAI-compatible subclasses (DeepSeek / MiMo / Zhipu), Codex, and a
``_GatedSession``-wrapped session are all built as their real classes.

The dormant ``gemini_chat`` path is handled separately: it is asserted for its
actual (broken) forwarding behavior, never as a MUST.
"""
from __future__ import annotations

import json

import pytest

from lingtai.kernel.llm.interface import ToolResultBlock
from tests.contracts.llm_conversation_input import regimes


@pytest.mark.parametrize(
    "regime",
    regimes.CONFORMING_BUILDABLE,
    ids=lambda r: r.name,
)
def test_canonical_tool_result_converts_to_provider_wire(
    regime: regimes.Regime,
) -> None:
    session, transport = regime.build()
    regimes.seed_matching_tool_call(session)
    block = regimes.canonical_tool_result()

    response = session.send([block])

    wire = regime.sent_wire(transport)
    expected = regime.expected_tool_result_wire
    assert expected is not None, f"{regime.name} has no expected tool-result wire"
    assert _wire_contains(wire, expected), (
        f"{regime.name}: expected {expected!r} within captured wire {wire!r}"
    )
    if regime.extra_tool_result_assert is not None:
        regime.extra_tool_result_assert(wire)
    regimes.assert_response_envelope(response, regime.expected_response, regime.name)


def _wire_contains(wire, expected) -> bool:
    """True if ``expected`` appears in the captured wire.

    Dict expectations must match an item exactly (recursing into Anthropic's
    grouped ``user`` message content). String expectations must be a substring
    of a rendered prompt.
    """
    if isinstance(expected, str):
        return isinstance(wire, str) and expected in wire
    # ``expected`` is a dict; search list items and nested content lists.
    for item in wire:
        if item == expected:
            return True
        content = item.get("content") if isinstance(item, dict) else None
        if isinstance(content, list) and expected in content:
            return True
    return False


def test_no_regime_forwards_unconverted_dataclass() -> None:
    """No conforming regime may leave a ``ToolResultBlock`` in the wire.

    A raw dataclass reaching the transport is exactly the OpenAI Responses /
    Gemini Chat defect class. Conforming regimes must have serialized it.
    """
    for regime in regimes.CONFORMING_BUILDABLE:
        session, transport = regime.build()
        regimes.seed_matching_tool_call(session)
        session.send([regimes.canonical_tool_result()])
        wire = regime.sent_wire(transport)
        payload = wire if isinstance(wire, str) else json.dumps(wire, default=str)
        # A serialized ToolResultBlock repr would contain "ToolResultBlock(".
        assert "ToolResultBlock(" not in payload, (
            f"{regime.name} forwarded an unconverted ToolResultBlock: {wire!r}"
        )


def test_deepseek_and_mimo_inject_reasoning_content_on_paired_turn() -> None:
    """DeepSeek/MiMo's ``_build_messages`` must inject ``reasoning_content`` on
    the assistant tool-call turn of a paired continuation, and the base
    ``OpenAIChatSession`` must NOT — proving the subclasses are exercised as
    their real classes, not generic stand-ins.
    """
    subclass_names = {"deepseek_chat", "mimo_chat"}
    subclasses = [r for r in regimes.CONFORMING_BUILDABLE if r.name in subclass_names]
    assert {r.name for r in subclasses} == subclass_names, "subclass regimes missing"

    for regime in subclasses:
        session, transport = regime.build()
        regimes.seed_matching_tool_call(session)
        session.send([regimes.canonical_tool_result()])
        wire = regime.sent_wire(transport)
        assistant = [m for m in wire if m.get("role") == "assistant"]
        assert assistant and all(m.get("reasoning_content") for m in assistant), (
            f"{regime.name}: expected reasoning_content on the assistant "
            f"tool-call turn, got {assistant!r}"
        )

    # The base OpenAIChatSession must not add reasoning_content on the same path.
    base = next(r for r in regimes.CONFORMING_BUILDABLE if r.name == "openai_chat")
    session, transport = base.build()
    regimes.seed_matching_tool_call(session)
    session.send([regimes.canonical_tool_result()])
    wire = base.sent_wire(transport)
    base_assistant = [m for m in wire if m.get("role") == "assistant"]
    assert base_assistant and not any(
        m.get("reasoning_content") for m in base_assistant
    ), f"openai_chat must not inject reasoning_content: {base_assistant!r}"


def test_zhipu_merges_consecutive_same_role_messages() -> None:
    """Zhipu's ``_build_messages`` merges adjacent same-role messages (GLM
    rejects same-role runs). Two consecutive user turns collapse to one on the
    wire, while the base ``OpenAIChatSession`` leaves them separate — proving
    ZhipuChatSession is exercised as its real class.
    """
    zhipu = next(r for r in regimes.CONFORMING_BUILDABLE if r.name == "zhipu_chat")
    session, transport = zhipu.build()
    session.interface.add_user_message("first user")
    session.send("second user")
    users = [m for m in zhipu.sent_wire(transport) if m.get("role") == "user"]
    assert len(users) == 1, f"zhipu should merge consecutive users, got {users!r}"
    assert "first user" in users[0]["content"] and "second user" in users[0]["content"]

    base = next(r for r in regimes.CONFORMING_BUILDABLE if r.name == "openai_chat")
    bsession, btransport = base.build()
    bsession.interface.add_user_message("first user")
    bsession.send("second user")
    busers = [m for m in base.sent_wire(btransport) if m.get("role") == "user"]
    assert len(busers) == 2, f"base must NOT merge consecutive users, got {busers!r}"


def test_gated_session_forwards_both_inputs_through_the_gate() -> None:
    """The ``_GatedSession`` proxy forwards ``send(str)`` and paired
    ``send(list[ToolResultBlock])`` through ``gate.submit`` to the inner real
    session, and returns the inner session's ``LLMResponse``. This characterizes
    the current gate forwarding (the default max_rpm>0 / MiniMax composition)
    without redesigning it.
    """
    gated, client = regimes._build_gated_openai_chat()
    gate = gated._gate  # the synchronous stand-in

    r1 = gated.send(regimes.USER_TEXT)
    assert gate.calls == 1, "send(str) must route through the gate exactly once"
    regimes.assert_response_envelope(r1, regimes.ExpectedResponse(), "gated:str")

    regimes.seed_matching_tool_call(gated)
    r2 = gated.send([regimes.canonical_tool_result()])
    assert gate.calls == 2, "send(list) must route through the gate too"
    regimes.assert_response_envelope(r2, regimes.ExpectedResponse(), "gated:tool")

    wire = regimes._openai_completions_sent(client)
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == regimes.TOOL_CALL_ID
        for m in wire
    ), f"gated tool result did not reach the inner session wire: {wire!r}"


# ---------------------------------------------------------------------------
# Documented non-conforming path — characterized, not asserted as a MUST
# ---------------------------------------------------------------------------


def test_gemini_chat_characterization_forwards_unconverted_block() -> None:
    """GeminiChatSession (json_schema-only, dormant) hands the SDK the raw list.

    This pins the *current* behavior of the ``gemini_chat`` regime: the canonical
    ``ToolResultBlock`` is forwarded to genai ``chat.send_message`` with no
    conversion. This is a documented defect on a path no production
    ``create_session`` caller reaches; the fix is a named follow-up (a native
    ``genai.types.Part`` converter), out of this slice's scope. If a future
    change makes GeminiChatSession serialize canonical blocks, this test should
    be updated to the conforming assertion.
    """
    regime = regimes.GEMINI_CHAT_REGIME
    session, transport = regime.build()
    block = regimes.canonical_tool_result()

    session.send([block])

    sent = regime.sent_wire(transport)
    # The exact same list object of dataclasses reaches the SDK, unconverted.
    assert isinstance(sent, list)
    assert sent and isinstance(sent[0], ToolResultBlock)
    assert sent[0].id == regimes.TOOL_CALL_ID
    assert not regime.conforms
