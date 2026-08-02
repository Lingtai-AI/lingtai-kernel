"""Focused preservation coverage for normalized Feishu message resources."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lark_channel import (
    AudioContent,
    Conversation,
    FileContent,
    Identity,
    InboundMessage,
    PostContent,
    ResourceDescriptor,
)

from lingtai.mcp_servers.feishu.account import FeishuInboundEvent
from lingtai.mcp_servers.feishu import manager as manager_module
from lingtai.mcp_servers.feishu.manager import FeishuManager


class _FakeAccount:
    alias = "main"

    def __init__(self, resources: dict[str, tuple[str, bytes] | Exception]) -> None:
        self.resources = resources
        self.requests: list[tuple[str, str]] = []

    def add_reaction(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def get_message_resource(
        self, _message_id: str, file_key: str, resource_type: str
    ) -> tuple[str, bytes]:
        self.requests.append((file_key, resource_type))
        result = self.resources[file_key]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.account = account

    def get_account(self, _alias: str) -> _FakeAccount:
        return self.account


def _incoming(
    *,
    message_id: str,
    message_type: str,
    body_text: str,
    resources: list[ResourceDescriptor],
) -> FeishuInboundEvent:
    if message_type == "audio":
        content = AudioContent(
            raw={"caption": "kept"},
            file_key=resources[0].file_key,
            duration_ms=resources[0].duration_ms,
        )
    elif message_type == "file":
        content = FileContent(
            raw={"caption": "kept"},
            file_key=resources[0].file_key,
            file_name=resources[0].file_name,
        )
    else:
        content = PostContent(
            raw={"caption": "kept"},
            text=body_text,
        )
    message = InboundMessage(
        id=message_id,
        create_time=0,
        conversation=Conversation(chat_id="oc_media", chat_type="p2p"),
        sender=Identity(open_id="ou_sender", display_name="Sender"),
        content=content,
        content_text=body_text,
        body_text=body_text,
        resources=resources,
        raw_content_type=message_type,
    )
    return FeishuInboundEvent(
        message=message,
        feishu={
            "schema": "2.0",
            "header": {"event_id": f"event-{message_id}"},
            "event": {"message": {"message_id": message_id}},
        },
    )


def _manager(
    tmp_path: Path,
    account: _FakeAccount,
    monkeypatch: Any,
) -> FeishuManager:
    monkeypatch.setattr(
        manager_module._typing_manager,
        "start_typing",
        lambda *_args, **_kwargs: None,
    )
    return FeishuManager(
        _FakeService(account),
        working_dir=tmp_path,
        on_inbound=lambda _event: None,
    )


def _only_payload(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    message_path = next(
        (tmp_path / "feishu" / "main" / "inbox").glob("*/message.json")
    )
    return json.loads(message_path.read_text(encoding="utf-8")), message_path.parent


def test_normalized_resources_download_and_surface_in_read_and_search(
    tmp_path: Path, monkeypatch: Any
) -> None:
    account = _FakeAccount({
        "video-key": ("../../clip.mp4", b"video"),
        "cover-key": ("cover.png", b"cover"),
        "sticker-key": ("sticker.png", b"sticker"),
        "image-key": ("image.jpg", b"image"),
        "file-key": ("../report.txt", b"file"),
    })
    manager = _manager(tmp_path, account, monkeypatch)
    manager.on_incoming(
        "main",
        _incoming(
            message_id="om_resources",
            message_type="post",
            body_text="resource bundle",
            resources=[
                ResourceDescriptor(
                    type="video",
                    file_key="video-key",
                    file_name="clip.mp4",
                    cover_image_key="cover-key",
                ),
                ResourceDescriptor(type="sticker", file_key="sticker-key"),
                ResourceDescriptor(type="image", file_key="image-key"),
                ResourceDescriptor(
                    type="file", file_key="file-key", file_name="report.txt"
                ),
            ],
        ),
    )

    payload, message_dir = _only_payload(tmp_path)
    attachments = payload["attachments"]
    assert [item["type"] for item in attachments] == [
        "video",
        "image",
        "sticker",
        "image",
        "file",
    ]
    assert all(item["status"] == "downloaded" for item in attachments)
    assert attachments[1]["role"] == "cover"
    assert attachments[1]["parent_file_key"] == "video-key"
    attachment_dir = message_dir / "attachments"
    assert all(Path(item["path"]).parent == attachment_dir for item in attachments)
    assert all("/" not in item["filename"] for item in attachments)
    assert all(Path(item["path"]).read_bytes() for item in attachments)
    assert payload["media"]["type"] == "video"
    assert account.requests == [
        ("video-key", "video"),
        ("cover-key", "image"),
        ("sticker-key", "sticker"),
        ("image-key", "image"),
        ("file-key", "file"),
    ]

    read_result = manager.handle({
        "action": "read",
        "account": "main",
        "chat_id": "oc_media",
    })
    assert read_result["messages"][0]["attachments"] == attachments
    search_result = manager.handle({
        "action": "search",
        "account": "main",
        "chat_id": "oc_media",
        "query": "resource bundle",
    })
    assert search_result["messages"][0]["attachments"] == attachments

    _preview, metadata = manager._build_conversation_preview_and_metadata(
        "main", "oc_media", payload["id"]
    )
    current = metadata["latest_incoming"]
    assert [item["type"] for item in current["attachments"]] == [
        "video",
        "image",
        "sticker",
        "image",
        "file",
    ]
    assert all("file_key" not in item for item in current["attachments"])
    assert all("parent_file_key" not in item for item in current["attachments"])
    assert [item["path"] for item in current["attachments"]] == [
        item["path"] for item in attachments
    ]
    assert "inspect the listed paths" in current["comment"]

    _preview, historical = manager._build_conversation_preview_and_metadata(
        "main", "oc_media", "main:oc_media:om_other"
    )
    assert "attachments" not in historical["latest_incoming"]


def test_download_failure_retains_the_original_resource_descriptor(
    tmp_path: Path, monkeypatch: Any
) -> None:
    account = _FakeAccount({"file-key": RuntimeError("download unavailable")})
    manager = _manager(tmp_path, account, monkeypatch)
    incoming = _incoming(
        message_id="om_failure",
        message_type="file",
        body_text="file placeholder",
        resources=[
            ResourceDescriptor(
                type="file", file_key="file-key", file_name="report.txt"
            )
        ],
    )

    manager.on_incoming("main", incoming)

    payload, message_dir = _only_payload(tmp_path)
    assert payload["text"] == "file placeholder"
    assert payload["content"] == {
        "kind": "file",
        "file_key": "file-key",
        "file_name": "report.txt",
    }
    assert payload["feishu"] == incoming.feishu
    assert payload["attachments"] == [{
        "type": "file",
        "file_key": "file-key",
        "file_name": "report.txt",
        "status": "failed",
        "error": "download unavailable",
    }]
    assert payload["media"]["download_error"] == "download unavailable"
    assert not (message_dir / "attachments").exists()

    _preview, metadata = manager._build_conversation_preview_and_metadata(
        "main", "oc_media", payload["id"]
    )
    current = metadata["latest_incoming"]
    assert current["attachments"] == [{
        "type": "file",
        "status": "failed",
        "filename": "report.txt",
        "error": "download unavailable",
    }]
    assert "file_key" not in current["attachments"][0]
    assert "feishu.read" in current["comment"]


def test_audio_transcription_failure_keeps_download_and_normalized_text(
    tmp_path: Path, monkeypatch: Any
) -> None:
    account = _FakeAccount({"audio-key": ("", b"voice")})
    manager = _manager(tmp_path, account, monkeypatch)
    monkeypatch.setattr(
        manager_module,
        "_transcribe_voice",
        lambda _path: {"error": "model unavailable"},
    )

    manager.on_incoming(
        "main",
        _incoming(
            message_id="om_audio",
            message_type="audio",
            body_text='<audio key="audio-key"/>',
            resources=[ResourceDescriptor(type="audio", file_key="audio-key")],
        ),
    )

    payload, message_dir = _only_payload(tmp_path)
    assert payload["text"] == '<audio key="audio-key"/>'
    assert payload["voice_transcript"] is None
    attachment = payload["attachments"][0]
    assert attachment["status"] == "downloaded"
    assert attachment["filename"].endswith(".ogg")
    assert Path(attachment["path"]).parent == message_dir / "attachments"
    assert Path(attachment["path"]).read_bytes() == b"voice"
    assert attachment["transcription_status"] == "failed"
    assert attachment["transcription_error"] == "model unavailable"
