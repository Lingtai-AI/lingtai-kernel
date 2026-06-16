"""Regression tests for Telegram notification mirror read-state handling."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai_kernel.notifications import submit


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
                "media": None,
                "callback_query": None,
                "reply_to_message_id": None,
            }
        ),
        encoding="utf-8",
    )
    return compound_id


def _write_telegram_notification(workdir: Path, message_id: str, *, preview: str = "hello") -> None:
    submit(
        workdir,
        "mcp.telegram",
        header="1 new event from MCP 'telegram'",
        icon="💬",
        priority="high",
        instructions="Call the MCP 'telegram' read/check action to fetch.",
        data={
            "count": 1,
            "source": "telegram",
            "has_human_messages": True,
            "previews": [
                {
                    "from": "alice",
                    "subject": "telegram message from alice via main",
                    "preview": preview,
                    "platform": "telegram",
                    "conversation_ref": "main:123",
                    "message_ref": message_id,
                }
            ],
        },
    )



def test_incoming_event_populates_generic_notification_refs(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=inbound_events.append,
    )

    manager.on_incoming(
        "main",
        {
            "message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "hello",
            }
        },
    )

    assert len(inbound_events) == 1
    metadata = inbound_events[0]["metadata"]
    assert metadata["message_id"] == "main:123:53"
    assert metadata["platform"] == "telegram"
    assert metadata["conversation_ref"] == "main:123"
    assert metadata["message_ref"] == "main:123:53"


def test_callback_query_incoming_does_not_publish_non_unique_message_ref(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=inbound_events.append,
    )

    manager.on_incoming(
        "main",
        {
            "callback_query": {
                "id": "callback-unique-id",
                "from": {"id": 1, "username": "alice"},
                "data": "yes",
                "message": {
                    "message_id": 53,
                    "chat": {"id": 123, "type": "private"},
                },
            }
        },
    )

    metadata = inbound_events[0]["metadata"]
    assert metadata["type"] == "callback_query"
    assert metadata["message_id"] == "main:123:53"
    assert metadata["message_ref"] is None


def test_callback_query_notification_is_not_cleared_by_reused_message_anchor(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "agent"
    compound_id = _write_inbox_message(workdir)
    submit(
        workdir,
        "mcp.telegram",
        header="1 new event from MCP 'telegram'",
        icon="💬",
        priority="high",
        data={
            "count": 1,
            "source": "telegram",
            "previews": [
                {
                    "from": "alice",
                    "subject": "telegram callback_query from alice via main",
                    "preview": f"[just now] #{compound_id} alice: yes",
                }
            ],
        },
    )

    _manager(workdir).handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 10}
    )

    assert (workdir / ".notification" / "mcp.telegram.json").exists()

def test_read_marks_message_read_and_clears_handled_notification(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    compound_id = _write_inbox_message(workdir)
    _write_telegram_notification(workdir, compound_id)

    result = _manager(workdir).handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 10}
    )

    assert result["status"] == "ok"
    assert [m["id"] for m in result["messages"]] == [compound_id]
    assert json.loads((workdir / "telegram" / "main" / "read.json").read_text()) == [
        compound_id
    ]
    assert not (workdir / ".notification" / "mcp.telegram.json").exists()


def test_read_keeps_notification_until_all_preview_messages_are_read(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    read_id = _write_inbox_message(workdir, chat_id=123, message_id=53)
    other_id = _write_inbox_message(workdir, chat_id=456, message_id=54)
    submit(
        workdir,
        "mcp.telegram",
        header="2 new events from MCP 'telegram'",
        icon="💬",
        priority="high",
        data={
            "count": 2,
            "source": "telegram",
            "has_human_messages": True,
            "previews": [
                {"from": "alice", "subject": "one", "preview": "one", "message_ref": read_id},
                {"from": "bob", "subject": "two", "preview": "two", "message_ref": other_id},
            ],
        },
    )

    result = _manager(workdir).handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 10}
    )

    assert result["status"] == "ok"
    assert (workdir / ".notification" / "mcp.telegram.json").exists()


def test_read_keeps_notification_when_preview_has_no_message_identity(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    _write_inbox_message(workdir)
    submit(
        workdir,
        "mcp.telegram",
        header="1 new event from MCP 'telegram'",
        icon="💬",
        priority="high",
        data={
            "count": 1,
            "source": "telegram",
            "previews": [
                {
                    "from": "alice",
                    "subject": "old malformed mirror",
                    "preview": "hello without an anchor",
                }
            ],
        },
    )

    _manager(workdir).handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 10}
    )

    assert (workdir / ".notification" / "mcp.telegram.json").exists()


def test_reply_marks_replied_message_read_and_clears_notification(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    compound_id = _write_inbox_message(workdir)
    _write_telegram_notification(workdir, compound_id)

    result = _manager(workdir).handle(
        {"action": "reply", "message_id": compound_id, "text": "handled"}
    )

    assert result["status"] == "sent"
    assert compound_id in json.loads(
        (workdir / "telegram" / "main" / "read.json").read_text()
    )
    assert not (workdir / ".notification" / "mcp.telegram.json").exists()


def test_legacy_conversation_preview_ids_can_clear_old_notification(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    compound_id = _write_inbox_message(workdir)
    submit(
        workdir,
        "mcp.telegram",
        header="1 new event from MCP 'telegram'",
        icon="💬",
        priority="high",
        data={
            "count": 1,
            "source": "telegram",
            "previews": [
                {
                    "from": "alice",
                    "subject": "legacy telegram message",
                    "preview": f"[just now] #{compound_id} alice: hello",
                }
            ],
        },
    )

    _manager(workdir).handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 10}
    )

    assert not (workdir / ".notification" / "mcp.telegram.json").exists()
