"""Wire tool-description single-source contract.

FunctionSchema.description keeps the full tool prose — the system prompt's
``## tools`` section and canonical ChatInterface tool snapshots render it —
while every provider payload built from registered schemas carries the constant
``WIRE_TOOL_DESCRIPTION`` as the function-level description. Parameter and
property descriptions inside ``parameters`` are never touched.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from lingtai.kernel.llm.base import WIRE_TOOL_DESCRIPTION, FunctionSchema
from lingtai.kernel.base_agent.tools import _refresh_tool_inventory_section

FULL_DESCRIPTION = (
    "Send an email. Full multi-paragraph usage guidance lives here and must "
    "keep rendering into the system prompt verbatim."
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "to": {"type": "string", "description": "Recipient address."},
        "body": {"type": "string", "description": "Plain-text body."},
        "priority": {
            "type": "string",
            "enum": ["low", "normal", "high"],
            "description": "Delivery priority.",
        },
    },
    "required": ["to", "body"],
}


def _schemas() -> list[FunctionSchema]:
    return [
        FunctionSchema(
            name="email",
            description=FULL_DESCRIPTION,
            parameters=copy.deepcopy(PARAMETERS),
        ),
        FunctionSchema(
            name="bash",
            description="Run a shell command.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command."},
                },
                "required": ["command"],
            },
        ),
    ]


def test_wire_constant_is_the_contract_sentence():
    assert WIRE_TOOL_DESCRIPTION == "See the system prompt for tool usage guidance."


def test_function_schema_keeps_original_description():
    schema = _schemas()[0]
    assert schema.description == FULL_DESCRIPTION
    # Canonical ChatInterface snapshots (to_dict/list_to_dicts) keep the
    # original prose so from_dicts round-trips losslessly.
    assert schema.to_dict()["description"] == FULL_DESCRIPTION
    round_tripped = FunctionSchema.from_dicts(FunctionSchema.list_to_dicts([schema]))
    assert round_tripped[0].description == FULL_DESCRIPTION


def test_main_and_daemon_tools_sections_render_full_prose():
    sections: dict[str, str] = {}
    agent = SimpleNamespace(
        _config=SimpleNamespace(language="en"),
        _intrinsics={},
        _intrinsic_modules={},
        _tool_schemas=_schemas(),
        _prompt_manager=SimpleNamespace(
            write_section=lambda name, text, protected=False: sections.__setitem__(name, text)
        ),
    )
    _refresh_tool_inventory_section(agent)
    assert FULL_DESCRIPTION in sections["tools"]
    assert WIRE_TOOL_DESCRIPTION not in sections["tools"]

    # Daemon emanations use the bounded worker prompt introduced by #972; tool
    # schemas are provider-visible, while the prompt carries only tool names.
    from lingtai.tools.daemon import DaemonManager

    daemon_prompt = DaemonManager._build_emanation_prompt(
        SimpleNamespace(_agent=agent), "Inspect the repository", _schemas()
    )
    assert "`email`" in daemon_prompt and "`bash`" in daemon_prompt
    assert FULL_DESCRIPTION not in daemon_prompt
    assert WIRE_TOOL_DESCRIPTION not in daemon_prompt


def test_openai_chat_completions_wire_description():
    from lingtai.llm.openai.adapter import _build_tools

    schemas = _schemas()
    tools = _build_tools(schemas)
    assert [t["function"]["name"] for t in tools] == ["email", "bash"]
    for tool in tools:
        assert tool["type"] == "function"
        assert tool["function"]["description"] == WIRE_TOOL_DESCRIPTION
    # Nested parameter descriptions byte-for-byte unchanged.
    assert json.dumps(tools[0]["function"]["parameters"], sort_keys=True) == json.dumps(
        PARAMETERS, sort_keys=True
    )
    # Originals untouched.
    assert schemas[0].description == FULL_DESCRIPTION


def test_openai_responses_wire_description():
    from lingtai.llm.openai.adapter import _build_responses_tools

    schemas = _schemas()
    tools = _build_responses_tools(schemas)
    for tool in tools:
        assert tool["type"] == "function"
        assert tool["description"] == WIRE_TOOL_DESCRIPTION
    # Flat Responses shape retained (fields hoisted, no nested "function").
    assert tools[0]["name"] == "email"
    assert "function" not in tools[0]
    # The scrub pass leaves this already-well-formed schema byte-identical.
    assert json.dumps(tools[0]["parameters"], sort_keys=True) == json.dumps(
        PARAMETERS, sort_keys=True
    )
    assert schemas[0].description == FULL_DESCRIPTION


def test_openai_responses_preserves_daemon_backend_options_passthrough_schema():
    from lingtai.llm.openai.adapter import _build_responses_tools
    from lingtai.tools.daemon import get_schema

    tools = _build_responses_tools([
        FunctionSchema(name="daemon", description="daemon", parameters=get_schema())
    ])
    backend_options = tools[0]["parameters"]["properties"]["tasks"]["items"][
        "properties"
    ]["backend_options"]

    assert "additionalProperties" in backend_options
    assert backend_options["properties"] == {}
    assert any(
        option == {"type": "string"}
        for option in backend_options["additionalProperties"]["anyOf"]
    )


def test_anthropic_wire_description_and_cache_control():
    from lingtai.llm.anthropic.adapter import _build_tools

    schemas = _schemas()
    tools = _build_tools(schemas, cache_tools=True)
    for tool in tools:
        assert tool["description"] == WIRE_TOOL_DESCRIPTION
    assert tools[0]["name"] == "email"
    assert json.dumps(
        {k: v for k, v in tools[0]["input_schema"].items()}, sort_keys=True
    ) == json.dumps(PARAMETERS, sort_keys=True)
    # cache_tools breakpoint stays on the last tool only.
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]
    assert schemas[0].description == FULL_DESCRIPTION


def test_gemini_wire_descriptions():
    pytest.importorskip("google.genai")
    from lingtai.llm.gemini.adapter import (
        _build_function_declarations,
        _build_interactions_tools,
    )

    schemas = _schemas()
    declarations = _build_function_declarations(schemas)
    for decl in declarations:
        assert decl.description == WIRE_TOOL_DESCRIPTION
    assert declarations[0].name == "email"
    # The SDK normalizes JSON Schema types into its enum-backed Schema model;
    # verify the nested descriptions, required fields, and enum survive intact.
    native_parameters = declarations[0].parameters
    assert native_parameters.required == PARAMETERS["required"]
    assert native_parameters.properties["to"].description == "Recipient address."
    assert native_parameters.properties["body"].description == "Plain-text body."
    assert native_parameters.properties["priority"].description == "Delivery priority."
    assert native_parameters.properties["priority"].enum == ["low", "normal", "high"]

    interactions = _build_interactions_tools(schemas)
    for tool in interactions:
        assert tool["type"] == "function"
        assert tool["description"] == WIRE_TOOL_DESCRIPTION
    assert json.dumps(interactions[0]["parameters"], sort_keys=True) == json.dumps(
        PARAMETERS, sort_keys=True
    )
    assert schemas[0].description == FULL_DESCRIPTION
