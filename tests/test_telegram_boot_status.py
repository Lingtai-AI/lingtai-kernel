"""Regression tests for Telegram addon dead-on-boot diagnostics (issue #711).

When eager boot fails the manager is None and every tool call used to return a
static "check stderr" message — unactionable for an agent that cannot read the
server's stderr, leaving the human channel permanently and opaquely dead. These
tests pin the contract that the captured boot exception is surfaced inline
through ``telegram action=status`` and that the dead-manager error for other
actions cites the cause and points the agent at that status action.

They drive the real registered MCP ``CallToolRequest`` handler so the whole
dead-on-boot path is exercised end to end.
"""
from __future__ import annotations

import asyncio
import json

import mcp.types as types

from lingtai.mcp_servers.telegram.server import (
    _dispatch_tool_call,
    build_server,
)


def _call(manager, boot_error, arguments) -> dict:
    """Invoke the real telegram tool handler and return the decoded result."""
    server = build_server(manager, boot_error=boot_error)
    handler = server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="telegram", arguments=arguments),
    )
    result = asyncio.run(handler(request))
    return json.loads(result.root.content[0].text)


def test_status_action_surfaces_boot_error_when_manager_dead():
    """A dead manager still answers status with the captured boot exception."""
    boot_error = "FileNotFoundError: Telegram config not found: /old/linux/path.json"

    result = _call(None, boot_error, {"action": "status"})

    # The actionable exception must be inline, not stranded on stderr.
    assert result["boot_error"] == boot_error
    assert result["manager_initialized"] is False
    assert result["status"] == "degraded"


def test_dead_manager_nonstatus_error_points_at_status_not_stderr():
    """Non-status calls on a dead manager cite the boot cause and status action,
    never the old unreadable 'check stderr' instruction."""
    boot_error = "ValueError: LINGTAI_TELEGRAM_CONFIG env var not set"

    result = _call(None, boot_error, {"action": "send"})

    assert result["status"] == "error"
    assert "check stderr" not in result["error"].lower()
    assert boot_error in result["error"]
    assert "action=status" in result["error"]


def test_status_action_carries_none_boot_error_when_boot_succeeded():
    """When boot succeeded there is no boot exception; boot_error is None."""

    class _StubService:
        _running = True

    class _StubManager:
        _service = _StubService()

    result = _call(_StubManager(), None, {"action": "status"})

    assert result["boot_error"] is None
    assert result["manager_initialized"] is True
    assert result["status"] == "ok"


def test_dispatch_status_is_handled_before_manager_guard():
    """Unit-level: status is answered by the server even with no boot_error,
    independent of the manager, so it never falls through to 'unknown action'."""
    result = asyncio.run(_dispatch_tool_call(None, None, {"action": "status"}))

    assert "boot_error" in result
    assert result["manager_initialized"] is False
