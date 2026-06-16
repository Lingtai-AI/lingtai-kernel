"""Regression tests for issue #170 point 7.

The Telegram ``check`` action reports a per-conversation ``unread`` count. Sent
(bot-originated) messages are persisted to ``sent/`` and are never recorded in
``read.json`` — so before the fix every bot reply was counted as unread and
surfaced to the agent as a fresh ``[NEW]`` message. Outgoing messages must not
be counted unread; inbound unread behavior must be unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from lingtai.mcp_servers.telegram.manager import TelegramManager


class _DummyService:
    """Minimal stand-in; ``_check`` only touches the filesystem."""


def _make_manager(tmp_path: Path) -> TelegramManager:
    return TelegramManager(
        _DummyService(),  # type: ignore[arg-type]
        working_dir=tmp_path,
        on_inbound=lambda _payload: None,
    )


def _write_inbox_message(
    tmp_path: Path,
    account: str,
    chat_id: int,
    msg_id: int,
    *,
    text: str = "hi",
    sender: str = "alice",
    date: str = "2026-06-16T12:00:00Z",
) -> str:
    compound_id = f"{account}:{chat_id}:{msg_id}"
    msg_dir = tmp_path / "telegram" / account / "inbox" / str(msg_id)
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": compound_id,
        "from": {"username": sender},
        "chat": {"id": chat_id},
        "date": date,
        "text": text,
    }
    (msg_dir / "message.json").write_text(json.dumps(payload), encoding="utf-8")
    return compound_id


def _write_sent_message(
    tmp_path: Path,
    account: str,
    chat_id: int,
    msg_id: int,
    *,
    text: str = "ack",
    date: str = "2026-06-16T12:05:00Z",
) -> str:
    compound_id = f"{account}:{chat_id}:{msg_id}"
    # Use a distinct on-disk dir name (sent records key by uuid in production).
    msg_dir = tmp_path / "telegram" / account / "sent" / f"sent-{msg_id}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": compound_id,
        "to": {"chat_id": chat_id},
        "date": date,
        "text": text,
        "status": "sent",
    }
    (msg_dir / "message.json").write_text(json.dumps(payload), encoding="utf-8")
    return compound_id


def _conversation(result: dict, chat_id: int) -> dict:
    for conv in result["messages"]:
        if conv["chat_id"] == chat_id:
            return conv
    raise AssertionError(f"no conversation for chat {chat_id} in {result}")


def test_sent_message_not_counted_unread(tmp_path: Path) -> None:
    """A bot-sent reply must not inflate the unread/[NEW] count."""
    account, chat_id = "default", 4242
    _write_sent_message(tmp_path, account, chat_id, 100)
    mgr = _make_manager(tmp_path)

    result = mgr._check({"account": account})

    conv = _conversation(result, chat_id)
    assert conv["total"] == 1
    assert conv["unread"] == 0


def test_inbound_message_still_counted_unread(tmp_path: Path) -> None:
    """Inbound (human) unread behavior is unchanged by the fix."""
    account, chat_id = "default", 4242
    _write_inbox_message(tmp_path, account, chat_id, 1)
    mgr = _make_manager(tmp_path)

    result = mgr._check({"account": account})

    conv = _conversation(result, chat_id)
    assert conv["total"] == 1
    assert conv["unread"] == 1


def test_mixed_conversation_counts_only_inbound_unread(tmp_path: Path) -> None:
    """A conversation with both directions counts only the inbound message."""
    account, chat_id = "default", 777
    _write_inbox_message(tmp_path, account, chat_id, 1, text="please reply")
    _write_sent_message(tmp_path, account, chat_id, 2, text="on it")
    mgr = _make_manager(tmp_path)

    result = mgr._check({"account": account})

    conv = _conversation(result, chat_id)
    assert conv["total"] == 2
    assert conv["unread"] == 1


def test_read_inbound_not_counted_unread(tmp_path: Path) -> None:
    """An inbound message already in read.json is not counted unread."""
    account, chat_id = "default", 12
    compound_id = _write_inbox_message(tmp_path, account, chat_id, 1)
    read_path = tmp_path / "telegram" / account / "read.json"
    read_path.parent.mkdir(parents=True, exist_ok=True)
    read_path.write_text(json.dumps([compound_id]), encoding="utf-8")
    mgr = _make_manager(tmp_path)

    result = mgr._check({"account": account})

    conv = _conversation(result, chat_id)
    assert conv["total"] == 1
    assert conv["unread"] == 0
