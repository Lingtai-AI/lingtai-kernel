"""Runtime adapter: options -> agent kwargs mapping, disable derivation, config,
and the no-service path."""
from __future__ import annotations

from unittest.mock import MagicMock

from lingtai_sdk import (
    LingTaiOptions,
    MCPStdioServerConfig,
    SystemPromptAssets,
    build_llm_service,
    options_to_agent_kwargs,
)
from lingtai_sdk.runtime import derive_disable_list


def test_build_llm_service_returns_none_when_underspecified():
    assert build_llm_service(LingTaiOptions()) is None
    assert build_llm_service(LingTaiOptions(provider="anthropic")) is None
    assert build_llm_service(LingTaiOptions(model="m")) is None


def test_options_to_agent_kwargs_basic_mapping():
    svc = MagicMock()
    o = LingTaiOptions(
        working_dir="/agents/a",
        agent_name="alice",
        capabilities=["file"],
    )
    kw = options_to_agent_kwargs(o, service=svc)
    assert kw["service"] is svc
    assert kw["working_dir"] == "/agents/a"
    assert kw["agent_name"] == "alice"
    assert kw["capabilities"] == ["file"]
    assert "disable" not in kw  # nothing disallowed


def test_disable_derivation_from_disallowed_tools():
    o = LingTaiOptions(disallowed_tools=["bash", "file", "not_a_capability"])
    disable = derive_disable_list(o)
    # 'file' group expands to its members; 'bash' kept; unknown dropped.
    assert "bash" in disable
    assert "read" in disable and "write" in disable
    assert "not_a_capability" not in disable


def test_kwargs_include_disable_and_prompt_assets():
    svc = MagicMock()
    o = LingTaiOptions(
        working_dir="/a",
        disallowed_tools=["bash"],
        system_prompt=SystemPromptAssets(covenant="be good"),
    )
    kw = options_to_agent_kwargs(o, service=svc)
    assert kw["disable"] == ["bash"]
    assert kw["covenant"] == "be good"


def test_kwargs_surface_mcp_and_allowed_without_forwarding():
    svc = MagicMock()
    o = LingTaiOptions(
        working_dir="/a",
        allowed_tools=["read", "write"],
        mcp_servers={"srv": MCPStdioServerConfig(command="npx", env={"K": "v"})},
    )
    kw = options_to_agent_kwargs(o, service=svc)
    assert kw["_sdk_allowed_tools"] == ["read", "write"]
    # MCP runtime dict retains secrets (feeds live connect_mcp*), under the
    # underscore-prefixed SDK-internal key.
    assert kw["_sdk_mcp_servers"]["srv"]["env"] == {"K": "v"}


def test_config_built_only_when_relevant_fields_set():
    svc = MagicMock()
    assert "config" not in options_to_agent_kwargs(
        LingTaiOptions(working_dir="/a"), service=svc
    )
    kw = options_to_agent_kwargs(
        LingTaiOptions(working_dir="/a", context_limit=200000, max_turns=10),
        service=svc,
    )
    cfg = kw["config"]
    assert cfg.context_limit == 200000
    assert cfg.max_turns == 10
