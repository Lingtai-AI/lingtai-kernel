"""MCP's built-in Agent Plugin package stays a packaging-only host contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.agent import Agent
from lingtai.services.plugin_registry import read_plugin
from lingtai.tools.mcp import DECLARATION
from tests._service_helpers import make_mock_llm_service

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src/lingtai/tools/mcp"
_SKILL_ROOT = _PACKAGE_ROOT / "skills/mcp-manual"


def test_mcp_package_is_valid_and_owns_exactly_one_manual_skill():
    """The standard reader, not a tool-local helper, validates the package."""
    record, problems = read_plugin(_PACKAGE_ROOT)

    assert problems == []
    assert record is not None
    assert record["name"] == DECLARATION.name == "mcp"
    assert record["skills"] == ["mcp-manual"]
    assert record["skill_paths"] == [str(_SKILL_ROOT)]
    assert record["mcp_servers"] == []


@pytest.fixture
def mcp_agent(tmp_path):
    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="mcp-builtin-package",
        working_dir=tmp_path / "agent",
        capabilities={"mcp": {}},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def test_agent_mounts_the_owned_skill_without_plugin_registry_side_effects(mcp_agent):
    """Packaging supplies only the declared manual; MCP state remains external."""
    installed = mcp_agent.working_dir / ".library/intrinsic/capabilities/mcp/SKILL.md"
    assert installed.read_text(encoding="utf-8") == (_SKILL_ROOT / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert not (mcp_agent.working_dir / "mcp_registry.jsonl").exists()

    result = mcp_agent._tool_handlers["mcp"](
        {"action": "manual", "input": {}, "reasoning": "read the owned manual"}
    )
    assert result["status"] == "ok"
    assert result["manual_path"] == str(installed)
