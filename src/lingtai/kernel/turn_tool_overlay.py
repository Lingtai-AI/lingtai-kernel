"""Turn-local tool surface for a connection-owned MCP lease.

The Core only sees the immutable schema/handler view.  Client process ownership
and teardown stay with the outer ACP adapter, never in the Agent's global tool
table.  The run loop binds the view for one correlated turn at a time.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Callable, Mapping, Protocol


class TurnToolOverlay(Protocol):
    owner: object
    schemas: tuple
    handlers: Mapping[str, Callable]

    @property
    def closed(self) -> bool: ...


_current: ContextVar[TurnToolOverlay | None] = ContextVar(
    "lingtai_turn_tool_overlay", default=None
)


def bind_turn_tool_overlay(overlay: TurnToolOverlay) -> Token:
    return _current.set(overlay)


def reset_turn_tool_overlay(token: Token) -> None:
    _current.reset(token)


def clear_turn_tool_overlay() -> None:
    _current.set(None)


def current_turn_tool_overlay(agent) -> TurnToolOverlay | None:
    overlay = _current.get()
    if overlay is None:
        return None
    if overlay.owner is not agent or overlay.closed:
        raise RuntimeError("connection tool overlay is unavailable")
    names = set(overlay.handlers)
    if names & (
        set(agent._intrinsics)
        | set(agent._tool_handlers)
        | {schema.name for schema in agent._tool_schemas}
    ):
        raise RuntimeError("connection tool overlay collides with Agent tools")
    return overlay
