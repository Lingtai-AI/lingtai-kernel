"""Focused vertical proof for Web's declared host-plugin mount."""
from __future__ import annotations

from lingtai.agent import Agent
from tests._service_helpers import make_mock_llm_service


def test_official_web_mount_preserves_declared_surface_and_packaged_manual(tmp_path):
    from lingtai.tools.web_search import DECLARATION

    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="web-official-plugin",
        working_dir=tmp_path / "agent",
        capabilities={"web": {}},
    )
    try:
        assert DECLARATION.requires == ("workdir", "web_runtime", "provider_identity")
        assert DECLARATION.actions == ("search", "browse")
        assert DECLARATION.public_actions == ("search", "browse", "settings", "manual")
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("web") == 1

        schema = agent._tool_schemas[[s.name for s in agent._tool_schemas].index("web")]
        assert schema.parameters["properties"]["action"]["enum"] == [
            "search", "browse", "settings", "manual",
        ]

        manual = agent._tool_handlers["web"](
            {"action": "manual", "input": {}, "reasoning": "guidance"}
        )
        assert manual["status"] == "ok"
        assert manual["manual"]
        assert manual["manual_path"].endswith("capabilities/web/SKILL.md")
    finally:
        agent.stop(timeout=1.0)


def test_official_web_provider_identity_is_a_narrow_read_through_label(tmp_path):
    """The granted label follows the live service and never exposes it."""
    from lingtai.adapters.tool_plugin_host import (
        AgentProviderIdentityAdapter,
        agent_host_ports,
    )

    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="web-official-plugin-identity",
        working_dir=tmp_path / "agent",
        capabilities={"web": {}},
    )
    try:
        table = agent_host_ports(agent, "web")
        port = table["provider_identity"]
        assert isinstance(port, AgentProviderIdentityAdapter)
        assert port.provider == agent.service.provider == "anthropic"
        assert sorted(name for name in dir(port) if not name.startswith("_")) == ["provider"]
        assert "web_runtime" not in table
        assert "active_provider" not in table

        # A non-string label is reported as ``None``, never coerced.
        assert AgentProviderIdentityAdapter(lambda: None).provider is None
        assert AgentProviderIdentityAdapter(lambda: 42).provider is None
    finally:
        agent.stop(timeout=1.0)


def test_official_web_refresh_re_claims_the_same_declaration(tmp_path):
    """A refresh re-registers the identical static declaration idempotently."""
    from lingtai.tools.web_search import DECLARATION, WebManager

    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="web-official-plugin-refresh",
        working_dir=tmp_path / "agent",
        capabilities={"web": {}},
    )
    try:
        first = agent._tool_handlers["web"].__self__
        assert isinstance(first, WebManager)
        agent._perform_refresh()
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("web") == 1
        second = agent._tool_handlers["web"].__self__
        assert isinstance(second, WebManager)
    finally:
        agent.stop(timeout=1.0)
