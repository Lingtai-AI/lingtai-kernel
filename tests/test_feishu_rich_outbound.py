"""Focused Feishu text/markdown/post outbound and thread-routing coverage."""
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
        self.fail_edit = False

    def send_content(
        self, receive_id: str, receive_id_type: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("send", (receive_id, receive_id_type, message), {}))
        message_ids = (
            ["om_chunk_1", "om_chunk_2"]
            if "markdown" in message
            else ["om_sent"]
        )
        return {
            "message_id": message_ids[0],
            "message_ids": message_ids,
            "chat_id": "oc_chat",
        }

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
        return {
            "message_id": "om_reply",
            "message_ids": ["om_reply"],
            "chat_id": chat_id,
            "thread_id": "omt_topic" if reply_in_thread else "",
        }

    def update_content(
        self, message_id: str, message: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("edit", (message_id, message), {}))
        if self.fail_edit:
            raise RuntimeError("edit rejected")
        return {"message_id": message_id, "message_ids": [message_id]}

    def add_reaction(self, message_id: str, emoji_type: str) -> bool:
        self.calls.append(("reaction", (message_id, emoji_type), {}))
        return True

    def delete_message(self, _message_id: str) -> bool:
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
    return (
        FeishuManager(
            _FakeService(account),
            working_dir=tmp_path,
            on_inbound=lambda _payload: None,
        ),
        account,
    )


def _call(manager: FeishuManager, action: str, input_: dict[str, Any]) -> dict:
    return handle_feishu(
        manager,
        {"action": action, "input": input_, "reasoning": "focused test"},
    )


def _sent_records(tmp_path: Path) -> list[tuple[Path, dict[str, Any]]]:
    paths = sorted(tmp_path.glob("feishu/main/sent/*/message.json"))
    return [
        (path, json.loads(path.read_text(encoding="utf-8"))) for path in paths
    ]


def _write_target(
    tmp_path: Path,
    *,
    message_id: str = "om_target",
    thread_id: str | None = None,
) -> str:
    compound_id = f"main:oc_chat:{message_id}"
    target = tmp_path / "feishu" / "main" / "inbox" / message_id
    target.mkdir(parents=True)
    (target / "message.json").write_text(
        json.dumps({
            "id": compound_id,
            "feishu_message_id": message_id,
            "chat_id": "oc_chat",
            "thread_id": thread_id,
            "message_type": "text",
            "text": "target",
            "date": "2026-08-03T00:00:00Z",
        }),
        encoding="utf-8",
    )
    return compound_id


def test_send_legacy_text_and_markdown_chunks_share_one_record_shape(tmp_path):
    manager, account = _manager(tmp_path)

    legacy = _call(
        manager,
        "send",
        {"receive_id": "ou_user", "receive_id_type": "open_id", "text": "hi"},
    )
    markdown = _call(
        manager,
        "send",
        {
            "receive_id": "oc_chat",
            "receive_id_type": "chat_id",
            "content": {"type": "markdown", "markdown": "**rich**"},
        },
    )

    assert legacy == {
        "status": "sent",
        "message_id": "main:oc_chat:om_sent",
        "message_ids": ["main:oc_chat:om_sent"],
        "chunk_count": 1,
        "chunks": [{"index": 1, "message_id": "main:oc_chat:om_sent"}],
        "content_type": "text",
    }
    assert markdown["message_ids"] == [
        "main:oc_chat:om_chunk_1",
        "main:oc_chat:om_chunk_2",
    ]
    assert markdown["chunk_count"] == 2
    assert markdown["content_type"] == "markdown"
    assert [call[0] for call in account.calls] == ["send", "send"]

    records = [record for _path, record in _sent_records(tmp_path)]
    assert {record["content"]["type"] for record in records} == {
        "text", "markdown",
    }
    rich = next(record for record in records if record["content"]["type"] == "markdown")
    assert rich["message_type"] == "post"
    assert rich["feishu_message_ids"] == ["om_chunk_1", "om_chunk_2"]
    assert rich["message_ids"] == markdown["message_ids"]
    assert rich["chunks"] == markdown["chunks"]


def test_reply_defaults_to_persisted_thread_and_allows_explicit_flat_override(
    tmp_path,
):
    manager, account = _manager(tmp_path)
    topic_target = _write_target(tmp_path, thread_id="omt_topic")

    topic = _call(
        manager,
        "reply",
        {
            "message_id": topic_target,
            "content": {"type": "markdown", "markdown": "topic reply"},
        },
    )
    flat = _call(
        manager,
        "reply",
        {
            "message_id": topic_target,
            "text": "flat reply",
            "reply_in_thread": False,
        },
    )

    reply_calls = [call for call in account.calls if call[0] == "reply"]
    assert reply_calls[0][2] == {"reply_in_thread": True}
    assert reply_calls[1][2] == {"reply_in_thread": False}
    assert topic["reply_in_thread"] is True
    assert topic["thread_id"] == "omt_topic"
    assert flat["reply_in_thread"] is False
    records = [record for _path, record in _sent_records(tmp_path)]
    assert {record["reply_in_thread"] for record in records} == {True, False}
    threaded = next(record for record in records if record["reply_in_thread"])
    assert threaded["thread_id"] == "omt_topic"
    assert threaded["reply_to"] == topic_target


def test_edit_refreshes_persisted_content_only_after_transport_success(tmp_path):
    manager, account = _manager(tmp_path)
    sent = _call(
        manager,
        "send",
        {"receive_id": "ou_user", "text": "before", "placeholder": True},
    )

    edited = _call(
        manager,
        "edit",
        {
            "message_id": sent["message_id"],
            "content": {"type": "post", "post": {
                "zh_cn": {"title": "after", "content": [[
                    {"tag": "text", "text": "updated"}
                ]]}
            }},
        },
    )
    path, record = _sent_records(tmp_path)[0]
    assert edited["status"] == "edited"
    assert edited["content_type"] == "post"
    assert record["content"]["type"] == "post"
    assert record["message_type"] == "post"
    assert record["text"] == "after\nupdated"
    assert record["status"] == "sent"
    assert record["edited_at"]

    before_failure = path.read_text(encoding="utf-8")
    account.fail_edit = True
    failed = _call(
        manager,
        "edit",
        {"message_id": sent["message_id"], "text": "must not persist"},
    )
    assert failed == {"error": "edit rejected"}
    assert path.read_text(encoding="utf-8") == before_failure


def test_account_sdk_outbound_single_attempt_adapter_projects_chunks():
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    class _Sender:
        async def _materialize(
            self, outbound: Any, **kwargs: Any,
        ) -> list[dict[str, str]]:
            calls.append(("materialize", (outbound,), kwargs))
            if getattr(outbound, "markdown", None):
                return [
                    {"msg_type": "post", "content": "chunk 1"},
                    {"msg_type": "post", "content": "chunk 2"},
                ]
            return [{"msg_type": "text", "content": "reply"}]

        async def _create(self, *args: Any) -> lark.SendResult:
            calls.append(("create", args, {}))
            index = sum(call[0] == "create" for call in calls)
            return lark.SendResult.ok(
                message_id=f"om_{index}",
                raw={"data": {"chat_id": "oc_chat"}},
            )

        async def _reply(self, *args: Any) -> lark.SendResult:
            calls.append(("reply", args, {}))
            return lark.SendResult.ok(
                message_id="om_reply",
                raw={"data": {"chat_id": "oc_chat", "thread_id": "omt_topic"}},
            )

    class _Channel:
        sender = _Sender()

        async def edit_message(
            self, message_id: str, message: dict[str, Any]
        ) -> SimpleNamespace:
            calls.append(("edit", (message_id, message), {}))
            return SimpleNamespace(
                success=True,
                message_id=message_id,
                chunk_ids=None,
                raw={},
            )

    account = FeishuAccount("main", "app", "secret", ["ou_user"])
    account._channel = _Channel()

    sent = account.send_content(
        "ou_user", "open_id", {"markdown": "long markdown"}
    )
    replied = account.reply_content(
        "om_target",
        "oc_chat",
        {"text": "reply"},
        reply_in_thread=True,
    )
    edited = account.update_content("om_1", {"markdown": "edited"})

    assert sent["message_ids"] == ["om_1", "om_2"]
    assert replied["message_ids"] == ["om_reply"]
    reply_call = next(call for call in calls if call[0] == "reply")
    assert reply_call[1][0] == "om_target"
    assert reply_call[1][2] is True
    assert sum(call[0] == "reply" for call in calls) == 1
    assert edited["message_id"] == "om_1"


@pytest.mark.parametrize(
    "input_",
    [
        {"receive_id": "ou_user"},
        {
            "receive_id": "ou_user",
            "text": "one",
            "content": {"type": "text", "text": "two"},
        },
        {
            "receive_id": "ou_user",
            "content": {"type": "markdown", "text": "wrong field"},
        },
        {
            "receive_id": "ou_user",
            "content": {"type": "post", "post": {}},
        },
    ],
)
def test_invalid_outbound_union_never_reaches_transport(tmp_path, input_):
    manager, account = _manager(tmp_path)
    result = _call(manager, "send", input_)
    assert result["status"] == "failed"
    assert account.calls == []
