"""Tests for the 10k character cap on the persistent notification block.

The persistent block is re-serialized into provider context on every turn, so a
busy hub agent (many unread emails plus several IM lanes) could grow context
fast and pay a large per-call cache miss.  ``build_notification_persistent_payload``
is the single chokepoint for both the ACTIVE and IDLE injection paths: over the
cap it spills the full block to ``logs/notification-overflow-<ts>.json`` and
returns a compacted copy that still carries every message id.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import lingtai.kernel.meta_block as meta_block

MAX = meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS


def _cap_agent(tmp_path):
    """Minimal agent stand-in: a working dir plus the Telegram delta-lane state.

    Mirrors the fixture style of ``tests/test_meta_block.py`` (``_notif_agent`` /
    the unit-level ``SimpleNamespace`` agents used by the other
    ``build_notification_persistent_payload`` tests).
    """
    return SimpleNamespace(
        _working_dir=str(tmp_path),
        _notification_persistent_telegram_message_ids=[],
        _notification_persistent_telegram_last_tool_id=None,
    )


def _envelope_chars(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _spill_files(tmp_path):
    return sorted((tmp_path / "logs").glob("notification-overflow-*.json"))


def _telegram_message(message_id: int, *, text: str) -> dict:
    return {
        "id": f"main:123:{message_id}",
        "direction": "incoming",
        "sender": "Jason",
        "date": f"2026-08-10T09:00:{message_id % 60:02d}Z",
        "relative_time": "just now",
        "text": text,
        "text_truncated": False,
    }


def _telegram_payload(messages: list[dict]) -> dict:
    return {
        "notifications": {
            "mcp.telegram": {
                "data": {
                    "count": 1,
                    "previews": [
                        {
                            "from": "Jason",
                            "subject": "telegram message from Jason via main",
                            "platform": "telegram",
                            "conversation_ref": "main:123",
                            "message_ref": messages[-1]["id"],
                            "recent_messages": messages,
                            "latest_incoming": messages[-1],
                        }
                    ],
                }
            }
        }
    }


def _email(email_id: int, *, message: str) -> dict:
    return {
        "id": f"email-{email_id}",
        "from": "human",
        "from_address": "human@example.com",
        "to": ["mimo-1"],
        "cc": [],
        "subject": f"Subject {email_id}",
        "date": "2026-08-10T07:00:00Z",
        "message": message,
        "message_chars": len(message),
        "message_truncated": False,
        "unread": True,
        "received_at": "2026-08-10T07:00:00Z",
    }


def _email_payload(emails: list[dict]) -> dict:
    return {
        "notifications": {
            "email": {
                "data": {
                    "count": len(emails),
                    "newest_received_at": "2026-08-10T07:00:00Z",
                    "email_ids": [email["id"] for email in emails],
                    "emails": emails,
                }
            }
        }
    }


def test_small_payload_unchanged_and_no_spill(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text=f"message {i}") for i in range(1, 4)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _telegram_payload(messages)
    )

    persistent = payload["notification_persistent"]
    assert "overflow" not in persistent
    telegram = persistent["mcp"]["telegram"]
    assert [m["id"] for m in telegram["messages"]] == [
        "main:123:1",
        "main:123:2",
        "main:123:3",
    ]
    # Full text survives untouched — zero behavior change under the cap.
    assert [m["text"] for m in telegram["messages"]] == [
        "message 1",
        "message 2",
        "message 3",
    ]
    assert _envelope_chars(payload) <= MAX
    assert not (tmp_path / "logs").exists() or _spill_files(tmp_path) == []


def test_large_email_payload_spills_and_compacts(tmp_path):
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 3000) for i in range(1, 26)]
    notification_payload = _email_payload(emails)
    original = copy.deepcopy(notification_payload)

    payload = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )

    assert _envelope_chars(payload) <= MAX
    persistent = payload["notification_persistent"]
    overflow = persistent["overflow"]
    assert overflow["truncated"] is True
    assert overflow["full_chars"] > MAX
    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 1
    assert overflow["path"] == str(spill_files[0])

    # The spill file holds the FULL original block, not the compacted one.
    spilled = json.loads(spill_files[0].read_text(encoding="utf-8"))
    spilled_emails = spilled["notification_persistent"]["email"]["emails"]
    assert len(spilled_emails) == 25
    assert spilled_emails[0]["message"] == "E" * 3000

    email_lane = persistent["email"]
    assert email_lane["count"] == 25
    assert email_lane["newest_received_at"] == "2026-08-10T07:00:00Z"
    assert "context_comment" in email_lane
    # Every id survives, in both the id list and the per-email records.
    expected_ids = [f"email-{i}" for i in range(1, 26)]
    assert email_lane["email_ids"] == expected_ids
    assert [e["id"] for e in email_lane["emails"]] == expected_ids
    for compacted_email in email_lane["emails"]:
        assert len(compacted_email["message"]) < 3000
        assert compacted_email["message"].endswith("...")
        # Routing/structural fields are never truncated away.
        assert compacted_email["subject"].startswith("Subject ")
        assert compacted_email["from"] == "human"
        assert compacted_email["from_address"] == "human@example.com"

    # The caller's notification payload is never mutated.
    assert notification_payload == original


def test_email_overflow_comment_points_at_the_spill_file(tmp_path):
    """A block that fits at the widest budget carries the per-email spill note."""
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 3000) for i in range(1, 7)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )

    assert _envelope_chars(payload) <= MAX
    persistent = payload["notification_persistent"]
    spill_path = persistent["overflow"]["path"]
    for compacted_email in persistent["email"]["emails"]:
        assert compacted_email["message"] == "E" * 200 + "..."
        assert spill_path in compacted_email["comment"]


def test_large_im_payload_spills_and_compacts(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 2000) for i in range(1, 41)]
    notification_payload = _telegram_payload(messages)
    original = copy.deepcopy(notification_payload)

    payload = meta_block.build_notification_persistent_payload(
        agent, notification_payload
    )

    assert _envelope_chars(payload) <= MAX
    persistent = payload["notification_persistent"]
    overflow = persistent["overflow"]
    assert overflow["truncated"] is True
    assert overflow["full_chars"] > MAX
    spill_files = _spill_files(tmp_path)
    assert len(spill_files) == 1
    assert overflow["path"] == str(spill_files[0])

    spilled = json.loads(spill_files[0].read_text(encoding="utf-8"))
    spilled_messages = spilled["notification_persistent"]["mcp"]["telegram"]["messages"]
    assert spilled_messages[0]["text"] == "T" * 2000

    telegram = persistent["mcp"]["telegram"]
    # Seed block: the lane's min-context window, every id preserved.
    assert [m["id"] for m in telegram["messages"]] == [
        m["id"] for m in spilled_messages
    ]
    for message in telegram["messages"]:
        assert len(message["text"]) < 2000
        assert message["direction"] == "incoming"
        assert message["sender"] == "Jason"
        assert message["date"]
    assert telegram["previous_block"]["path"] == (
        meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_PATH
    )
    assert notification_payload == original


def test_delivery_tracking_sees_every_id_after_compaction(tmp_path):
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 2000) for i in range(1, 41)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _telegram_payload(messages)
    )
    spilled = json.loads(_spill_files(tmp_path)[0].read_text(encoding="utf-8"))
    full_ids = [
        m["id"] for m in spilled["notification_persistent"]["mcp"]["telegram"]["messages"]
    ]

    meta_block.record_notification_persistent_delivery(
        agent, payload, tool_call_id="call-cap"
    )

    delivered = agent._notification_persistent_telegram_message_ids
    assert delivered == full_ids
    assert agent._notification_persistent_telegram_last_tool_id == "call-cap"


def test_pathological_payload_stubs_ids_instead_of_losing_them(tmp_path):
    """Even when every heavy field is emptied, ids must survive the cap."""
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 200) for i in range(1, 201)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )

    assert _envelope_chars(payload) <= MAX
    email_lane = payload["notification_persistent"]["email"]
    expected_ids = [f"email-{i}" for i in range(1, 201)]
    assert [e["id"] for e in email_lane["emails"]] == expected_ids
    # Some records were reduced to id-only stubs, and each one is named.
    dropped = email_lane["dropped_ids"]
    assert dropped
    assert set(dropped).issubset(set(expected_ids))
    for email in email_lane["emails"]:
        if email["id"] in dropped:
            assert set(email) == {"id"}


def test_two_overflows_in_one_second_keep_both_spill_files(tmp_path):
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 3000) for i in range(1, 26)]

    first = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )
    second = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )

    paths = {
        first["notification_persistent"]["overflow"]["path"],
        second["notification_persistent"]["overflow"]["path"],
    }
    assert len(paths) == 2
    assert len(_spill_files(tmp_path)) == 2
