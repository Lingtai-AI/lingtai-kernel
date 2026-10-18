"""Tests for Agent — capabilities layer."""
from __future__ import annotations

import pytest
from lingtai.agent import Agent
from lingtai.services.vision import VisionService
from lingtai.services.websearch import SearchService, SearchResult
from tests._service_helpers import make_mock_llm_service as make_mock_service




class FakeVisionService(VisionService):
    def analyze_image(self, image_path, prompt=None):
        return "fake"


class FakeSearchService(SearchService):
    def search(self, query, max_results=5):
        return [SearchResult(title="t", url="u", snippet="s")]


def test_agent_no_capabilities_boots_core_floor(tmp_path):
    """Agent with no explicit capabilities still boots the always-on tool floor.

    The default-on set covers knowledge/skills/shell/avatar/daemon/mcp;
    vision is always registered (provider-bound, defaults to the active LLM).
    All shipped selectable builtins (including web) are enabled when omitted.
    """
    from lingtai.tools.registry import BUILTIN_TOOLS, CORE_DEFAULTS
    assert set(CORE_DEFAULTS) == set(BUILTIN_TOOLS)
    agent = Agent(service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test")
    registered = {name for name, _ in agent._capabilities}
    assert registered == set(CORE_DEFAULTS), (
        f"expected exactly the core defaults, got {registered - set(CORE_DEFAULTS)} extra / "
        f"{set(CORE_DEFAULTS) - registered} missing"
    )
    manifest_registered = {
        name for name, _ in agent._build_manifest().get("capabilities", [])
    }
    for name in CORE_DEFAULTS:
        assert agent.has_capability(name) is True
        assert name in manifest_registered
    assert "vision" in registered
    assert "web" in registered
    from lingtai.adapters.tool_plugin_host import daemon_runtime_for_agent
    assert daemon_runtime_for_agent(agent, {}).preset_capability_defaults == {"web": {}, "vision": {}}
    assert manifest_registered == set(BUILTIN_TOOLS)
    agent.stop(timeout=1.0)


def test_agent_unsupported_vision_registers_manual_route(tmp_path):
    """Unsupported vision remains registered for explicit manual guidance."""
    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=tmp_path / "test",
        capabilities={"vision": {"provider": "not-real"}},
    )
    try:
        manifest_registered = {
            name for name, _ in agent._build_manifest().get("capabilities", [])
        }
        assert agent.has_capability("vision") is True
        assert agent.get_capability("vision") is not None
        assert "vision" in agent._tool_handlers
        assert "vision" in manifest_registered
    finally:
        agent.stop(timeout=1.0)


def test_agent_disable_strips_core_capability(tmp_path):
    """`disable=[...]` opt-out drops a default-on capability."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        disable=["bash", "avatar"],
    )
    registered = {name for name, _ in agent._capabilities}
    assert "bash" not in registered
    assert "avatar" not in registered
    assert "knowledge" in registered  # other defaults still on
    agent.stop(timeout=1.0)


def test_agent_capabilities_dict_kwarg_overrides_default(tmp_path):
    """Passing kwargs for a default-on capability merges over the defaults.

    `bash` carries no core-default kwargs (the Shell binding resolves omitted
    config to yolo), so an explicit `{"yolo": False}` or `policy_file` survives
    the merge unmasked.
    """
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"bash": {"yolo": False}},
    )
    shell_entry = [(n, k) for n, k in agent._capabilities if n == "shell"]
    assert shell_entry and shell_entry[0][1].get("yolo") is False
    agent.stop(timeout=1.0)


def test_agent_capabilities_list(tmp_path):
    """capabilities= as list of strings is honored alongside the core defaults."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["web"],
    )
    registered = {name for name, _ in agent._capabilities}
    assert "web" in registered
    # The core defaults still register alongside the opt-in list entry.
    assert "shell" in registered
    # The removed ``file`` family and its retired per-operation names are not
    # capabilities at all.
    assert "file" not in registered
    assert "read" not in registered
    assert "write" not in registered
    agent.stop(timeout=1.0)


def test_agent_capabilities_dict(tmp_path):
    """capabilities= as dict registers user-supplied capabilities with kwargs.

    Core defaults still register alongside; the assertion focuses on the
    opt-in tools getting their handler wired.
    """
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={
            "vision": {"vision_service": FakeVisionService()},
            "web": {"search_service": FakeSearchService()},
        },
    )
    registered = {name for name, _ in agent._capabilities}
    assert "vision" in registered
    assert "web" in registered
    assert "vision" in agent._tool_handlers
    assert "web" in agent._tool_handlers
    agent.stop(timeout=1.0)


def test_agent_get_capability(tmp_path):
    """get_capability() returns the manager instance."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"vision": {"vision_service": FakeVisionService()}},
    )
    mgr = agent.get_capability("vision")
    assert mgr is not None
    assert agent.get_capability("nonexistent") is None
    agent.stop(timeout=1.0)


def test_agent_seal_after_start(tmp_path):
    """add_tool() raises after start() on Agent too."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities={"vision": {"vision_service": FakeVisionService()}},
    )
    agent.start()
    try:
        with pytest.raises(RuntimeError, match="Cannot modify tools after start"):
            agent.add_tool("foo", schema={"type": "object", "properties": {}}, handler=lambda a: {}, description="x")
    finally:
        agent.stop(timeout=2.0)


def test_vision_requires_provider(tmp_path):
    """No provider still registers vision's manual route."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["vision"],
    )
    assert agent.get_capability("vision") is not None
    assert "vision" in {s.name for s in agent._tool_schemas}
    agent.stop(timeout=1.0)


def test_web_defaults_to_duckduckgo(tmp_path):
    """Web search capability falls back to duckduckgo when no provider given."""
    agent = Agent(
        service=make_mock_service(), agent_name="test", working_dir=tmp_path / "test",
        capabilities=["web"],
    )
    mgr = agent.get_capability("web")
    assert mgr is not None
    assert "web" in {s.name for s in agent._tool_schemas}
    agent.stop(timeout=1.0)


@pytest.mark.parametrize("disabled", [False, True], ids=["configured", "explicit-off"])
def test_daemon_adapter_registered_tool_defaults(tmp_path, disabled):
    """Actual Agent adapter replays config, not parent handlers or preset tools."""
    from copy import deepcopy
    from lingtai.adapters.tool_plugin_host import daemon_runtime_for_agent

    tools = {"web": {"engines": {"duckduckgo": {"max_results": 3}}},
             "vision": {"provider": "not-real"}}
    agent = Agent(service=make_mock_service(), agent_name="host",
                  working_dir=tmp_path / "host", capabilities=tools,
                  disable=["web", "vision"] if disabled else [])
    try:
        runtime = daemon_runtime_for_agent(agent, {})
        expected = {} if disabled else tools
        assert runtime.preset_capability_defaults == expected
        parent_schemas = tuple(agent._tool_schemas)
        parent_handlers = dict(agent._tool_handlers)
        if not disabled:
            snapshot = runtime.preset_capability_defaults
            snapshot["web"]["engines"]["duckduckgo"]["max_results"] = 99
            assert runtime.preset_capability_defaults == tools
            for name, kwargs in runtime.preset_capability_defaults.items():
                schemas, handlers = runtime.setup_preset_capability(name, kwargs)
                assert set(schemas) == set(handlers) == {name}
                assert handlers[name] is not parent_handlers[name]
            assert tuple(agent._tool_schemas) == parent_schemas
            assert agent._tool_handlers == parent_handlers
            # A runtime bound before reconstruction must observe current
            # registration, not replay an older init snapshot after disable.
            before = deepcopy(agent._capabilities)
            agent._capabilities = [(n, k) for n, k in before if n != "web"]
            assert set(runtime.preset_capability_defaults) == {"vision"}
            agent._capabilities = before
    finally:
        agent.stop(timeout=1.0)
