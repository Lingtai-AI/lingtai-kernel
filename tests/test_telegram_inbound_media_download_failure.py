"""Regression coverage for Telegram inbound document download failures."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lingtai.mcp_servers.telegram import manager as telegram_manager
from lingtai.mcp_servers.telegram.manager import TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class _FakeAccount:
    alias = "main"

    def __init__(
        self,
        *,
        download: tuple[str, bytes] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._download = download
        self._error = error
        self.requested_file_ids: list[str] = []
        self.sent_messages: list[tuple[int, str]] = []

    def get_file(self, file_id: str) -> tuple[str, bytes]:
        self.requested_file_ids.append(file_id)
        if self._error is not None:
            raise self._error
        assert self._download is not None
        return self._download

    def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> dict[str, Any]:
        self.sent_messages.append((chat_id, text))
        return {"message_id": 9001, "chat": {"id": chat_id}, "text": text}

    def set_message_reaction(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account

    def get_account(self, _alias: str) -> _FakeAccount:
        return self.default_account

    def list_accounts(self) -> list[str]:
        return ["main"]


def _document_update(*, caption: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "message_id": 53,
        "date": 1781600000,
        "from": {"id": 1, "username": "alice"},
        "chat": {"id": 123, "type": "private"},
        "document": {
            "file_name": "report.zip",
            "file_size": 25 * 1024 * 1024,
            "file_id": "document-file-id",
            "mime_type": "application/zip",
        },
    }
    if caption is not None:
        message["caption"] = caption
    return {"message": message}


def _receive(
    tmp_path: Path,
    monkeypatch: Any,
    account: _FakeAccount,
    update: dict[str, Any],
) -> tuple[TelegramManager, list[dict[str, Any]]]:
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(account),
        working_dir=tmp_path,
        on_inbound=inbound_events.append,
        notification_store=FakeNotificationStore(),
    )
    monkeypatch.setattr(telegram_manager._typing_manager, "start_typing", lambda *_args: None)
    manager.on_incoming("main", update)
    return manager, inbound_events


def _read_latest(manager: TelegramManager) -> dict[str, Any]:
    result = manager._read({"account": "main", "chat_id": 123, "limit": 1})
    assert result["status"] == "ok"
    return result["messages"][0]


def test_hosted_size_error_is_visible_actionable_and_raw_only(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: Any,
) -> None:
    reason = "Bad Request: file is too big"
    account = _FakeAccount(error=RuntimeError(f"Telegram API error: {reason}"))
    caplog.set_level(logging.WARNING)

    manager, inbound_events = _receive(
        tmp_path, monkeypatch, account, _document_update()
    )
    message = _read_latest(manager)

    assert message["media"] == {
        "type": "document",
        "file_name": "report.zip",
        "file_size": 25 * 1024 * 1024,
        "file_id": "document-file-id",
        "mime_type": "application/zip",
        "download_error": reason,
    }
    assert "path" not in message["media"]
    assert not list(
        (tmp_path / "telegram" / "main" / "inbox").glob("*/attachments/*")
    )
    assert reason in message["text"]
    assert "parts no larger than 20 MB" in message["text"]
    assert "another transfer method" in message["text"]

    assert account.requested_file_ids == ["document-file-id"]
    assert account.sent_messages == []
    assert len(inbound_events) == 1
    event = inbound_events[0]
    latest = event["metadata"]["latest_incoming"]
    assert event["metadata"]["has_media"] is True
    assert reason in event["body"]
    assert "parts no larger than 20 MB" in latest["text"]
    assert latest["media"] == {
        "type": "document",
        "mime_type": "application/zip",
    }
    assert {"file_name", "file_size", "file_id"}.isdisjoint(latest["media"])
    assert reason in caplog.text


def test_failed_document_preserves_caption_as_exact_prefix(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    caption = "  important  \n"
    account = _FakeAccount(error=OSError("disk detail must not be retained"))

    manager, _events = _receive(
        tmp_path, monkeypatch, account, _document_update(caption=caption)
    )
    message = _read_latest(manager)

    assert message["media"]["download_error"] == "OSError"
    assert message["text"].startswith(caption + "\n\n[")
    assert message["text"][: len(caption)] == caption
    assert "resend the document" in message["text"]
    assert "another transfer method" in message["text"]


def test_arbitrary_exception_text_and_token_url_are_not_retained(
    tmp_path: Path,
    monkeypatch: Any,
    caplog: Any,
) -> None:
    secret_url = "https://api.telegram.org/file/bot123456:SECRET/private/report.zip"
    account = _FakeAccount(error=RuntimeError(f"GET {secret_url} timed out"))
    caplog.set_level(logging.WARNING)

    manager, inbound_events = _receive(
        tmp_path, monkeypatch, account, _document_update()
    )
    message = _read_latest(manager)
    persisted = next((tmp_path / "telegram" / "main" / "inbox").glob("*/message.json"))
    observable_output = json.dumps(
        {
            "stored": json.loads(persisted.read_text(encoding="utf-8")),
            "read": message,
            "events": inbound_events,
            "logs": caplog.text,
        }
    )

    assert message["media"]["download_error"] == "RuntimeError"
    assert "RuntimeError" in message["text"]
    assert "RuntimeError" in caplog.text
    assert secret_url not in observable_output
    assert "SECRET" not in observable_output


def test_provider_reason_is_whitespace_normalized_and_bounded(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    description = "  provider\n\treason  " + ("detail " * 80)
    normalized = " ".join(description.split())
    account = _FakeAccount(
        error=RuntimeError(f"Telegram API error: {description}")
    )

    manager, _events = _receive(
        tmp_path, monkeypatch, account, _document_update()
    )
    reason = _read_latest(manager)["media"]["download_error"]

    assert reason == normalized[:200]
    assert len(reason) == 200
    assert "\n" not in reason
    assert "\t" not in reason


def test_http_exception_exposes_only_class_and_numeric_status(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    secret_url = "https://api.telegram.org/file/bot123456:SECRET/private/report.zip"

    class HTTPStatusError(RuntimeError):
        def __init__(self) -> None:
            super().__init__(f"413 response from {secret_url}")
            self.response = SimpleNamespace(status_code=413)

    account = _FakeAccount(error=HTTPStatusError())
    manager, inbound_events = _receive(
        tmp_path, monkeypatch, account, _document_update()
    )
    message = _read_latest(manager)
    observable_output = json.dumps({"read": message, "events": inbound_events})

    assert message["media"]["download_error"] == "HTTPStatusError (HTTP 413)"
    assert secret_url not in observable_output
    assert "SECRET" not in observable_output


def test_successful_inbound_document_keeps_existing_shape_path_bytes_and_caption(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    caption = "  keep this caption byte-for-byte  \n"
    data = b"existing-success-bytes\x00\xff"
    account = _FakeAccount(download=("telegram-report.zip", data))

    manager, inbound_events = _receive(
        tmp_path, monkeypatch, account, _document_update(caption=caption)
    )
    message = _read_latest(manager)
    media = message["media"]
    path = Path(media["path"])

    assert message["text"] == caption
    assert media == {
        "type": "document",
        "filename": "telegram-report.zip",
        "path": str(path),
        "size": len(data),
    }
    assert path.name == "telegram-report.zip"
    assert path.parent.name == "attachments"
    assert path.is_relative_to(tmp_path / "telegram" / "main" / "inbox")
    assert path.read_bytes() == data
    assert inbound_events[0]["metadata"]["latest_incoming"]["text"] == caption.replace(
        "\n", " "
    )


def test_non_document_download_failure_keeps_pre_existing_media_and_text_behavior(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    caption = "photo caption"
    account = _FakeAccount(error=RuntimeError("photo unavailable"))
    update = {
        "message": {
            "message_id": 53,
            "date": 1781600000,
            "from": {"id": 1, "username": "alice"},
            "chat": {"id": 123, "type": "private"},
            "caption": caption,
            "photo": [
                {
                    "file_id": "photo-file-id",
                    "file_size": 4096,
                    "width": 320,
                    "height": 240,
                }
            ],
        }
    }

    manager, inbound_events = _receive(tmp_path, monkeypatch, account, update)
    message = _read_latest(manager)

    assert message["media"] is None
    assert message["text"] == caption
    assert inbound_events[0]["metadata"]["has_media"] is False
    assert inbound_events[0]["metadata"]["latest_incoming"]["text"] == caption
