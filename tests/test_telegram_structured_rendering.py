"""Contract tests for native Telegram Rich Message rendering."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.render import (
    build_rich_message,
    plain_text_preview,
)
from tests._notification_store_helpers import FakeNotificationStore


STRUCTURED_MESSAGE = {
    "status": "success",
    "title": "修复完成",
    "summary": "三个 Telegram bot 已恢复。",
    "facts": [
        {"label": "版本", "value": "1.6.0"},
        {"label": "范围", "value": "所有 bot"},
    ],
    "bullets": ["已读回执即时发送", "任务卡保持常驻"],
    "steps": ["重启 host", "发送验收消息"],
    "code": {"text": "pytest -q", "language": "bash"},
    "next": {"label": "下一步", "text": "执行 GUI 验收"},
    "footer": "普通自由文本不受影响",
}


EXPECTED_RICH_MESSAGE = {
    "blocks": [
        {"type": "heading", "text": "✅ 修复完成", "size": 2},
        {"type": "paragraph", "text": "三个 Telegram bot 已恢复。"},
        {"type": "divider"},
        {
            "type": "list",
            "items": [
                {
                    "blocks": [{
                        "type": "paragraph",
                        "text": [
                            {"type": "bold", "text": "版本："},
                            "1.6.0",
                        ],
                    }],
                },
                {
                    "blocks": [{
                        "type": "paragraph",
                        "text": [
                            {"type": "bold", "text": "范围："},
                            "所有 bot",
                        ],
                    }],
                },
            ],
        },
        {
            "type": "list",
            "items": [
                {"blocks": [{"type": "paragraph", "text": "已读回执即时发送"}]},
                {"blocks": [{"type": "paragraph", "text": "任务卡保持常驻"}]},
            ],
        },
        {
            "type": "list",
            "items": [
                {
                    "blocks": [{"type": "paragraph", "text": "重启 host"}],
                    "type": "1",
                    "value": 1,
                },
                {
                    "blocks": [{"type": "paragraph", "text": "发送验收消息"}],
                    "type": "1",
                    "value": 2,
                },
            ],
        },
        {"type": "pre", "text": "pytest -q", "language": "bash"},
        {
            "type": "paragraph",
            "text": [
                {"type": "bold", "text": "下一步："},
                "执行 GUI 验收",
            ],
        },
        {"type": "footer", "text": "普通自由文本不受影响"},
    ],
}


class _Account:
    alias = "bot"

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_rich_message(self, chat_id, rich_message, **kwargs):
        self.calls.append(("send_rich_message", chat_id, rich_message, kwargs))
        return {"message_id": 1}

    def edit_message(self, **kwargs):
        self.calls.append(("edit_message", kwargs))
        return {"message_id": kwargs["message_id"]}

    def set_message_reaction(self, *_args, **_kwargs):
        return True


class _Service:
    def __init__(self, account):
        self.default_account = account

    def get_account(self, _alias):
        return self.default_account

    def list_accounts(self):
        return [self.default_account.alias]

    def taskcard_enabled(self):
        return False


def _manager(tmp_path: Path) -> tuple[TelegramManager, _Account]:
    account = _Account()
    manager = TelegramManager(
        _Service(account),
        working_dir=tmp_path,
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, account


def test_builder_uses_native_blocks_and_semantic_emphasis():
    assert build_rich_message(STRUCTURED_MESSAGE) == EXPECTED_RICH_MESSAGE
    assert plain_text_preview(STRUCTURED_MESSAGE) == (
        "✅ 修复完成\n"
        "三个 Telegram bot 已恢复。\n"
        "版本：1.6.0\n范围：所有 bot\n"
        "• 已读回执即时发送\n• 任务卡保持常驻\n"
        "1. 重启 host\n2. 发送验收消息\n"
        "pytest -q\n下一步：执行 GUI 验收\n"
        "普通自由文本不受影响"
    )


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ({"status": "celebration", "title": "x"}, "status"),
        ({"status": "success", "title": ""}, "title"),
        ({"status": "success", "title": "x", "bullets": "not-a-list"}, "bullets"),
        ({"status": "success", "title": "x", "facts": [{"label": "x"}]}, "facts"),
    ],
)
def test_builder_rejects_invalid_boundary_data(message, error):
    with pytest.raises(ValueError, match=error):
        build_rich_message(message)


def test_manager_sends_native_rich_message_and_persists_searchable_preview(tmp_path: Path):
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "bot",
        "chat_id": 123,
        "rendering_mode": "rich",
        "structured_message": STRUCTURED_MESSAGE,
    })

    assert result == {"status": "sent", "message_id": "bot:123:1"}
    assert account.calls == [
        ("send_rich_message", 123, EXPECTED_RICH_MESSAGE, {
            "reply_markup": None,
            "reply_to_message_id": None,
        }),
    ]
    sent_file = next((tmp_path / "telegram" / "bot" / "sent").glob("*/message.json"))
    sent = json.loads(sent_file.read_text(encoding="utf-8"))
    assert sent["text"].startswith("✅ 修复完成\n三个 Telegram bot 已恢复。")
    assert sent["structured_message"] == STRUCTURED_MESSAGE
    assert sent["rich_message"] == EXPECTED_RICH_MESSAGE


def test_manager_replies_and_edits_with_native_rich_message(tmp_path: Path):
    manager, account = _manager(tmp_path)

    reply = manager._reply({
        "message_id": "bot:123:456",
        "rendering_mode": "rich",
        "structured_message": STRUCTURED_MESSAGE,
    })
    edit = manager._edit({
        "message_id": "bot:123:1",
        "rendering_mode": "rich",
        "structured_message": STRUCTURED_MESSAGE,
    })

    assert reply["status"] == "sent"
    assert edit == {"status": "edited", "message_id": "bot:123:1"}
    assert account.calls[0] == (
        "send_rich_message",
        123,
        EXPECTED_RICH_MESSAGE,
        {"reply_markup": None, "reply_to_message_id": 456},
    )
    assert account.calls[1] == (
        "edit_message",
        {
            "chat_id": 123,
            "message_id": 1,
            "text": None,
            "rich_message": EXPECTED_RICH_MESSAGE,
            "reply_markup": None,
            "is_caption": False,
        },
    )


@pytest.mark.parametrize(
    "args",
    [
        {"text": "plain", "structured_message": STRUCTURED_MESSAGE},
        {"media": {"type": "photo", "path": "x"}, "structured_message": STRUCTURED_MESSAGE},
        {"entities": [], "structured_message": STRUCTURED_MESSAGE},
    ],
)
def test_rich_mode_rejects_conflicting_message_inputs(args, tmp_path: Path):
    manager, account = _manager(tmp_path)
    result = manager._send({
        "account": "bot",
        "chat_id": 123,
        "rendering_mode": "rich",
        **args,
    })
    assert "cannot be combined" in result["error"]
    assert account.calls == []


def test_non_rich_mode_rejects_structured_message(tmp_path: Path):
    manager, account = _manager(tmp_path)
    result = manager._send({
        "account": "bot",
        "chat_id": 123,
        "rendering_mode": "plain_text",
        "structured_message": STRUCTURED_MESSAGE,
    })
    assert result == {
        "error": "structured_message requires rendering_mode='rich'",
    }
    assert account.calls == []


class _CaptureAccount(TelegramAccount):
    def __init__(self):
        pass

    def _request(self, method, **kwargs):
        return {"method": method, "kwargs": kwargs, "message_id": 99}


def test_account_uses_send_rich_message_and_reply_parameters():
    account = _CaptureAccount()
    result = account.send_rich_message(
        123,
        EXPECTED_RICH_MESSAGE,
        reply_markup={"inline_keyboard": []},
        reply_to_message_id=456,
    )

    assert result["method"] == "sendRichMessage"
    assert result["kwargs"]["json"] == {
        "chat_id": 123,
        "rich_message": EXPECTED_RICH_MESSAGE,
        "reply_markup": {"inline_keyboard": []},
        "reply_parameters": {"message_id": 456},
    }


def test_account_edits_rich_message_without_text():
    account = _CaptureAccount()
    result = account.edit_message(
        123,
        456,
        None,
        rich_message=EXPECTED_RICH_MESSAGE,
    )

    assert result["method"] == "editMessageText"
    assert result["kwargs"]["json"] == {
        "chat_id": 123,
        "message_id": 456,
        "rich_message": EXPECTED_RICH_MESSAGE,
    }
