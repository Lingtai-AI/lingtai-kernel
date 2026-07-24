"""Contract tests for Telegram's advertised outbound media support."""

from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.manager import (
    SCHEMA,
    SUPPORTED_SEND_MEDIA_TYPES,
    TelegramManager,
)
from tests._notification_store_helpers import notification_store_for


class _Account:
    alias = "bot"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_photo(self, _chat_id, path, **_kwargs):
        self.calls.append(("photo", path))
        return {"message_id": 1}

    def send_document(self, _chat_id, path, **_kwargs):
        self.calls.append(("document", path))
        return {"message_id": 2}


class _Service:
    def __init__(self) -> None:
        self.account = _Account()

    def get_account(self, alias):
        assert alias == "bot"
        return self.account


def _manager(tmp_path: Path) -> tuple[TelegramManager, _Account]:
    service = _Service()
    manager = TelegramManager(
        service,
        working_dir=tmp_path,
        on_inbound=lambda _: None,
        notification_store=notification_store_for(tmp_path),
    )
    return manager, service.account


def test_send_schema_advertises_exactly_runtime_supported_media_types():
    advertised = SCHEMA["properties"]["media"]["properties"]["type"]["enum"]

    assert advertised == list(SUPPORTED_SEND_MEDIA_TYPES) == ["photo", "document"]


@pytest.mark.parametrize("media_type", SUPPORTED_SEND_MEDIA_TYPES)
def test_each_advertised_media_type_dispatches(tmp_path: Path, media_type: str):
    media_path = tmp_path / f"attachment.{media_type}"
    media_path.write_bytes(b"content")
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "bot",
        "chat_id": 123,
        "media": {"type": media_type, "path": str(media_path)},
    })

    assert result == {
        "status": "sent",
        "message_id": f"bot:123:{1 if media_type == 'photo' else 2}",
    }
    assert account.calls == [(media_type, str(media_path))]


@pytest.mark.parametrize("media_type", ["voice", "audio"])
def test_unadvertised_media_types_remain_rejected(tmp_path: Path, media_type: str):
    media_path = tmp_path / "unsupported-media"
    media_path.write_bytes(b"content")
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "bot",
        "chat_id": 123,
        "media": {"type": media_type, "path": str(media_path)},
    })

    assert result == {"error": f"Unknown media type: {media_type}"}
    assert account.calls == []
