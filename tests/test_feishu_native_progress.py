"""Focused Feishu native Typing reaction and progress-card coverage."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lark_channel import Conversation, Identity, InboundMessage, TextContent

from lingtai.mcp_servers.feishu._family import handle_feishu
from lingtai.mcp_servers.feishu.account import FeishuInboundEvent
from lingtai.mcp_servers.feishu import manager as manager_module
from lingtai.mcp_servers.feishu.manager import (
    FeishuManager,
    TypingIndicatorManager,
)


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._next_message = 0

    def add_typing_reaction(self, message_id: str) -> str:
        self.calls.append(("typing_add", (message_id,)))
        return "reaction-typing"

    def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
        self.calls.append(("reaction_remove", (message_id, reaction_id)))
        return True

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        self.calls.append(("reaction", (message_id, emoji_type)))
        return True

    def send_content(
        self, receive_id: str, receive_id_type: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("send", (receive_id, receive_id_type, message)))
        self._next_message += 1
        return {
            "message_id": f"om_{self._next_message}",
            "message_ids": [f"om_{self._next_message}"],
            "chat_id": "oc_chat",
        }

    def update_content(
        self, message_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("edit", (message_id, message)))
        return {"message_id": message_id, "message_ids": [message_id]}


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account
        self._account = account

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self._account.alias
        return self._account


def _manager(tmp_path: Path) -> tuple[FeishuManager, _FakeAccount]:
    account = _FakeAccount()
    return (
        FeishuManager(
            _FakeService(account),
            working_dir=tmp_path,
            on_inbound=lambda _payload: None,
        ),
        account,
    )


def _call(manager: FeishuManager, action: str, input_: dict[str, Any]) -> dict:
    return handle_feishu(
        manager,
        {"action": action, "input": input_, "reasoning": "focused test"},
    )


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(tmp_path.glob("feishu/main/sent/*/message.json"))
    ]


def test_typing_presence_uses_native_reaction_without_temp_messages() -> None:
    account = _FakeAccount()
    typing = TypingIndicatorManager()

    reaction_id = typing.start_typing(
        account, "oc_chat", "om_incoming", "ou_user", "open_id",
    )
    stopped = typing.stop_typing(account, "oc_chat")

    assert reaction_id == "reaction-typing"
    assert stopped is True
    assert account.calls == [
        ("typing_add", ("om_incoming",)),
        ("reaction_remove", ("om_incoming", "reaction-typing")),
    ]


def test_incoming_keeps_seen_reaction_and_starts_native_typing(
    tmp_path: Path, monkeypatch: Any,
) -> None:
    manager, account = _manager(tmp_path)

    class _TypingProbe:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def start_typing(self, *args: Any) -> str:
            self.calls.append(args)
            return "reaction-typing"

    typing = _TypingProbe()
    monkeypatch.setattr(manager_module, "_typing_manager", typing)
    message = InboundMessage(
        id="om_incoming",
        create_time=0,
        conversation=Conversation(chat_id="oc_chat", chat_type="p2p"),
        sender=Identity(open_id="ou_user", display_name="User"),
        content=TextContent(text="hello"),
        content_text="hello",
        body_text="hello",
        raw_content_type="text",
    )

    manager.on_incoming(
        "main",
        FeishuInboundEvent(
            message=message,
            feishu={
                "schema": "2.0",
                "header": {"event_id": "event-incoming"},
                "event": {"message": {"message_id": "om_incoming"}},
            },
        ),
    )

    assert ("reaction", ("om_incoming", "OK")) in account.calls
    assert len(typing.calls) == 1
    assert typing.calls[0][1:] == (
        "oc_chat", "om_incoming", "ou_user", "open_id",
    )


def test_progress_card_updates_in_place_and_final_send_is_separate(
    tmp_path: Path,
) -> None:
    manager, account = _manager(tmp_path)

    progress = _call(manager, "send", {
        "receive_id": "ou_user",
        "text": "phase one",
        "placeholder": True,
    })
    updated = _call(manager, "edit", {
        "message_id": progress["message_id"],
        "content": {"type": "markdown", "markdown": "**phase two**"},
    })
    final = _call(manager, "send", {
        "receive_id": "ou_user",
        "text": "durable final",
    })

    assert progress["content_type"] == "card"
    assert progress["placeholder"] is True
    assert updated["content_type"] == "card"
    assert updated["placeholder"] is True
    assert final["content_type"] == "text"
    assert "placeholder" not in final

    send_calls = [call for call in account.calls if call[0] == "send"]
    edit_call = next(call for call in account.calls if call[0] == "edit")
    assert send_calls[0][1][2]["card"]["schema"] == "2.0"
    assert edit_call[1][1]["card"]["schema"] == "2.0"
    assert send_calls[1][1][2] == {"text": "durable final"}

    records = _records(tmp_path)
    progress_record = next(record for record in records if record.get("placeholder"))
    final_record = next(record for record in records if not record.get("placeholder"))
    assert progress_record["status"] == "placeholder"
    assert progress_record["content"]["type"] == "card"
    assert "phase two" in progress_record["text"]
    assert final_record["status"] == "sent"
    assert final_record["content"] == {"type": "text", "text": "durable final"}


def test_progress_card_rejects_custom_card_replacement_before_transport(
    tmp_path: Path,
) -> None:
    manager, account = _manager(tmp_path)
    progress = _call(manager, "send", {
        "receive_id": "ou_user",
        "text": "phase one",
        "placeholder": True,
    })
    calls_before = list(account.calls)

    rejected = _call(manager, "edit", {
        "message_id": progress["message_id"],
        "content": {
            "type": "card",
            "card": {
                "schema": "2.0",
                "body": {"elements": []},
            },
        },
    })

    assert "progress card edits only support" in rejected["error"]
    assert account.calls == calls_before
