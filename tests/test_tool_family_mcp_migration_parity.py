"""Parity evidence for the `mcp` ToolFamily migration.

`mcp` keeps its public tool name and adds its opted-in SHOW child between the
existing `info` / `manual` actions in the LTP v2 action-separated envelope
(`action` + `input` + `reasoning` + `summarize`) built on the generic
`lingtai.tools.tool_family` infrastructure.

What these tests pin, beyond the pre-existing `tests/test_mcp_capability.py`
and `tests/test_mcp_identity_discovery.py` behavior suites:

- the composed schema's exact public surface and Chat/Responses wire parity;
- that all actions declare a canonical strict-empty `input`, so any extra
  input field fails *before* the handler performs any I/O;
- that `info` still only re-reads the registry (no manual body, no mutation);
- that `manual` returns the exact manual body/path with no registry rescan or
  registry mutation, flattened back to mcp's own `mcp_manual` public shape
  with no double wrap;
- that the unknown-action envelope keeps its established shape and malformed
  action handling while listing the additive settings action.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.agent import Agent
from lingtai.services.mcp_registry import REGISTRY_FILENAME
from lingtai.tools.mcp import get_schema
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)

_UNKNOWN_ACTION_HINT = "only 'info', 'settings', or 'manual' is supported"


@pytest.fixture
def mcp_agent(tmp_path):
    agent = Agent(
        service=make_mock_service(),
        agent_name="mcp-family-parity",
        working_dir=tmp_path / "agent",
        capabilities={"mcp": {}},
        addons=["imap"],
    )
    try:
        yield agent, tmp_path / "agent"
    finally:
        agent.stop(timeout=1.0)


def _handler(agent):
    handler = agent._tool_handlers.get("mcp")
    assert handler is not None
    return handler


# ---------------------------------------------------------------------------
# Composed schema surface
# ---------------------------------------------------------------------------

def test_schema_exposes_exact_public_actions_and_envelope():
    schema = get_schema()
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["properties"]["action"]["enum"] == ["info", "settings", "manual"]
    # `reasoning` is required Host metadata declared by the family itself.
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["properties"]["reasoning"]["type"] == "string"
    assert schema["properties"]["summarize"]["type"] == "boolean"


def test_all_actions_declare_canonical_strict_empty_input():
    from lingtai.tools.tool_family.manual import MANUAL_INPUT_SCHEMA

    schema = get_schema()
    assert branch_actions(schema) == ["info", "settings", "manual"]
    branches = action_input_schemas(schema).values()
    for branch in branches:
        assert branch["type"] == "object"
        assert branch["properties"] == {}
        assert branch["additionalProperties"] is False
        assert "required" not in branch or branch["required"] == []
        # Root envelope fields never leak into a child input branch.
        for leaked in ("action", "reasoning", "_reasoning", "summarize"):
            assert leaked not in branch["properties"]
    # Every public child uses the canonical strict-empty shape, embedded
    # verbatim (no ``title`` or other presentational addition).
    for branch in branches:
        assert branch == MANUAL_INPUT_SCHEMA


def test_schema_only_and_dispatching_families_declare_identical_children(mcp_agent):
    """The module-level schema-only family and the real host-bound one are
    built from a single child-list source, so their advertised input shapes
    cannot drift apart."""
    from lingtai.adapters.tool_plugin_host import agent_host_ports
    from lingtai.kernel.tool_plugin import ToolPluginHost
    from lingtai.tools.mcp import DECLARATION, _build_family

    agent, _ = mcp_agent
    # The dispatching family is built against the same least-privilege facade
    # the kernel registrar grants at boot — not against the Agent.
    host = ToolPluginHost.grant(DECLARATION, agent_host_ports(agent, "mcp"))
    schema_only = _build_family(None).build_schema()
    dispatching = _build_family(host).build_schema()
    assert schema_only == dispatching


def test_schema_correlates_each_action_const_to_its_own_input():
    """The root ``oneOf`` is the discriminated union: three branches, each
    keyed by a distinct ``action`` const — which is exactly what keeps three
    byte-identical strict-empty inputs unambiguous."""
    schema = get_schema()
    assert_compact_envelope(schema, ["info", "settings", "manual"])
    assert len(schema["oneOf"]) == 3
    seen = {}
    for action, branch in zip(["info", "settings", "manual"], schema["oneOf"]):
        assert set(branch["properties"]) == {"action", "input"}
        assert branch["properties"]["action"] == {"const": action}
        seen[action] = branch["properties"]["input"]
    assert set(seen) == {"info", "settings", "manual"}
    for action_input in seen.values():
        assert action_input["additionalProperties"] is False
        assert action_input["properties"] == {}


def test_schema_survives_chat_and_responses_wires():
    from lingtai.kernel.llm.base import FunctionSchema
    from lingtai.llm.openai.adapter import _build_responses_tools, _build_tools

    fn = FunctionSchema(name="mcp", description="mcp", parameters=get_schema())
    chat = _build_tools([fn])[0]["function"]["parameters"]
    responses = _build_responses_tools([fn])[0]["parameters"]

    # Chat Completions passes the composed schema through byte-for-byte.
    assert chat == get_schema()
    for wire in (chat, responses):
        assert wire["required"] == ["action", "input", "reasoning"]
        assert wire["additionalProperties"] is False
        assert set(wire["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert wire["properties"]["action"]["enum"] == ["info", "settings", "manual"]
        # The root ``oneOf`` survives as ``oneOf`` on BOTH wires (the
        # Responses scrub only rewrites nested ``oneOf``); nothing is
        # duplicated under ``properties.input`` or a root ``allOf``.
        assert "anyOf" not in wire and "allOf" not in wire
        for duplicate in ("oneOf", "anyOf", "allOf"):
            assert duplicate not in wire["properties"]["input"]
        assert branch_actions(wire) == ["info", "settings", "manual"]
    # Identical per-action mapping on both wires.
    assert chat["oneOf"] == responses["oneOf"]


def test_real_agent_registers_exactly_one_public_mcp_tool(mcp_agent):
    from lingtai.kernel.base_agent.tools import _build_tool_schemas

    agent, _ = mcp_agent
    schemas = [s for s in _build_tool_schemas(agent) if s.name == "mcp"]
    assert len(schemas) == 1
    params = schemas[0].parameters
    assert params["properties"]["action"]["enum"] == ["info", "settings", "manual"]
    assert params["required"] == ["action", "input", "reasoning"]


# ---------------------------------------------------------------------------
# info: read-only registry behavior, unchanged result
# ---------------------------------------------------------------------------

def test_info_returns_health_snapshot_without_manual_body(mcp_agent):
    agent, _ = mcp_agent
    result = _handler(agent)(
        {"action": "info", "input": {}, "reasoning": "check registry health"}
    )
    assert set(result) == {
        "status",
        "registry_path",
        "registered_count",
        "registered",
        "problems",
    }
    assert result["status"] == "ok"
    assert result["registered_count"] == 1
    assert result["registered"][0]["name"] == "imap"
    # `info` never carries the manual body — the signpost split is preserved.
    assert "mcp_manual" not in result
    assert "content" not in result and "structuredContent" not in result


def test_info_re_reads_registry_and_does_not_mutate_it(mcp_agent, monkeypatch):
    import lingtai.services.mcp_registry as registry_service

    agent, workdir = mcp_agent
    registry_path = workdir / REGISTRY_FILENAME
    before = registry_path.read_bytes()

    reads: list[Path] = []
    original = registry_service.read_registry

    def _counting_read(working_dir):
        reads.append(working_dir)
        return original(working_dir)

    monkeypatch.setattr(registry_service, "read_registry", _counting_read)

    result = _handler(agent)(
        {"action": "info", "input": {}, "reasoning": "re-read the registry"}
    )
    assert result["status"] == "ok"
    # `info` re-reads the registry from disk on every call ...
    assert len(reads) == 1
    # ... and never writes it.
    assert registry_path.read_bytes() == before


# ---------------------------------------------------------------------------
# manual: exact body/path, no registry side effect, no double wrap
# ---------------------------------------------------------------------------

def test_manual_returns_exact_body_and_path(mcp_agent):
    agent, workdir = mcp_agent
    result = _handler(agent)(
        {"action": "manual", "input": {}, "reasoning": "load mcp guidance"}
    )
    # mcp's own flat public shape survives the migration verbatim: the body key
    # stays the tool-specific `mcp_manual`, not the generic `manual`.
    assert set(result) == {"status", "mcp_manual", "manual_path"}
    assert result["status"] == "ok"
    expected_path = (
        workdir / ".library" / "intrinsic" / "capabilities" / "mcp" / "SKILL.md"
    )
    assert result["manual_path"] == str(expected_path)
    assert result["mcp_manual"] == expected_path.read_text(encoding="utf-8")
    assert result["mcp_manual"]


def test_manual_result_is_not_double_wrapped(mcp_agent):
    agent, _ = mcp_agent
    result = _handler(agent)(
        {"action": "manual", "input": {}, "reasoning": "load mcp guidance"}
    )
    # The canonical child result is adapted by the Host, not nested inside
    # another action result.
    assert "content" not in result
    assert "structuredContent" not in result
    for value in result.values():
        assert not (isinstance(value, dict) and "status" in value)


def test_manual_performs_no_registry_rescan_or_mutation(mcp_agent, monkeypatch):
    import lingtai.services.mcp_registry as registry_service

    agent, workdir = mcp_agent
    registry_path = workdir / REGISTRY_FILENAME
    before = registry_path.read_bytes()

    def _fail(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("manual must not touch the MCP registry")

    monkeypatch.setattr(registry_service, "read_registry", _fail)
    monkeypatch.setattr(registry_service, "read_identities", _fail)

    result = _handler(agent)(
        {"action": "manual", "input": {}, "reasoning": "load mcp guidance"}
    )
    assert result["status"] == "ok"
    assert registry_path.read_bytes() == before


def test_manual_reports_degraded_when_manual_missing(mcp_agent):
    agent, workdir = mcp_agent
    manual_path = (
        workdir / ".library" / "intrinsic" / "capabilities" / "mcp" / "SKILL.md"
    )
    manual_path.unlink()

    result = _handler(agent)(
        {"action": "manual", "input": {}, "reasoning": "load mcp guidance"}
    )
    # Truthful degraded shape is preserved, including the exact error text.
    assert result["status"] == "degraded"
    assert result["mcp_manual"] == ""
    assert result["manual_path"] == str(manual_path)
    assert result["error"] == (
        "mcp manual missing — initializer may have failed or capability not "
        "installed correctly"
    )


# ---------------------------------------------------------------------------
# Envelope enforcement: fail before handler I/O
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["info", "settings", "manual"])
def test_extra_input_field_is_rejected_before_any_io(mcp_agent, monkeypatch, action):
    import lingtai.services.mcp_registry as registry_service
    import lingtai.tools.tool_family.manual as manual_child_module

    agent, workdir = mcp_agent
    registry_path = workdir / REGISTRY_FILENAME
    before = registry_path.read_bytes()

    def _fail(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("invalid input must fail before handler I/O")

    monkeypatch.setattr(registry_service, "read_registry", _fail)
    monkeypatch.setattr(registry_service, "read_identities", _fail)
    # Patch the name the manual child actually calls: `tool_family/manual.py`
    # binds `load_installed_manual` at module import, so patching
    # `lingtai.tools._manual` would never intercept it.
    monkeypatch.setattr(manual_child_module, "load_installed_manual", _fail)

    result = _handler(agent)(
        {"action": action, "input": {"bogus": 1}, "reasoning": "invalid call"}
    )
    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "unsupported mcp input field",
    }
    assert registry_path.read_bytes() == before


def test_non_object_input_is_rejected(mcp_agent):
    agent, _ = mcp_agent
    result = _handler(agent)(
        {"action": "info", "input": "not-an-object", "reasoning": "invalid call"}
    )
    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "input must be an object",
    }


def test_unknown_root_field_is_rejected(mcp_agent):
    agent, _ = mcp_agent
    result = _handler(agent)(
        {"action": "info", "input": {}, "reasoning": "why", "bogus": 1}
    )
    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "unsupported mcp argument",
    }


def test_summarize_is_root_only_and_type_checked(mcp_agent):
    agent, _ = mcp_agent
    handler = _handler(agent)

    bad = handler(
        {"action": "info", "input": {}, "reasoning": "why", "summarize": "yes"}
    )
    assert bad == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "summarize must be a boolean",
    }

    # A valid root `summarize` is stripped before dispatch and never reaches
    # the action, so the action result is unchanged by it.
    plain = handler({"action": "info", "input": {}, "reasoning": "why"})
    summarized = handler(
        {"action": "info", "input": {}, "reasoning": "why", "summarize": True}
    )
    assert summarized == plain

    # `summarize` inside `input` is action input, not the root control.
    nested = handler(
        {"action": "info", "input": {"summarize": True}, "reasoning": "why"}
    )
    assert nested["error_code"] == "INVALID_ARGUMENT"


def test_canonical_summarize_spelling_is_registered_once_centrally():
    """`mcp` now declares a root `summarize`, so the one central registry of
    migrated LTP v2 families must recognize it — otherwise the control would
    be silently inert. There is no second summarizer."""
    from lingtai.kernel.tool_result_summary import (
        _LTP_V2_MIGRATED_FAMILIES,
        summary_requested,
    )

    assert "mcp" in _LTP_V2_MIGRATED_FAMILIES
    assert summary_requested({"summarize": True}, tool_name="mcp") is True
    # The legacy flag keeps working, and an unmigrated tool's own
    # `summarize`-named field is still never reinterpreted as this control.
    # ``bash`` is the legacy input alias for the migrated ``shell`` family and
    # is never itself on the allowlist, so it stands in for "unmigrated" here.
    assert summary_requested({"summary": True}, tool_name="mcp") is True
    assert summary_requested({"summarize": True}, tool_name="bash") is False


def test_mcp_error_results_are_never_summarized():
    """Both of mcp's failure envelopes must stay exact for recovery: the
    kernel-wide `error` status and the family `failed` status."""
    from lingtai.kernel.tool_result_summary import maybe_summarize_result

    def _boom(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("error results must not be summarized")

    for result in (
        {"status": "error", "message": "unknown action: 'x', ..."},
        {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "..."},
    ):
        assert (
            maybe_summarize_result(
                result,
                args={"action": "info", "input": {}, "summarize": True},
                tool_name="mcp",
                tool_call_id="c1",
                summarizer_fn=_boom,
            )
            is result
        )


# ---------------------------------------------------------------------------
# Unknown action parity
# ---------------------------------------------------------------------------

def test_unknown_action_envelope_preserves_shape_with_current_actions(mcp_agent):
    agent, _ = mcp_agent
    handler = _handler(agent)

    assert handler({"action": "register", "input": {}, "reasoning": "why"}) == {
        "status": "error",
        "message": f"unknown action: 'register', {_UNKNOWN_ACTION_HINT}",
    }
    # Missing `action` renders the empty-string default, not None.
    assert handler({}) == {
        "status": "error",
        "message": f"unknown action: '', {_UNKNOWN_ACTION_HINT}",
    }
    # Invalid JSON can make `action` unhashable (issue #513): the router must
    # render the unknown-action envelope rather than raising TypeError.
    assert handler({"action": []}) == {
        "status": "error",
        "message": f"unknown action: [], {_UNKNOWN_ACTION_HINT}",
    }
    assert handler({"action": {}}) == {
        "status": "error",
        "message": f"unknown action: {{}}, {_UNKNOWN_ACTION_HINT}",
    }


def test_unknown_action_is_rejected_before_input_validation(mcp_agent):
    """An unknown action wins over a malformed envelope, exactly as before the
    migration: the caller learns the action is unsupported, not that its input
    was the wrong shape."""
    agent, _ = mcp_agent
    result = _handler(agent)({"action": "register", "input": {"bogus": 1}})
    assert result == {
        "status": "error",
        "message": f"unknown action: 'register', {_UNKNOWN_ACTION_HINT}",
    }


def test_unknown_action_does_not_touch_the_registry(mcp_agent, monkeypatch):
    import lingtai.services.mcp_registry as registry_service

    agent, workdir = mcp_agent

    def _fail(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("an unknown action must perform no registry I/O")

    monkeypatch.setattr(registry_service, "read_registry", _fail)
    monkeypatch.setattr(registry_service, "read_identities", _fail)

    before = (workdir / REGISTRY_FILENAME).read_bytes()
    result = _handler(agent)({"action": "register", "input": {}, "reasoning": "why"})
    assert result["status"] == "error"
    assert (workdir / REGISTRY_FILENAME).read_bytes() == before


# ---------------------------------------------------------------------------
# External MCP direct insertion is untouched by this migration
# ---------------------------------------------------------------------------

def test_registry_file_remains_the_only_registration_channel(mcp_agent):
    """The tool stays signpost-only: direct insertion into
    `mcp_registry.jsonl` (by the agent, or by boot-time addon decompression)
    remains the sole registration channel, and `info` reflects it."""
    agent, workdir = mcp_agent
    registry_path = workdir / REGISTRY_FILENAME

    record = {
        "name": "extern",
        "summary": "externally inserted",
        "transport": "http",
        "url": "https://example.com/mcp",
        "source": "user",
    }
    with registry_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    result = _handler(agent)(
        {"action": "info", "input": {}, "reasoning": "check registry health"}
    )
    names = {entry["name"] for entry in result["registered"]}
    assert names == {"imap", "extern"}
    assert result["registered_count"] == 2
