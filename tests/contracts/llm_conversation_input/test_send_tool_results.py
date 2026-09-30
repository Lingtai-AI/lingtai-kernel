"""``send(list[ToolResultBlock])`` converts the canonical block per regime.

This is the heart of the characterization: the kernel builds tool results via
``LLMService.make_tool_result`` -> ``adapter.make_tool_result_message`` (always a
canonical ``ToolResultBlock``) and hands the list to ``ChatSession.send``. For
each *conforming* production regime this test proves the canonical block is
converted into the exact provider wire form the regime declares, AND that the
``send`` returns a real ``LLMResponse`` with concrete usage. The
reasoning-fallback-configured ``OpenAIChatSession``, the stateless Responses
session, Codex, and a ``_GatedSession``-wrapped session all exercise their
concrete production session implementations with mocked transports.
"""
from __future__ import annotations

import json

import pytest

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

    A raw dataclass reaching the transport is exactly the historical OpenAI
    Responses defect class. Conforming regimes must have serialized it.
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


def test_reasoning_fallback_injects_reasoning_content_on_paired_turn() -> None:
    """The reasoning-fallback-configured session must inject
    ``reasoning_content`` on the assistant tool-call turn of a paired
    continuation, while the unconfigured base ``OpenAIChatSession`` must NOT —
    proving the configured construction is exercised.
    """
    regime = next(
        r for r in regimes.CONFORMING_BUILDABLE
        if r.name == "openai_chat_reasoning_fallback"
    )
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


def test_gated_session_forwards_both_inputs_through_the_gate() -> None:
    """The ``_GatedSession`` proxy forwards ``send(str)`` and paired
    ``send(list[ToolResultBlock])`` through ``gate.submit`` to the inner real
    session, and returns the inner session's ``LLMResponse``. This characterizes
    the current gate forwarding (the default max_rpm>0 composition) without
    redesigning it.
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
