"""Process-local, explicitly owned stdio MCP overlay for one driving session."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from lingtai.kernel.llm import FunctionSchema
from lingtai.kernel.session import SessionManager
from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES

from . import mcp as mcp_service
from .mcp import MCPClient


@dataclass(frozen=True, slots=True)
class StdioMCPServerConfig:
    name: str
    command: str
    args: tuple[str, ...]
    env: tuple[tuple[str, str], ...]


class SessionMCPLease:
    """Own clients and exactly the tool records published by one overlay."""

    def __init__(
        self,
        agent,
        clients: list[Any],
        owned_tools: dict[str, tuple[Any, Any, FunctionSchema]],
    ):
        self._agent = agent
        self._clients = clients
        self._owned_tools = owned_tools
        self._lock = threading.Lock()
        self._closed = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        agent = self._agent
        surface_lock = _surface_lock(agent)
        with surface_lock:
            routes = getattr(agent, "_mcp_clients_by_tool", {})
            handlers = agent._tool_handlers
            owned_schemas: list[FunctionSchema] = []
            for name, (client, handler, schema) in self._owned_tools.items():
                # Each surface record has its own ownership identity. A later
                # owner may replace only one layer (for example a non-MCP
                # handler), so remove our remaining route/handler independently.
                if handlers.get(name) is handler:
                    handlers.pop(name, None)
                if routes.get(name) is client:
                    routes.pop(name, None)
                    getattr(agent, "_mcp_tool_metadata", {}).pop(name, None)
                    getattr(agent, "_mcp_tool_names", set()).discard(name)
                owned_schemas.append(schema)
            agent._tool_schemas = [
                schema
                for schema in agent._tool_schemas
                if all(schema is not owned for owned in owned_schemas)
            ]
            leases = getattr(agent, "_session_mcp_leases", None)
            if isinstance(leases, list) and self in leases:
                leases.remove(self)
            try:
                _update_live_tools(agent)
            except Exception:
                pass
        for client in reversed(self._clients):
            try:
                client.close()
            except Exception:
                pass


class ConnectionMCPLease:
    """An MCP tool view owned by one ACP connection, not Agent globals."""

    def __init__(self, agent, clients, schemas, handlers):
        self.owner = agent
        self._clients = clients
        self.schemas = tuple(schemas)
        self.handlers = MappingProxyType(dict(handlers))
        self._lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for client in reversed(self._clients):
            try:
                client.close()
            except Exception:
                pass


def open_connection_mcp_stdio(agent, configs: tuple[StdioMCPServerConfig, ...]) -> ConnectionMCPLease:
    """Preflight one connection's tools without publishing them globally."""
    clients: list[Any] = []
    schemas: list[FunctionSchema] = []
    handlers: dict[str, Any] = {}
    try:
        for config in configs:
            client = MCPClient(
                command=config.command,
                args=list(config.args),
                env=dict(config.env),
                name=config.name,
            )
            clients.append(client)
            client.start()
            tools = client.list_tools()
            if not isinstance(tools, list):
                raise ValueError("MCP tools/list result must be an array")
            for tool in tools:
                if not isinstance(tool, dict):
                    raise ValueError("MCP tool records must be objects")
                name = tool.get("name")
                schema = tool.get("schema", {})
                description = tool.get("description", "")
                if not isinstance(name, str) or not name:
                    raise ValueError("MCP tool name must be a non-empty string")
                if name in handlers:
                    raise ValueError(f"duplicate connection MCP tool name: {name}")
                if not isinstance(schema, dict) or not isinstance(description, str):
                    raise ValueError(f"malformed MCP tool record: {name}")

                def handler(args: dict, *, c=client, n=name, s=schema):
                    return c.call_tool(n, mcp_service.prepare_mcp_tool_arguments(args, s))

                handlers[name] = handler
                schemas.append(FunctionSchema(name=name, description=description, parameters=schema))

        names = set(handlers)
        with _surface_lock(agent):
            collisions = names.intersection(
                set(agent._intrinsics)
                | set(agent._tool_handlers)
                | {schema.name for schema in agent._tool_schemas}
                | set(OFFICIAL_TOOL_PLUGIN_NAMES)
            )
        if collisions:
            raise ValueError("connection MCP tool name collision: " + ", ".join(sorted(collisions)))
        return ConnectionMCPLease(agent, clients, schemas, handlers)
    except Exception:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass
        raise


def _surface_lock(agent) -> threading.RLock:
    lock = getattr(agent, "_session_mcp_surface_lock", None)
    if lock is None:
        lock = threading.RLock()
        agent._session_mcp_surface_lock = lock
    return lock


def _update_live_tools(agent) -> None:
    chat = getattr(agent, "_chat", None)
    if chat is not None:
        session = getattr(agent, "_session", None)
        if isinstance(session, SessionManager) and session.chat is chat:
            session._rebuild_session(chat.interface)
        else:
            chat.update_tools(agent._build_tool_schemas())
    agent._token_decomp_dirty = True


def mount_session_mcp_stdio(
    agent,
    configs: tuple[StdioMCPServerConfig, ...],
) -> SessionMCPLease:
    """Start, preflight, and publish a complete stdio overlay atomically."""

    clients: list[Any] = []
    catalogs: list[tuple[Any, list[dict[str, Any]]]] = []
    try:
        for config in configs:
            client = MCPClient(
                command=config.command,
                args=list(config.args),
                env=dict(config.env),
                name=config.name,
            )
            clients.append(client)
            client.start()
            catalogs.append((client, client.list_tools()))

        seen: set[str] = set()
        records: list[tuple[Any, str, dict, str, dict]] = []
        for client, tools in catalogs:
            if not isinstance(tools, list):
                raise ValueError("MCP tools/list result must be an array")
            for tool in tools:
                if not isinstance(tool, dict):
                    raise ValueError("MCP tool records must be objects")
                name = tool.get("name")
                schema = tool.get("schema", {})
                description = tool.get("description", "")
                if not isinstance(name, str) or not name:
                    raise ValueError("MCP tool name must be a non-empty string")
                if name in seen:
                    raise ValueError(f"duplicate session MCP tool name: {name}")
                if not isinstance(schema, dict) or not isinstance(description, str):
                    raise ValueError(f"malformed MCP tool record: {name}")
                seen.add(name)
                records.append((client, name, schema, description, tool))

        lock = _surface_lock(agent)
        with lock:
            collisions = seen.intersection(
                set(agent._intrinsics)
                | set(agent._tool_handlers)
                | set(OFFICIAL_TOOL_PLUGIN_NAMES)
            )
            if collisions:
                raise ValueError(
                    "session MCP tool name collision: " + ", ".join(sorted(collisions))
                )
            schemas_before = list(agent._tool_schemas)
            handlers_before = dict(agent._tool_handlers)
            names_before = set(getattr(agent, "_mcp_tool_names", set()))
            routes_before = dict(getattr(agent, "_mcp_clients_by_tool", {}))
            metadata_before = dict(getattr(agent, "_mcp_tool_metadata", {}))
            try:
                if not hasattr(agent, "_mcp_tool_names"):
                    agent._mcp_tool_names = set()
                if not hasattr(agent, "_mcp_clients_by_tool"):
                    agent._mcp_clients_by_tool = {}
                if not hasattr(agent, "_mcp_tool_metadata"):
                    agent._mcp_tool_metadata = {}
                owned_tools: dict[str, tuple[Any, Any, FunctionSchema]] = {}
                for client, name, schema, description, tool in records:
                    def handler(args: dict, *, c=client, n=name, s=schema):
                        return c.call_tool(
                            n, mcp_service.prepare_mcp_tool_arguments(args, s)
                        )
                    function_schema = FunctionSchema(
                        name=name,
                        description=description,
                        parameters=schema,
                    )
                    agent._tool_handlers[name] = handler
                    agent._tool_schemas.append(function_schema)
                    agent._mcp_tool_names.add(name)
                    agent._mcp_clients_by_tool[name] = client
                    agent._mcp_tool_metadata[name] = mcp_service.tool_metadata(tool)
                    owned_tools[name] = (client, handler, function_schema)
                lease = SessionMCPLease(agent, clients, owned_tools)
                _update_live_tools(agent)
                if not hasattr(agent, "_session_mcp_leases"):
                    agent._session_mcp_leases = []
                agent._session_mcp_leases.append(lease)
                return lease
            except Exception:
                agent._tool_schemas = schemas_before
                agent._tool_handlers.clear()
                agent._tool_handlers.update(handlers_before)
                agent._mcp_tool_names = names_before
                agent._mcp_clients_by_tool = routes_before
                agent._mcp_tool_metadata = metadata_before
                try:
                    _update_live_tools(agent)
                except Exception:
                    pass
                raise
    except Exception:
        for client in reversed(clients):
            try:
                client.close()
            except Exception:
                pass
        raise


__all__ = [
    "ConnectionMCPLease", "SessionMCPLease", "StdioMCPServerConfig",
    "mount_session_mcp_stdio", "open_connection_mcp_stdio",
]
