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
        {"store_dir": str(tmp_path / "store")},
        working_dir=tmp_path,
    )


def _bridge_message(wamid: str, body: str, *, ts: int = 1700000000, from_id: str = "15551234567@c.us") -> dict:
    return {
        "id": wamid,
        "from": from_id,
        "body": body,
        "type": "text",
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
    captured = _collected_metadata(manager, _bridge_message("wamid.A", "hello"))
    meta = captured["metadata"]
    assert captured["event_id"] == "wa:15551234567@c.us:wamid.A"
    assert meta["conversation_ref"] == "whatsapp:15551234567@c.us"
    assert meta["message_id"] == "wamid.A"
    assert meta["latest_incoming"]["text"] == "hello"
    assert meta["recent_messages"][-1]["text"] == "hello"


def test_history_oldest_first_includes_outgoing(manager: WhatsAppManager):
    manager._handle_incoming(_bridge_message("wamid.B", "follow-up"))
    manager._store_message("15551234567@c.us", "sent", {
        "id": "out1", "body": "my reply", "type": "text", "timestamp": 1700000100, "fromMe": True,
    })
    captured = _collected_metadata(manager, _bridge_message("wamid.C", "third"))
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
