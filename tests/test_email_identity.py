"""Tests for email identity injection — every sent message carries sender's manifest,
every received summary surfaces sender's identity card.

Replaces test_mail_identity.py which targeted the deleted mail intrinsic.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from lingtai.agent import Agent
from lingtai_kernel.intrinsics import email as email_mod


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _make_agent(tmp_path, *, agent_name="alice", admin=None):
    """Build a real Agent rooted at tmp_path with a stable manifest."""
    return Agent(
        service=make_mock_service(),
        agent_name=agent_name,
        working_dir=tmp_path / agent_name,
        admin=admin or {},
    )


# ---------------------------------------------------------------------------
# Identity attached on send
# ---------------------------------------------------------------------------


def test_send_payload_contains_identity(tmp_path):
    """The sent record (in mailbox/sent/{id}/message.json) carries an identity dict."""
    agent = _make_agent(tmp_path, agent_name="alice", admin={"karma": True})
    mail_svc = MagicMock()
    mail_svc.address = "alice@example"
    mail_svc.send.return_value = None
    agent._mail_service = mail_svc

    mgr = agent._email_manager
    result = mgr.handle({
        "action": "send",
        "address": "/other/agent",
        "message": "hello",
        "subject": "test",
    })
    assert result["status"] == "sent"

    # The mailbox/sent/ entry should carry identity
    sent_dir = agent.working_dir / "mailbox" / "sent"
    sent_entries = [d for d in sent_dir.iterdir() if d.is_dir()]
    assert len(sent_entries) == 1
    sent_record = json.loads((sent_entries[0] / "message.json").read_text())
    assert "identity" in sent_record
    identity = sent_record["identity"]
    assert identity["agent_name"] == "alice"
    assert identity["admin"] == {"karma": True}

    agent.stop(timeout=1.0)


def test_send_identity_with_no_admin(tmp_path):
    """Identity works when admin is empty."""
    agent = _make_agent(tmp_path, agent_name="bob", admin={})
    mail_svc = MagicMock()
    mail_svc.address = "bob@example"
    mail_svc.send.return_value = None
    agent._mail_service = mail_svc

    mgr = agent._email_manager
    mgr.handle({"action": "send", "address": "/other", "message": "hi"})

    sent_dir = agent.working_dir / "mailbox" / "sent"
    sent_entry = next(d for d in sent_dir.iterdir() if d.is_dir())
    sent_record = json.loads((sent_entry / "message.json").read_text())
    assert sent_record["identity"]["agent_name"] == "bob"
    assert sent_record["identity"]["admin"] == {}

    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Identity surfaced in check
# ---------------------------------------------------------------------------


def _seed_inbox(working_dir: Path, msg_id: str, payload: dict) -> None:
    """Write a message directly to mailbox/inbox/{msg_id}/message.json."""
    inbox = working_dir / "mailbox" / "inbox" / msg_id
    inbox.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "_mailbox_id": msg_id, "received_at": "2026-05-01T10:00:00Z"}
    (inbox / "message.json").write_text(json.dumps(payload))


def test_check_shows_agent_name(tmp_path):
    """check surfaces sender_name when identity has agent_name."""
    agent = _make_agent(tmp_path)
    _seed_inbox(agent.working_dir, "msg1", {
        "from": "/sender/path",
        "subject": "hi",
        "message": "body",
        "identity": {
            "address": "/agents/sender",
            "agent_name": "bob",
            "admin": {"karma": False},
        },
    })
    result = agent._email_manager.handle({"action": "check"})
    assert result["total"] == 1
    msg = result["emails"][0]
    # Inbox check uses mail-style summary which formats from as "name (address)"
    assert msg["from"] == "bob (/sender/path)"
    assert msg.get("sender_name") == "bob"
    assert msg.get("is_human") is False  # admin is not None → it's an agent

    agent.stop(timeout=1.0)


def test_check_no_identity_backwards_compat(tmp_path):
    """Messages without identity surface plain from address; no sender_* fields."""
    agent = _make_agent(tmp_path)
    _seed_inbox(agent.working_dir, "msg1", {
        "from": "/sender/path",
        "subject": "old mail",
        "message": "no identity",
    })
    result = agent._email_manager.handle({"action": "check"})
    msg = result["emails"][0]
    assert msg["from"] == "/sender/path"
    assert "sender_name" not in msg
    assert "is_human" not in msg

    agent.stop(timeout=1.0)


def test_check_human_sender(tmp_path):
    """admin=None in identity → is_human=True."""
    agent = _make_agent(tmp_path)
    _seed_inbox(agent.working_dir, "msg1", {
        "from": "human-operator",
        "subject": "task",
        "message": "do this",
        "identity": {
            "address": "/human",
            "agent_name": "the human",
            "admin": None,
        },
    })
    result = agent._email_manager.handle({"action": "check"})
    msg = result["emails"][0]
    assert msg.get("is_human") is True

    agent.stop(timeout=1.0)


# ---------------------------------------------------------------------------
# Identity surfaced in read
# ---------------------------------------------------------------------------


def test_read_includes_identity(tmp_path):
    """read surfaces sender_name and is_human in the result."""
    agent = _make_agent(tmp_path)
    _seed_inbox(agent.working_dir, "msg1", {
        "from": "alice@example",
        "subject": "hello",
        "message": "from alice",
        "identity": {
            "address": "/agents/alice",
            "agent_name": "alice",
            "admin": {"karma": False},
        },
    })
    result = agent._email_manager.handle({"action": "read", "email_id": ["msg1"]})
    assert "emails" in result
    entry = result["emails"][0]
    assert entry.get("sender_name") == "alice"
    assert entry.get("is_human") is False

    agent.stop(timeout=1.0)


def test_read_no_identity_backwards_compat(tmp_path):
    """read of messages without identity is backwards compatible."""
    agent = _make_agent(tmp_path)
    _seed_inbox(agent.working_dir, "msg1", {
        "from": "anon",
        "subject": "old",
        "message": "no identity",
    })
    result = agent._email_manager.handle({"action": "read", "email_id": ["msg1"]})
    entry = result["emails"][0]
    assert entry["from"] == "anon"
    assert "sender_name" not in entry
    assert "is_human" not in entry

    agent.stop(timeout=1.0)
