"""Outbound-file containment tests for MCP send surfaces (issue #631).

A prompt-injection or over-broad tool call must not be able to exfiltrate
arbitrary local files through IMAP ``attachments``, Telegram ``media.path``
or WeChat ``media_path``.  These tests pin the shared policy in
``lingtai.mcp_servers._outbound_files`` and its enforcement at each manager
boundary: absolute paths outside the agent working directory, ``..`` escapes
and symlink escapes are rejected before any bytes are read or sent.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lingtai.mcp_servers._outbound_files import (
    OutboundFileError,
    resolve_outbound_file,
)
from lingtai.mcp_servers.imap.manager import IMAPMailManager
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.wechat import api as wx_api
from lingtai.mcp_servers.wechat import media as wx_media
from lingtai.mcp_servers.wechat.manager import WechatManager
from tests._notification_store_helpers import FakeNotificationStore

# ---------------------------------------------------------------------------
# Shared policy helper
# ---------------------------------------------------------------------------

class TestResolveOutboundFile:
    def test_relative_path_resolves_against_workdir(self, tmp_path):
        assert resolve_outbound_file("report.pdf", tmp_path) == (
            tmp_path / "report.pdf"
        ).resolve()

    def test_absolute_path_inside_workdir_allowed(self, tmp_path):
        inside = tmp_path / "sub" / "file.txt"
        assert resolve_outbound_file(str(inside), tmp_path) == inside.resolve()

    def test_absolute_path_outside_workdir_rejected(self, tmp_path):
        outside = tmp_path.parent / "secret.txt"
        with pytest.raises(OutboundFileError):
            resolve_outbound_file(str(outside), tmp_path)

    def test_dotdot_escape_rejected(self, tmp_path):
        with pytest.raises(OutboundFileError):
            resolve_outbound_file("../secret.txt", tmp_path)

    def test_symlink_escape_rejected(self, tmp_path):
        secret = tmp_path.parent / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(secret)
        with pytest.raises(OutboundFileError):
            resolve_outbound_file("link.txt", tmp_path)

    def test_missing_file_inside_workdir_still_resolves(self, tmp_path):
        # resolve() does not require existence; a not-yet-created artifact
        # path inside the workdir is allowed (the caller checks is_file()).
        assert resolve_outbound_file("pending.txt", tmp_path) == (
            tmp_path / "pending.txt"
        ).resolve()

# ---------------------------------------------------------------------------
# IMAP manager (attachments)
# ---------------------------------------------------------------------------

class FakeImapAccount:
    address = "me@example.com"

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.stored_flags: list[tuple] = []

    def fetch_full(self, folder: str, uid: str) -> dict:
        return {
            "from": "Sender <sender@example.com>",
            "from_address": "sender@example.com",
            "subject": "Question",
            "message_id": "<orig@example.com>",
            "references": "<parent@example.com>",
        }

    def send_email(self, **kwargs):
        self.sent.append(kwargs)

    def store_flags(self, folder: str, uid: str, flags: list[str]) -> bool:
        self.stored_flags.append((folder, uid, flags))
        return True


class FakeImapService:
    def __init__(self, account: FakeImapAccount) -> None:
        self.default_account = account
        self._map = {account.address: account}

    def get_account(self, address: str | None):
        if address is None:
            return self.default_account
        return self._map.get(address)


def _imap_manager(tmp_path: Path, account: FakeImapAccount) -> IMAPMailManager:
    return IMAPMailManager(
        FakeImapService(account),
        working_dir=tmp_path,
        tcp_alias="/tmp/imap-bridge",
        on_inbound=lambda payload: None,
    )


def test_imap_send_rejects_attachment_outside_workdir(tmp_path):
    account = FakeImapAccount()
    manager = _imap_manager(tmp_path, account)

    result = manager._send({
        "address": "victim@example.com",
        "message": "leak",
        "attachments": [str(tmp_path.parent / "id_rsa")],
    }, account)

    assert "error" in result
    assert "outside working directory" in result["error"]
    assert account.sent == []


def test_imap_send_accepts_relative_attachment_inside_workdir(tmp_path):
    account = FakeImapAccount()
    manager = _imap_manager(tmp_path, account)
    (tmp_path / "report.pdf").write_text("x", encoding="utf-8")

    result = manager._send({
        "address": "a@example.com",
        "message": "hi",
        "attachments": ["report.pdf"],
    }, account)

    assert result["status"] == "delivered"
    assert account.sent[0]["attachments"] == [
        str((tmp_path / "report.pdf").resolve())
    ]


def test_imap_reply_rejects_attachment_outside_workdir(tmp_path):
    account = FakeImapAccount()
    manager = _imap_manager(tmp_path, account)

    result = manager._reply({
        "email_id": "me@example.com:INBOX:42",
        "message": "re",
        "attachments": ["/etc/passwd"],
    }, account)

    assert "error" in result
    assert "outside working directory" in result["error"]
    assert account.sent == []


# ---------------------------------------------------------------------------
# Telegram manager (media.path)
# ---------------------------------------------------------------------------

class FakeTgAccount:
    alias = "mybot"

    def __init__(self) -> None:
        self.calls: list = []

    def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", args, kwargs))
        return {"message_id": 1}

    def send_photo(self, *args, **kwargs):
        self.calls.append(("send_photo", args, kwargs))
        return {"message_id": 2}

    def send_document(self, *args, **kwargs):
        self.calls.append(("send_document", args, kwargs))
        return {"message_id": 3}

    def set_message_reaction(self, *args, **kwargs):
        return True


class FakeTgService:
    def __init__(self) -> None:
        self.default_account = FakeTgAccount()

    def get_account(self, alias):
        return self.default_account

    def list_accounts(self):
        return ["mybot"]


def _tg_manager(tmp_path: Path) -> tuple[TelegramManager, FakeTgAccount]:
    service = FakeTgService()
    manager = TelegramManager(
        service,
        working_dir=tmp_path,
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, service.default_account


def test_telegram_send_rejects_media_path_outside_workdir(tmp_path):
    manager, account = _tg_manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "media": {"type": "document", "path": "/etc/passwd"},
    })

    assert "error" in result
    assert "outside working directory" in result["error"]
    assert account.calls == []


def test_telegram_send_accepts_media_path_inside_workdir(tmp_path):
    manager, account = _tg_manager(tmp_path)
    media_path = tmp_path / "demo.txt"
    media_path.write_text("demo", encoding="utf-8")

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "text": "demo",
        "media": {"type": "document", "path": str(media_path)},
    })

    assert "error" not in result
    assert account.calls[0][0] == "send_document"
    assert account.calls[0][1][1] == str(media_path.resolve())


def test_telegram_send_rejects_dotdot_media_path(tmp_path):
    manager, account = _tg_manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "media": {"type": "photo", "path": "../secret.png"},
    })

    assert "error" in result
    assert "outside working directory" in result["error"]
    assert account.calls == []


# ---------------------------------------------------------------------------
# WeChat manager (media_path)
# ---------------------------------------------------------------------------

def _wx_manager(tmp_path: Path) -> WechatManager:
    return WechatManager(
        token="test-token",
        user_id="test-bot",
        working_dir=tmp_path,
        on_inbound=lambda event: None,
    )


def test_wechat_send_rejects_media_path_outside_workdir(tmp_path, monkeypatch):
    manager = _wx_manager(tmp_path)
    uploads: list = []

    async def _fake_upload(path, base_url, token, user_id):
        uploads.append(str(path))
        raise AssertionError("upload_media must not run for an outside path")

    monkeypatch.setattr(wx_media, "upload_media", _fake_upload)

    result = manager._handle_send({
        "user_id": "wxid@im.wechat",
        "media_path": "/etc/passwd",
    })

    assert "error" in result
    assert "outside working directory" in result["error"]
    assert uploads == []


def test_wechat_send_accepts_media_path_inside_workdir(tmp_path, monkeypatch):
    manager = _wx_manager(tmp_path)
    media_path = tmp_path / "pic.png"
    media_path.write_bytes(b"PNG")
    seen: dict = {}

    async def _fake_upload(path, base_url, token, user_id):
        seen["upload_path"] = str(path)
        return {"media_id": "m1", "media_type": "image"}

    async def _fake_send_message(*args, **kwargs):
        seen["sent"] = True

    monkeypatch.setattr(wx_media, "upload_media", _fake_upload)
    monkeypatch.setattr(
        wx_media, "make_media_item", lambda info, path: object()
    )
    monkeypatch.setattr(wx_api, "send_message", _fake_send_message)
    monkeypatch.setattr(manager, "_run_async", asyncio.run)

    result = manager._handle_send({
        "user_id": "wxid@im.wechat",
        "media_path": str(media_path),
    })

    assert result["status"] == "ok"
    assert seen["upload_path"] == str(media_path.resolve())
    assert seen["sent"] is True
