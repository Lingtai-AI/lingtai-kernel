"""Tool contracts for the LingTai SDK facade.

These are *metadata* types — stable, read-only descriptions of tools and a
small permission-mode vocabulary. They do not execute anything and do not
couple to runtime internals beyond reading the capability registry to keep the
built-in tool-name list from drifting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class PermissionMode:
    """Permission-mode constants, mirroring the Anthropic Agent SDK vocabulary.

    Forward-compatibility only in this release: ``LingTaiOptions.permission_mode``
    is recorded and surfaced, but the runtime does not yet enforce a mode. Hosts
    and the future ``lingtai-cli`` will interpret these.
    """

    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    ACCEPT_ALL = "acceptAll"
    PLAN = "plan"
    BYPASS = "bypassPermissions"

    ALL = (DEFAULT, ACCEPT_EDITS, ACCEPT_ALL, PLAN, BYPASS)


@dataclass
class ToolSpec:
    """Read-only description of a single tool available to an agent.

    ``source`` is one of ``"capability"`` (a built-in capability tool),
    ``"intrinsic"`` (a kernel intrinsic), or ``"mcp"`` (a tool registered from
    an MCP server). ``input_schema`` is the JSON-Schema-shaped parameter spec.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    source: str = "capability"


@dataclass
class ToolResult:
    """A stable shape for a tool's result, for hosts that wrap dispatch.

    Intentionally decoupled from the runtime's internal tool-result objects —
    callers may adapt to/from this without depending on kernel types.
    """

    tool: str
    content: Any = None
    is_error: bool = False


def builtin_tool_names() -> tuple[str, ...]:
    """Return the live set of built-in capability and group names.

    Read from ``lingtai.capabilities`` so this never drifts from the runtime
    registry. Includes both individual capability names (e.g. ``"read"``) and
    group names (e.g. ``"file"``). Sorted for determinism.
    """
    from lingtai.capabilities import _BUILTIN, _GROUPS

    return tuple(sorted(set(_BUILTIN) | set(_GROUPS)))


def __getattr__(name: str) -> Any:
    """Lazily expose ``BUILTIN_TOOLS`` without importing the runtime at module load.

    ``builtin_tool_names()`` reads ``lingtai.capabilities``. Keeping the legacy
    constant name behind ``__getattr__`` preserves ``from lingtai_sdk.tools import
    BUILTIN_TOOLS`` while keeping a plain ``import lingtai_sdk.tools`` contract-only.
    """
    if name == "BUILTIN_TOOLS":
        return builtin_tool_names()
    raise AttributeError(name)
