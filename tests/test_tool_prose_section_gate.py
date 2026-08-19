"""``LINGTAI_TOOL_PROSE_SECTION_ENABLED`` — the resident ``## tools`` gate.

The resident ``## tools`` prose walkthrough renders the exact same text each
tool's tool-calling schema already carries as its top-level ``description``.
Rendering both puts two copies of every tool's prose into one turn of context;
on the CLI-backed adapters (``claude_code``/``kimi_code``), which serialise the
full schema description into their own ``# AVAILABLE TOOLS`` block right beside
the composed system prompt, the two copies are byte-identical.

The section is therefore OPT-IN and DEFAULT OFF. This module pins both states
end to end on a real ``Agent``: what the composed system prompt contains, what
the provider wire carries, and that the prose reaches the model exactly once
either way.
"""
from __future__ import annotations

import json

import pytest

from lingtai.agent import Agent
from lingtai.kernel.base_agent.tools import _build_tool_schemas
from lingtai.kernel.config import (
    TOOL_PROSE_SECTION_ENABLED_ENV,
    tool_prose_section_enabled,
)
from lingtai.kernel.llm.base import WIRE_TOOL_DESCRIPTION, wire_tool_description
from tests._service_helpers import make_gemini_mock_service as make_mock_service


# ---------------------------------------------------------------------------
# The switch itself
# ---------------------------------------------------------------------------


def test_default_is_off(monkeypatch):
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    assert tool_prose_section_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_truthy_values_opt_in(monkeypatch, value):
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, value)
    assert tool_prose_section_enabled() is True


@pytest.mark.parametrize("value", ["", "  ", "0", "off", "false", "no", "maybe", "2"])
def test_everything_else_stays_off(monkeypatch, value):
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, value)
    assert tool_prose_section_enabled() is False


def test_gate_is_read_per_call_not_cached(monkeypatch):
    """An ``env_file`` flip must apply at the next rebuild without a restart."""
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    assert wire_tool_description("PROSE") == "PROSE"
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")
    assert wire_tool_description("PROSE") == WIRE_TOOL_DESCRIPTION
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    assert wire_tool_description("PROSE") == "PROSE"


# ---------------------------------------------------------------------------
# End-to-end: a real Agent's composed system prompt in both states
# ---------------------------------------------------------------------------


def _probe(tmp_path, name):
    """Boot a real Agent and return (composed prompt, tools section, schemas)."""
    agent = Agent(
        service=make_mock_service(), agent_name=name, working_dir=tmp_path / name
    )
    try:
        return (
            agent._prompt_manager.render(),
            agent._prompt_manager.read_section("tools"),
            _build_tool_schemas(agent),
        )
    finally:
        agent.stop(timeout=1.0)


def test_default_prompt_omits_the_prose_section(tmp_path, monkeypatch):
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    prompt, section, schemas = _probe(tmp_path, "off")

    assert section is None
    assert "## tools" not in prompt
    # Every tool still ships its prose — on the schema, which is what the model
    # is actually given to call the tool with.
    assert schemas, "the probe agent must register tools"
    for schema in schemas:
        assert schema.description
        assert schema.description not in prompt


def test_opt_in_restores_the_prose_section(tmp_path, monkeypatch):
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")
    prompt, section, schemas = _probe(tmp_path, "on")

    assert section
    assert "## tools" in prompt
    for schema in schemas:
        assert f"### {schema.name}" in section


def test_opt_in_prompt_is_strictly_larger_and_default_loses_nothing_else(
    tmp_path, monkeypatch
):
    """The gate changes the ``tools`` section and nothing else in the prompt.

    Both probes reuse one working directory so identity/meta text is
    byte-identical and the only surviving difference is the gated section.
    """
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    off_prompt, _, off_schemas = _probe(tmp_path, "delta")
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")
    on_prompt, on_section, on_schemas = _probe(tmp_path, "delta")

    assert len(on_prompt) > len(off_prompt)
    # Removing the gated block from the opted-in prompt reproduces the default
    # prompt exactly: nothing else in the composition moved.
    assert on_prompt.replace(f"## tools\n{on_section}\n\n", "", 1) == off_prompt

    # The tool-calling schemas — names, descriptions, parameters — are byte
    # identical in both states. Only the *prompt* differs.
    assert [s.name for s in off_schemas] == [s.name for s in on_schemas]
    for off, on in zip(off_schemas, on_schemas):
        assert off.description == on.description
        assert json.dumps(off.parameters, sort_keys=True) == json.dumps(
            on.parameters, sort_keys=True
        )


def test_prose_reaches_the_model_exactly_once_in_both_states(tmp_path, monkeypatch):
    """Union of system prompt + wire payload contains each tool's prose once."""
    from lingtai.llm.openai.adapter import _build_tools as openai_tools

    for enabled in (False, True):
        if enabled:
            monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")
        else:
            monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
        prompt, _, schemas = _probe(tmp_path, f"once-{enabled}")
        wire = json.dumps(openai_tools(schemas), ensure_ascii=False)
        for schema in schemas:
            in_prompt = schema.description in prompt
            in_wire = json.dumps(schema.description, ensure_ascii=False)[1:-1] in wire
            assert in_prompt != in_wire, (
                f"{schema.name} prose is on "
                f"{'both surfaces' if in_prompt else 'neither surface'} "
                f"(enabled={enabled})"
            )
