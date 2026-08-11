"""Chat Completions / Responses wire parity for the generic ToolFamily schema.

Proves the composed ``oneOf``-over-``input`` schema survives both
provider wire shapes at the smallest existing adapter seam
(``lingtai.llm.openai.adapter._build_tools`` /
``_build_responses_tools``) without any adapter code change — a fake
``widget`` family, unrelated to ``web``, is the fixture.
"""
from __future__ import annotations

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.tools.tool_family import ChildTool, ToolFamily


def _widget_family() -> ToolFamily:
    def spin_handler(input_):
        return {"status": "ok", "action": "spin", "speed": input_.get("speed")}

    def manual_handler(_input):
        return {"status": "ok", "manual": "widget manual", "manual_path": "/fake/manual_path"}

    return ToolFamily(
        "widget",
        [
            ChildTool(
                "spin",
                {
                    "type": "object",
                    "properties": {"speed": {"type": "integer"}},
                    "required": ["speed"],
                    "additionalProperties": False,
                },
                spin_handler,
                title="spin input",
            ),
            ChildTool(
                "manual",
                {"type": "object", "properties": {}, "additionalProperties": False},
                manual_handler,
                title="manual input",
            ),
        ],
    )


def test_generic_family_schema_survives_chat_and_responses_wires():
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    fam = _widget_family()
    schema = FunctionSchema(name="widget", description="widget", parameters=fam.build_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    for wire, combinator in ((chat, "oneOf"), (responses, "anyOf")):
        assert wire["type"] == "object"
        # ``reasoning`` is REQUIRED Host InvocationContext/audit metadata,
        # declared by the family schema itself (see ToolFamily.build_schema).
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["properties"]["reasoning"]["type"] == "string"
        input_schema = wire["properties"]["input"]
        assert input_schema["type"] == "object"
        branches = input_schema[combinator]
        assert [b["title"] for b in branches] == ["spin input", "manual input"]
        for branch in branches:
            assert branch["additionalProperties"] is False
            assert "reasoning" not in branch["properties"]
            assert "_reasoning" not in branch["properties"]
            assert "summarize" not in branch["properties"]

    assert fam.handle(
        {"action": "spin", "input": {"speed": 4}, "reasoning": "r"}
    ) == {"status": "ok", "action": "spin", "speed": 4}


def test_generic_family_handler_parity_across_dispatch_and_child_registry():
    """The same family instance dispatches identically regardless of which
    provider wire the call arrived from — dispatch does not depend on wire
    shape, only on the normalized ``{action, input, ...}`` args dict every
    provider adapter converges to before ``ToolExecutor`` dispatch."""
    fam = _widget_family()
    result = fam.handle({"action": "spin", "input": {"speed": 4}, "reasoning": "chat-wire-call"})
    assert result == {"status": "ok", "action": "spin", "speed": 4}
    result2 = fam.handle({"action": "spin", "input": {"speed": 4}, "reasoning": "responses-wire-call"})
    assert result2 == {"status": "ok", "action": "spin", "speed": 4}


def test_real_agent_startup_builds_web_family_schema_on_both_wires(tmp_path):
    """Integration proof at the real Agent composition boundary (not just the
    unit-level FunctionSchema construction above): a fresh Agent with the web
    capability produces one ``web`` tool whose schema composes correctly and
    reaches both provider builders unchanged from ``_build_tool_schemas``."""
    from lingtai.agent import Agent
    from lingtai.kernel.base_agent.tools import _build_tool_schemas
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools
    from tests._service_helpers import make_gemini_mock_service as make_mock_service

    agent = Agent(
        service=make_mock_service(),
        agent_name="wire-parity-test",
        working_dir=tmp_path,
        capabilities={"web": {"provider": "duckduckgo"}},
    )
    try:
        schemas = _build_tool_schemas(agent)
        web = next(s for s in schemas if s.name == "web")
        chat = _build_tools([web])[0]["function"]["parameters"]
        responses = _build_responses_tools([web])[0]["parameters"]
        assert set(chat["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert set(responses["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert chat["required"] == ["action", "input", "reasoning"]
        assert responses["required"] == ["action", "input", "reasoning"]
        assert chat["properties"]["input"]["type"] == "object"
        assert responses["properties"]["input"]["type"] == "object"
        assert len(chat["properties"]["input"]["oneOf"]) == 3
        assert len(responses["properties"]["input"]["anyOf"]) == 3
    finally:
        agent.stop(timeout=1.0)
