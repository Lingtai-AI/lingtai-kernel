"""``email``'s composed family schema must survive both OpenAI wires intact.

The generic wire proof (``tests/test_tool_family_wire_parity.py``) covers the
infrastructure using ``web``. This file is ``email``'s own: it proves the same
seam holds for Email's 15-child public registry, through
a **real Agent startup** — so what is asserted is the schema the model actually
receives after ``_build_tool_schemas`` composition, not a unit-level
``get_schema()`` dict.

Both provider builders are exercised at the existing adapter seam with no
adapter code changes; the Responses builder is the interesting one, because
``_scrub_responses_schema`` is where a root ``allOf`` would be dropped if that
route did not accept it.
"""
from __future__ import annotations

from lingtai.kernel.base_agent.tools import _build_tool_schemas
from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

_PUBLIC_ACTIONS = [
    "send", "check", "read", "dismiss", "reply", "reply_all",
    "search", "archive", "delete",
    "contacts", "add_contact", "remove_contact", "edit_contact",
    "settings",
    "manual",
]


def _email_schema(tmp_path):
    """Build a real Agent and return its registered ``email`` FunctionSchema."""
    from lingtai.agent import Agent
    from tests._service_helpers import make_mock_llm_service as make_mock_service

    agent = Agent(
        service=make_mock_service(),
        agent_name="email-wire-parity-test",
        working_dir=tmp_path,
    )
    try:
        schemas = _build_tool_schemas(agent)
        return next(s for s in schemas if s.name == "email")
    finally:
        agent.stop()


def test_email_is_exactly_one_model_facing_tool(tmp_path):
    """Children consume no model tool slots: 15 actions, one advertised tool."""
    from lingtai.agent import Agent
    from tests._service_helpers import make_mock_llm_service as make_mock_service

    agent = Agent(
        service=make_mock_service(),
        agent_name="email-count-test",
        working_dir=tmp_path,
    )
    try:
        names = [s.name for s in _build_tool_schemas(agent)]
    finally:
        agent.stop()

    assert names.count("email") == 1
    # No child leaked out as its own root tool.
    for action in _PUBLIC_ACTIONS:
        assert f"email_{action}" not in names
        if action not in ("check", "read", "search", "manual", "delete"):
            # (those words are legitimate action names of *other* families)
            assert action not in names


def test_closed_root_survives_both_wires(tmp_path):
    email = _email_schema(tmp_path)
    chat = _build_tools([email])[0]["function"]["parameters"]
    responses = _build_responses_tools([email])[0]["parameters"]

    for params in (chat, responses):
        assert set(params["properties"]) == {
            "action", "input", "reasoning", "summarize"
        }
        assert params["additionalProperties"] is False
        assert set(params["required"]) == {"action", "input", "reasoning"}
        assert params["properties"]["action"]["enum"] == _PUBLIC_ACTIONS
        assert params["properties"]["reasoning"]["type"] == "string"


def test_action_input_all_of_correlation_survives_both_wires(tmp_path):
    """Root ``allOf`` correlation must reach the model identically on both routes."""
    email = _email_schema(tmp_path)
    chat = _build_tools([email])[0]["function"]["parameters"]
    responses = _build_responses_tools([email])[0]["parameters"]

    for params in (chat, responses):
        conditions = params["allOf"]
        assert [
            c["if"]["properties"]["action"]["const"] for c in conditions
        ] == _PUBLIC_ACTIONS
        for condition in conditions:
            assert condition["if"]["required"] == ["action"]
            assert "input" in condition["then"]["properties"]


def test_every_action_branch_is_disclosed_on_both_wires(tmp_path):
    """All 15 branches reach the model on both routes, with closed inputs.

    Settings opt-in makes the composed input union ``anyOf`` on both routes.
    This asserts the branch set and their closedness on both wires.
    """
    email = _email_schema(tmp_path)
    chat = _build_tools([email])[0]["function"]["parameters"]
    responses = _build_responses_tools([email])[0]["parameters"]

    assert "anyOf" in chat["properties"]["input"]
    assert "anyOf" in responses["properties"]["input"]

    for params in (chat, responses):
        node = params["properties"]["input"]
        branches = node["anyOf"]
        assert [b["title"] for b in branches] == [
            "settings inventory input" if a == "settings" else f"{a} input"
            for a in _PUBLIC_ACTIONS
        ]
        for branch in branches:
            assert branch["additionalProperties"] is False


def test_handler_parity_dispatches_through_the_registered_intrinsic(tmp_path):
    """The registered handler and the module entry point are the same boundary."""
    from lingtai.agent import Agent
    from tests._service_helpers import make_mock_llm_service as make_mock_service

    agent = Agent(
        service=make_mock_service(),
        agent_name="email-handler-parity-test",
        working_dir=tmp_path,
    )
    try:
        # A read-only action through the real registered intrinsic handler.
        result = agent._intrinsics["email"]({"action": "contacts", "input": {}})
        assert result == {"status": "ok", "contacts": []}
        # And the same envelope's cross-action rejection at that same seam.
        rejected = agent._intrinsics["email"](
            {"action": "contacts", "input": {"query": "x"}}
        )
        assert rejected["error_code"] == "INVALID_ARGUMENT"
    finally:
        agent.stop()
