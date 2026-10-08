"""Web-owned composition-port and manual-contract regressions.

These tests stay beside the Web family and prove the typed ``web_runtime``
boundary, its fail-closed negative cases, and the real Agent bind path through
the declaration-scoped ``extra_ports_for`` seam.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.agent import Agent
from lingtai.kernel.tool_plugin import HostPortError, ToolPluginHost
from tests._service_helpers import make_mock_llm_service
from lingtai.tools.web_search import (
    DECLARATION,
    WebComposition,
    WebCompositionPort,
    _EngineSpec,
    _bind,
    setup,
)


class _BrowserPort:
    pass


class _Workdir:
    def __init__(self, path: Path) -> None:
        self.path = path


class _ProviderIdentity:
    provider = "gemini"


def _composition(tmp_path: Path) -> WebComposition:
    return WebComposition(
        browser_port=_BrowserPort(),
        specs={"duckduckgo": _EngineSpec("duckduckgo", provider="duckduckgo")},
        default_engine="duckduckgo",
        default_source="built_in_default",
        legacy_fallback_from=None,
    )


def _host(tmp_path: Path, **ports: object) -> ToolPluginHost:
    return ToolPluginHost(
        "web",
        {
            "workdir": _Workdir(tmp_path),
            "provider_identity": _ProviderIdentity(),
            **ports,
        },
    )


def test_web_composition_port_is_narrow_and_publishes_one_manager(tmp_path):
    value = _composition(tmp_path)
    assert isinstance(value, WebCompositionPort)
    assert value.specs["duckduckgo"].provider == "duckduckgo"
    assert value.manager is None

    bound = _bind(_host(tmp_path, web_runtime=value))
    assert bound.name == "web"
    assert value.manager is not None
    assert bound.handler == value.manager.handle
    assert not hasattr(value, "agent")
    assert not hasattr(value, "service")


def test_web_declaration_requires_exactly_its_three_ports():
    assert DECLARATION.requires == ("workdir", "web_runtime", "provider_identity")
    assert DECLARATION.public_actions == ("search", "browse", "settings", "manual")


def test_web_bind_missing_host_port_fails_closed(tmp_path):
    with pytest.raises(HostPortError, match="web_runtime"):
        _bind(_host(tmp_path))


def test_web_bind_refuses_a_legacy_runtime_carrier(tmp_path):
    """There is no fallback to any other carrier than the typed ``web_runtime``."""
    class _Carrier:
        def __init__(self, value: object) -> None:
            self.value = value

    with pytest.raises(HostPortError, match="web_runtime"):
        _bind(_host(tmp_path, runtime=_Carrier(_composition(tmp_path))))


def test_web_bind_wrong_host_port_fails_typed_contract(tmp_path):
    with pytest.raises(HostPortError, match="typed WebComposition"):
        _bind(_host(tmp_path, web_runtime=object()))


def test_web_grant_without_setup_composition_fails_before_bind(tmp_path):
    """The standard table never carries ``web_runtime``; only ``setup`` grants it."""
    from lingtai.adapters.tool_plugin_host import agent_host_ports

    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="web-composition-port-grant",
        working_dir=tmp_path / "agent",
        capabilities={},
    )
    try:
        table = agent_host_ports(agent, "web")
        assert "web_runtime" not in table
        assert table["provider_identity"].provider == agent.service.provider
        with pytest.raises(HostPortError):
            ToolPluginHost.grant(DECLARATION, table)
    finally:
        agent.stop(timeout=1.0)


def test_web_composition_publishes_one_manager_only(tmp_path):
    value = _composition(tmp_path)
    bound = _bind(_host(tmp_path, web_runtime=value))
    first = value.manager
    assert first is not None
    assert bound.handler == first.handle
    # Re-publishing the same manager is idempotent; a different one is refused.
    value.publish_manager(first)
    from lingtai.kernel.tool_plugin import ToolPluginError

    with pytest.raises(ToolPluginError):
        _bind(_host(tmp_path, web_runtime=value))
    assert value.manager is first


def test_web_real_agent_bind_returns_manager_and_preserves_manual_surface(tmp_path):
    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="web-composition-port",
        working_dir=tmp_path / "agent",
        capabilities={},
    )
    try:
        manual_dir = agent.working_dir / ".library" / "intrinsic" / "capabilities" / "web"
        manual_dir.mkdir(parents=True, exist_ok=True)
        (manual_dir / "SKILL.md").write_text("# Web manual\n", encoding="utf-8")
        manager = setup(agent, browser_port=_BrowserPort())
        assert manager._family.has_manual()
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert agent._tool_handlers["web"] == manager.handle
        result = manager.handle(
            {"action": "manual", "input": {}, "reasoning": "load guidance"}
        )
        assert result["status"] == "ok"
        assert result["manual"] == "# Web manual\n"
    finally:
        agent.stop(timeout=1.0)


def _real_web_agent(tmp_path: Path) -> Agent:
    return Agent(
        service=make_mock_llm_service(),
        agent_name="web-manual-contract",
        working_dir=tmp_path / "agent",
        capabilities={},
    )


def _write_web_manual(agent: Agent, body: str) -> Path:
    path = agent.working_dir / ".library" / "intrinsic" / "capabilities" / "web" / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_web_manual_child_returns_canonical_result_before_flat_adaptation(tmp_path):
    agent = _real_web_agent(tmp_path)
    try:
        path = _write_web_manual(agent, "# Web manual\nFull body here.")
        manager = setup(agent, browser_port=_BrowserPort())
        envelope = {"action": "manual", "input": {}, "reasoning": "load guidance"}
        canonical = manager._family.handle(envelope)
        assert canonical["status"] == "ok"
        assert canonical["content"] == [{"type": "text", "text": "# Web manual\nFull body here."}]
        assert canonical["structuredContent"] == {"manual_path": str(path)}
        assert "manual" not in canonical

        flat = manager.handle(envelope)
        assert flat["status"] == "ok"
        assert flat["manual"] == "# Web manual\nFull body here."
        assert flat["manual_path"] == str(path)
        assert "content" not in flat
    finally:
        agent.stop(timeout=1.0)


def test_web_manual_child_missing_file_preserves_degraded_public_shape(tmp_path):
    agent = _real_web_agent(tmp_path)
    try:
        path = agent.working_dir / ".library" / "intrinsic" / "capabilities" / "web" / "SKILL.md"
        path.unlink(missing_ok=True)
        manager = setup(agent, browser_port=_BrowserPort())
        result = manager.handle(
            {"action": "manual", "input": {}, "reasoning": "load guidance"}
        )
        assert result["status"] == "degraded"
        assert result["manual"] == ""
        assert result["manual_path"] == str(path)
        assert result["action"] == "manual"
        assert "error" in result
        assert "content" not in result
    finally:
        agent.stop(timeout=1.0)


def test_web_manual_child_rejects_nonempty_input_on_real_agent_bind(tmp_path):
    agent = _real_web_agent(tmp_path)
    try:
        manager = setup(agent, browser_port=_BrowserPort())
        result = manager.handle(
            {
                "action": "manual",
                "input": {"topic": "search"},
                "reasoning": "load guidance",
            }
        )
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
    finally:
        agent.stop(timeout=1.0)
