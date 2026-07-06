"""Regression tests for Telegram ``check`` unread accounting.

The bot's own outgoing replies live in ``sent/`` and must never be counted
as unread by ``check`` — otherwise the counter is inflated by messages the
agent already produced and can never drain (issue #715).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.telegram.manager import TelegramManager


class _FakeAccount:
    alias = "main"

    def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> dict[str, Any]:
        return {"message_id": 9001, "chat": {"id": chat_id}, "text": text}

    def set_message_reaction(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def send_chat_action(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeService:
    default_account = _FakeAccount()

    def get_account(self, _alias: str) -> _FakeAccount:
        return self.default_account


def _manager(workdir: Path) -> TelegramManager:
    return TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=lambda _event: None,
    )


def _write_inbox_message(
    workdir: Path,
    *,
    account: str = "main",
    chat_id: int = 123,
    message_id: int = 53,
    text: str = "hello",
) -> str:
    compound_id = f"{account}:{chat_id}:{message_id}"
    msg_dir = workdir / "telegram" / account / "inbox" / f"uuid-{chat_id}-{message_id}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    (msg_dir / "message.json").write_text(
        json.dumps(
            {
                "id": compound_id,
                "from": {"username": "alice"},
                "chat": {"id": chat_id, "type": "private"},
                "date": "2026-05-21T23:53:00Z",
                "text": text,
            }
        ),
        encoding="utf-8",
    )
    return compound_id


def _write_sent_message(
    workdir: Path,
    *,
    account: str = "main",
    chat_id: int = 123,
    message_id: int = 900,
    text: str = "on it",
    date: str = "2026-05-21T23:54:00Z",
) -> str:
    compound_id = f"{account}:{chat_id}:{message_id}"
    msg_dir = workdir / "telegram" / account / "sent" / f"uuid-sent-{message_id}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    (msg_dir / "message.json").write_text(
        json.dumps(
            {
                "id": compound_id,
                "to": {"chat_id": chat_id},
                "date": date,
                "text": text,
                "status": "sent",
            }
        ),
        encoding="utf-8",
    )
    return compound_id


def _conversation(result: dict, chat_id: int) -> dict:
    for conv in result["messages"]:
        if conv["chat_id"] == chat_id:
            return conv
    raise AssertionError(f"no conversation for chat_id={chat_id}: {result}")


def test_check_does_not_count_outgoing_replies_as_unread(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    # One incoming message that has already been read...
    incoming_id = _write_inbox_message(workdir)
    _manager(workdir)._mark_read("main", [incoming_id])
    # ...and several of the bot's own replies in the same chat.
    for i in range(5):
        _write_sent_message(workdir, message_id=900 + i)

    result = _manager(workdir).handle({"action": "check", "account": "main"})

    conv = _conversation(result, 123)
    # 1 incoming + 5 outgoing all appear in the conversation view...
    assert conv["total"] == 6
    # ...but the read incoming and every outgoing reply drain to zero unread.
    assert conv["unread"] == 0


def test_check_counts_only_unread_incoming(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    _write_inbox_message(workdir, message_id=53, text="first")
    _write_inbox_message(workdir, message_id=54, text="second")
    _write_sent_message(workdir, message_id=900)

    result = _manager(workdir).handle({"action": "check", "account": "main"})

    conv = _conversation(result, 123)
    assert conv["total"] == 3
    # Two unread incoming; the outgoing reply is not counted.
    assert conv["unread"] == 2
