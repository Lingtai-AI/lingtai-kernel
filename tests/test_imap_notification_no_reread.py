"""IMAP wake-notification producer: routing metadata and headers-only body.

The per-account IDLE/reconcile listener (``account.py``) fetches headers
only — it never populates a ``message`` body on the callback payload. The
notification the host forwards to the agent must therefore never look like
a body preview, but the exact compound ``email_id`` IS already known at wake
time, so it should travel as the shared LICC routing metadata
(``platform``/``conversation_ref``/``message_ref``) rather than forcing a
redundant ``check``/``search`` merely to reacquire an ID the notification
already carries.

These tests pin the producer half of that contract: routing keys present in
every wake event, the headers-only body/truncation facts staying truthful
when ``message`` is absent (today's real shape) versus present (forward
compatibility), and the production LICC closure propagating that metadata
unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from lingtai.mcp_servers import _licc_compat
from lingtai.mcp_servers.imap import server as imap_server
from lingtai.mcp_servers.imap.manager import IMAPMailManager


class _FakeAccount:
    def __init__(self, address: str) -> None:
        self.address = address


class _FakeService:
    """Minimal IMAPMailService stand-in — no network, no listener thread."""

    def __init__(self, address: str = "agent@example.com") -> None:
        self._account = _FakeAccount(address)

    def get_account(self, address: str | None):
        return self._account

    @property
    def default_account(self):
        return self._account


def _manager(events: list[dict], tmp_path: Path) -> IMAPMailManager:
    return IMAPMailManager(
        service=_FakeService(),
        working_dir=tmp_path,
        tcp_alias=str(tmp_path),
        on_inbound=events.append,
    )


def test_wake_event_carries_generic_routing_keys(tmp_path):
    """The compound email_id must reach the agent as message_ref/
    conversation_ref/platform, not only as the imap-specific email_id key
    the shared kernel filter silently drops from the model-visible preview."""
    events: list[dict] = []
    mgr = _manager(events, tmp_path)

    mgr.on_imap_received({
        "account": "agent@example.com",
        "email_id": "agent@example.com:INBOX:42",
        "from": "alice@example.org",
        "subject": "Q3 numbers",
    })

    assert len(events) == 1
    metadata = events[0]["metadata"]
    assert metadata["platform"] == "imap"
    assert metadata["conversation_ref"] == "agent@example.com"
    assert metadata["message_ref"] == "agent@example.com:INBOX:42"
    # Legacy imap-specific keys stay for any internal/backward-compat reader.
    assert metadata["email_id"] == "agent@example.com:INBOX:42"
    assert metadata["account"] == "agent@example.com"


def test_headers_only_payload_yields_empty_untruncated_body(tmp_path):
    """Today's real production shape: no `message` key at all (the listener
    fetch is headers-only). The body must stay empty rather than fabricate
    a "preview" — an empty body must not be confused with a short, complete
    message; it means content is absent and read() is required."""
    events: list[dict] = []
    mgr = _manager(events, tmp_path)

    mgr.on_imap_received({
        "account": "agent@example.com",
        "email_id": "agent@example.com:INBOX:7",
        "from": "bob@example.org",
        "subject": "hello",
        # no "message" key — matches the real reconcile()/fetch_headers_by_uids
        # header-only envelope shape.
    })

    event = events[0]
    assert event["body"] == ""
    assert event["metadata"]["preview_truncated"] is False
    assert event["metadata"]["full_length"] == 0


def test_short_message_is_not_truncated(tmp_path):
    events: list[dict] = []
    mgr = _manager(events, tmp_path)

    mgr.on_imap_received({
        "account": "agent@example.com",
        "email_id": "agent@example.com:INBOX:8",
        "from": "carol@example.org",
        "subject": "hi",
        "message": "short body",
    })

    event = events[0]
    assert event["body"] == "short body"
    assert event["metadata"]["preview_truncated"] is False
    assert event["metadata"]["full_length"] == len("short body")


def test_long_message_is_truncated_at_300_chars(tmp_path):
    """Forward-compat path: if a future producer populates `message`, the
    existing 300-char preview cap and truncation flag keep working."""
    events: list[dict] = []
    mgr = _manager(events, tmp_path)
    long_body = "x" * 500

    mgr.on_imap_received({
        "account": "agent@example.com",
        "email_id": "agent@example.com:INBOX:9",
        "from": "dave@example.org",
        "subject": "long",
        "message": long_body,
    })

    event = events[0]
    assert event["body"] == "x" * 300 + "..."
    assert event["metadata"]["preview_truncated"] is True
    assert event["metadata"]["full_length"] == 500


def test_production_closure_propagates_routing_metadata(tmp_path, monkeypatch):
    """The real server.build_manager() closure reaches the shared kernel
    LICC wrapper with the routing keys intact, end to end."""
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    config_path = tmp_path / "imap_config.json"
    config_path.write_text(
        json.dumps({
            "accounts": [{
                "email_address": "agent@example.com",
                "email_password": "unused-in-this-test",
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("LINGTAI_IMAP_CONFIG", str(config_path))

    calls: list[dict] = []

    def fake_kernel_push(sender, subject, body, *, metadata=None, wake=True, event_id=None):
        calls.append({
            "sender": sender, "subject": subject, "body": body,
            "metadata": metadata, "wake": wake, "event_id": event_id,
        })
        return True

    monkeypatch.setattr(_licc_compat, "_kernel_push_inbox_event", fake_kernel_push)
    assert imap_server.push_inbox_event is _licc_compat.push_inbox_event

    mgr, _bridge, _working_dir = imap_server.build_manager()
    try:
        mgr.on_imap_received({
            "account": "agent@example.com",
            "email_id": "agent@example.com:INBOX:100",
            "from": "erin@example.org",
            "subject": "server closure",
        })
    finally:
        pass

    assert len(calls) == 1
    metadata = calls[0]["metadata"]
    assert metadata["platform"] == "imap"
    assert metadata["conversation_ref"] == "agent@example.com"
    assert metadata["message_ref"] == "agent@example.com:INBOX:100"
    assert calls[0]["body"] == ""
