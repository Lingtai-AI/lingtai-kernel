"""Wire tool-description single-source contract.

FunctionSchema.description keeps the full tool prose and canonical
ChatInterface tool snapshots render it. Which surface the *model* sees it on
depends on ``LINGTAI_TOOL_PROSE_SECTION_ENABLED``:

* opted in — the system prompt's ``## tools`` section carries the prose and
  every provider payload carries the constant ``WIRE_TOOL_DESCRIPTION`` pointer
  at it. That is the historical behavior this module pins, so the fixture below
  turns the switch on for the whole file.
* default (off) — the ``## tools`` section is not rendered and the provider
  payload carries the full prose instead. Pinned by
  ``tests/test_tool_prose_section_gate.py`` and by the default-state tests at
  the bottom of this module.

Either way the prose reaches the model exactly once, and parameter/property
descriptions inside ``parameters`` are never touched.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from lingtai.kernel.config import TOOL_PROSE_SECTION_ENABLED_ENV
from lingtai.kernel.llm.base import WIRE_TOOL_DESCRIPTION, FunctionSchema
from lingtai.kernel.base_agent.tools import _refresh_tool_inventory_section


@pytest.fixture(autouse=True)
def _prose_section_opted_in(monkeypatch):
    """Pin this module to the opt-in state the pointer contract belongs to.

    The gate is read from ``os.environ`` at every render, so without this an
    ambient operator value (or the default-off state) would silently flip which
    surface carries the prose.
    """
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")

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
        SimpleNamespace(_runtime=SimpleNamespace(language="en")),
        "Inspect the repository",
        _schemas(),
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


def test_web_action_input_schema_survives_chat_and_responses_wires():
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools
    from lingtai.tools.web_search import get_schema

    schema = FunctionSchema(name="web", description="web", parameters=get_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]
    # The injected settings child makes the disclosure branches overlap at
    # ``input={}``, so the family deliberately emits nested ``anyOf`` on both
    # wires. The branches themselves remain identical.
    for wire in (chat, responses):
        assert wire["type"] == "object"
        # ``reasoning`` is REQUIRED Host InvocationContext/audit metadata —
        # the family schema declares it itself (not left to Agent schema
        # composition's blanket property injection, which never touches
        # ``required``) so it is required even from ``get_schema()`` alone.
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["properties"]["reasoning"]["type"] == "string"
        assert wire["properties"]["summarize"]["type"] == "boolean"
        branches = wire["properties"]["input"]["anyOf"]
        assert [branch["title"] for branch in branches] == [
            "search input", "browse input", "settings inventory input", "manual input",
        ]
        for branch in branches:
            assert branch["additionalProperties"] is False
            assert set(branch["required"]) == set(branch["properties"])
            assert "summarize" not in branch["properties"]
            assert "reasoning" not in branch["properties"]
            assert "_reasoning" not in branch["properties"]
        assert branches[1]["properties"]["cursor"]["type"] == ["string", "null"]
        assert branches[3]["properties"] == {}


def test_web_root_all_of_correlation_survives_chat_and_responses_wires():
    """Root ``allOf`` schema-level action/input correlation must survive on
    BOTH wires — Chat Completions (which never strips any root key) and the
    Responses adapter (which, after this candidate's live-evidence-driven
    fix, preserves root ``allOf``/``oneOf`` instead of unconditionally
    stripping them). Responses may still normalize the nested ``input``
    disclosure surface's ``oneOf`` to ``anyOf``, but the root ``allOf``
    condition itself — and its exact per-action correlation — must remain
    identical on both wires, with no child leakage into the root."""
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools
    from lingtai.tools.web_search import get_schema

    schema = FunctionSchema(name="web", description="web", parameters=get_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    for wire in (chat, responses):
        assert "allOf" in wire
        conditions = wire["allOf"]
        assert [c["if"]["properties"]["action"]["const"] for c in conditions] == [
            "search", "browse", "settings", "manual",
        ]
        for condition in conditions:
            assert condition["if"]["required"] == ["action"]
            then_input = condition["then"]["properties"]["input"]
            assert then_input["additionalProperties"] is False
            assert "reasoning" not in then_input.get("properties", {})
            assert "_reasoning" not in then_input.get("properties", {})
            assert "summarize" not in then_input.get("properties", {})
        # No child leakage into the root: only the four public envelope
        # fields, exactly as before this correlation was added.
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["required"] == ["action", "input", "reasoning"]

    # The two wires' allOf conditions carry identical per-action correlation
    # (Chat Completions' allOf is untouched; Responses' allOf is preserved
    # verbatim by the root-aware scrub).
    assert chat["allOf"] == responses["allOf"]


def test_web_final_agent_schema_root_is_exactly_action_input_reasoning_summarize(tmp_path):
    """A real fresh ``Agent`` startup must prove the exact final closed root:
    ``action | input | reasoning | summarize``, with ``reasoning`` REQUIRED.
    The family's own schema (``ToolFamily.build_schema()``) already declares
    ``reasoning`` required and describes it identically to what Agent schema
    composition (``_build_tool_schemas``) re-injects into every tool's
    ``properties`` uniformly — that injection never touches ``required``, so
    a family must declare ``reasoning`` required itself. ``summarize`` is
    owned by web's own schema (LTP v2 is migrated per-family, not by central
    injection). No public ``summary`` alias and no nested branch admits
    ``reasoning``, ``_reasoning``, or ``summarize``.
    """
    from lingtai.agent import Agent
    from lingtai.kernel.base_agent.tools import _build_tool_schemas
    from tests._service_helpers import make_gemini_mock_service as make_mock_service

    agent = Agent(
        service=make_mock_service(),
        agent_name="wire-test",
        working_dir=tmp_path,
        capabilities={"web": {"provider": "duckduckgo"}},
    )
    try:
        schemas = _build_tool_schemas(agent)
        web = next(s for s in schemas if s.name == "web")
        params = web.parameters
        assert params["required"] == ["action", "input", "reasoning"]
        assert params["additionalProperties"] is False
        assert set(params["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert "summary" not in params["properties"]
        for branch in params["properties"]["input"]["anyOf"]:
            assert "reasoning" not in branch["properties"]
            assert "_reasoning" not in branch["properties"]
            assert "summarize" not in branch["properties"]
            assert "summary" not in branch["properties"]
    finally:
        agent.stop(timeout=1.0)


def test_openai_responses_preserves_daemon_backend_options_passthrough_schema():
    from lingtai.llm.openai.adapter import _build_responses_tools
    from lingtai.tools.daemon import get_schema

    tools = _build_responses_tools([
        FunctionSchema(name="daemon", description="daemon", parameters=get_schema())
    ])
    # Post-ToolFamily migration ``tasks`` lives inside ``emanate``'s own
    # ``input`` branch; the nested passthrough schema below is unchanged.
    emanate_branch = next(
        branch
        for branch in tools[0]["parameters"]["properties"]["input"]["anyOf"]
        if branch["title"] == "emanate input"
    )
    backend_options = emanate_branch["properties"]["tasks"]["items"][
        "properties"
    ]["backend_options"]

    assert "additionalProperties" in backend_options
    assert set(backend_options["properties"]) == {"config", "env"}
    config = backend_options["properties"]["config"]
    assert config["anyOf"] == backend_options["additionalProperties"]["anyOf"]
    assert any(
        option == {"type": "string"}
        for option in backend_options["additionalProperties"]["anyOf"]
    )


def test_openai_responses_root_all_of_if_then_survives():
    """A live non-strict Codex Responses probe on 2026-07-27 showed a raw
    root ``allOf``/``if``/``then`` schema is accepted on the current route
    without error. The adapter must preserve it verbatim rather than
    stripping it."""
    from lingtai.llm.openai.adapter import _build_responses_tools

    params = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "manual"]},
            "input": {"type": "object"},
            "reasoning": {"type": "string"},
        },
        "required": ["action", "input", "reasoning"],
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "search"}}, "required": ["action"]},
                "then": {"properties": {"input": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}}},
            },
            {
                "if": {"properties": {"action": {"const": "manual"}}, "required": ["action"]},
                "then": {"properties": {"input": {"type": "object", "properties": {}, "additionalProperties": False}}},
            },
        ],
    }
    tools = _build_responses_tools([FunctionSchema(name="web", description="web", parameters=params)])
    out = tools[0]["parameters"]
    assert "allOf" in out
    assert len(out["allOf"]) == 2
    assert out["allOf"][0]["if"]["properties"]["action"]["const"] == "search"
    assert out["allOf"][0]["then"]["properties"]["input"]["properties"] == {"query": {"type": "string"}}
    assert out["allOf"][1]["if"]["properties"]["action"]["const"] == "manual"
    assert out["allOf"][1]["then"]["properties"]["input"]["properties"] == {}
    # Ordinary root fields untouched by the root-combinator handling.
    assert out["type"] == "object"
    assert out["required"] == ["action", "input", "reasoning"]
    assert out["additionalProperties"] is False
    assert set(out["properties"]) == {"action", "input", "reasoning"}


def test_openai_responses_root_one_of_survives_as_one_of():
    """Companion to the ``allOf`` case: real backend evidence showed a raw
    root ``oneOf`` is also accepted without error, so it must survive as
    ``oneOf`` (not be rewritten to ``anyOf``, unlike a nested ``oneOf``)."""
    from lingtai.llm.openai.adapter import _build_responses_tools

    params = {
        "type": "object",
        "oneOf": [
            {"properties": {"action": {"const": "search"}}},
            {"properties": {"action": {"const": "manual"}}},
        ],
    }
    tools = _build_responses_tools([FunctionSchema(name="web", description="web", parameters=params)])
    out = tools[0]["parameters"]
    assert "oneOf" in out
    assert "anyOf" not in out
    assert [branch["properties"]["action"]["const"] for branch in out["oneOf"]] == ["search", "manual"]


def test_openai_responses_nested_one_of_still_becomes_any_of_even_with_root_all_of():
    """A nested ``oneOf`` (e.g. inside an ``allOf`` branch's ``then``, or
    under a property) must still be rewritten to ``anyOf`` exactly as before
    — only the schema *root* gets the new preservation behavior."""
    from lingtai.llm.openai.adapter import _build_responses_tools

    params = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "input": {
                "oneOf": [
                    {"type": "object", "properties": {"query": {"type": "string"}}},
                    {"type": "object", "properties": {}},
                ],
            },
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "search"}}},
                "then": {
                    "oneOf": [
                        {"properties": {"input": {"type": "string"}}},
                        {"properties": {"input": {"type": "integer"}}},
                    ],
                },
            },
        ],
    }
    tools = _build_responses_tools([FunctionSchema(name="web", description="web", parameters=params)])
    out = tools[0]["parameters"]
    # Root allOf preserved as allOf.
    assert "allOf" in out
    # Nested oneOf under a property rewritten to anyOf.
    assert "oneOf" not in out["properties"]["input"]
    assert "anyOf" in out["properties"]["input"]
    # Nested oneOf inside an allOf branch's "then" also rewritten to anyOf.
    then_node = out["allOf"][0]["then"]
    assert "oneOf" not in then_node
    assert "anyOf" in then_node


def test_openai_responses_root_any_of_not_and_enum_are_still_stripped():
    """Root ``anyOf``, ``not``, and ``enum`` remain untested against the live
    backend (only root ``oneOf``/``allOf`` were probed), so they are still
    stripped from the schema root exactly as before this change — no
    broadening beyond the evidenced keywords."""
    from lingtai.llm.openai.adapter import _build_responses_tools

    params = {
        "type": "object",
        "properties": {"action": {"type": "string"}},
        "anyOf": [{"required": ["action"]}],
        "not": {"required": ["forbidden"]},
        "enum": ["a", "b"],
    }
    tools = _build_responses_tools([FunctionSchema(name="widget", description="widget", parameters=params)])
    out = tools[0]["parameters"]
    assert "anyOf" not in out
    assert "not" not in out
    assert "enum" not in out
    assert out["type"] == "object"
    assert out["properties"] == {"action": {"type": "string"}}


def test_openai_responses_ordinary_schema_without_root_combinators_unchanged():
    """An ordinary schema with none of the root-combinator keywords is
    completely unaffected by this change — same as before."""
    from lingtai.llm.openai.adapter import _build_responses_tools

    schemas = _schemas()
    tools = _build_responses_tools(schemas)
    assert json.dumps(tools[0]["parameters"], sort_keys=True) == json.dumps(PARAMETERS, sort_keys=True)


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


# ---------------------------------------------------------------------------
# Default state — the switch is OFF unless explicitly opted in
# ---------------------------------------------------------------------------


def _default_off(monkeypatch):
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)


def test_default_off_moves_prose_from_section_to_every_wire(monkeypatch):
    """With the section off, the prose lives on the wire instead — never both."""
    _default_off(monkeypatch)

    from lingtai.llm.anthropic.adapter import _build_tools as anthropic_tools
    from lingtai.llm.gemini.adapter import _build_function_declarations
    from lingtai.llm.openai.adapter import _build_responses_tools
    from lingtai.llm.openai.adapter import _build_tools as openai_tools

    sections: dict[str, str] = {}
    deleted: list[str] = []
    agent = SimpleNamespace(
        _config=SimpleNamespace(language="en"),
        _intrinsics={},
        _intrinsic_modules={},
        _tool_schemas=_schemas(),
        _prompt_manager=SimpleNamespace(
            write_section=lambda name, text, protected=False: sections.__setitem__(name, text),
            delete_section=lambda name: deleted.append(name),
        ),
    )
    _refresh_tool_inventory_section(agent)
    assert sections == {}, "the prose section must not be written by default"
    assert deleted == ["tools"], "a previously written section must be dropped"

    schemas = _schemas()
    wires = [
        openai_tools(schemas)[0]["function"]["description"],
        _build_responses_tools(schemas)[0]["description"],
        anthropic_tools(schemas, cache_tools=False)[0]["description"],
        _build_function_declarations(schemas)[0].description,
    ]
    for description in wires:
        assert description == FULL_DESCRIPTION
        assert description != WIRE_TOOL_DESCRIPTION

    # Nested parameter descriptions stay byte-for-byte identical in both states.
    assert json.dumps(
        openai_tools(schemas)[0]["function"]["parameters"], sort_keys=True
    ) == json.dumps(PARAMETERS, sort_keys=True)


def test_default_off_keeps_pointer_for_a_schema_with_no_prose(monkeypatch):
    """An empty description still yields a non-empty wire description."""
    _default_off(monkeypatch)

    from lingtai.llm.openai.adapter import _build_tools as openai_tools

    schema = FunctionSchema(name="silent", description="", parameters={"type": "object"})
    assert openai_tools([schema])[0]["function"]["description"] == WIRE_TOOL_DESCRIPTION
