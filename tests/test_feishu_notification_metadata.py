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

from lingtai.mcp_servers.feishu.manager import (
    _CONVERSATION_PREVIEW_MESSAGES,
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
        "message_type": "text",
        "from_open_id": "ou_jason",
        "text": text,
        "parent_id": parent_id,
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
