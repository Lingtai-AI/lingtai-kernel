"""Focused Feishu local-command and internal control-card coverage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import lark_channel as lark
from lark_channel import Conversation, Identity, InboundMessage, TextContent

from lingtai.mcp_servers.feishu.account import (
    FeishuInboundCardAction,
    FeishuInboundEvent,
)
from lingtai.mcp_servers.feishu.control_cards import FeishuControlCards
from lingtai.mcp_servers.feishu.manager import FeishuManager
from lingtai.mcp_servers.feishu.service import FeishuService


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.replies: list[tuple[Any, ...]] = []
        self.updates: list[tuple[Any, ...]] = []

    def reply_content(
        self,
        message_id: str,
        chat_id: str,
        message: dict[str, Any],
        *,
        reply_in_thread: bool,
    ) -> dict[str, Any]:
        self.replies.append((message_id, chat_id, message, reply_in_thread))
        return {"message_id": "om_control", "chat_id": chat_id}

    def update_content(
        self, message_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.updates.append((message_id, message))
        return {"message_id": message_id}


class _FakeService:
    def __init__(self, account: _FakeAccount | None = None) -> None:
        self.default_account = account or _FakeAccount()
        self._enabled = True
        self._normal_rows = 5
        self._listener: Callable[[bool], None] | None = None

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == "main"
        return self.default_account

    def set_taskcard_listener(self, listener: Callable[[bool], None]) -> None:
        self._listener = listener

    def taskcard_enabled(self) -> bool:
        return self._enabled

    def set_taskcard_enabled(self, enabled: bool) -> None:
        changed = self._enabled != enabled
        self._enabled = enabled
        if changed and self._listener:
            self._listener(enabled)

    def taskcard_normal_rows(self) -> int:
        return self._normal_rows

    def set_taskcard_normal_rows(self, value: int) -> None:
        self._normal_rows = value


def _manager(
    tmp_path: Path,
    *,
    language: str = "zh",
    wakes: list[dict[str, Any]] | None = None,
    service: _FakeService | None = None,
) -> tuple[FeishuManager, _FakeService]:
    (tmp_path / ".agent.json").write_text(
        json.dumps({"language": language}), encoding="utf-8"
    )
    actual_service = service or _FakeService()
    manager = FeishuManager(
        actual_service,
        working_dir=tmp_path,
        on_inbound=(wakes if wakes is not None else []).append,
    )
    return manager, actual_service


def _message(
    text: str,
    *,
    message_id: str = "om_command",
    chat_type: str = "p2p",
    thread_id: str | None = None,
) -> FeishuInboundEvent:
    inbound = InboundMessage(
        id=message_id,
        create_time=0,
        conversation=Conversation(
            chat_id="oc_chat",
            chat_type=chat_type,
            thread_id=thread_id,
        ),
        sender=Identity(open_id="ou_allowed", sender_type="user"),
        content=TextContent(raw={"text": text}, text=text),
        raw={},
        content_text=text,
        body_text=text,
        mentioned_bot=chat_type != "p2p",
        raw_content_type="text",
    )
    return FeishuInboundEvent(
        message=inbound,
        feishu={
            "schema": "2.0",
            "header": {"event_id": f"evt-{message_id}"},
        },
    )


def _control_action(event_id: str, command: str) -> FeishuInboundCardAction:
    event = lark.CardActionEvent(
        message_id="om_control",
        chat_id="oc_chat",
        operator=lark.EventOperator(open_id="ou_allowed"),
        action=lark.CardActionPayload(
            tag="button",
            value={
                "lingtai_control": {
                    "version": 1,
                    "command": command,
                    "argument": "",
                }
            },
        ),
        raw={
            "schema": "2.0",
            "header": {"event_id": event_id},
            "event": {"context": {"open_thread_id": "omt_topic"}},
        },
    )
    return FeishuInboundCardAction(action=event, feishu=event.raw)


def test_help_is_replied_locally_in_topic_without_agent_wake(
    tmp_path: Path,
) -> None:
    wakes: list[dict[str, Any]] = []
    manager, service = _manager(tmp_path, wakes=wakes)

    manager.on_incoming(
        "main",
        _message("/help", chat_type="topic", thread_id="omt_topic"),
    )

    assert wakes == []
    assert not list(tmp_path.glob("feishu/main/inbox/*/message.json"))
    assert len(service.default_account.replies) == 1
    message_id, chat_id, payload, reply_in_thread = service.default_account.replies[0]
    assert (message_id, chat_id, reply_in_thread) == (
        "om_command",
        "oc_chat",
        True,
    )
    card = payload["card"]
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "LingTai · 控制中心"
    values = [
        element["value"]
        for element in card["body"]["elements"]
        if element.get("tag") == "button"
    ]
    assert values
    assert all(set(value) == {"lingtai_control"} for value in values)


def test_replayed_local_command_is_deduped_before_second_reply(
    tmp_path: Path,
) -> None:
    manager, service = _manager(tmp_path)
    message = _message("/status")

    manager.on_incoming("main", message)
    manager.on_incoming("main", message)

    assert len(service.default_account.replies) == 1


def test_unknown_slash_command_is_not_intercepted(tmp_path: Path) -> None:
    manager, service = _manager(tmp_path)

    assert manager._handle_local_command("main", _message("/future-command")) is False
    assert service.default_account.replies == []


def test_unknown_language_falls_back_to_english_and_wen_is_available(
    tmp_path: Path,
) -> None:
    english, english_service = _manager(tmp_path, language="fr")
    english.on_incoming("main", _message("/help", message_id="om_en"))
    english_title = english_service.default_account.replies[0][2]["card"]["header"][
        "title"
    ]["content"]

    wen_dir = tmp_path / "wen"
    wen_dir.mkdir()
    wen, wen_service = _manager(wen_dir, language="wen")
    wen.on_incoming("main", _message("/help", message_id="om_wen"))
    wen_title = wen_service.default_account.replies[0][2]["card"]["header"]["title"][
        "content"
    ]

    assert english_title == "LingTai · Controls"
    assert wen_title == "LingTai · 总览"


def test_internal_callback_updates_in_place_without_persist_or_wake(
    tmp_path: Path,
) -> None:
    wakes: list[dict[str, Any]] = []
    manager, service = _manager(tmp_path, wakes=wakes)
    callback = _control_action("evt-control", "status")

    manager.on_card_action("main", callback)
    manager.on_card_action("main", callback)

    assert wakes == []
    assert len(service.default_account.updates) == 1
    assert service.default_account.updates[0][0] == "om_control"
    assert not list(tmp_path.glob("feishu/main/inbox/*/message.json"))
    store = (tmp_path / "feishu" / "control_callbacks.json").read_text(encoding="utf-8")
    assert "evt-control" not in store

    restarted_service = _FakeService()
    restarted, _service = _manager(tmp_path, wakes=wakes, service=restarted_service)
    restarted.on_card_action("main", callback)
    assert restarted_service.default_account.updates == []


def test_non_control_value_remains_a_business_callback(tmp_path: Path) -> None:
    assert FeishuControlCards.callback_text({"action": "confirm"}) is None

    wakes: list[dict[str, Any]] = []
    manager, _service = _manager(tmp_path, wakes=wakes)
    event = lark.CardActionEvent(
        message_id="om_business",
        chat_id="oc_chat",
        operator=lark.EventOperator(open_id="ou_allowed"),
        action=lark.CardActionPayload(tag="button", value={"action": "confirm"}),
        raw={"header": {"event_id": "evt-business"}},
    )

    manager.on_card_action(
        "main", FeishuInboundCardAction(action=event, feishu=event.raw)
    )

    assert len(wakes) == 1
    assert wakes[0]["wake"] is True


def test_feishu_taskcard_preferences_persist_for_the_agent(
    tmp_path: Path,
) -> None:
    config = [
        {
            "alias": "main",
            "app_id": "cli_test",
            "app_secret": "test-secret",
        }
    ]
    service = FeishuService(tmp_path, config, lambda _alias, _data: None)
    service.set_taskcard_enabled(False)
    service.set_taskcard_normal_rows(7)

    restarted = FeishuService(tmp_path, config, lambda _alias, _data: None)

    assert restarted.taskcard_enabled() is False
    assert restarted.taskcard_normal_rows() == 7
    assert json.loads(
        (tmp_path / "feishu" / "taskcard.json").read_text(encoding="utf-8")
    ) == {"taskcard": False, "normal_rows": 7}
