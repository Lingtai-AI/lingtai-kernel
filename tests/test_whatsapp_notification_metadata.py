"""WhatsApp producer: structured LICC notification metadata for the persistent lane.

`WhatsAppManager.ingest_webhook` must attach bounded routing keys plus
structured `recent_messages` / `latest_incoming` context to the LICC event
metadata, mirroring the Telegram producer, so the kernel can project durable
content into `_meta.notification_persistent.mcp.whatsapp` and keep the
transient `_meta.agent_meta.notifications.attention.mcp.whatsapp` hook identity-only.
"""
from __future__ import annotations

import json

import pytest

from lingtai.mcp_servers.whatsapp.manager import (
    _CONVERSATION_CONTEXT_MESSAGES,
    _STRUCTURED_MESSAGE_TEXT_CAP,
    WhatsAppManager,
)

ACCESS_TOKEN = "secret-access-token"
APP_SECRET = "secret-app-secret"


@pytest.fixture()
def manager(tmp_path):
    return WhatsAppManager(
        accounts_config=[
            {
                "alias": "default",
                "phone_number_id": "10001",
                "access_token": ACCESS_TOKEN,
                "app_secret": APP_SECRET,
            }
        ],
        working_dir=tmp_path,
    )


def _webhook_payload(*messages: dict) -> dict:
    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "10001"},
                            "messages": list(messages),
                        }
                    }
                ]
            }
        ]
    }


def _text_message(wamid: str, body: str, *, wa_id: str = "15551234567") -> dict:
    return {
        "from": wa_id,
        "id": wamid,
        "timestamp": "1752000000",
        "type": "text",
        "text": {"body": body},
    }


def _capture_inbound(manager):
    events = []
    manager.on_inbound = events.append
    return events


def test_ingest_webhook_attaches_routing_and_structured_context(manager):
    inbound = _capture_inbound(manager)

    manager.ingest_webhook("default", _webhook_payload(_text_message("wamid.A", "hello")))

    assert len(inbound) == 1
    metadata = inbound[0]["metadata"]
    compound = "default:15551234567:wamid.A"
    assert metadata["platform"] == "whatsapp"
    assert metadata["conversation_ref"] == "default:15551234567"
    assert metadata["message_ref"] == compound
    assert metadata["message_id"] == compound

    latest = metadata["latest_incoming"]
    assert latest["id"] == compound
    assert latest["direction"] == "incoming"
    assert latest["text"] == "hello"
    assert latest["text_truncated"] is False
    assert latest["type"] == "text"
    assert latest["is_current"] is True

    recent = metadata["recent_messages"]
    assert [m["id"] for m in recent] == [compound]
    # LICC metadata must be JSON-safe for the inbox structured-metadata copy.
    json.dumps(metadata)


def test_structured_context_includes_outgoing_history_oldest_first(manager):
    inbound = _capture_inbound(manager)
    # Pre-existing conversation: an older incoming message and the agent's reply.
    manager._store_message(
        "default",
        "inbox",
        {
            "id": "default:15551234567:wamid.old",
            "wa_id": "15551234567",
            "message_id": "wamid.old",
            "text": "earlier question",
            "type": "text",
            "direction": "incoming",
            "stored_at": "2026-07-06T06:00:00+00:00",
        },
    )
    manager._store_message(
        "default",
        "sent",
        {
            "id": "default:15551234567:wamid.reply",
            "wa_id": "15551234567",
            "message_id": "wamid.reply",
            "text": "earlier answer",
            "direction": "outgoing",
            "payload": {"messaging_product": "whatsapp", "type": "text"},
            "response": {"messages": [{"id": "wamid.reply"}]},
            "stored_at": "2026-07-06T06:01:00+00:00",
        },
    )

    manager.ingest_webhook("default", _webhook_payload(_text_message("wamid.B", "follow-up")))

    metadata = inbound[0]["metadata"]
    recent = metadata["recent_messages"]
    assert [m["id"] for m in recent] == [
        "default:15551234567:wamid.old",
        "default:15551234567:wamid.reply",
        "default:15551234567:wamid.B",
    ]
    assert recent[1]["direction"] == "outgoing"
    assert recent[1]["type"] == "text"  # derived from the stored send payload
    assert metadata["latest_incoming"]["id"] == "default:15551234567:wamid.B"
    # Raw Cloud API objects and webhook metadata never ride into the
    # structured context — only allowlisted identity/content fields do.
    for item in recent:
        assert "payload" not in item
        assert "response" not in item
        assert "metadata" not in item
    assert ACCESS_TOKEN not in json.dumps(metadata)
    assert APP_SECRET not in json.dumps(metadata)


def test_structured_context_is_bounded(manager):
    inbound = _capture_inbound(manager)
    for i in range(15):
        manager._store_message(
            "default",
            "inbox",
            {
                "id": f"default:15551234567:wamid.{i}",
                "wa_id": "15551234567",
                "message_id": f"wamid.{i}",
                "text": f"message {i}",
                "type": "text",
                "direction": "incoming",
                "stored_at": f"2026-07-06T06:00:{i:02d}+00:00",
            },
        )

    long_text = "x" * (_STRUCTURED_MESSAGE_TEXT_CAP + 100)
    manager.ingest_webhook("default", _webhook_payload(_text_message("wamid.long", long_text)))

    metadata = inbound[0]["metadata"]
    recent = metadata["recent_messages"]
    assert len(recent) == _CONVERSATION_CONTEXT_MESSAGES
    # The window keeps the newest messages, ending with the current one.
    assert recent[-1]["id"] == "default:15551234567:wamid.long"
    assert recent[-1]["text"] == "x" * _STRUCTURED_MESSAGE_TEXT_CAP
    assert recent[-1]["text_truncated"] is True


def test_media_message_context_keeps_type_only(manager):
    inbound = _capture_inbound(manager)

    manager.ingest_webhook(
        "default",
        _webhook_payload(
            {
                "from": "15551234567",
                "id": "wamid.IMG",
                "timestamp": "1752000000",
                "type": "image",
            }
        ),
    )

    event = inbound[0]
    assert "[image]" in event["body"]
    latest = event["metadata"]["latest_incoming"]
    assert latest["type"] == "image"
    assert latest["text"] is None
    assert latest["text_truncated"] is False


def test_other_contact_messages_stay_out_of_context(manager):
    inbound = _capture_inbound(manager)
    manager._store_message(
        "default",
        "inbox",
        {
            "id": "default:19998887777:wamid.other",
            "wa_id": "19998887777",
            "message_id": "wamid.other",
            "text": "unrelated conversation",
            "type": "text",
            "direction": "incoming",
            "stored_at": "2026-07-06T06:00:00+00:00",
        },
    )

    manager.ingest_webhook("default", _webhook_payload(_text_message("wamid.C", "hi")))

    recent = inbound[0]["metadata"]["recent_messages"]
    assert [m["id"] for m in recent] == ["default:15551234567:wamid.C"]
