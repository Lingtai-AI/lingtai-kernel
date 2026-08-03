"""LingTai WhatsApp MCP server (personal-account mode)."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from .._results import json_tool_result as _tool_result
from .._results import text_resource_result as _resource_result
from .._results import unknown_resource_error as _unknown_resource
from .._results import unknown_tool_error as _unknown_tool
from .licc import push_inbox_event
from .manager import WhatsAppManager, SCHEMA, DESCRIPTION
from .resources import resource_text

log = logging.getLogger("lingtai.mcp_servers.whatsapp")

_SERVER_INSTRUCTIONS = (
    "lingtai-whatsapp: personal-account WhatsApp client via a local whatsapp-web.js "
    "bridge. Configure via LINGTAI_WHATSAPP_CONFIG. Pair by scanning the QR code "
    "returned by the get_qr action (phone: WhatsApp Settings -> Linked Devices)."
)


def load_config() -> tuple[dict[str, Any], Path | None]:
    from .manager import load_config as _load
    return _load()


def build_manager(config: dict[str, Any] | None = None) -> WhatsAppManager:
    cfg = config if config is not None else (load_config()[0] or {})
    return WhatsAppManager(cfg)


def serve() -> Server:
    manager: WhatsAppManager | None = None

    async def _ensure_manager() -> WhatsAppManager:
        nonlocal manager
        if manager is None:
            manager = build_manager()
        return manager

    app = Server("whatsapp", instructions=_SERVER_INSTRUCTIONS)

    @app.list_tools()
    async def list_tools(ctx: ServerRequestContext | None = None) -> list[types.Tool]:
        return [types.Tool(**SCHEMA)]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any], ctx: ServerRequestContext | None = None) -> list[types.TextContent]:
        if name != SCHEMA["name"]:
            raise _unknown_tool(name)
        m = await _ensure_manager()
        action = (arguments or {}).get("action")
        args = (arguments or {}).get("args") or {}
        if action == "manual":
            return [_tool_result({"manual": "whatsapp-mcp-manual"})]
        result = m.action(action, args)
        return [_tool_result(result)]

    @app.list_resource_templates()
    async def list_resource_templates(ctx: ServerRequestContext | None = None) -> list[types.ResourceTemplate]:
        return []

    @app.list_resources()
    async def list_resources(ctx: ServerRequestContext | None = None) -> list[types.Resource]:
        return [
            types.Resource(uri="lingtai://manifest", name="manifest", mimeType="application/json", description="WhatsApp MCP manifest"),
            types.Resource(uri="lingtai://skills/whatsapp", name="whatsapp-mcp-manual", mimeType="text/markdown", description="WhatsApp MCP skill"),
            types.Resource(uri="lingtai://status", name="status", mimeType="application/json", description="WhatsApp MCP status"),
            types.Resource(uri="lingtai://docs/configuration", name="configuration", mimeType="text/markdown", description="WhatsApp MCP configuration"),
            types.Resource(uri="lingtai://docs/troubleshooting", name="troubleshooting", mimeType="text/markdown", description="WhatsApp MCP troubleshooting"),
            types.Resource(uri="lingtai://onboarding/whatsapp", name="onboarding", mimeType="text/markdown", description="WhatsApp MCP onboarding"),
            types.Resource(uri="lingtai://onboarding/html-template", name="onboarding-html-template", mimeType="text/html", description="WhatsApp MCP onboarding HTML template"),
        ]

    @app.read_resource()
    async def read_resource(uri: str, ctx: ServerRequestContext | None = None) -> types.ReadResourceResult:
        status: dict[str, Any] = {}
        if manager is not None:
            try:
                status = manager.action("status")
            except Exception:
                pass
        try:
            text, mime = resource_text(uri, status=status)
        except KeyError:
            raise _unknown_resource(uri)
        return _resource_result(text, mime)

    return app


async def main() -> None:
    app = serve()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def run() -> None:
    asyncio.run(main())
