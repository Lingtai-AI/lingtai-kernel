"""No-reread contract for the WhatsApp LICC notification.

A CURRENT full message in the notification (metadata + inline body excerpt)
must be usable without an extra `read`/`check`/`search` call, and truncation
must be a truthful, explicit fact rather than an unmarked silent cut. See
``src/lingtai/mcp_servers/whatsapp/manager.py`` (``_conversation_context``,
``_handle_incoming``) and the "Do not reread a full current message" section
of ``src/lingtai/mcp_servers/whatsapp/SKILL.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lingtai.mcp_servers.whatsapp.manager import WhatsAppManager


@pytest.fixture()
def manager(tmp_path: Path, monkeypatch) -> WhatsAppManager:
    # Even direct _handle_incoming calls must not publish into a host inbox.
    import lingtai.mcp_servers.whatsapp.manager as manager_mod
    monkeypatch.setattr(manager_mod, "push_inbox_event", lambda *args, **kwargs: True)
    return WhatsAppManager(
        {"store_dir": str(tmp_path / "store"), "autostart": False},
        working_dir=tmp_path,
    )


def _bridge_message(wamid: str, body: str, *, ts: int = 1700000000, from_id: str = "15551234567@c.us") -> dict:
    return {
        "id": wamid,
        "from": from_id,
        "body": body,
        "type": "chat",
        "timestamp": ts,
        "fromMe": False,
    }


def _collected(manager: WhatsAppManager, message: dict) -> dict:
    import lingtai.mcp_servers.whatsapp.manager as manager_mod
    captured = {}
    original = manager_mod.push_inbox_event

    def spy(sender, subject, body, *, metadata=None, wake=True, event_id=None):
        captured["metadata"] = metadata
        captured["body"] = body
        return True

    manager_mod.push_inbox_event = spy
    try:
        manager._handle_incoming(message)
    finally:
        manager_mod.push_inbox_event = original
    return captured


def test_short_current_message_is_marked_not_truncated(manager: WhatsAppManager):
    """A short message is the complete current message: no reread signal anywhere."""
    captured = _collected(manager, _bridge_message("wamid.short", "hello there"))
    latest = captured["metadata"]["latest_incoming"]
    assert latest["text"] == "hello there"
    assert latest["text_truncated"] is False
    assert "(truncated at 2000 chars;" not in captured["body"]


def test_long_current_message_is_truthfully_flagged_in_metadata(manager: WhatsAppManager):
    """Beyond the structured 500-char cap, text_truncated must be True, not silent."""
    long_text = "x" * 900
    captured = _collected(manager, _bridge_message("wamid.long", long_text))
    latest = captured["metadata"]["latest_incoming"]
    assert len(latest["text"]) == 500
    assert latest["text_truncated"] is True


def test_body_excerpt_beyond_cap_carries_an_explicit_recovery_note(manager: WhatsAppManager):
    """The inline body excerpt cap (2000 chars) must self-describe when it cuts content."""
    long_text = "y" * 2500
    captured = _collected(manager, _bridge_message("wamid.verylong", long_text, from_id="19998887777@c.us"))
    body = captured["body"]
    assert "y" * 2000 in body
    assert "y" * 2001 not in body
    assert "truncated at 2000 chars" in body
    assert "read" in body
    assert "19998887777@c.us" in body


def test_body_excerpt_at_or_under_cap_has_no_truncation_note(manager: WhatsAppManager):
    """No false-positive recovery note when the message actually fits."""
    exact_text = "z" * 2000
    captured = _collected(manager, _bridge_message("wamid.exact", exact_text))
    assert "(truncated at 2000 chars;" not in captured["body"]
    assert exact_text in captured["body"]


def test_recent_messages_carry_the_same_truthful_truncation_flag(manager: WhatsAppManager):
    for i in range(3):
        manager._handle_incoming(_bridge_message(f"wamid.{i}", f"m{i}", ts=1700000000 + i))
    captured = _collected(manager, _bridge_message("wamid.hist-long", "h" * 700, ts=1700000010))
    recent = captured["metadata"]["recent_messages"]
    truncated_flags = {m["id"]: m["text_truncated"] for m in recent}
    assert truncated_flags["wamid.0"] is False
    assert truncated_flags["wamid.hist-long"] is True
    assert all(len(m["text"]) <= 500 for m in recent)


def test_between_caps_message_has_complete_excerpt_despite_truncated_structured_flag(manager: WhatsAppManager):
    """600 chars: latest_incoming.text_truncated is True (>500) but the 2000-cap
    inline excerpt is complete — the manual says a complete excerpt does not
    need both flags to agree, so no truncation note may appear here."""
    text = "q" * 600
    captured = _collected(manager, _bridge_message("wamid.between", text))
    latest = captured["metadata"]["latest_incoming"]
    assert latest["text_truncated"] is True
    assert "(truncated at 2000 chars;" not in captured["body"]
    assert text in captured["body"]


def test_manual_states_no_unnecessary_reread_rule():
    from lingtai.mcp_servers.whatsapp.plugin import WHATSAPP_PLUGIN

    body = " ".join(WHATSAPP_PLUGIN.skill_body.split())
    assert "Do not reread a full current message" in body
    assert "text_truncated" in body
    assert "does not need both flags to agree" in body
    assert "history overflow" in body


def test_manual_does_not_falsely_promise_every_notification_is_full():
    from lingtai.mcp_servers.whatsapp.plugin import WHATSAPP_PLUGIN

    body = " ".join(WHATSAPP_PLUGIN.skill_body.split())
    assert "Each notification already carries the current full message" not in body


def test_manual_narrows_reconciliation_to_actually_missing_context():
    from lingtai.mcp_servers.whatsapp.plugin import WHATSAPP_PLUGIN

    body = " ".join(WHATSAPP_PLUGIN.skill_body.split())
    assert "actually missing from the agent's memory" in body
    assert "not merely because a restart happened while the context is still present" in body


def test_manual_directs_missing_media_to_resend_not_reread():
    from lingtai.mcp_servers.whatsapp.plugin import WHATSAPP_PLUGIN

    body = " ".join(WHATSAPP_PLUGIN.skill_body.split())
    assert "no inbound download support" in body
    assert "ask the sender to resend it" in body


def test_manager_description_aligns_with_no_reread_rule():
    from lingtai.mcp_servers.whatsapp.manager import DESCRIPTION

    assert "do not call check/read/search" in DESCRIPTION
    assert "re-fetch an ID already given" in DESCRIPTION


def test_live_schema_action_description_aligns_with_no_reread_rule():
    from lingtai.mcp_servers.whatsapp.manager import SCHEMA

    description = SCHEMA["properties"]["action"]["description"]
    assert "don't call check/read/search just to reread it" in description


def test_notification_header_carries_no_reread_line():
    from lingtai.mcp_servers.whatsapp.manager import _NOTIFICATION_HEADER_TEMPLATE

    rendered = _NOTIFICATION_HEADER_TEMPLATE.format(channel="WhatsApp")
    assert "do not call check/read/search merely to reread" in rendered


def test_notification_header_line_reaches_the_live_notification_body(manager: WhatsAppManager):
    captured = _collected(manager, _bridge_message("wamid.header", "hi"))
    assert "do not call check/read/search merely to reread" in captured["body"]
