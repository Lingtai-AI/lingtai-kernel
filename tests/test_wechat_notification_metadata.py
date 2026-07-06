"""WeChat producer structured notification metadata.

The WeChat manager forwards each inbound message to the host via LICC with a
markdown conversation preview in ``body`` plus structured ``recent_messages``
/ ``latest_incoming`` metadata built from the same merged inbox+sent window.
The kernel inbox copies those fields into ``.notification/mcp.wechat.json``
previews, which feed the durable ``_meta.notification_persistent.mcp.wechat``
lane while the transient ``_meta.notifications.mcp.wechat`` lane stays a
compact identity hook.  These tests pin the producer half of that contract:
bounded per-message text, window size, current/latest selection, direction
and sender attribution, item-type passthrough, and routing keys — with no
credential material copied off the landed records.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from lingtai.mcp_servers.wechat.manager import (
    WechatManager,
    _CONVERSATION_PREVIEW_MESSAGES,
    _STRUCTURED_MESSAGE_TEXT_CAP,
)
from lingtai.mcp_servers.wechat.types import (
    MessageItem, MessageItemType, TextItem, WeixinMessage,
)


def _manager(tmp_path: Path, events: list[dict]) -> WechatManager:
    return WechatManager(
        token="test-token",
        user_id="test-bot",
        working_dir=tmp_path,
        on_inbound=events.append,
    )


def _text_msg(*, from_user: str, text: str, message_id: int) -> WeixinMessage:
    return WeixinMessage(
        message_id=message_id,
        from_user_id=from_user,
        message_type=1,
        item_list=[
            MessageItem(
                type=MessageItemType.TEXT,
                text_item=TextItem(text=text),
            )
        ],
    )


def _deliver(mgr: WechatManager, msg: WeixinMessage) -> None:
    asyncio.run(mgr._on_incoming(msg))


def _write_inbox(tmp_path: Path, *, from_user: str, body: str, date: str,
                 item_types: list[int] | None = None) -> str:
    msg_id = str(uuid.uuid4())
    msg_dir = tmp_path / "wechat" / "inbox" / msg_id
    msg_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": msg_id,
        "from_user_id": from_user,
        "body": body,
        "date": date,
        "raw_item_types": item_types or [1],
    }
    (msg_dir / "message.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return msg_id


def _write_sent(tmp_path: Path, *, to_user: str, text: str, date: str) -> str:
    msg_id = str(uuid.uuid4())
    msg_dir = tmp_path / "wechat" / "sent" / msg_id
    msg_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "id": msg_id,
        "to_user_id": to_user,
        "text": text,
        "media_path": None,
        "date": date,
    }
    (msg_dir / "message.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    return msg_id


def test_licc_event_carries_routing_keys_and_structured_messages(tmp_path):
    """End-to-end: an inbound message forwards a LICC event whose metadata
    carries the generic routing keys plus recent_messages/latest_incoming."""
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    user = "wxid_alice@im.wechat"

    _deliver(mgr, _text_msg(from_user=user, text="hello there", message_id=1))

    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["platform"] == "wechat"
    assert metadata["conversation_ref"] == user
    landed_id = metadata["message_ref"]
    assert landed_id == metadata["message_id"]

    recent = metadata["recent_messages"]
    assert [m["id"] for m in recent] == [landed_id]
    current = recent[0]
    assert current["direction"] == "incoming"
    assert current["sender"] == user
    assert current["text"] == "hello there"
    assert current["text_truncated"] is False
    assert current["is_current"] is True
    assert current["item_types"] == [int(MessageItemType.TEXT)]
    assert metadata["latest_incoming"] == current

    # No credential/config material leaks off the landed record.
    for item in recent:
        assert "token" not in item
        assert "stable_key" not in item
        assert "upstream_message_id" not in item


def test_structured_messages_merge_sent_and_cap_text(tmp_path):
    user = "wxid_bob@im.wechat"
    long_text = "x" * (_STRUCTURED_MESSAGE_TEXT_CAP + 200)
    in1 = _write_inbox(
        tmp_path, from_user=user, body=long_text, date="2026-07-06T01:00:00+00:00"
    )
    out1 = _write_sent(
        tmp_path, to_user=user, text="my reply", date="2026-07-06T01:01:00+00:00"
    )
    in2 = _write_inbox(
        tmp_path, from_user=user, body="follow-up", date="2026-07-06T01:02:00+00:00"
    )
    # Another conversation must not bleed into this window.
    _write_inbox(
        tmp_path, from_user="wxid_other@im.wechat", body="noise",
        date="2026-07-06T01:03:00+00:00",
    )

    mgr = _manager(tmp_path, [])
    body, metadata = mgr._build_conversation_preview_and_metadata(user, in2)

    recent = metadata["recent_messages"]
    assert [m["id"] for m in recent] == [in1, out1, in2]

    truncated = recent[0]
    assert truncated["text_truncated"] is True
    assert len(truncated["text"]) == _STRUCTURED_MESSAGE_TEXT_CAP
    assert truncated["text"].endswith("…")

    outgoing = recent[1]
    assert outgoing["direction"] == "outgoing"
    assert outgoing["sender"] == "me"
    assert outgoing["text"] == "my reply"

    assert metadata["latest_incoming"]["id"] == in2
    assert recent[2]["is_current"] is True
    # The markdown preview and the structured window describe the same merge.
    assert "my reply" in body
    assert "follow-up" in body


def test_structured_window_is_bounded_to_preview_size(tmp_path):
    user = "wxid_carol@im.wechat"
    ids = [
        _write_inbox(
            tmp_path, from_user=user, body=f"m{i}",
            date=f"2026-07-06T01:{i:02d}:00+00:00",
        )
        for i in range(_CONVERSATION_PREVIEW_MESSAGES + 4)
    ]

    mgr = _manager(tmp_path, [])
    _body, metadata = mgr._build_conversation_preview_and_metadata(user, ids[-1])

    recent = metadata["recent_messages"]
    assert len(recent) == _CONVERSATION_PREVIEW_MESSAGES
    assert [m["id"] for m in recent] == ids[-_CONVERSATION_PREVIEW_MESSAGES:]


def test_latest_incoming_falls_back_to_newest_incoming(tmp_path):
    """When the current id is not incoming/present (e.g. degraded call), the
    newest incoming message still anchors latest_incoming."""
    user = "wxid_dave@im.wechat"
    in1 = _write_inbox(
        tmp_path, from_user=user, body="ping", date="2026-07-06T01:00:00+00:00"
    )
    _write_sent(
        tmp_path, to_user=user, text="pong", date="2026-07-06T01:01:00+00:00"
    )

    mgr = _manager(tmp_path, [])
    _body, metadata = mgr._build_conversation_preview_and_metadata(
        user, "no-such-id"
    )

    assert metadata["latest_incoming"]["id"] == in1
    assert all("is_current" not in m for m in metadata["recent_messages"])
