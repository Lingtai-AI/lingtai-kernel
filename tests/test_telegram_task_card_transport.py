"""Regression tests for the real MCP transport boundary of Telegram."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import mcp.types as types
import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError

from lingtai.mcp_servers.telegram._family import _telegram_input_schemas
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.server import build_server
from tests._notification_store_helpers import FakeNotificationStore
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)


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
    def __init__(self, working_dir):
        self._working_dir = Path(working_dir)
        self.default_account = _FakeAccount()

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account

    def list_accounts(self):
        return [self.default_account.alias]

    def account_details(self):
        return [{"alias": self.default_account.alias}]

    def identity_path(self):
        return self._working_dir / "system" / "mcp_identities" / "telegram.json"


def _make_manager(tmp_path):
    service = _FakeService(tmp_path)
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, service.default_account


def _call_tool_via_transport(manager, name, arguments):
    async def _run():
        async with Client(build_server(manager)) as client:
            return await client.call_tool(name, arguments)

    return anyio.run(_run)


def _list_tools_via_transport(manager):
    async def _run():
        async with Client(build_server(manager)) as client:
            return (await client.list_tools()).tools

    return anyio.run(_run)


def _flatten_exception(exc):
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _flatten_exception(inner)
    else:
        yield exc


def _payload(result):
    assert result.content, "expected at least one content block"
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


def test_public_telegram_action_reaches_manager(tmp_path):
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "telegram", {
        "action": "accounts",
        "input": {},
        "reasoning": "inspect available accounts",
    })

    assert result.is_error is False
    assert _payload(result)["status"] == "ok"
    assert not any(c[0] == "send_message" for c in account.calls)


def test_task_card_tool_name_is_now_unknown_at_the_mcp_boundary(tmp_path):
    manager, account = _make_manager(tmp_path)

    with pytest.raises(BaseException) as raised:
        _call_tool_via_transport(manager, "task_card", {"action": "manual"})

    errors = [
        exc for exc in _flatten_exception(raised.value) if isinstance(exc, MCPError)
    ]
    assert errors, f"expected an MCPError, got {raised.value!r}"
    assert errors[0].code == types.INVALID_PARAMS
    assert errors[0].message == "Unknown tool: task_card"
    assert errors[0].data == {"requested": "task_card"}
    assert not any(c[0] == "send_message" for c in account.calls)


def test_public_action_wrong_type_rejected(tmp_path):
    manager, account = _make_manager(tmp_path)

    result = _call_tool_via_transport(manager, "telegram", {
        "action": "send",
        "chat_id": "not-an-integer",
        "text": "hi",
    })

    assert result.is_error is True
    assert not any(c[0] == "send_message" for c in account.calls)


def test_unknown_tool_name_rejected(tmp_path):
    manager, account = _make_manager(tmp_path)

    with pytest.raises(BaseException) as raised:
        _call_tool_via_transport(manager, "not_a_real_tool", {"action": "send"})

    errors = [
        exc for exc in _flatten_exception(raised.value) if isinstance(exc, MCPError)
    ]
    assert errors, f"expected an MCPError, got {raised.value!r}"
    assert errors[0].code == types.INVALID_PARAMS
    assert errors[0].message == "Unknown tool: not_a_real_tool"
    assert errors[0].data == {"requested": "not_a_real_tool"}
    assert not any(c[0] == "send_message" for c in account.calls)


def test_public_family_has_a_strict_root_and_action_owned_branches(tmp_path):
    manager, _ = _make_manager(tmp_path)
    tools = _list_tools_via_transport(manager)
    assert [tool.name for tool in tools] == ["telegram"]
    tool = tools[0]
    schema = tool.input_schema
    actions = schema["properties"]["action"]["enum"]
    assert schema["required"] == ["action", "input", "reasoning"]
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["additionalProperties"] is False
    # Over the real MCP transport the schema is still one root ``oneOf``
    # discriminated union: one branch per action in enum order, each pairing
    # the action const with that action's own closed canonical input.
    assert_compact_envelope(schema, actions)
    assert branch_actions(schema) == actions
    canonical = _telegram_input_schemas()
    for action, branch in action_input_schemas(schema).items():
        assert branch["additionalProperties"] is False, action
        if action == "settings":
            assert branch == {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
            continue
        assert branch == canonical[action], action
