"""Focused producer-to-LICC tests for WhatsApp notification identity."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from lingtai.mcp_servers.whatsapp import manager as whatsapp_manager_module
from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager


def _message(message_id: str | None, sender: str, body: str = "hello") -> dict:
    payload = {
        "from": sender,
        "body": body,
        "type": "chat",
        "timestamp": 1752000000,
    }
    if message_id is not None:
        payload["id"] = message_id
    return payload


def _manager(tmp_path: Path) -> WhatsAppManager:
    return WhatsAppManager(
        {"store_dir": str(tmp_path / "store"), "autostart": False},
        working_dir=tmp_path,
    )


def _expected_event_id(sender: str, stored_id: str) -> str:
    encoded = json.dumps([sender, stored_id], ensure_ascii=False, separators=(",", ":")).encode()
    return f"wa_{hashlib.sha256(encoded).hexdigest()}"


def test_real_canonical_writer_accepts_bounded_id_and_preserves_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_MCP_NAME", "whatsapp")
    manager = _manager(tmp_path)
    sender = "15551234567@c.us"
    message_id = "wamid:punctuation/with a long " + "x" * 300

    manager._handle_incoming(_message(message_id, sender, "long body"))

    events = sorted((tmp_path / ".mcp_inbox" / "whatsapp").glob("*.json"))
    assert len(events) == 1
    assert len(events[0].stem) == 67
    payload = json.loads(events[0].read_text(encoding="utf-8"))
    assert events[0].stem == _expected_event_id(sender, message_id)
    assert payload["metadata"]["from"] == sender
    assert payload["metadata"]["message_id"] == message_id


@pytest.mark.parametrize(
    "sender, message_id",
    [
        ("15551234567@c.us", "wamid.A"),
        ("15551234567@c.us", None),
        ("15551234568@c.us", "wamid.A"),
        ("15551234567@c.us", "wamid.B"),
    ],
)
def test_real_writer_ids_are_distinct_for_distinct_stored_tuples(tmp_path, monkeypatch, sender, message_id):
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_MCP_NAME", "whatsapp")
    manager = _manager(tmp_path)
    manager._handle_incoming(_message(message_id, sender))

    event = next((tmp_path / ".mcp_inbox" / "whatsapp").glob("*.json"))
    stored_id = message_id
    if stored_id is None:
        message_path = next((tmp_path / "store" / "15551234567_c.us" / "inbox").glob("*.json"))
        stored_id = json.loads(message_path.read_text(encoding="utf-8"))["id"]
    assert event.stem == _expected_event_id(sender, stored_id)
    assert all(char.isalnum() or char in "_.-" for char in event.stem)


def test_writer_false_is_visible_without_replay_or_state_reordering(tmp_path, monkeypatch, caplog):
    manager = _manager(tmp_path)
    monkeypatch.setattr(whatsapp_manager_module, "push_inbox_event", lambda **_kwargs: False)
    message = _message("wamid.failed", "15551234567@c.us")

    with caplog.at_level(logging.WARNING):
        manager._handle_incoming(message)

    assert "returned false" in caplog.text
    assert "no automatic replay" in caplog.text
    assert len(list((tmp_path / "store").rglob("*.json"))) >= 2
    assert len(list((tmp_path / ".mcp_inbox").rglob("*.json"))) == 0
    # The normal replay guard remains authoritative; a writer failure is not
    # silently converted into a second historical delivery.
    manager._handle_incoming(message)
    assert len(list((tmp_path / "store").rglob("*.json"))) >= 2
