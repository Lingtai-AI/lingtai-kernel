"""MCP server configuration contracts for the LingTai SDK facade.

These typed configs mirror the runtime's accepted MCP shapes (the dicts that
``lingtai.agent.Agent._load_mcp_from_workdir`` / ``connect_mcp*`` consume) and
the Anthropic Agent SDK's vocabulary. ``to_runtime_dict()`` emits exactly the
dict the runtime expects.

Secrets (``headers`` values, ``env`` values) are kept out of ``__repr__`` and
out of redacted serialization.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_REDACTED = "***"


def _redact_mapping(mapping: dict[str, str] | None) -> dict[str, str]:
    """Return a copy of *mapping* with every value replaced by ``"***"``."""
    if not mapping:
        return {}
    return {k: _REDACTED for k in mapping}


@dataclass
class MCPServerConfig:
    """Base class for MCP server configs. Use a concrete subclass."""

    type: str = "stdio"

    def to_runtime_dict(self, *, redact: bool = False) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass(repr=False)
class MCPStdioServerConfig(MCPServerConfig):
    """A subprocess MCP server launched via ``command``/``args``/``env``.

    Maps to ``{"type": "stdio", "command": ..., "args": [...], "env": {...}}``.
    """

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    type: str = "stdio"

    def to_runtime_dict(self, *, redact: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "stdio", "command": self.command}
        if self.args:
            out["args"] = list(self.args)
        if self.env:
            out["env"] = _redact_mapping(self.env) if redact else dict(self.env)
        return out

    def __repr__(self) -> str:
        env_keys = sorted(self.env)
        return (
            f"MCPStdioServerConfig(command={self.command!r}, args={self.args!r}, "
            f"env_keys={env_keys!r})"
        )


@dataclass(repr=False)
class MCPHttpServerConfig(MCPServerConfig):
    """A remote streamable-HTTP MCP server.

    Maps to ``{"type": "http", "url": ..., "headers": {...}}``.
    """

    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    type: str = "http"

    def to_runtime_dict(self, *, redact: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "http", "url": self.url}
        if self.headers:
            out["headers"] = (
                _redact_mapping(self.headers) if redact else dict(self.headers)
            )
        return out

    def __repr__(self) -> str:
        header_keys = sorted(self.headers)
        return f"MCPHttpServerConfig(url={self.url!r}, header_keys={header_keys!r})"


@dataclass(repr=False)
class MCPSSEServerConfig(MCPServerConfig):
    """A remote Server-Sent-Events MCP server.

    Forward-compatibility placeholder: the runtime currently consumes ``stdio``
    and ``http`` transports. ``to_runtime_dict`` emits ``{"type": "sse", ...}``
    so the future ``lingtai-cli`` and runtime can adopt it without an API change.
    """

    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    type: str = "sse"

    def to_runtime_dict(self, *, redact: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "sse", "url": self.url}
        if self.headers:
            out["headers"] = (
                _redact_mapping(self.headers) if redact else dict(self.headers)
            )
        return out

    def __repr__(self) -> str:
        header_keys = sorted(self.headers)
        return f"MCPSSEServerConfig(url={self.url!r}, header_keys={header_keys!r})"


@dataclass(repr=False)
class MCPSdkServerConfig(MCPServerConfig):
    """An in-process ("SDK") MCP server instance.

    Forward-compatibility placeholder: the runtime does not yet host in-process
    MCP servers. The ``instance`` is held opaquely and is NOT serialized into
    ``to_runtime_dict`` (it is not JSON-representable). Documented TODO for the
    future CLI/runtime adapter.
    """

    name: str = ""
    instance: Any = None
    type: str = "sdk"

    def to_runtime_dict(self, *, redact: bool = False) -> dict[str, Any]:
        # The live instance is intentionally omitted — it cannot be persisted to
        # init.json. We surface only the declared name so the future runtime can
        # resolve it from an in-process registry.
        return {"type": "sdk", "name": self.name}

    def __repr__(self) -> str:
        return f"MCPSdkServerConfig(name={self.name!r}, instance=<{type(self.instance).__name__}>)"
