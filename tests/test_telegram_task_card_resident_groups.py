"""Authorized Task Card resident-owner and provider-call-group contract tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class Account:
    alias = "bot"

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}
        self.calls: list[tuple] = []
        self.next_id = 40

    def send_message(self, chat_id: int, text: str, **_: Any) -> dict:
        message_id = self.next_id
        self.next_id += 1
        self.calls.append(("send", chat_id, message_id, text))
        return {"message_id": message_id}

    def edit_message(self, chat_id: int, message_id: int, text: str, **_: Any) -> dict:
        self.calls.append(("edit", chat_id, message_id, text))
        return {"ok": True}

    def delete_message(self, chat_id: int, message_id: int) -> dict:
        self.calls.append(("delete", chat_id, message_id))
        return {"ok": True}

    def set_message_reaction(self, *_: Any, **__: Any) -> None:
        return None

    def get_task_card(self, chat_id: int) -> str | None:
        return self.ids.get(str(chat_id))

    def set_task_card(self, chat_id: int, value: str) -> None:
        self.ids[str(chat_id)] = value

    def list_task_card_chats(self) -> list[int]:
        return [int(key) for key in self.ids]

    def get_last_message_id(self, _chat_id: int) -> None:
        return None


class Service:
    def __init__(self, account: Account) -> None:
        self.account = account
        self.enabled = True
        self.rows = 1
        self.default_account = account

    def get_account(self, _alias: str) -> Account:
        return self.account

    def list_accounts(self) -> list[str]:
        return ["bot"]

    def taskcard_enabled(self) -> bool:
        return self.enabled

    def taskcard_normal_rows(self) -> int:
        return self.rows


def manager(tmp_path: Path, account: Account, service: Service | None = None) -> tuple[TelegramManager, Service]:
    service = service or Service(account)
    return (
        TelegramManager(
            service,
            working_dir=tmp_path,
            notification_store=FakeNotificationStore(),
            on_inbound=lambda _: None,
        ),
        service,
    )


def write_events(tmp_path: Path, events: list[dict]) -> None:
    path = tmp_path / "logs" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_first_real_inbound_established_chat_ensures_one_resident(tmp_path: Path) -> None:
    account = Account()
    telegram, service = manager(tmp_path, account)
    telegram.on_incoming(
        "bot",
        {
            "message": {
                "message_id": 8,
                "date": 1781600000,
                "from": {"username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "hello",
            }
        },
    )
    assert service.enabled is True
    assert account.ids == {"123": "bot:123:40"}
    assert [call[0] for call in account.calls] == ["send"]


def test_restart_rehydrates_and_updates_same_resident_id(tmp_path: Path) -> None:
    account = Account()
    telegram, _ = manager(tmp_path, account)
    telegram._ensure_task_card_resident("bot", 123)
    account.calls.clear()
    write_events(tmp_path, [
        {"type": "diary", "api_call_id": "api-1", "text": "first"},
        {"type": "tool_call", "api_call_id": "api-1", "tool_name": "bash", "tool_args": {"action": "run"}},
    ])
    restarted, _ = manager(tmp_path, account)
    restarted._poll_event_tail()
    assert account.ids["123"] == "bot:123:40"
    assert [call[0] for call in account.calls] == ["edit"]
    assert account.calls[0][2] == 40


def test_explicit_off_suppresses_and_on_reprojects_same_resident_once(tmp_path: Path) -> None:
    account = Account()
    telegram, service = manager(tmp_path, account)
    telegram._ensure_task_card_resident("bot", 123)
    resident_id = account.ids["123"]
    account.calls.clear()

    service.enabled = False
    telegram._broadcast_task_card_event_window()
    assert account.calls == []
    assert account.ids["123"] == resident_id

    service.enabled = True
    assert telegram._taskcard_enabled() is True
    assert [call[0] for call in account.calls] == ["edit"]
    assert account.calls[0][2] == 40
    assert account.ids["123"] == resident_id

    # Merely observing the still-enabled setting again is not another transition.
    assert telegram._taskcard_enabled() is True
    assert [call[0] for call in account.calls] == ["edit"]


def test_api_call_groups_emit_one_divider_and_numeric_window_counts_calls(tmp_path: Path) -> None:
    account = Account()
    telegram, service = manager(tmp_path, account)
    telegram._ensure_task_card_resident("bot", 123)
    account.calls.clear()
    service.rows = 2
    write_events(tmp_path, [
        {"type": "diary", "api_call_id": "api-1", "text": "text one"},
        {"type": "tool_call", "api_call_id": "api-1", "tool_name": "bash", "tool_args": {"action": "run"}},
        {"type": "diary", "api_call_id": "api-2", "text": "text two"},
        {"type": "tool_call", "api_call_id": "api-2", "tool_name": "read", "tool_args": {"action": "open"}},
    ])
    telegram._poll_event_tail()
    rendered = account.calls[-1][3]
    divider = telegram._TASK_CARD_API_CALL_DIVIDER
    assert rendered.count(divider) == 2
    assert rendered.count("text one") == 1
    assert rendered.count("bash.run") == 1
    assert rendered.count("text two") == 1
    assert rendered.count("read.open") == 1

    service.rows = 1
    telegram._broadcast_task_card_event_window()
    latest = account.calls[-1][3]
    assert latest.count(divider) == 1
    assert "text two" in latest and "read.open" in latest
    assert "text one" not in latest and "bash.run" not in latest


def test_public_text_is_bounded_and_hidden_or_raw_content_is_excluded(tmp_path: Path) -> None:
    account = Account()
    telegram, _ = manager(tmp_path, account)
    telegram._ensure_task_card_resident("bot", 123)
    account.calls.clear()
    visible = "visible " + ("x" * 2000)
    write_events(tmp_path, [
        {"type": "diary", "api_call_id": "api-1", "text": visible},
        {"type": "thinking", "api_call_id": "api-1", "text": "HIDDEN_THINKING"},
        {"type": "system_prompt", "api_call_id": "api-1", "text": "HIDDEN_SYSTEM"},
        {"type": "tool_result", "api_call_id": "api-1", "result": "RAW_RESULT"},
        {"type": "tool_call", "api_call_id": "api-1", "tool_name": "bash", "tool_args": {
            "action": "run", "command": "RAW_SECRET_COMMAND", "_reasoning": "safe"
        }},
    ])
    telegram._poll_event_tail()
    rendered = account.calls[-1][3]
    assert len(rendered) <= telegram._TASK_CARD_TEXT_LIMIT
    assert "visible" in rendered
    assert "HIDDEN_THINKING" not in rendered
    assert "HIDDEN_SYSTEM" not in rendered
    assert "RAW_RESULT" not in rendered
    assert "RAW_SECRET_COMMAND" not in rendered
