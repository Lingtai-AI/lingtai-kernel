"""LingTaiClient: build_agent_kwargs purity, create_agent construction against a
mock LLM service, no auto-start, and tool inventory."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from lingtai_sdk import LingTaiClient, LingTaiOptions
from lingtai_kernel.state import AgentState


def _mock_service():
    svc = MagicMock()
    svc.provider = "anthropic"
    svc.model = "claude-opus-4-8"
    svc._base_url = None
    return svc


def test_build_agent_kwargs_is_pure_with_injected_service():
    svc = _mock_service()
    client = LingTaiClient(LingTaiOptions(working_dir="/a", capabilities=["read"]))
    kw = client.build_agent_kwargs(service=svc)
    assert kw["service"] is svc
    assert kw["working_dir"] == "/a"
    assert kw["capabilities"] == ["read"]


def test_create_agent_requires_working_dir():
    client = LingTaiClient(LingTaiOptions(provider="anthropic", model="m"))
    with pytest.raises(ValueError, match="working_dir"):
        client.create_agent(service=_mock_service())


def test_create_agent_constructs_without_starting(tmp_path):
    svc = _mock_service()
    options = LingTaiOptions(
        working_dir=str(tmp_path / "alice"),
        agent_name="alice",
        capabilities=["read"],
    )
    client = LingTaiClient(options)
    agent = client.create_agent(service=svc)
    try:
        assert agent.agent_name == "alice"
        # Not started: lifecycle state is the constructed default (IDLE), not ACTIVE.
        assert agent._state == AgentState.IDLE
        # SDK-internal keys never reach the Agent constructor.
        assert not hasattr(agent, "_sdk_mcp_servers")
    finally:
        agent.stop()


def test_tool_inventory_reflects_registered_tools(tmp_path):
    svc = _mock_service()
    options = LingTaiOptions(
        working_dir=str(tmp_path / "bob"),
        agent_name="bob",
        capabilities=["read", "write"],
    )
    client = LingTaiClient(options)
    agent = client.create_agent(service=svc)
    try:
        specs = client.tool_inventory()
        names = {s.name for s in specs}
        # read/write capabilities register tools of the same name.
        assert "read" in names
        assert "write" in names
        # Every spec has a known source.
        assert all(s.source in {"capability", "intrinsic", "mcp"} for s in specs)
    finally:
        agent.stop()


def test_tool_inventory_empty_before_create():
    client = LingTaiClient(LingTaiOptions())
    assert client.tool_inventory() == []
