"""Compact-until-disclosed ``tools/list`` for the co-shipped communication servers.

Each curated channel's tool (``telegram``, ``imap``, ``feishu``, ``wechat``,
``whatsapp``, ``cloud_mail``) first advertises
a same-name strict LTP-v2 schema whose only action is the family's reserved
``manual``, built from the package's own manual child so it cannot drift. The
full handler stays callable throughout; only what ``tools/list`` shows changes.
The schema expands exactly once per process — after a *successfully served*
``manual`` call, or right before the provider pushes a real inbound event over
LICC — and announces it with the standard ``notifications/tools/list_changed``:
on the server's ``subscriptions/listen`` bus for 2026-07-28 clients, and as a
direct notification for a handshake-era connection. The host that consumes it
(``lingtai.services.mcp``) names no provider.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import mcp.types as types
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ToolsListChanged
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from lingtai.tools.tool_family import ToolFamily

from ._plugin import CuratedMcpPlugin
from ._results import payload_is_error

log = logging.getLogger(__name__)


def compact_tool_for(plugin: CuratedMcpPlugin, full_tool: types.Tool) -> types.Tool:
    """The same-name, manual-only tool advertised before disclosure."""
    identity = (full_tool.description or "").strip().split("\n", 1)[0].split(". ", 1)[0]
    return types.Tool(
        name=plugin.name,
        description=(
            f"{identity}. Compact stand-in: call action='manual' with input {{}} to "
            f"read the manual; the complete {plugin.name} schema is disclosed on your "
            f"next round. A real inbound {plugin.name} event discloses it automatically."
        ),
        input_schema=ToolFamily(plugin.name, [plugin.manual_child()]).build_schema(),
    )


class ToolDisclosure:
    """Process-local, monotonic disclosure state for one server's tool.

    :meth:`expand` is safe from a provider's inbound thread while the loop is
    serving; :meth:`expand_async` is awaited inside ``on_call_tool``.
    """

    def __init__(self, plugin: CuratedMcpPlugin, full_tool: types.Tool) -> None:
        self._plugin = plugin
        self._full_tool = full_tool
        self._compact_tool = compact_tool_for(plugin, full_tool)
        self._bus = InMemorySubscriptionBus()
        self.listen_handler = ListenHandler(self._bus)  # pass as on_subscriptions_listen=
        self._lock = threading.Lock()
        self._expanded = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._legacy_session: Any = None

    @property
    def expanded(self) -> bool:
        with self._lock:
            return self._expanded

    def tools(self) -> list[types.Tool]:
        with self._lock:
            return [self._full_tool if self._expanded else self._compact_tool]

    def bind_loop(self) -> None:
        """Remember the serving loop so an inbound-thread expansion can signal."""
        self._loop = asyncio.get_running_loop()

    def unbind(self) -> None:
        self._loop = None
        self._legacy_session = None

    def observe(self, ctx: Any) -> None:
        """Remember a handshake-era session for direct delivery (modern ones listen)."""
        if ctx.protocol_version not in MODERN_PROTOCOL_VERSIONS:
            self._legacy_session = ctx.session

    def _flip(self) -> bool:
        with self._lock:
            if self._expanded:
                return False
            self._expanded = True
            return True

    def expand(self, reason: str) -> bool:
        """Disclose now and schedule the signal; returns True only on the transition."""
        if not self._flip():
            return False
        log.info("%s tool schema disclosed (%s)", self._plugin.name, reason)
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(lambda: loop.create_task(self._emit()))
        return True

    async def expand_async(self, reason: str) -> bool:
        if not self._flip():
            return False
        log.info("%s tool schema disclosed (%s)", self._plugin.name, reason)
        await self._emit()
        return True

    async def _emit(self) -> None:
        try:
            await self._bus.publish(ToolsListChanged())
            if self._legacy_session is not None:
                await self._legacy_session.send_tool_list_changed()
        except Exception as exc:  # advisory signal: never fail the trigger
            log.debug("%s: tools/list_changed delivery failed: %s", self._plugin.name, exc)

    async def after_call(self, arguments: dict, result: dict) -> None:
        """Trigger: a successfully served ``manual`` call discloses the full schema."""
        if arguments.get("action") == "manual" and not payload_is_error(result):
            await self.expand_async("manual")
