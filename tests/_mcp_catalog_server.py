"""Test-only stdio MCP server whose tool catalog changes while it runs.

Run as ``python -m tests._mcp_catalog_server``. It is a real MCP SDK v2
low-level ``Server`` over the real stdio transport: it serves
``subscriptions/listen`` through the SDK's ``ListenHandler`` (so it negotiates
protocol 2026-07-28 and advertises ``tools.listChanged``), starts with a small
catalog, and — when the ``bump`` tool is called — rewrites that catalog and
publishes the standard ``ToolsListChanged`` event. Nothing here is specific to
any product family; it exists only so the kernel's catalog reconciliation can
be proven against the standard protocol, not a fake.
"""
from __future__ import annotations

import asyncio

import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged

from lingtai.mcp_servers._results import json_tool_result, unknown_tool_error

_STATE = {"phase": 1}
_BUS = InMemorySubscriptionBus()


def _catalog() -> list[types.Tool]:
    bump = types.Tool(
        name="bump",
        description="Advance this server to its second catalog.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    if _STATE["phase"] == 1:
        return [
            types.Tool(
                name="echo",
                description="echo v1",
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
            ),
            types.Tool(
                name="old",
                description="retired by bump",
                input_schema={"type": "object", "properties": {}},
            ),
            bump,
        ]
    return [
        types.Tool(
            name="echo",
            description="echo v2",
            title="Echo v2",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}, "count": {"type": "integer"}},
            },
        ),
        types.Tool(
            name="pong",
            description="added by bump",
            input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        ),
        bump,
    ]


async def _list_tools(
    _ctx: ServerRequestContext, _params: types.PaginatedRequestParams | None
) -> types.ListToolsResult:
    return types.ListToolsResult(tools=_catalog())


async def _call_tool(
    _ctx: ServerRequestContext, params: types.CallToolRequestParams
) -> types.CallToolResult:
    if params.name == "bump":
        _STATE["phase"] = 2
        # Standard level-trigger: announce the change before returning the
        # result, so a client that refetches sees the second catalog.
        await _BUS.publish(ToolsListChanged())
        return json_tool_result({"status": "ok", "phase": 2})
    if params.name in {tool.name for tool in _catalog()}:
        return json_tool_result(
            {"status": "ok", "tool": params.name, "args": dict(params.arguments or {})}
        )
    raise unknown_tool_error(params.name)


def build_server() -> Server:
    return Server(
        "lingtai-test-catalog",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
        on_subscriptions_listen=ListenHandler(_BUS),
    )


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
