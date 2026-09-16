"""Compact provider tool disclosure over the standard MCP ``tools/list_changed``.

One end-to-end case per co-shipped messaging server: the real ``python -m``
entrypoint mounts through ``Agent.connect_mcp`` with a manual-only schema, a
served ``manual`` call makes the server announce a change, and the host's
channel-neutral reconcile swaps in the full schema. A second in-memory case
covers the handshake-era direct notification and the rejected-call guard.
"""
from __future__ import annotations

import json
import sys
import time

import pytest
from mcp import Client

from lingtai.agent import Agent
from lingtai.kernel.llm import ToolCall
from tests._service_helpers import make_gemini_mock_service

PROVIDERS = ["telegram", "feishu", "wechat"]


def _full_schema(provider: str) -> dict:
    module = __import__(f"lingtai.mcp_servers.{provider}.manager", fromlist=["SCHEMA"])
    return module.SCHEMA


def _actions(agent: Agent, name: str) -> list[str]:
    schema = next(s for s in agent._tool_schemas if s.name == name)
    return schema.parameters["properties"]["action"]["enum"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_manual_call_discloses_full_schema_through_host_reconcile(tmp_path, provider):
    agent = Agent(
        service=make_gemini_mock_service(), agent_name="test",
        working_dir=tmp_path / "agent", capabilities={"mcp": {}},
    )
    try:
        # No provider config in LINGTAI_AGENT_DIR: the server still serves.
        assert agent.connect_mcp(
            sys.executable, ["-m", f"lingtai.mcp_servers.{provider}"],
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": ":".join(p for p in sys.path if p),
                "LINGTAI_AGENT_DIR": str(tmp_path / "server"),
            },
        ) == [provider]
        assert _actions(agent, provider) == ["manual"]
        client = agent._mcp_clients_by_tool[provider]
        assert client.catalog_listen_state == "listening"

        result = agent._dispatch_tool(ToolCall(
            name=provider, args={"action": "manual", "input": {}, "_reasoning": "read"}, id="c1",
        ))
        assert result["status"] == "ok"
        deadline = time.monotonic() + 10
        while _actions(agent, provider) == ["manual"] and time.monotonic() < deadline:
            time.sleep(0.05)
        schema = next(s for s in agent._tool_schemas if s.name == provider)
        assert schema.parameters == _full_schema(provider)
        assert agent._dispatch_tool(ToolCall(
            name=provider, args={"action": "status", "input": {}, "_reasoning": "r"}, id="c2",
        ))["status"] in ("ok", "error")  # full action set is now routable

        # A catalog that reaches for a name it does not own leaves the surface alone.
        before = list(agent._tool_schemas)
        rejected = agent._reconcile_mcp_client_tools(
            client, [{"name": "mcp", "description": "", "schema": {"type": "object"}}],
        )
        assert rejected["status"] == "rejected"
        assert agent._tool_schemas == before
    finally:
        agent.stop()


@pytest.mark.anyio
async def test_legacy_client_gets_direct_notification_only_after_served_manual():
    from lingtai.mcp_servers.telegram.server import build_server

    notified: list[str] = []

    async def on_message(message):
        if getattr(message, "method", None) == "notifications/tools/list_changed":
            notified.append(message.method)

    async with Client(build_server(None), mode="legacy", message_handler=on_message) as client:
        await client.list_tools()  # observes the handshake-era session
        bad = await client.call_tool(
            "telegram", {"action": "manual", "input": {"unexpected": 1}, "reasoning": "r"},
        )
        assert json.loads(bad.content[0].text)["status"] in ("error", "failed")
        assert notified == []
        await client.call_tool("telegram", {"action": "manual", "input": {}, "reasoning": "r"})
        tools = (await client.list_tools(cache_mode="bypass")).tools
        assert tools[0].input_schema == _full_schema("telegram")
    assert notified == ["notifications/tools/list_changed"]


@pytest.fixture
def anyio_backend():
    return "asyncio"
