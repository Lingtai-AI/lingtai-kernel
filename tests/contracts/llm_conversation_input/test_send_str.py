"""``send(str)`` reaches every production regime's transport as provider text.

The kernel ``ChatSession`` ABC declares ``send(str)`` as one of exactly two
input shapes. This test proves that a plain user-text turn is serialized into
the expected provider wire form for every *conforming* production session
regime — including the reasoning-fallback-configured session, the stateless
Responses session, Codex, and a ``_GatedSession``-wrapped session — using a
mocked transport (no network). It also asserts each ``send`` returns a real
``LLMResponse`` with concrete ``UsageMetadata``.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.llm.openai.adapter import CodexOpenAIAdapter, OpenAIAdapter

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
    # Structured regimes (Anthropic messages):
    # the text appears somewhere in the serialized structure.
    import json

    assert regimes.USER_TEXT in json.dumps(wire, default=str), (
        f"{regime.name}: text not found in {wire!r}"
    )


@pytest.mark.parametrize("provider", ["codex", "responses", "chat_completions"])
def test_tool_schema_policy_on_creation_and_update(monkeypatch, provider):
    """Adapter conformance: conditional schemas survive; only Codex sets strict=False."""
    monkeypatch.setenv("LINGTAI_CODEX_TRANSPORT", "rest")
    monkeypatch.delenv("LINGTAI_CODEX_WS", raising=False)
    client = regimes._CodexRestClient()
    monkeypatch.setattr("lingtai.llm.openai.adapter.openai.OpenAI", lambda **kw: client)
    native = provider == "codex"
    adapter = (CodexOpenAIAdapter if native else OpenAIAdapter)(
        api_key="inert", base_url="https://adapter.invalid",
        wire_api="responses" if native else provider,
    )
    parameters = {
        "type": "object",
        "properties": {"action": {"type": "string"}, "value": {"type": "string"}},
        "allOf": [{"if": {"properties": {"action": {"const": "set"}}},
                   "then": {"required": ["value"]}}],
    }
    for phase in ("initial", "updated"):
        schemas = [FunctionSchema(phase + name, "description", deepcopy(parameters))
                   for name in ("one", "two")]
        snapshot = deepcopy(schemas)
        if phase == "initial":
            session = adapter.create_chat("arbitrary-model", "system", tools=schemas)
        else:
            session.update_tools(schemas)
        tools = session._tools
        if native:
            session.send(regimes.USER_TEXT)
            payload = client.responses.kwargs[-1]
            tools = payload["tools"]
            assert session._ws_frame_request(payload, payload["input"])["tools"] == tools
        assert len(tools) == len(schemas)
        for tool, schema in zip(tools, schemas):
            function = tool.get("function", tool)
            if native:
                assert function.get("strict") is False
            else:
                assert "strict" not in function
            assert function["name"] == schema.name
            assert function["parameters"] == parameters
        assert schemas == snapshot
    for empty in ([], None):
        session.update_tools(empty)
        assert session._tools is None
        if native:
            session.send(regimes.USER_TEXT)
            assert "tools" not in client.responses.kwargs[-1]
    session.update_tools(schemas)
    assert session._tools
    if native:
        assert all(tool.get("strict") is False for tool in session._tools)
