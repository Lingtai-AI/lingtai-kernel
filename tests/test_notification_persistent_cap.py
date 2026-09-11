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
import os
from types import SimpleNamespace

import lingtai.kernel.meta_block as meta_block

MAX = meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS
ENV = meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_ENV


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


def _telegram_message(message_id: int, *, text: str, is_current: bool = False) -> dict:
    message = {
        "id": f"main:123:{message_id}",
        "direction": "incoming",
        "sender": "Jason",
        "date": f"2026-08-10T09:00:{message_id % 60:02d}Z",
        "relative_time": "just now",
        "text": text,
        "text_truncated": False,
    }
    if is_current:
        message["is_current"] = True
    return message


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


def test_env_cap_resolver_default_and_ceiling(monkeypatch):
    """Unset env returns the default; values above the 10k ceiling clamp."""
    monkeypatch.delenv(ENV, raising=False)
    assert meta_block._notification_persistent_max_chars() == MAX

    monkeypatch.setenv(ENV, "5000")
    assert meta_block._notification_persistent_max_chars() == 5000

    # The 10k ceiling cannot be raised via env — clamp back to default.
    monkeypatch.setenv(ENV, "20000")
    assert meta_block._notification_persistent_max_chars() == MAX


def test_env_cap_resolver_invalid_values_fall_back(monkeypatch):
    """Missing, non-numeric, zero, and negative values fall back to default."""
    for raw in ("", "abc", "0", "-5", "12.5"):
        if raw == "":
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, raw)
        assert meta_block._notification_persistent_max_chars() == MAX, raw


def test_env_cap_tightened_by_operator(monkeypatch, tmp_path):
    """A payload under the default cap spills once the operator lowers it."""
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 200) for i in range(1, 16)]

    # Under the default 10k cap the same payload fits with zero changes.
    default_payload = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )
    assert "overflow" not in default_payload["notification_persistent"]
    assert _envelope_chars(default_payload) <= MAX

    # Lowering the cap via env makes the identical payload spill and compact.
    monkeypatch.setenv(ENV, "500")
    payload = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )

    persistent = payload["notification_persistent"]
    assert persistent["overflow"]["truncated"] is True
    # Structural fields stay intact; every id still survives the tighter cap.
    expected_ids = [f"email-{i}" for i in range(1, 16)]
    assert [e["id"] for e in persistent["email"]["emails"]] == expected_ids
    assert persistent["email"]["email_ids"] == expected_ids


def test_low_positive_caps_never_return_unchecked_persistent_payload(
    monkeypatch, tmp_path
):
    """P1-2: every valid positive cap bounds the returned persistent envelope.

    The shared-bar contract promises EVERY model-visible notification block
    satisfies the effective cap by construction.  A normal one-email payload
    (706-708 serialized chars) must therefore never be returned unchecked at
    low positive caps (1/50/200/300): the shared 2048 floor clamps those values
    UP so the payload (and any terminal stub) fits inside the effective cap.
    """
    agent = _cap_agent(tmp_path)
    emails = [_email(1, message="ordinary email body")]
    for configured in (1, 50, 200, 300):
        monkeypatch.setenv(ENV, str(configured))
        payload = meta_block.build_notification_persistent_payload(
            agent, _email_payload(emails)
        )
        # Provider-visible serialization (default ASCII escaping, wrapper key
        # included) must fit the effective cap = max(configured, 2048).
        effective = max(configured, meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS_MIN)
        serialized = len(json.dumps(payload, default=str))
        assert serialized <= effective, (configured, effective, serialized)


def test_persistent_terminal_marker_only_envelope_is_capped_by_construction(
    monkeypatch, tmp_path
):
    """P1-2: even a pathological persistent stub falls back to a marker-only
    envelope that strictly fits; a too-long spill path is stripped and the
    exact spill basename is retained as ``spill_file``."""
    monkeypatch.setenv(ENV, "2048")  # floor
    # A lane with an enormous number of id-only records exceeds even the 2048
    # floor after stubbing, forcing the terminal marker-only fallback.
    records = [
        {
            "id": f"email-{i}",
            "event_id": f"evt-{i}",
            "message": "X" * 300,
        }
        for i in range(1, 2000)
    ]
    persistent = {"email": {"emails": records, "email_ids": [r["id"] for r in records]}}
    capped = meta_block._cap_notification_persistent(
        _cap_agent(tmp_path), copy.deepcopy(persistent)
    )
    # Whatever degradation path was chosen, the returned wrapper fits the cap.
    assert len(json.dumps({"notification_persistent": capped}, default=str)) <= 2048
    # The recovery handle survives on the envelope.
    assert capped["overflow"]["truncated"] is True
    # If the path was omitted, the exact spill basename is the locator.
    if capped["overflow"].get("path_omitted"):
        assert capped["overflow"].get("spill_file") or capped["overflow"]["spill_failed"]


def test_compacted_im_messages_get_truthful_truncated_flag_and_comment(tmp_path):
    """The cap must never blank a heavy field while leaving a stale
    ``text_truncated: false`` — a shortened record has to say so."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 2000) for i in range(1, 41)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _telegram_payload(messages)
    )

    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    shortened = [m for m in telegram["messages"] if len(m["text"]) < 2000]
    assert shortened, "sanity: the cap must have actually shortened something"
    for message in shortened:
        assert message["text_truncated"] is True
        assert (
            meta_block.NOTIFICATION_PERSISTENT_TELEGRAM_TRUNCATED_COMMENT
            in message.get("comment", "")
        )


def test_compacted_emails_get_truthful_message_truncated_flag(tmp_path):
    """Mirrors the IM truthfulness fix for the email lane's own flag."""
    agent = _cap_agent(tmp_path)
    emails = [_email(i, message="E" * 3000) for i in range(1, 26)]

    payload = meta_block.build_notification_persistent_payload(
        agent, _email_payload(emails)
    )

    email_lane = payload["notification_persistent"]["email"]
    dropped_ids = set(email_lane.get("dropped_ids", []))
    for email in email_lane["emails"]:
        if email["id"] in dropped_ids:
            continue
        if len(email["message"]) < 3000:
            assert email["message_truncated"] is True


def test_compact_im_record_recovery_is_conditional_even_with_raw_envelope():
    """A shorter copy keeps a truthful flag, not an unconditional read command.

    Keep raw data untouched; do not infer completeness from a channel key.
    """
    lane = meta_block._TELEGRAM_PERSISTENT_LANE
    record_with_envelope = {
        "id": "main:1:1",
        "text": "T" * 500,
        "telegram": {"update_id": 1, "message": {"text": "T" * 500}},
    }
    record_without_envelope = {"id": "main:1:2", "text": "T" * 500}

    compacted_with = meta_block._compact_im_persistent_record(
        record_with_envelope, 50, lane, protect_current=False
    )
    compacted_without = meta_block._compact_im_persistent_record(
        record_without_envelope, 50, lane, protect_current=False
    )

    assert compacted_with["text_truncated"] is True
    assert "Only if required content is absent" in compacted_with["comment"]
    assert "do not reread a complete alternate/raw current copy" in compacted_with["comment"]
    # The raw envelope itself is untouched — it is outside the heavy fields.
    assert compacted_with["telegram"] == {
        "update_id": 1,
        "message": {"text": "T" * 500},
    }

    assert compacted_without["text_truncated"] is True
    assert lane.truncated_comment in compacted_without["comment"]


def test_current_message_protected_over_obsolete_history_before_truncation(tmp_path):
    """Common-contract priority: the cap exhausts obsolete history before it
    ever shortens the current/new message that the producer flagged
    ``is_current``."""
    agent = _cap_agent(tmp_path)
    messages = [_telegram_message(i, text="T" * 2000) for i in range(1, 26)]
    messages[-1] = _telegram_message(25, text="T" * 2000, is_current=True)

    payload = meta_block.build_notification_persistent_payload(
        agent, _telegram_payload(messages)
    )

    assert _envelope_chars(payload) <= MAX
    telegram = payload["notification_persistent"]["mcp"]["telegram"]
    current = next(m for m in telegram["messages"] if m.get("is_current"))
    # Fully intact: never touched by the cap, even though history around it was.
    assert current["text"] == "T" * 2000
    assert current["text_truncated"] is False
    assert "comment" not in current

    history = [m for m in telegram["messages"] if not m.get("is_current")]
    assert history
    for message in history:
        if "text" in message:
            assert len(message["text"]) < 2000
        else:
            assert message["id"] in telegram["dropped_ids"]


def test_drop_stage_protects_current_message_first(tmp_path):
    """Direct unit test on the pathological id-only-stub fallback: obsolete
    history is stubbed before the current message is even considered."""
    records = [
        {"id": f"main:1:{i}", "event_id": f"evt-{i}", "text": "H" * 200}
        for i in range(1, 20)
    ]
    records.append(
        {
            "id": "main:1:current",
            "event_id": "evt-current",
            "text": "C" * 200,
            "is_current": True,
        }
    )
    persistent = {"mcp": {"telegram": {"messages": records}}}

    dropped = meta_block._drop_notification_persistent_records(
        copy.deepcopy(persistent), max_chars=3000
    )

    telegram = dropped["mcp"]["telegram"]
    current = next(m for m in telegram["messages"] if m.get("is_current"))
    assert current["text"] == "C" * 200
    assert "main:1:current" not in telegram.get("dropped_ids", [])
    assert any(
        set(m) == {"id", "event_id"}
        for m in telegram["messages"]
        if not m.get("is_current")
    )
    assert meta_block._notification_persistent_envelope_chars(dropped) <= 3000


def test_bulky_non_text_history_is_stubbed_before_current_is_touched(tmp_path):
    """Regression for the compact/stub ordering defect: obsolete history that
    carries a heavy NON-text field (outside ``NOTIFICATION_PERSISTENT_IM_HEAVY_FIELDS``,
    so per-field compaction cannot shrink it) must still be fully stubbed
    before the current/new message is compacted or stubbed at all, as long as
    stubbing history alone makes the envelope fit. Before the fix, the second
    (unprotected) compact pass ran through every budget tier — touching the
    current message — before stubbing was ever tried on this history."""
    records = [
        {
            "id": f"main:1:{i}",
            "event_id": f"evt-{i}",
            "text": "short",
            "reply_context": "M" * 400,  # bulky, but not a compacted field
        }
        for i in range(1, 30)
    ]
    records.append(
        {
            "id": "main:1:current",
            "event_id": "evt-current",
            "text": "C" * 300,
            "is_current": True,
        }
    )
    persistent = {"mcp": {"telegram": {"messages": records}}}

    capped = meta_block._cap_notification_persistent(
        _cap_agent(tmp_path), copy.deepcopy(persistent)
    )

    assert (
        meta_block._notification_persistent_envelope_chars(capped)
        <= meta_block.NOTIFICATION_PERSISTENT_MAX_CHARS
    )
    telegram = capped["mcp"]["telegram"]
    current = next(m for m in telegram["messages"] if m.get("is_current"))
    # Current is completely untouched: stubbing obsolete history's bulky
    # non-text field alone made the envelope fit, so compaction/stubbing
    # never had to reach the current message.
    assert current["text"] == "C" * 300
    assert "text_truncated" not in current
    assert "comment" not in current
    assert telegram.get("dropped_ids")
    assert any(
        set(m) == {"id", "event_id"}
        for m in telegram["messages"]
        if not m.get("is_current")
    )


def test_drop_stage_stubs_current_message_only_as_last_resort(tmp_path):
    """Once every other record is already an id-only stub and the envelope
    still does not fit, the current message may finally be stubbed too."""
    records = [
        {"id": f"main:1:{i}", "event_id": f"evt-{i}", "text": "H" * 200}
        for i in range(1, 20)
    ]
    records.append(
        {
            "id": "main:1:current",
            "event_id": "evt-current",
            "text": "C" * 200,
            "is_current": True,
        }
    )
    persistent = {"mcp": {"telegram": {"messages": records}}}

    dropped = meta_block._drop_notification_persistent_records(
        copy.deepcopy(persistent), max_chars=300
    )

    telegram = dropped["mcp"]["telegram"]
    current = next(m for m in telegram["messages"] if m.get("id") == "main:1:current")
    assert set(current) == {"id", "event_id"}
    assert "main:1:current" in telegram["dropped_ids"]
