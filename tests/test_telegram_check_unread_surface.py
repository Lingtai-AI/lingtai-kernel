"""Regression test for Lingtai-AI/lingtai#470.

`telegram.check` must surface an actionable *unread incoming* human message
when unread > 0, instead of only previewing whatever message is newest overall
(which is often the bot's own last reply).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.telegram.manager import TelegramManager


class _FakeAccount:
    alias = "main"

    def get_account(self, _alias: str) -> "_FakeAccount":
        return self


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


def _write_message(
    workdir: Path,
    folder: str,
    record: dict[str, Any],
    *,
    account: str = "main",
) -> None:
    msg_dir = workdir / "telegram" / account / folder / record["id"].replace(":", "-")
    msg_dir.mkdir(parents=True, exist_ok=True)
    (msg_dir / "message.json").write_text(json.dumps(record), encoding="utf-8")


def test_check_surfaces_unread_incoming_when_bot_replied_last(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    manager = _manager(workdir)
    chat_id = 123

    # Incoming human message (unread), then a NEWER outgoing bot reply.
    _write_message(
        workdir,
        "inbox",
        {
            "id": f"main:{chat_id}:53",
            "from": {"username": "alice", "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "date": "2026-06-30T09:00:00Z",
            "text": "please clean up the extra files",
        },
    )
    _write_message(
        workdir,
        "sent",
        {
            "id": f"main:{chat_id}:54",
            "to": {"chat_id": chat_id},
            "from": {"is_bot": True},
            "date": "2026-06-30T09:01:00Z",
            "text": "记住了：你不喜欢电脑里有多余东西",
        },
    )

    result = manager.handle({"action": "check"})
    assert result["status"] == "ok"
    conv = next(c for c in result["messages"] if c["chat_id"] == chat_id)

    # last_* still reflects newest-overall (the bot's own reply) — unchanged.
    assert conv["last_from"].get("is_bot") is True
    assert conv["unread"] >= 1

    # The fix: an actionable unread incoming preview is now exposed.
    lui = conv["latest_unread_incoming"]
    assert lui is not None
    assert lui["id"] == f"main:{chat_id}:53"
    assert lui["text"] == "please clean up the extra files"
    assert lui["from"].get("is_bot") is False


def test_check_omits_unread_incoming_when_nothing_unread(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    manager = _manager(workdir)
    chat_id = 200

    _write_message(
        workdir,
        "inbox",
        {
            "id": f"main:{chat_id}:10",
            "from": {"username": "bob", "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "date": "2026-06-30T08:00:00Z",
            "text": "hi",
        },
    )
    # Mark it read so the conversation has zero unread.
    manager._mark_read("main", [f"main:{chat_id}:10"])

    result = manager.handle({"action": "check"})
    conv = next(c for c in result["messages"] if c["chat_id"] == chat_id)
    assert conv["unread"] == 0
    # No placeholder key when there is nothing actionable to surface.
    assert "latest_unread_incoming" not in conv
