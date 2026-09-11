"""Guard the Telegram no-reread contract: a complete current message in the
persistent notification lane must not require an extra ``telegram.read`` just
to reread identical text, confirm an id already given, or resolve ambiguity/
media/callback presence. ``telegram.read`` is still the correct recovery path
when a record's own ``text_truncated`` is true or a needed attachment path is
missing (and no ``download_error`` is already recorded for it). Synthetic
``updates``/callback-only records stay read/search-only and are never reply
targets. No heuristic (e.g. a trailing ``...`` or a shared-cap ``overflow``
flag) may substitute for the record's own ``text_truncated`` field.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.telegram import manager as tg_manager
from lingtai.mcp_servers.telegram import _family as tg_family
from lingtai.mcp_servers.telegram.manager import DESCRIPTION, SCHEMA, TelegramManager
from tests._notification_store_helpers import notification_store_for


class _FakeAccount:
    alias = "main"

    def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> dict[str, Any]:
        return {"message_id": 9001, "chat": {"id": chat_id}, "text": text}

    def set_message_reaction(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def send_chat_action(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _FakeService:
    default_account = _FakeAccount()

    def get_account(self, _alias: str) -> _FakeAccount:
        return self.default_account

    def list_accounts(self) -> list[str]:
        return ["main"]


# ---------------------------------------------------------------------------
# Guidance text: no stale "reread for ambiguity/media/callback/anchoring"
# triggers, no heuristic-truncation substitute for text_truncated, and the
# non-synthetic/download_error carve-outs are present in every surface that
# advertises the rule.
# ---------------------------------------------------------------------------

_FORBIDDEN_REREAD_TRIGGERS = ("media/callback-heavy", "exact anchoring")
_STALE_RULE_SENTENCE = "truncated, ambiguous, media/callback-heavy"
# The rejected heuristic: treating a trailing "..." (or the shared-cap
# "overflow" flag) as truncation evidence on its own, instead of the record's
# own text_truncated field. Natural user ellipses are not evidence.
_FORBIDDEN_HEURISTIC_PHRASES = (
    "trailing `...`",
    "trailing \"...\"",
    "treat a trailing",
    "regardless of that flag",
)


def _normalize_ws(text: str) -> str:
    """Collapse Markdown line-wrap newlines/indentation for substring checks.

    Source `.md` files hard-wrap prose across lines; a multi-word phrase can
    legitimately straddle a wrap without being stale. Only single continuous
    Python string literals (DESCRIPTION, the _family.py schema text) are safe
    to substring-match without this.
    """
    return " ".join(text.split())


def _assert_no_reread_guidance(text: str) -> None:
    lowered = _normalize_ws(text.lower())
    for phrase in _FORBIDDEN_REREAD_TRIGGERS:
        assert phrase not in lowered, phrase
    assert _STALE_RULE_SENTENCE not in lowered
    for phrase in _FORBIDDEN_HEURISTIC_PHRASES:
        assert phrase.lower() not in lowered, phrase
    assert "text_truncated" in text


def test_notification_header_drops_stale_and_heuristic_reread_triggers() -> None:
    header = tg_manager._NOTIFICATION_HEADER_TEMPLATE
    _assert_no_reread_guidance(header)
    lowered = _normalize_ws(header.lower())
    assert "telegram.read" in header
    assert "vision" in lowered or "file tool" in lowered
    # Non-synthetic carve-out: synthetic/callback-only records are never
    # implied to be valid reply targets just because they carry an id.
    assert "synthetic" in lowered
    assert "never reply targets" in lowered or "never outbound targets" in lowered
    # download_error carve-out: a failed download needs a resend, not a reread.
    assert "download_error" in header
    assert "resend" in lowered


def test_skill_manual_matches_notification_header_no_reread_rule() -> None:
    body = tg_manager._SKILL_BODY
    _assert_no_reread_guidance(body)
    lowered = _normalize_ws(body.lower())
    assert "synthetic" in lowered
    assert "download_error" in body


def test_skill_manual_first_action_step_does_not_mandate_reread_ceremony() -> None:
    body = tg_manager._SKILL_BODY
    lowered = _normalize_ws(body.lower())
    assert "do not call `check`, `read`, or `search` merely to reread" in lowered
    assert "obtain an id already given" in lowered


# ---------------------------------------------------------------------------
# Schema/description coherence: the actual public root schema and reply
# branch must agree with the manual/header — none of them may say the only
# valid reply id source is read/search, and none may mandate a read/check/
# search ceremony ahead of acting on a current notification.
# ---------------------------------------------------------------------------

def _assert_schema_text_allows_notification_id(text: str) -> None:
    lowered = text.lower()
    assert "current notification" in lowered
    assert "do not call" in lowered or "should not" in lowered


def test_root_description_and_action_schema_agree_on_no_reread() -> None:
    # DESCRIPTION and SCHEMA (== _family.TELEGRAM_SCHEMA) are the two fields
    # actually registered with the MCP client (server.py: description=,
    # input_schema=); both must state the current-notification id source.
    _assert_schema_text_allows_notification_id(DESCRIPTION)
    action_description = SCHEMA["properties"]["action"]["description"]
    _assert_schema_text_allows_notification_id(action_description)
    assert SCHEMA is tg_family.TELEGRAM_SCHEMA


def test_reply_branch_schema_names_notification_id_source() -> None:
    reply_branch = None
    for branch in SCHEMA["properties"]["input"]["anyOf"]:
        if branch.get("description", "").lower().startswith("reply to one message"):
            reply_branch = branch
            break
    assert reply_branch is not None, "reply branch not found in root schema"
    lowered = reply_branch["description"].lower()
    assert "current notification" in lowered
    message_id_description = reply_branch["properties"]["message_id"]["description"]
    assert "current notification" in message_id_description.lower()


# ---------------------------------------------------------------------------
# Behavior: a complete short message can be replied to directly using the id
# the persistent record already carries — no `read` call in between.
# ---------------------------------------------------------------------------

def test_reply_from_notification_id_succeeds_without_prior_read(tmp_path: Path) -> None:
    workdir = tmp_path / "agent"
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=inbound_events.append,
        notification_store=notification_store_for(workdir),
    )

    manager.on_incoming(
        "main",
        {
            "message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "short and complete",
            }
        },
    )

    latest = inbound_events[0]["metadata"]["latest_incoming"]
    assert latest["text_truncated"] is False
    compound_id = latest["id"]

    result = manager.handle(
        {"action": "reply", "message_id": compound_id, "text": "handled"}
    )

    assert result["status"] == "sent"
    assert compound_id in json.loads(
        (workdir / "telegram" / "main" / "read.json").read_text()
    )
    assert not (workdir / ".notification" / "mcp.telegram.json").exists()


def test_truncated_current_message_flags_recovery_and_read_returns_full_text(
    tmp_path: Path,
) -> None:
    workdir = tmp_path / "agent"
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=inbound_events.append,
        notification_store=notification_store_for(workdir),
    )

    long_text = "x" * 900
    manager.on_incoming(
        "main",
        {
            "message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": long_text,
            }
        },
    )

    latest = inbound_events[0]["metadata"]["latest_incoming"]
    assert latest["text_truncated"] is True
    assert len(latest["text"]) < len(long_text)

    read_result = manager.handle(
        {"action": "read", "account": "main", "chat_id": 123, "limit": 1}
    )
    assert read_result["status"] == "ok"
    assert read_result["messages"][0]["text"] == long_text


def test_ellipsis_in_text_alone_does_not_flag_truncation(tmp_path: Path) -> None:
    """A natural user ellipsis is not truncation evidence (no heuristic reread)."""
    workdir = tmp_path / "agent"
    inbound_events: list[dict[str, Any]] = []
    manager = TelegramManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=inbound_events.append,
        notification_store=notification_store_for(workdir),
    )

    manager.on_incoming(
        "main",
        {
            "message": {
                "message_id": 53,
                "date": 1781600000,
                "from": {"id": 1, "username": "alice"},
                "chat": {"id": 123, "type": "private"},
                "text": "well...",
            }
        },
    )

    latest = inbound_events[0]["metadata"]["latest_incoming"]
    assert latest["text"] == "well..."
    assert latest["text_truncated"] is False
