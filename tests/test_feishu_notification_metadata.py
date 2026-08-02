"""Tests for the Feishu producer's LICC notification metadata.

Locks the structured ``recent_messages`` / ``latest_incoming`` conversation
context and the generic routing keys (``platform`` / ``conversation_ref`` /
``message_ref``) that the Feishu MCP attaches to LICC events.  The kernel
inbox allowlists exactly these fields into notification previews, and
``meta_block.build_notification_persistent_payload`` moves them into the
``_meta.agent_meta.notifications.persistent.mcp.feishu`` lane.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lark_channel import Conversation, Identity, InboundMessage, Mention, TextContent

from lingtai.mcp_servers.feishu.account import FeishuInboundEvent
from lingtai.mcp_servers.feishu.manager import (
    _CONVERSATION_PREVIEW_MESSAGES,
    _STRUCTURED_MESSAGE_MENTION_CAP,
    _STRUCTURED_MESSAGE_TEXT_CAP,
    FeishuManager,
)


class _FakeService:
    """Accountless stand-in: reactions/typing are best-effort and skipped."""

    def get_account(self, alias: str) -> Any:
        raise KeyError(alias)


def _manager(workdir: Path, events: list[dict] | None = None) -> FeishuManager:
    sink = events if events is not None else []
    return FeishuManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=sink.append,
    )


def _write_message(
    workdir: Path,
    *,
    account: str = "main",
    folder: str = "inbox",
    chat_id: str = "oc_chat",
    msg_id: str,
    text: str,
    date: str,
    parent_id: str = "",
    thread_id: str | None = None,
    message_type: str = "text",
    mentions: list[dict] | None = None,
    media: dict | None = None,
) -> str:
    compound_id = f"{account}:{chat_id}:{msg_id}"
    msg_dir = workdir / "feishu" / account / folder / f"uuid-{msg_id}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "id": compound_id,
        "feishu_message_id": msg_id,
        "chat_id": chat_id,
        "chat_type": "p2p",
        "thread_id": thread_id,
        "message_type": message_type,
        "from_open_id": "ou_jason",
        "text": text,
        "parent_id": parent_id,
        "mentions": mentions or [],
        "media": media,
        "voice_transcript": None,
    }
    if folder == "sent":
        payload["sent_at"] = date
    else:
        payload["date"] = date
    (msg_dir / "message.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return compound_id


def _incoming_event(
    *,
    msg_id: str = "om_new",
    chat_id: str = "oc_chat",
    text: str = "hi there",
    parent_id: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                message_id=msg_id,
                chat_id=chat_id,
                chat_type="p2p",
                message_type="text",
                content=json.dumps({"text": text}),
                create_time="",
                parent_id=parent_id,
            ),
            sender=SimpleNamespace(
                sender_id=SimpleNamespace(open_id="ou_jason"),
            ),
        ),
    )


def test_structured_metadata_orders_and_marks_current(tmp_path):
    mgr = _manager(tmp_path)
    _write_message(tmp_path, msg_id="om_1", text="first", date="2026-07-06T09:00:01Z")
    _write_message(
        tmp_path,
        folder="sent",
        msg_id="om_2",
        text="my reply",
        date="2026-07-06T09:00:02Z",
    )
    current = _write_message(
        tmp_path, msg_id="om_3", text="third", date="2026-07-06T09:00:03Z"
    )

    preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", current
    )

    structured = metadata["recent_messages"]
    assert [m["id"] for m in structured] == [
        "main:oc_chat:om_1",
        "main:oc_chat:om_2",
        "main:oc_chat:om_3",
    ]
    assert [m["direction"] for m in structured] == [
        "incoming",
        "outgoing",
        "incoming",
    ]
    # Sent messages render as the agent itself.
    assert structured[1]["sender"] == "me"
    # Only the current message carries is_current.
    assert structured[2]["is_current"] is True
    assert all("is_current" not in m for m in structured[:2])
    assert all(m["text_truncated"] is False for m in structured)
    assert metadata["latest_incoming"]["id"] == current
    # The markdown preview still names the current compound id for anchoring.
    assert f"#{current}" in preview
    assert "Feishu" in preview


def test_structured_metadata_caps_message_text(tmp_path):
    mgr = _manager(tmp_path)
    long_text = "x" * (_STRUCTURED_MESSAGE_TEXT_CAP + 100)
    current = _write_message(
        tmp_path, msg_id="om_1", text=long_text, date="2026-07-06T09:00:01Z"
    )

    _preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", current
    )

    item = metadata["recent_messages"][0]
    assert len(item["text"]) == _STRUCTURED_MESSAGE_TEXT_CAP
    assert item["text"].endswith("…")
    assert item["text_truncated"] is True
    # Structured metadata must stay within the kernel inbox's structured-field
    # JSON cap or it would be silently dropped from the notification preview.
    assert len(json.dumps(metadata, ensure_ascii=False)) < 20_000


def test_structured_metadata_window_is_bounded(tmp_path):
    mgr = _manager(tmp_path)
    for i in range(1, 13):
        _write_message(
            tmp_path,
            msg_id=f"om_{i:02d}",
            text=f"message {i}",
            date=f"2026-07-06T09:00:{i:02d}Z",
        )

    _preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", "main:oc_chat:om_12"
    )

    structured = metadata["recent_messages"]
    assert len(structured) == _CONVERSATION_PREVIEW_MESSAGES
    assert structured[0]["id"] == "main:oc_chat:om_03"
    assert structured[-1]["id"] == "main:oc_chat:om_12"


def test_structured_metadata_reply_refs(tmp_path):
    mgr = _manager(tmp_path)
    _write_message(tmp_path, msg_id="om_1", text="parent", date="2026-07-06T09:00:01Z")
    current = _write_message(
        tmp_path,
        msg_id="om_2",
        text="reply",
        date="2026-07-06T09:00:02Z",
        parent_id="om_1",
    )

    _preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", current
    )

    item = metadata["recent_messages"][-1]
    assert item["parent_id"] == "om_1"
    assert item["reply_to"] == "main:oc_chat:om_1"


def test_on_incoming_licc_event_carries_routing_and_structured_context(tmp_path):
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    mgr.on_incoming("main", _incoming_event(msg_id="om_new", text="hi there"))

    assert len(events) == 1
    event = events[0]
    assert event["wake"] is True
    assert "feishu message from" in event["subject"]
    # Body is the guidance-headed markdown conversation preview.
    assert "Feishu" in event["body"]
    assert "#main:oc_chat:om_new" in event["body"]

    metadata = event["metadata"]
    # Generic LICC routing keys the kernel inbox allowlists into previews.
    assert metadata["platform"] == "feishu"
    assert metadata["conversation_ref"] == "main:oc_chat"
    assert metadata["message_ref"] == "main:oc_chat:om_new"
    # Structured context for the persistent notification lane.
    structured = metadata["recent_messages"]
    assert structured[-1]["id"] == "main:oc_chat:om_new"
    assert structured[-1]["is_current"] is True
    assert structured[-1]["text"] == "hi there"
    assert metadata["latest_incoming"]["id"] == "main:oc_chat:om_new"
    # Legacy Feishu-specific routing keys are preserved alongside.
    assert metadata["message_id"] == "main:oc_chat:om_new"
    assert metadata["chat_id"] == "oc_chat"
    assert metadata["account"] == "main"


def _sdk_incoming_event(*, body_text: str) -> FeishuInboundEvent:
    inbound = InboundMessage(
        id="om_normalized",
        create_time=0,
        conversation=Conversation(
            chat_id="oc_topic",
            chat_type="topic",
            thread_id="omt_thread",
        ),
        sender=Identity(
            open_id="ou_sender",
            union_id="on_sender",
            user_id="user_sender",
            display_name="Sender",
            sender_type="user",
        ),
        mentions=[
            Mention(key="@_user_1", open_id="ou_bot", name="Mac PA", is_bot=True),
            Mention(key="@_user_2", open_id="ou_other", name="Other"),
        ],
        content=TextContent(raw={"text": "@_user_1 hello"}, text="@Mac PA hello"),
        raw={"parent_id": "om_parent", "root_id": "om_root"},
        content_text="@Mac PA hello",
        body_text=body_text,
        mentioned_bot=True,
        raw_content_type="text",
    )
    envelope = {
        "schema": "2.0",
        "header": {"event_id": "event-normalized"},
        "event": {"message": {"message_id": "om_normalized"}},
    }
    return FeishuInboundEvent(message=inbound, feishu=envelope)


def test_sdk_incoming_persists_normalized_context_without_licc_raw_copy(tmp_path):
    events: list[dict] = []
    mgr = _manager(tmp_path, events)

    incoming = _sdk_incoming_event(body_text="hello")
    mgr.on_incoming("main", incoming)

    message_path = next((tmp_path / "feishu" / "main" / "inbox").glob("*/message.json"))
    payload = json.loads(message_path.read_text(encoding="utf-8"))
    assert payload["id"] == "main:oc_topic:om_normalized"
    assert payload["thread_id"] == "omt_thread"
    assert payload["root_id"] == "om_root"
    assert payload["reply_to"] == "main:oc_topic:om_parent"
    assert payload["text"] == "hello"
    assert payload["content"] == {"kind": "text", "text": "@Mac PA hello"}
    assert payload["mentions"][0]["is_bot"] is True
    assert payload["from_name"] == "Sender"
    assert payload["sender_type"] == "user"
    assert payload["feishu"] == incoming.feishu

    read_result = mgr.handle({"action": "read", "account": "main", "chat_id": "oc_topic"})
    read_message = read_result["messages"][0]
    assert read_message["from_name"] == "Sender"
    assert read_message["thread_id"] == "omt_thread"
    assert read_message["content"] == payload["content"]
    assert read_message["feishu"] == incoming.feishu

    search_result = mgr.handle({
        "action": "search",
        "account": "main",
        "chat_id": "oc_topic",
        "query": "Sender",
    })
    assert search_result["total"] == 1
    assert search_result["messages"][0]["from_name"] == "Sender"

    metadata = events[0]["metadata"]
    assert metadata["thread_id"] == "omt_thread"
    structured = metadata["latest_incoming"]
    assert structured["type"] == "text"
    assert structured["thread_id"] == "omt_thread"
    assert len(structured["mentions"]) == 2
    assert "feishu" not in structured
    assert "content" not in structured


def test_sdk_bare_bot_mention_keeps_empty_normalized_body(tmp_path):
    mgr = _manager(tmp_path)

    mgr.on_incoming("main", _sdk_incoming_event(body_text=""))

    message_path = next((tmp_path / "feishu" / "main" / "inbox").glob("*/message.json"))
    payload = json.loads(message_path.read_text(encoding="utf-8"))
    assert payload["text"] == ""


def test_structured_metadata_caps_mentions(tmp_path):
    mgr = _manager(tmp_path)
    mentions = [
        {"key": f"@_user_{index}", "open_id": f"ou_{index}"}
        for index in range(_STRUCTURED_MESSAGE_MENTION_CAP + 1)
    ]
    current = _write_message(
        tmp_path,
        msg_id="om_mentions",
        text="mentions",
        date="2026-07-06T09:00:01Z",
        thread_id="omt_thread",
        message_type="post",
        mentions=mentions,
    )

    _preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", current
    )

    item = metadata["latest_incoming"]
    assert item["type"] == "post"
    assert item["thread_id"] == "omt_thread"
    assert item["mentions"] == mentions[:_STRUCTURED_MESSAGE_MENTION_CAP]
    assert item["mentions_truncated"] is True
