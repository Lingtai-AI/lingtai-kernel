"""``send(str)`` reaches every production regime's transport as provider text.

The kernel ``ChatSession`` ABC declares ``send(str)`` as one of exactly two
input shapes. This test proves that a plain user-text turn is serialized into
the expected provider wire form for every *conforming* production session
regime — including the concrete OpenAI-compatible subclasses (DeepSeek / MiMo /
Zhipu), Codex, and a ``_GatedSession``-wrapped session — using a mocked
transport (no network). It also asserts each ``send`` returns a real
``LLMResponse`` with concrete ``UsageMetadata``.
"""
from __future__ import annotations

import pytest

from tests.contracts.llm_conversation_input import regimes


@pytest.mark.parametrize(
    "regime",
    regimes.CONFORMING_BUILDABLE,
    ids=lambda r: r.name,
)
def test_send_str_reaches_transport_as_provider_text(regime: regimes.Regime) -> None:
    session, transport = regime.build()

    response = session.send(regimes.USER_TEXT)

    wire = regime.sent_wire(transport)
    _assert_text_present(regime, wire)
    regimes.assert_response_envelope(response, regime.expected_response, regime.name)


def _assert_text_present(regime: regimes.Regime, wire) -> None:
    """The user text must be present in the captured wire in the regime shape."""
    if regime.expected_text_wire is not None:
        # Dict-shaped regimes (OpenAI Chat / Responses / Codex) carry an item.
        assert regime.expected_text_wire in wire, (
            f"{regime.name}: expected {regime.expected_text_wire!r} in {wire!r}"
        )
        return
    # String-rendered regimes (Claude Code) — the text appears in the prompt.
    if isinstance(wire, str):
        assert regimes.USER_TEXT in wire, f"{regime.name}: {wire!r}"
        return
    # Structured regimes (Anthropic messages / Gemini Interactions input):
    # the text appears somewhere in the serialized structure.
    import json

    assert regimes.USER_TEXT in json.dumps(wire, default=str), (
        f"{regime.name}: text not found in {wire!r}"
    )
