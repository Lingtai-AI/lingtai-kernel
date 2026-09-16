"""Standard-protocol dynamic ``tools/list`` disclosure for curated MCP servers.

A curated server's own tool (``telegram``/``feishu``/``wechat``) initially
advertises a compact, same-name strict LTP-v2 schema whose only callable
action is the family's reserved ``manual``. The complete handler stays
dispatchable from process start regardless — this changes only what
``tools/list`` advertises, never what can be called (the SDK never applies
the advertised ``input_schema`` to a call; the family itself is the dispatch
gate, as it already is with a fully-disclosed schema). Disclosure is
process-local and monotonic: it expands exactly once, on whichever of these
two provider-observed triggers fires first:

* a *successfully served* ``manual`` call (a failed/rejected one does not
  expand), or
* this server delivering one real inbound event to the host over LICC — the
  flip happens immediately before that delivery, using the provider's own
  existing inbound-callback wiring, so the same host turn the event wakes
  already sees the full schema.

Either trigger emits the one standard MCP change signal,
``notifications/tools/list_changed``, on whichever route the connected client
actually negotiated: a ``subscriptions/listen`` stream (opened by a
2026-07-28+ client that asked for ``tools_list_changed``) served by the
installed SDK's own :class:`~mcp.server.subscriptions.ListenHandler` /
:class:`~mcp.server.subscriptions.SubscriptionBus`, or — for a handshake-era
connection, which never opens a listen stream — a direct
``ServerSession.send_tool_list_changed()`` notification. No custom host hook
and no new wire vocabulary is invented: a generic host that re-lists tools on
this exact standard signal sees the full schema on its next provider round,
and the kernel/host code that consumes it (``lingtai.services.mcp``) names no
provider.

This module is provider-infrastructure shared across the curated packages —
the same role as ``_results.py``/``_entrypoint.py``/``_plugin.py`` — not host
or kernel code. Disclosure state, its triggers, and its transport wiring stay
entirely inside each curated server process.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import mcp.types as types
from mcp.server import NotificationOptions, Server, ServerRequestContext
from mcp.server.session import ServerSession
from mcp.server.subscriptions import (
    InMemorySubscriptionBus,
    ListenHandler,
    SubscriptionBus,
    ToolsListChanged,
)
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

from lingtai.tools.tool_family import ToolFamily

from ._plugin import CuratedMcpPlugin
from ._results import payload_is_error

log = logging.getLogger(__name__)

__all__ = [
    "DisclosingServer",
    "ToolDisclosure",
    "compact_tool_for",
    "disclose_after_call",
]


def _first_sentence(text: str, *, limit: int = 200) -> str:
    """The identity line of an advertised description: first sentence, bounded."""
    head = (text or "").strip().split("\n", 1)[0]
    cut = head.find(". ")
    sentence = head if cut < 0 else head[: cut + 1]
    return sentence[:limit].strip()


def compact_tool_for(plugin: CuratedMcpPlugin, full_tool: types.Tool) -> types.Tool:
    """Build the compact, same-name, manual-only tool advertised before disclosure.

    The schema is derived from the plugin's own reserved ``manual`` child —
    the exact child the full family already composes ``manual`` from — so the
    compact envelope can never drift from the real one and nothing here
    hand-authors a second copy of the manual action's shape or the root
    ``reasoning``/envelope fields (``ToolFamily.build_schema()`` supplies
    those identically either way).
    """
    schema = ToolFamily(plugin.name, [plugin.manual_child()]).build_schema()
    identity = _first_sentence(full_tool.description or "")
    description = (
        f"{identity} Compact stand-in: the full {plugin.name} tool schema is "
        f"not loaded yet. Call action='manual' with input {{}} to read its "
        "manual; the complete schema is disclosed on your next round. A real "
        f"inbound {plugin.name} event discloses it automatically."
    ).strip()
    return types.Tool(name=plugin.name, description=description, input_schema=schema)


class ToolDisclosure:
    """Compact-until-disclosed ``tools/list`` state for one curated server's tool.

    Thread-safe: :meth:`expand` may be called from a provider's own inbound
    thread (the poll loop or WS callback that already exists for that
    provider) while the MCP event loop is serving requests. :meth:`expand_async`
    is for the manual trigger, awaited from inside the already-async
    ``on_call_tool`` handler.
    """

    def __init__(
        self,
        plugin: CuratedMcpPlugin,
        full_tool: types.Tool,
        *,
        start_expanded: bool = False,
    ) -> None:
        if full_tool.name != plugin.name:
            raise ValueError(
                f"full_tool must carry the plugin's own name {plugin.name!r}, "
                f"got {full_tool.name!r}"
            )
        self._plugin = plugin
        self._full_tool = full_tool
        self._compact_tool = compact_tool_for(plugin, full_tool)
        self._bus: SubscriptionBus = InMemorySubscriptionBus()
        self._listen_handler = ListenHandler(self._bus)
        self._lock = threading.Lock()
        # ``start_expanded`` exists only for a caller that builds a server
        # without going through ``serve()`` (an ad hoc tool, an existing
        # caller unaware of disclosure) and expects the historical
        # always-full ``tools/list`` — never for the real running server.
        self._expanded = start_expanded
        self._reason: str | None = "default" if start_expanded else None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._legacy_session: ServerSession | None = None

    # -- state ----------------------------------------------------------

    @property
    def expanded(self) -> bool:
        with self._lock:
            return self._expanded

    @property
    def reason(self) -> str | None:
        """Which trigger disclosed the full schema (``None`` while compact)."""
        with self._lock:
            return self._reason

    @property
    def listen_handler(self) -> ListenHandler:
        """Pass as ``on_subscriptions_listen=`` to this server's ``Server(...)``."""
        return self._listen_handler

    def tools(self) -> list[types.Tool]:
        """The current ``tools/list`` payload — stable single-tool order."""
        with self._lock:
            return [self._full_tool if self._expanded else self._compact_tool]

    # -- runtime binding --------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remember the serving loop so an inbound-thread expansion can signal on it."""
        self._loop = loop if loop is not None else asyncio.get_running_loop()

    def unbind_loop(self) -> None:
        self._loop = None
        self._legacy_session = None

    def observe(self, ctx: "ServerRequestContext[Any, Any]") -> None:
        """Remember a handshake-era connection for direct ``list_changed`` delivery.

        A 2026-07-28+ connection receives change notifications only through a
        ``subscriptions/listen`` stream it opened, so it is never captured here.
        """
        if ctx.protocol_version in MODERN_PROTOCOL_VERSIONS:
            return
        self._legacy_session = ctx.session

    # -- triggers ---------------------------------------------------------

    def _flip(self, reason: str) -> bool:
        with self._lock:
            if self._expanded:
                return False
            self._expanded = True
            self._reason = reason
            return True

    def expand(self, reason: str) -> bool:
        """Disclose the full schema now; schedule the change signal. Any thread.

        Returns ``True`` only on the compact→full transition. With no loop
        bound yet (an inbound event before the server started serving),
        nothing needs signalling: the first ``tools/list`` already returns
        the full schema.
        """
        if not self._flip(reason):
            return False
        log.info("%s tool schema disclosed (%s)", self._plugin.name, reason)
        loop = self._loop
        if loop is None or loop.is_closed():
            return True
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(self._emit())
        else:
            loop.call_soon_threadsafe(lambda: loop.create_task(self._emit()))
        return True

    async def expand_async(self, reason: str) -> bool:
        """Disclose from inside a request handler and await the signal."""
        if not self._flip(reason):
            return False
        log.info("%s tool schema disclosed (%s)", self._plugin.name, reason)
        await self._emit()
        return True

    async def _emit(self) -> None:
        """Emit ``notifications/tools/list_changed`` on whichever route applies."""
        try:
            await self._bus.publish(ToolsListChanged())
        except Exception as exc:  # advisory signal: never fail the trigger
            log.debug(
                "%s: tools/list_changed bus publish failed: %s", self._plugin.name, exc
            )
        session = self._legacy_session
        if session is not None:
            try:
                await session.send_tool_list_changed()
            except Exception as exc:  # advisory signal: never fail the trigger
                log.debug(
                    "%s: direct tools/list_changed delivery failed: %s",
                    self._plugin.name, exc,
                )


async def disclose_after_call(
    disclosure: ToolDisclosure, arguments: dict, result: dict,
) -> None:
    """Trigger (a): a successfully served ``manual`` call discloses the full schema.

    Call after computing a tool result, with the exact arguments the call
    dispatched and the exact result it produced; a failed/rejected ``manual``
    (``payload_is_error(result)``) never expands. Advisory only — never
    raises into the caller, so a disclosure failure cannot fail the manual
    call whose result is already computed.
    """
    if arguments.get("action") != "manual" or payload_is_error(result):
        return
    try:
        await disclosure.expand_async("manual")
    except Exception as exc:
        log.warning("%s: tool disclosure after manual failed: %s", disclosure._plugin.name, exc)


class DisclosingServer(Server):
    """Low-level ``Server`` whose handshake-era capabilities are era-honest.

    A modern (2026-07-28) client's ``tools.listChanged`` capability read is
    derived from whether ``subscriptions/listen`` is served at all; the
    handshake era instead reads ``NotificationOptions`` passed at
    initialization. This server really does emit ``tools/list_changed`` on
    that direct wire (see :meth:`ToolDisclosure._emit`), so
    ``tools.listChanged`` defaults to true for every runner that asks this
    instance for its initialization options — stdio and in-process transports
    alike — rather than silently under-advertising a capability it uses.
    """

    def create_initialization_options(
        self,
        notification_options: NotificationOptions | None = None,
        experimental_capabilities: dict[str, dict[str, Any]] | None = None,
        extensions: dict[str, dict[str, Any]] | None = None,
    ):
        if notification_options is None:
            notification_options = NotificationOptions(tools_changed=True)
        return super().create_initialization_options(
            notification_options, experimental_capabilities, extensions,
        )
