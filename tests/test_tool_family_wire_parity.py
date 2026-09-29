"""Chat Completions / Responses wire parity for the generic ToolFamily schema.

Proves the composed root-``oneOf`` discriminated-union schema survives both
provider wire shapes at the smallest existing adapter seam
(``lingtai.llm.openai.adapter._build_tools`` /
``_build_responses_tools``) without any adapter code change — a fake
``widget`` family, unrelated to ``web``, is the fixture. The Responses
builder preserves a *root* ``oneOf`` verbatim while rewriting nested ones to
``anyOf``, which is exactly why the union lives at the root rather than
under ``properties.input``.
"""
from __future__ import annotations

import json

import pytest

from lingtai.kernel.llm.base import FunctionSchema
from lingtai.tools.tool_family import (
    TRIGGER_UNSUPPORTED_INPUT_FIELD,
    ChildTool,
    DiagnosticDescriptor,
    ToolFamily,
)
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)


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

    # Chat Completions passes the composed schema through byte-for-byte.
    assert chat == fam.build_schema()
    for wire in (chat, responses):
        assert wire["type"] == "object"
        # ``reasoning`` is REQUIRED Host InvocationContext/audit metadata,
        # declared by the family schema itself (see ToolFamily.build_schema).
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["properties"]["reasoning"]["type"] == "string"
        assert wire["properties"]["input"]["type"] == "object"
        # The root ``oneOf`` survives as ``oneOf`` on BOTH wires (the
        # Responses scrub only rewrites nested ``oneOf``), with the same
        # per-action correlation.
        assert "anyOf" not in wire and "allOf" not in wire
        assert branch_actions(wire) == ["spin", "manual"]
        for branch in action_input_schemas(wire).values():
            assert branch["additionalProperties"] is False
            assert "reasoning" not in branch["properties"]
            assert "_reasoning" not in branch["properties"]
            assert "summarize" not in branch["properties"]
    assert chat["oneOf"] == responses["oneOf"]
    # The Responses scrub's only touch on this shape: the typed root
    # ``input`` gains an empty ``properties`` map (a typed object without one
    # is rejected by that backend); it gains no duplicate child schema.
    assert responses["properties"]["input"]["properties"] == {}
    assert set(responses["properties"]["input"]) == {"type", "description", "properties"}

    assert fam.handle(
        {"action": "spin", "input": {"speed": 4}, "reasoning": "r"}
    ) == {"status": "ok", "action": "spin", "speed": 4}


def test_nested_child_one_of_is_rewritten_only_on_responses_while_root_survives():
    """A child whose own ``input_schema`` carries a nested ``oneOf`` (a
    real shape: e.g. telegram ``send``'s text-XOR-media alternatives) keeps
    it on Chat Completions but sees it rewritten to ``anyOf`` on Responses —
    while the root discriminated union is preserved on both. This is the
    exact seam that makes a root ``oneOf`` the right home for correlation."""
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    def handler(input_):
        return {"status": "ok"}

    send = ChildTool(
        "send",
        {
            "type": "object",
            "properties": {"text": {"type": "string"}, "media": {"type": "string"}},
            "oneOf": [{"required": ["text"]}, {"required": ["media"]}],
            "additionalProperties": False,
        },
        handler,
    )
    fam = ToolFamily("widget", [send])
    schema = FunctionSchema(name="widget", description="widget", parameters=fam.build_schema())
    chat = _build_tools([schema])[0]["function"]["parameters"]
    responses = _build_responses_tools([schema])[0]["parameters"]

    assert branch_actions(chat) == branch_actions(responses) == ["send"]
    chat_send = action_input_schemas(chat)["send"]
    responses_send = action_input_schemas(responses)["send"]
    assert chat_send["oneOf"] == [{"required": ["text"]}, {"required": ["media"]}]
    assert "oneOf" not in responses_send
    assert responses_send["anyOf"] == [{"required": ["text"]}, {"required": ["media"]}]
    assert "oneOf" in responses and "anyOf" not in responses


def _union_family() -> ToolFamily:
    """Fixture with a plain child, a child carrying its own nested ``oneOf``
    (the real telegram/whatsapp ``send`` shape), and a strict-empty child
    with an explicit ``required: []`` — the three shapes a provider
    transform could plausibly duplicate, drop, or rewrite."""

    def handler(_input):
        return {"status": "ok"}

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
                handler,
            ),
            ChildTool(
                "send",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}, "media": {"type": "string"}},
                    "oneOf": [{"required": ["text"]}, {"required": ["media"]}],
                    "additionalProperties": False,
                },
                handler,
            ),
            ChildTool(
                "manual",
                {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                handler,
            ),
        ],
    )


def _assert_union_intact(params: dict, fam: ToolFamily) -> None:
    """The closed envelope, ordered action consts, and byte-exact child
    schemas survive a provider transform, with no duplicate and no drop."""
    assert_compact_envelope(params, list(fam.child_names))
    assert branch_actions(params) == ["spin", "send", "manual"]
    for name, branch in action_input_schemas(params).items():
        assert branch == dict(fam._children[name].input_schema)
    # The ``speed`` property key serializes exactly once: no duplicate copy
    # of the child schema anywhere in the transformed parameters.
    assert json.dumps(params).count('"speed": {') == 1


# ---------------------------------------------------------------------------
# Non-OpenAI adapter transform parity. These prove only that each adapter's
# *local* conversion keeps (or, where the SDK forbids it, rejects) the
# compact root union — not live provider acceptance.
# ---------------------------------------------------------------------------


def test_anthropic_build_tools_carries_the_compact_union_verbatim_in_input_schema():
    """Anthropic ``_build_tools`` hoists ``parameters`` into ``input_schema``
    without any schema transform, so the root ``oneOf``, closed envelope,
    ordered action consts, and exact child schemas survive byte-for-byte;
    the optional cache marker only touches the tool dict, never the schema."""
    from lingtai.llm.anthropic.adapter import _build_tools

    fam = _union_family()
    schema = FunctionSchema(name="widget", description="widget", parameters=fam.build_schema())
    plain = _build_tools([schema])[0]
    cached = _build_tools([schema], cache_tools=True)[0]

    assert set(plain) == {"name", "description", "input_schema"}
    assert plain["input_schema"] == fam.build_schema()
    assert cached["input_schema"] == plain["input_schema"]
    assert cached["cache_control"] == {"type": "ephemeral"}
    _assert_union_intact(plain["input_schema"], fam)
    assert action_input_schemas(plain["input_schema"])["send"]["oneOf"] == [
        {"required": ["text"]}, {"required": ["media"]},
    ]


def test_gemini_interactions_tools_keep_the_compact_union_after_sanitation():
    """Gemini Interactions ``_build_interactions_tools`` passes the dict
    through ``_sanitize_parameters_for_interactions``, whose only documented
    edit is dropping an empty root ``required: []``. The family root always
    requires ``action``/``input``/``reasoning``, so nothing is stripped: the
    root union, the nested child ``oneOf``, and even a child's own
    ``required: []`` (nested, not root) survive exactly."""
    gemini_adapter = pytest.importorskip("lingtai.llm.gemini.adapter")

    fam = _union_family()
    schema = FunctionSchema(name="widget", description="widget", parameters=fam.build_schema())
    tool = gemini_adapter._build_interactions_tools([schema])[0]

    assert tool["type"] == "function" and tool["name"] == "widget"
    params = tool["parameters"]
    assert params == fam.build_schema()
    _assert_union_intact(params, fam)
    assert action_input_schemas(params)["manual"]["required"] == []
    # The documented root-only sanitation is still in force for a schema
    # that does carry an empty root ``required``.
    assert gemini_adapter._sanitize_parameters_for_interactions(
        {"type": "object", "required": []}
    ) == {"type": "object"}


def test_gemini_canonical_function_declaration_rejects_root_combinators_on_old_and_new_shapes():
    """Local transform parity for the canonical Gemini Chat path
    (``_build_function_declarations`` → ``google.genai.types.FunctionDeclaration``).

    The installed SDK's pydantic ``Schema`` model forbids ``oneOf``,
    ``allOf``, and ``const`` (extra keys are an error), so it cannot carry
    the compact root union. This is NOT a regression of the compact
    composition: the previous duplicated shape — reconstructed here from the
    same registry — was rejected by the same validator for its nested
    ``properties.input.oneOf`` and root ``allOf``. Pinned so the limitation
    is explicit and any later Gemini-side sanitizer flips this test loudly."""
    pytest.importorskip("google.genai")
    from pydantic import ValidationError

    from lingtai.llm.gemini.adapter import _build_function_declarations

    fam = _union_family()
    new_shape = fam.build_schema()

    # The contract-v4 composition, rebuilt from the same live children.
    old_shape = {
        "type": "object",
        "properties": {
            "action": new_shape["properties"]["action"],
            "input": {
                "type": "object",
                "description": new_shape["properties"]["input"]["description"],
                "oneOf": [
                    {"title": f"{name} input", **dict(fam._children[name].input_schema)}
                    for name in fam.child_names
                ],
            },
            "reasoning": new_shape["properties"]["reasoning"],
            "summarize": new_shape["properties"]["summarize"],
        },
        "required": ["action", "input", "reasoning"],
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"action": {"const": name}}, "required": ["action"]},
                "then": {"properties": {"input": dict(fam._children[name].input_schema)}},
            }
            for name in fam.child_names
        ],
    }

    for label, shape, forbidden in (
        ("new", new_shape, "parameters.oneOf"),
        ("old", old_shape, "parameters.properties.input.oneOf"),
    ):
        schema = FunctionSchema(name="widget", description="widget", parameters=shape)
        with pytest.raises(ValidationError) as excinfo:
            _build_function_declarations([schema])
        assert forbidden in str(excinfo.value), label


def test_kimi_site_quirk_moves_root_type_into_each_union_branch():
    """The Kimi host quirk (``type`` beside ``anyOf``/``oneOf`` is rejected
    there) now fires at the composed root rather than at ``input``: the root
    ``type`` moves into every ``oneOf`` branch, the canonical schema is not
    mutated, and the per-action correlation is unchanged."""
    from lingtai.llm.openai.adapter import _apply_site_quirks, _build_tools

    fam = _widget_family()
    schema = FunctionSchema(name="widget", description="widget", parameters=fam.build_schema())
    tools = _build_tools([schema])
    before = json.dumps(tools, sort_keys=True)
    quirked = _apply_site_quirks("https://api.kimi.com/coding/v1", tools)
    params = quirked[0]["function"]["parameters"]

    assert json.dumps(tools, sort_keys=True) == before
    assert "type" not in params
    assert all(branch["type"] == "object" for branch in params["oneOf"])
    assert branch_actions(params) == ["spin", "manual"]
    assert action_input_schemas(params)["spin"]["required"] == ["speed"]
    # Non-Kimi hosts keep the canonical schema byte-for-byte.
    assert _apply_site_quirks("https://api.openai.com/v1", tools) == tools


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
        assert len(chat["oneOf"]) == 4
        assert len(responses["oneOf"]) == 4
        assert branch_actions(chat) == branch_actions(responses) == list(chat["properties"]["action"]["enum"])
        for wire in (chat, responses):
            assert "anyOf" not in wire["properties"]["input"]
            assert "oneOf" not in wire["properties"]["input"]
            assert "allOf" not in wire
    finally:
        agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Provider-wire blindness regression (SONNET_DIAGNOSTIC_CONTRACT.md "Required
# design" #1/#3): the diagnostic sidecar (assumed ``ChildTool.diagnostics``,
# see ``test_tool_family_generic.py``) is passive and dispatch-time-only. It
# must never be reachable through ``build_schema()`` or either provider wire
# builder — those only ever read a child's ``input_schema``/``title``.
# ---------------------------------------------------------------------------


def test_diagnostics_sidecar_never_reaches_either_provider_wire():
    """A fake family's child opts into a diagnostic sidecar (keyed by
    structural trigger, per ``ChildTool.diagnostics: Mapping[str,
    DiagnosticDescriptor]``) with distinctive descriptor text; neither the
    composed schema nor either provider wire representation may contain that
    text, or even the word "diagnostics"."""
    secret_marker = "WIDGET_SPIN_UNSUPPORTED_INPUT_FIELD_should_never_reach_a_wire"
    descriptor = DiagnosticDescriptor(
        code=secret_marker,
        expected_form="an input object containing only speed",
        reason="spin rejects foreign action input before it can spin",
        fix="remove the foreign field or choose the action that owns it",
    )

    def spin_handler(input_):
        return {"status": "ok"}

    child = ChildTool(
        "spin",
        {
            "type": "object",
            "properties": {"speed": {"type": "integer"}},
            "required": ["speed"],
            "additionalProperties": False,
        },
        spin_handler,
        title="spin input",
        diagnostics={TRIGGER_UNSUPPORTED_INPUT_FIELD: descriptor},
    )
    fam = ToolFamily("widget", [child])
    schema = fam.build_schema()

    dumped_schema = json.dumps(schema)
    assert "diagnostics" not in dumped_schema
    assert secret_marker not in dumped_schema
    assert descriptor.reason not in dumped_schema
    assert descriptor.fix not in dumped_schema
    assert descriptor.expected_form not in dumped_schema

    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    function_schema = FunctionSchema(name="widget", description="widget", parameters=schema)
    chat = _build_tools([function_schema])[0]
    responses = _build_responses_tools([function_schema])[0]
    for wire in (chat, responses):
        dumped_wire = json.dumps(wire)
        assert "diagnostics" not in dumped_wire
        assert secret_marker not in dumped_wire
        assert descriptor.reason not in dumped_wire
        assert descriptor.fix not in dumped_wire


def test_context_molt_diagnostic_descriptor_never_reaches_either_provider_wire(tmp_path):
    """Real-family regression: once ``context.molt`` opts into its own local
    diagnostic descriptor for a foreign ``files`` input field
    (`tools/context/__init__.py`, per the shared contract's "Required
    design" #6), that descriptor's own code/text must still never appear on
    either provider wire for the real, live-agent-composed ``context``
    schema — the sidecar is dispatch-time-only and never schema-composed."""
    from lingtai.agent import Agent
    from lingtai.kernel.base_agent.tools import _build_tool_schemas
    from tests._service_helpers import make_gemini_mock_service as make_mock_service

    agent = Agent(
        service=make_mock_service(), agent_name="wire-blindness-test",
        working_dir=tmp_path,
    )
    try:
        from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

        schemas = [s for s in _build_tool_schemas(agent) if s.name == "context"]
        chat = _build_tools(schemas)[0]
        responses = _build_responses_tools(schemas)[0]
        for wire in (chat, responses):
            dumped = json.dumps(wire)
            assert "diagnostics" not in dumped
            assert "CTX_MOLT_UNSUPPORTED_INPUT_FIELD" not in dumped
            assert "molt rejects foreign action input" not in dumped
    finally:
        agent.stop(timeout=1.0)
