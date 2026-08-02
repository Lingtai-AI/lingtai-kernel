"""Focused Feishu media/share/sticker outbound coverage."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import lark_channel as lark
import pytest

from lingtai.mcp_servers.feishu._family import handle_feishu
from lingtai.mcp_servers.feishu.account import FeishuAccount
from lingtai.mcp_servers.feishu.manager import FeishuManager


class _FakeAccount:
    alias = "main"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._next_id = 0

    def _result(self, chat_id: str = "oc_chat") -> dict[str, Any]:
        self._next_id += 1
        message_id = f"om_{self._next_id}"
        return {
            "message_id": message_id,
            "message_ids": [message_id],
            "chat_id": chat_id,
        }

    def send_content(
        self, receive_id: str, receive_id_type: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("send", (receive_id, receive_id_type, message), {}))
        return self._result(
            receive_id if receive_id_type == "chat_id" else "oc_chat"
        )

    def reply_content(
        self,
        message_id: str,
        chat_id: str,
        message: dict[str, Any],
        *,
        reply_in_thread: bool,
    ) -> dict[str, Any]:
        self.calls.append((
            "reply",
            (message_id, chat_id, message),
            {"reply_in_thread": reply_in_thread},
        ))
        return self._result(chat_id)

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        self.calls.append(("reaction", (message_id, emoji_type), {}))
        return True


class _FakeService:
    def __init__(self, account: _FakeAccount) -> None:
        self.default_account = account
        self.account = account

    def get_account(self, alias: str) -> _FakeAccount:
        assert alias == self.account.alias
        return self.account


def _manager(tmp_path: Path) -> tuple[FeishuManager, _FakeAccount]:
    account = _FakeAccount()
    manager = FeishuManager(
        _FakeService(account),
        working_dir=tmp_path,
        on_inbound=lambda _payload: None,
    )
    return manager, account


def _call(manager: FeishuManager, action: str, input_: dict[str, Any]) -> dict:
    return handle_feishu(
        manager,
        {"action": action, "input": input_, "reasoning": "focused test"},
    )


def _write_target(tmp_path: Path) -> str:
    compound_id = "main:oc_chat:om_target"
    target = tmp_path / "feishu" / "main" / "inbox" / "target"
    target.mkdir(parents=True)
    (target / "message.json").write_text(
        json.dumps({
            "id": compound_id,
            "feishu_message_id": "om_target",
            "chat_id": "oc_chat",
            "thread_id": None,
            "message_type": "text",
            "text": "target",
            "date": "2026-08-03T00:00:00Z",
        }),
        encoding="utf-8",
    )
    return compound_id


def _sent_records(tmp_path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(tmp_path.glob("feishu/main/sent/*/message.json"))
    ]


def test_send_and_reply_normalize_every_media_share_and_sticker_shape(
    tmp_path: Path,
) -> None:
    manager, account = _manager(tmp_path)
    local_file = tmp_path / "sample.bin"
    local_file.write_bytes(b"media")
    cases = [
        (
            {
                "type": "image",
                "source": {"type": "path", "path": str(local_file)},
                "caption": "**caption**",
            },
            {"image": {"source": str(local_file)}, "caption": "**caption**"},
        ),
        (
            {
                "type": "file",
                "source": {"type": "key", "key": "file_provider"},
                "file_name": "report.pdf",
            },
            {"file": {"source": "file_provider", "file_name": "report.pdf"}},
        ),
        (
            {"type": "audio", "source": {"type": "key", "key": "file_audio"}},
            {"audio": {"source": "file_audio"}},
        ),
        (
            {
                "type": "video",
                "source": {"type": "key", "key": "file_video"},
                "caption": "video caption",
            },
            {"video": {"source": "file_video"}, "caption": "video caption"},
        ),
        (
            {"type": "share_chat", "chat_id": "oc_shared"},
            {"share_chat": {"chat_id": "oc_shared"}},
        ),
        (
            {"type": "share_user", "user_id": "ou_shared"},
            {"share_user": {"user_id": "ou_shared"}},
        ),
        (
            {"type": "sticker", "file_key": "st_provider"},
            {"sticker": {"file_key": "st_provider"}},
        ),
    ]

    for content, expected_sdk in cases:
        result = _call(
            manager,
            "send",
            {"receive_id": "ou_user", "content": content},
        )
        assert result["status"] == "sent"
        assert result["content_type"] == content["type"]
        assert account.calls[-1][1][2] == expected_sdk

    target = _write_target(tmp_path)
    reply = _call(manager, "reply", {
        "message_id": target,
        "content": {
            "type": "image",
            "source": {"type": "key", "key": "img_reply"},
        },
    })
    assert reply["status"] == "sent"
    reply_call = next(call for call in account.calls if call[0] == "reply")
    assert reply_call[1][2] == {"image": {"source": "img_reply"}}
    assert reply_call[2] == {"reply_in_thread": False}


@pytest.mark.parametrize(
    "source",
    [
        {"type": "path", "path": "relative.png"},
        {"type": "path", "path": "https://example.invalid/image.png"},
        {"type": "key", "key": "https://example.invalid/image.png"},
    ],
)
def test_relative_paths_and_urls_never_reach_transport(
    tmp_path: Path, source: dict[str, str],
) -> None:
    manager, account = _manager(tmp_path)
    result = _call(manager, "send", {
        "receive_id": "ou_user",
        "content": {"type": "image", "source": source},
    })

    assert "error" in result
    assert account.calls == []


def test_edit_and_placeholder_reject_media_before_transport(tmp_path: Path) -> None:
    manager, account = _manager(tmp_path)
    media = {
        "type": "image",
        "source": {"type": "key", "key": "img_provider"},
    }

    placeholder = _call(manager, "send", {
        "receive_id": "ou_user",
        "content": media,
        "placeholder": True,
    })
    edited = _call(manager, "edit", {
        "message_id": "main:oc_chat:om_target",
        "content": media,
    })

    assert "placeholder only supports" in placeholder["error"]
    assert edited["status"] == "failed"
    assert edited["error_code"] == "INVALID_ARGUMENT"
    assert account.calls == []


def test_exact_source_is_readable_but_licc_media_summary_is_source_free(
    tmp_path: Path,
) -> None:
    manager, _account = _manager(tmp_path)
    local_file = tmp_path / "private-source.png"
    local_file.write_bytes(b"image-bytes")
    provider_key = "img_provider_secret"

    _call(manager, "send", {
        "receive_id": "oc_chat",
        "receive_id_type": "chat_id",
        "content": {
            "type": "image",
            "source": {"type": "path", "path": str(local_file)},
        },
    })
    _call(manager, "send", {
        "receive_id": "oc_chat",
        "receive_id_type": "chat_id",
        "content": {
            "type": "image",
            "source": {"type": "key", "key": provider_key},
        },
    })

    records = _sent_records(tmp_path)
    assert {record["content"]["source"]["type"] for record in records} == {
        "path", "key",
    }
    path_record = next(
        record for record in records
        if record["content"]["source"]["type"] == "path"
    )
    assert path_record["content"]["source"]["path"] == str(local_file)
    assert path_record["media"] == {
        "type": "image",
        "filename": local_file.name,
        "size": len(b"image-bytes"),
    }

    preview, metadata = manager._build_conversation_preview_and_metadata(
        "main", "oc_chat", "",
    )
    licc_projection = json.dumps(
        {"preview": preview, "metadata": metadata}, ensure_ascii=False,
    )
    assert str(local_file) not in licc_projection
    assert provider_key not in licc_projection


def _account_with_driver(
    *,
    create_message: Any,
    reply_message: Any | None = None,
) -> FeishuAccount:
    driver = SimpleNamespace(
        create_message=create_message,
        reply_message=reply_message or create_message,
        upload_image=None,
        upload_file=None,
    )
    sender = lark.OutboundSender(
        driver=driver,
        config=lark.OutboundConfig(retry=lark.RetryConfig(max_attempts=1)),
    )
    account = FeishuAccount("main", "app", "secret", ["ou_user"])
    account._channel = SimpleNamespace(sender=sender)
    return account


@pytest.mark.parametrize(
    ("message", "expected_msg_type"),
    [
        ({"image": {"source": "img_provider"}}, "image"),
        ({"file": {"source": "file_provider"}}, "file"),
        ({"audio": {"source": "file_audio"}}, "audio"),
        ({"video": {"source": "file_video"}}, "media"),
        ({"share_chat": {"chat_id": "oc_shared"}}, "share_chat"),
        ({"share_user": {"user_id": "ou_shared"}}, "share_user"),
        ({"sticker": {"file_key": "st_provider"}}, "sticker"),
    ],
)
def test_account_reuses_sdk_media_materialization(
    message: dict[str, Any], expected_msg_type: str,
) -> None:
    calls: list[dict[str, Any]] = []

    def create_message(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "code": 0,
            "msg": "ok",
            "data": {"message_id": "om_sent", "chat_id": "oc_chat"},
        }

    account = _account_with_driver(create_message=create_message)
    result = account.send_content("ou_user", "open_id", message)

    assert result["message_id"] == "om_sent"
    assert [call["msg_type"] for call in calls] == [expected_msg_type]


def test_format_error_is_reported_without_plaintext_fallback() -> None:
    calls: list[dict[str, Any]] = []

    def create_message(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"code": 230001, "msg": "invalid message content"}

    account = _account_with_driver(create_message=create_message)

    with pytest.raises(RuntimeError, match="format_error"):
        account.send_content("ou_user", "open_id", {"markdown": "**rich**"})

    assert [call["msg_type"] for call in calls] == ["post"]


def test_gone_reply_target_is_reported_without_fresh_send() -> None:
    creates: list[dict[str, Any]] = []
    replies: list[dict[str, Any]] = []

    def create_message(**kwargs: Any) -> dict[str, Any]:
        creates.append(kwargs)
        return {"code": 0, "data": {"message_id": "om_fresh"}}

    def reply_message(**kwargs: Any) -> dict[str, Any]:
        replies.append(kwargs)
        return {"code": 230002, "msg": "target gone"}

    account = _account_with_driver(
        create_message=create_message,
        reply_message=reply_message,
    )

    with pytest.raises(RuntimeError, match="target_revoked"):
        account.reply_content(
            "om_gone", "oc_chat", {"text": "reply"}, reply_in_thread=False,
        )

    assert len(replies) == 1
    assert creates == []
