"""Regression tests for the real MCP low-level transport boundary of the
private Task Card tool.

The card is projected by a *private MCP tool name* that ``list_tools`` never
returns. Because it is unlisted, ``mcp.server.lowlevel.Server.call_tool``
(default ``validate_input=True``) finds no cached definition and skips input
validation while still invoking the registered handler; the public ``telegram``
name keeps its default schema validation. These tests drive the real
``Server.request_handlers[CallToolRequest]`` so the genuine validation/dispatch
path runs in-process.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import mcp.types as types

from lingtai.kernel.base_agent import _TASK_CARD_TOOL
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.server import _PRIVATE_TASK_CARD_TOOL, build_server


def test_private_task_card_tool_name_literals_are_in_sync():
    """The private tool name is duplicated on purpose: the kernel must not import
    ``mcp_servers``, so ``kernel.base_agent._TASK_CARD_TOOL`` mirrors the server's
    ``_PRIVATE_TASK_CARD_TOOL`` as a literal. Both source comments say "keep the
    two in sync"; this pins that invariant so a drift in either literal — which
    would silently break the reverse channel — fails a test instead."""
    assert _TASK_CARD_TOOL == _PRIVATE_TASK_CARD_TOOL


class _FakeAccount:
    alias = "mybot"

    def __init__(self):
        self.calls: list = []

    def send_message(self, chat_id, text, reply_to_message_id=None, **kwargs):
        msg_id = len(self.calls) + 100
        self.calls.append(("send_message", chat_id, text, reply_to_message_id, kwargs))
        return {"message_id": msg_id}

    def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit_message", chat_id, message_id, text))
        return {"ok": True}


class _FakeService:
    def __init__(self):
        self.default_account = _FakeAccount()

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account


def _make_manager(tmp_path):
    service = _FakeService()
    manager = TelegramManager(
        service, working_dir=Path(tmp_path), on_inbound=lambda _: None,
    )
    return manager, service.default_account


def _call_tool_via_transport(manager, name, arguments):
    """Drive the real ``CallToolRequest`` handler registered by ``build_server``."""
    server = build_server(manager)
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return asyncio.run(handler(req)).root  # ServerResult wraps a CallToolResult


def _list_tools_via_transport(manager):
    server = build_server(manager)
    handler = server.request_handlers[types.ListToolsRequest]
    return asyncio.run(handler(None)).root.tools


def _payload(result):
    assert result.content, "expected at least one content block"
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


def test_private_tool_name_reaches_manager_and_creates_card(tmp_path):
    """The unlisted private tool name passes the transport (no validation) and
    creates a card. The caller sends NO ``action``; the server forces it."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, _PRIVATE_TASK_CARD_TOOL, {
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
        "tool": "bash",
        "tool_action": "run",
        "reasoning": "Check project structure",
    })

    assert result.isError is False
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert "message_id" in payload

    send_calls = [c for c in account.calls if c[0] == "send_message"]
    assert len(send_calls) == 1
    assert "📋 TASK CARD" in send_calls[0][2]


def test_list_tools_exposes_only_public_telegram(tmp_path):
    """The private tool name must be absent from ``list_tools``."""
    manager, _ = _make_manager(tmp_path)
    names = [t.name for t in _list_tools_via_transport(manager)]
    assert names == ["telegram"]
    assert _PRIVATE_TASK_CARD_TOOL not in names


def test_public_telegram_with_private_action_rejected_by_native_validation(tmp_path):
    """Even if the private action is guessed, the public ``telegram`` name
    validates against the unchanged public SCHEMA and rejects it."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "telegram", {
        "action": "_task_card_update",
        "sub_action": "create",
        "account": "mybot",
        "chat_id": 999,
    })

    assert result.isError is True
    assert not any(c[0] == "send_message" for c in account.calls)


def test_private_tool_cannot_be_repurposed_for_public_actions(tmp_path):
    """A public ``action`` smuggled through the private tool is overwritten by
    the server, so the hidden route only ever dispatches the task-card action."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, _PRIVATE_TASK_CARD_TOOL, {
        "action": "send",
        "chat_id": 999,
        "text": "smuggled public send",
        "sub_action": "create",
        "account": "mybot",
        "tool": "bash",
        "reasoning": "x",
    })

    # Forced to _task_card_update: a card is sent, not the smuggled public send.
    payload = _payload(result)
    assert payload["status"] == "ok"
    send_calls = [c for c in account.calls if c[0] == "send_message"]
    assert len(send_calls) == 1
    assert "📋 TASK CARD" in send_calls[0][2]
    assert "smuggled public send" not in send_calls[0][2]


def test_invalid_public_action_rejected_by_native_validation(tmp_path):
    """A bogus public action is rejected by the library's schema validation."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "telegram", {
        "action": "totally_not_a_real_action",
    })

    assert result.isError is True
    assert not any(c[0] == "send_message" for c in account.calls)


def test_public_action_wrong_type_rejected_by_native_validation(tmp_path):
    """A public ``send`` with a wrongly-typed ``chat_id`` is rejected by native
    validation (a non-enum constraint, so the whole SCHEMA is enforced)."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "telegram", {
        "action": "send",
        "chat_id": "not-an-integer",
        "text": "hi",
    })

    assert result.isError is True
    assert not any(c[0] == "send_message" for c in account.calls)


def test_unknown_tool_name_rejected(tmp_path):
    """Any other unlisted tool name remains rejected by the handler."""
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "not_a_real_tool", {"action": "send"})

    assert result.isError is True
    assert not any(c[0] == "send_message" for c in account.calls)
