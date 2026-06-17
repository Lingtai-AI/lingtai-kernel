"""lingtai_sdk — public, programmable facade over the LingTai agent runtime.

Inspired in spirit by the Anthropic Agent SDK. This package exposes *stable,
typed contracts* (options, tool/MCP/session types) and thin wrappers that
build and construct a native :class:`lingtai.agent.Agent` from a declarative
:class:`LingTaiOptions`. It does not move runtime implementation and does not
change runtime behavior.

Mental model:

- ``lingtai`` / ``lingtai_kernel`` are the *runtime SDK* (the engine).
- ``lingtai_sdk`` is the *public programmable API* (stable contracts + wrappers).
- A future ``lingtai-cli`` will own product assembly / ``init.json`` translation
  from project state into these runtime options. That layer is out of scope here.

Quick start::

    from lingtai_sdk import LingTaiOptions, LingTaiClient

    options = LingTaiOptions(
        provider="anthropic", model="claude-opus-4-8",
        working_dir="/agents/alice", agent_name="alice",
        capabilities=["file", "web_search"],
    )
    client = LingTaiClient(options)
    kwargs = client.build_agent_kwargs()   # pure, testable
    # agent = client.create_agent()        # constructs a live Agent (does not start it)
"""
from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    __version__ = _pkg_version("lingtai")
except Exception:  # pragma: no cover - distribution metadata absent (editable edge case)
    __version__ = "0+unknown"

from .tools import (
    BUILTIN_TOOLS,
    PermissionMode,
    ToolResult,
    ToolSpec,
    builtin_tool_names,
)
from .mcp import (
    MCPHttpServerConfig,
    MCPSdkServerConfig,
    MCPServerConfig,
    MCPSSEServerConfig,
    MCPStdioServerConfig,
)
from .session import InMemorySessionStore, SessionRef, SessionStore
from .options import LingTaiOptions, SystemPromptAssets
from .runtime import build_llm_service, options_to_agent_kwargs
from .client import LingTaiClient
from .query import query

__all__ = [
    "__version__",
    # Options
    "LingTaiOptions",
    "SystemPromptAssets",
    # Client + query
    "LingTaiClient",
    "query",
    # Runtime adapter
    "build_llm_service",
    "options_to_agent_kwargs",
    # Tool contracts
    "PermissionMode",
    "ToolSpec",
    "ToolResult",
    "builtin_tool_names",
    "BUILTIN_TOOLS",
    # MCP configs
    "MCPServerConfig",
    "MCPStdioServerConfig",
    "MCPHttpServerConfig",
    "MCPSSEServerConfig",
    "MCPSdkServerConfig",
    # Sessions
    "SessionRef",
    "SessionStore",
    "InMemorySessionStore",
]
