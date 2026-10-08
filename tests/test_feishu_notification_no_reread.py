"""Regression tests: the Feishu producer must not force a routine reread.

Locks that the notification header, message-semantics manual, live tool
schema, and top-level SKILL.md all tell the agent an ordinary current
incoming message's exact text and compound id are already delivered in the
persistent notification lane (the transient attention lane is ID-only), so
`feishu.read`/`feishu.check`/`feishu.search` are only for recovering content
actually truncated, omitted, or otherwise missing (attachments, card
callbacks, older history) — never a routine reread of content already shown.
Mirrors the equivalent Telegram guidance already shipped in
``lingtai.mcp_servers.telegram.notification_header.md``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.kernel.meta_block import sanitize_feishu_notification_after_persistent
from lingtai.mcp_servers.feishu._family import feishu_schema
from lingtai.mcp_servers.feishu.manager import (
    _STRUCTURED_MESSAGE_TEXT_CAP,
    FeishuManager,
)
from lingtai.mcp_servers.feishu import manager as feishu_manager_mod

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FEISHU_PKG = _REPO_ROOT / "src" / "lingtai" / "mcp_servers" / "feishu"


class _FakeService:
    def get_account(self, alias: str) -> Any:
        raise KeyError(alias)


def _manager(workdir: Path, events: list[dict] | None = None) -> FeishuManager:
    sink = events if events is not None else []
    return FeishuManager(
        _FakeService(),
        working_dir=workdir,
        on_inbound=sink.append,
    )


def _write_message(
    workdir: Path,
    *,
    account: str = "main",
    folder: str = "inbox",
    chat_id: str = "oc_chat",
    msg_id: str,
    text: str,
    date: str,
) -> str:
    compound_id = f"{account}:{chat_id}:{msg_id}"
    msg_dir = workdir / "feishu" / account / folder / f"uuid-{msg_id}"
    msg_dir.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "id": compound_id,
        "feishu_message_id": msg_id,
        "chat_id": chat_id,
        "chat_type": "p2p",
        "thread_id": None,
        "message_type": "text",
        "from_open_id": "ou_jason",
        "text": text,
        "parent_id": "",
        "mentions": [],
        "media": None,
        "voice_transcript": None,
        "date": date,
    }
    (msg_dir / "message.json").write_text(json.dumps(payload), encoding="utf-8")
    return compound_id


def test_notification_header_declares_full_content_and_no_routine_reread():
    template = feishu_manager_mod._NOTIFICATION_HEADER_TEMPLATE
    assert (
        "Do not call `feishu.read`, `feishu.check`, or `feishu.search` merely "
        "to reread" in template
    )
    assert "_meta.agent_meta.notifications.persistent.mcp.feishu.messages" in template
    assert "actually marked truncated" in template


def test_current_message_full_text_present_in_body_even_when_structured_capped(
    tmp_path: Path,
):
    """A structured `text_truncated=True` is a 500-char structured-field cap,
    not proof the current message is missing from the notification body: the
    markdown conversation preview carries the message's exact text unbounded
    per message (only the whole body has an overall 10,000-char budget)."""
    mgr = _manager(tmp_path)
    long_text = "y" * (_STRUCTURED_MESSAGE_TEXT_CAP + 50)
    current = _write_message(
        tmp_path, msg_id="om_1", text=long_text, date="2026-07-06T09:00:01Z"
    )

    preview, metadata = mgr._build_conversation_preview_and_metadata(
        "main", "oc_chat", current
    )

    structured = metadata["recent_messages"][0]
    assert structured["text_truncated"] is True
    assert len(structured["text"]) == _STRUCTURED_MESSAGE_TEXT_CAP
    # The raw conversation-preview body still carries the message in full.
    assert long_text in preview


def test_on_incoming_body_carries_no_reread_guidance(tmp_path: Path):
    events: list[dict] = []
    mgr = _manager(tmp_path, events)
    _write_message(tmp_path, msg_id="om_1", text="hi", date="2026-07-06T09:00:01Z")

    from types import SimpleNamespace

    mgr.on_incoming(
        "main",
        SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    message_id="om_new",
                    chat_id="oc_chat",
                    chat_type="p2p",
                    message_type="text",
                    content=json.dumps({"text": "hello again"}),
                    create_time="",
                    parent_id="",
                ),
                sender=SimpleNamespace(
                    sender_id=SimpleNamespace(open_id="ou_jason"),
                ),
            ),
        ),
    )

    assert len(events) == 1
    body = events[0]["body"]
    assert "Do not call `feishu.read`" in body
    assert "main:oc_chat:om_new" in body
    assert "hello again" in body


def test_skill_md_does_not_mandate_read_ceremony_after_a_notification():
    text = (_FEISHU_PKG / "SKILL.md").read_text(encoding="utf-8")
    assert "without a pending notification" in text
    assert "reply or react with that id directly instead of a routine" in text


def test_message_semantics_clarifies_recovery_vs_reread():
    text = (_FEISHU_PKG / "reference" / "message-semantics.md").read_text(
        encoding="utf-8"
    )
    assert (
        "SHOULD NOT call `read`, `check`, or `search` merely to reread" in text
    )
    assert "is a separate purpose from rereading the current" in text


def test_message_semantics_attention_lane_is_id_only_not_body():
    """Fix: the attention lane never carries message text; only the
    persistent lane does. A prior draft claimed both lanes carry the body,
    contradicting the attention lane's own ID-only definition."""
    text = (_FEISHU_PKG / "reference" / "message-semantics.md").read_text(
        encoding="utf-8"
    )
    assert "never carries message text" in text
    assert "Both lanes already" not in text


def test_sanitized_attention_lane_is_id_only_matches_docs():
    """Locks the actual kernel behavior the docs above describe: after
    persistent-lane construction, the feishu attention channel is reduced to
    message ids only, with no text/content fields."""
    payload = {
        "notifications": {
            "mcp.feishu": {
                "header": "stale header",
                "data": {
                    "from": "Jason",
                    "body": "some full message text",
                    "recent_messages": [{"text": "some full message text"}],
                },
                "instructions": "stale instructions",
            }
        }
    }
    sanitize_feishu_notification_after_persistent(payload)
    channel = payload["notifications"]["mcp.feishu"]
    assert channel["data"] == {"message_ids": []}
    assert "notification_persistent" in channel["instructions"]
    assert "some full message text" not in json.dumps(channel)


def test_live_schema_action_description_mentions_full_notification_no_reread():
    """Locks that the advertised tool schema (not just the manual docs) tells
    the model a full current notification already carries reply content/ids,
    so read/check/search are for recovering genuinely missing content."""
    schema = feishu_schema()
    description = schema["properties"]["action"]["description"]
    assert "full current notification" in description
    assert "not to reread it" in description


def test_live_schema_reply_message_id_describes_exact_id_origin():
    schema = feishu_schema()
    reply_branches = schema["properties"]["input"]["anyOf"]
    reply_branch = next(
        branch
        for branch in reply_branches
        if "message_id" in branch.get("properties", {})
        and "reply_in_thread" in branch.get("properties", {})
    )
    message_id_schema = reply_branch["properties"]["message_id"]
    assert "current notification" in message_id_schema["description"]
    assert "never guessed" in message_id_schema["description"]
