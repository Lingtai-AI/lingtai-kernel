"""Notification metadata guarantees for the personal-account WhatsApp MCP.

`WhatsAppManager._handle_incoming` (bridge event path) must attach bounded
routing keys plus structured recent_messages/latest_incoming context to the
LICC metadata, and must never leak raw payloads or secrets.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.whatsapp.manager import (
    WhatsAppManager,
)


@pytest.fixture()
def manager(tmp_path: Path) -> WhatsAppManager:
    return WhatsAppManager(
        # autostart=False keeps the test hermetic: no Node bridge subprocess.
        {"store_dir": str(tmp_path / "store"), "autostart": False},
        working_dir=tmp_path,
    )


def _bridge_message(wamid: str, body: str, *, ts: int = 1700000000, from_id: str = "15551234567@c.us") -> dict:
    # 'chat' is what whatsapp-web.js actually emits for a plain text message
    # (MessageTypes.TEXT === 'chat'); the bridge normalizes it to 'text', and
    # the manager must treat both as text either way.
    return {
        "id": wamid,
        "from": from_id,
        "body": body,
        "type": "chat",
        "timestamp": ts,
        "fromMe": False,
    }


def _collected_metadata(manager: WhatsAppManager, message: dict) -> dict:
    """Run _handle_incoming and capture the metadata the LICC push would carry."""
    import lingtai.mcp_servers.whatsapp.manager as manager_mod
    captured = {}
    original = manager_mod.push_inbox_event
    def spy(sender, subject, body, *, metadata=None, wake=True, event_id=None):
        captured["metadata"] = metadata
        captured["event_id"] = event_id
        captured["body"] = body
        return True
    manager_mod.push_inbox_event = spy
    try:
        manager._handle_incoming(message)
    finally:
        manager_mod.push_inbox_event = original
    return captured


def test_incoming_attaches_routing_and_structured_context(manager: WhatsAppManager):
    """B4: ``type: 'chat'`` is the producer's vocabulary for plain text."""
    captured = _collected_metadata(manager, _bridge_message("wamid.A", "hello"))
    meta = captured["metadata"]
    # event_id components are sanitized: a peer chooses the message id, and
    # downstream LICC code may use the event id as a file name.
    assert captured["event_id"] == "wa:15551234567_c.us:wamid.A"
    assert meta["conversation_ref"] == "whatsapp:15551234567@c.us"
    assert meta["message_id"] == "wamid.A"
    assert meta["latest_incoming"]["text"] == "hello"
    assert meta["recent_messages"][-1]["text"] == "hello"
    # The LICC body carries the shared cross-channel preview header, then the
    # newest message — the same shape telegram/feishu/wechat emit.
    assert captured["body"].startswith("**How to read this WhatsApp conversation preview")
    assert captured["body"].endswith("**Newest WhatsApp message**\nhello")


def test_whatsapp_web_chat_type_is_treated_as_text_not_media(manager: WhatsAppManager):
    """B4: `type: 'chat'` is the producer's vocabulary for plain text."""
    captured = _collected_metadata(manager, {
        "id": "wamid.chat", "from": "15551234567@c.us", "type": "chat",
        "body": "a real text message", "timestamp": 1700000000,
    })
    meta = captured["metadata"]
    assert meta["latest_incoming"]["text"] == "a real text message"
    assert meta["recent_messages"][-1]["text"] == "a real text message"
    assert captured["body"].endswith("**Newest WhatsApp message**\na real text message")


def test_unknown_non_media_type_is_treated_as_text(manager: WhatsAppManager):
    captured = _collected_metadata(manager, {
        "id": "wamid.unknown", "from": "15551234567@c.us", "type": "some_future_type",
        "body": "still readable", "timestamp": 1700000000,
    })
    assert captured["metadata"]["latest_incoming"]["text"] == "still readable"


def test_hostile_message_id_cannot_escape_the_store(manager: WhatsAppManager):
    """B3: the message id is chosen by the *sending* client."""
    hostile = "../../../../../../tmp/pwned_by_wa_review"
    _collected_metadata(manager, {
        "id": hostile, "from": "../../evil@c.us", "type": "chat",
        "body": "traversal", "timestamp": 1700000000,
    })
    written = [p for p in manager.store_dir.rglob("*.json")]
    assert written, "message was not stored at all"
    for path in written:
        assert manager.store_dir.resolve() in path.resolve().parents


def test_allowed_users_accepts_bare_digits_and_jids(tmp_path: Path):
    manager = WhatsAppManager(
        {
            "store_dir": str(tmp_path / "store"),
            "autostart": False,
            # Bare digits — the same format `send` accepts.
            "allowed_users": ["15551234567"],
        },
        working_dir=tmp_path,
    )
    allowed = _collected_metadata(manager, _bridge_message("wamid.ok", "let me in"))
    assert allowed["metadata"]["message_id"] == "wamid.ok"

    denied = _collected_metadata(
        manager, _bridge_message("wamid.no", "keep out", from_id="19998887777@c.us")
    )
    assert denied == {}


def test_history_oldest_first_includes_outgoing(manager: WhatsAppManager):
    # History is ordered by each message's own timestamp, so the fixture must
    # use the timestamps a real conversation would carry.
    manager._handle_incoming(_bridge_message("wamid.B", "follow-up", ts=1700000000))
    manager._store_message("15551234567@c.us", "sent", {
        "id": "out1", "body": "my reply", "type": "text", "timestamp": 1700000100, "fromMe": True,
    })
    captured = _collected_metadata(manager, _bridge_message("wamid.C", "third", ts=1700000200))
    texts = [(m["fromMe"], m["text"]) for m in captured["metadata"]["recent_messages"]]
    assert texts == [(False, "follow-up"), (True, "my reply"), (False, "third")]


def test_bounded_window_and_text_cap(manager: WhatsAppManager):
    for i in range(20):
        manager._handle_incoming(_bridge_message(f"wamid.{i}", f"m{i}", ts=1700000000 + i))
    captured = _collected_metadata(manager, _bridge_message("wamid.long", "x" * 2000))
    recent = captured["metadata"]["recent_messages"]
    assert len(recent) <= 10
    assert all(len(m["text"]) <= 500 for m in recent)
    assert len(captured["metadata"]["latest_incoming"]["text"]) == 500


def test_history_sorted_by_message_timestamp_not_mtime(manager: WhatsAppManager):
    """A late-arriving older message must not jump to the end of history."""
    manager._handle_incoming(_bridge_message("wamid.late", "sent earlier", ts=1700000050))
    manager._store_message("15551234567@c.us", "sent", {
        "id": "out1", "body": "my reply", "type": "text", "timestamp": 1700000100,
    })
    # Re-writing a stored message (the dedup mechanism) refreshes its mtime.
    manager._handle_incoming(_bridge_message("wamid.late", "sent earlier", ts=1700000050))
    captured = _collected_metadata(manager, _bridge_message("wamid.new", "newest", ts=1700000200))
    texts = [m["text"] for m in captured["metadata"]["recent_messages"]]
    assert texts == ["sent earlier", "my reply", "newest"]


def test_media_type_only_context(manager: WhatsAppManager):
    captured = _collected_metadata(manager, {
        "id": "wamid.media", "from": "15551234567@c.us", "type": "image",
        "body": "", "timestamp": 1700000000,
    })
    assert captured["metadata"]["latest_incoming"]["text"] == "[image]"


def test_no_raw_payload_or_secrets_in_metadata(manager: WhatsAppManager):
    captured = _collected_metadata(manager, _bridge_message("wamid.C", "hi"))
    blob = json.dumps(captured)
    assert "access_token" not in blob and "app_secret" not in blob and "verify_token" not in blob
    assert "__raw__" not in blob and "payload" not in blob
