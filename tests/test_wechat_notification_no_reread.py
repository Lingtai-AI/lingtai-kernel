"""WeChat notification no-reread contract.

A CURRENT WeChat notification (the LICC event body plus its structured
``recent_messages``/``latest_incoming`` metadata, and the durable
``_meta.agent_meta.notifications.persistent.mcp.wechat.messages`` lane the
kernel builds from it) already carries the full display text of every
message in its bounded window, including inline local media/file/voice
paths — unless a message is flagged ``text_truncated``. The agent must not
call ``wechat.read`` merely to reread that identical content or to
reacquire a ``message_id`` already present in the notification; ``read`` is
for actually-missing content (``text_truncated``) or explicit
refresh/recovery reconciliation, not a routine read-before-reply ceremony.
This module pins that contract at the places it is expressed: the outbound
header guidance text, the schema/manual descriptions that name where exact
IDs may come from, and ``reply`` accepting a ``message_id`` sourced straight
from the structured preview without any prior ``read`` call.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from lingtai.mcp_servers.wechat import _family
from lingtai.mcp_servers.wechat.manager import (
    DESCRIPTION,
    WechatManager,
    _NOTIFICATION_HEADER_TEMPLATE,
    _STRUCTURED_MESSAGE_TEXT_CAP,
)
from lingtai.mcp_servers.wechat.types import (
    MessageItem, MessageItemType, TextItem, WeixinMessage,
)

_SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "lingtai" / "mcp_servers" / "wechat" / "SKILL.md"
)
_OPERATIONS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "lingtai" / "mcp_servers" / "wechat" / "reference" / "operations.md"
)


def _manager(tmp_path: Path) -> WechatManager:
    return WechatManager(
        token="test-token",
        user_id="test-bot",
        working_dir=tmp_path,
        on_inbound=lambda _event: None,
    )


def _text_msg(*, from_user: str, text: str, message_id: int) -> WeixinMessage:
    return WeixinMessage(
        message_id=message_id,
        from_user_id=from_user,
        message_type=1,
        item_list=[
            MessageItem(type=MessageItemType.TEXT, text_item=TextItem(text=text)),
        ],
    )


def test_header_names_persistent_path_and_truncation_escape_hatch():
    """The header must point at the durable lane, state the no-reread rule in
    unambiguous SHOULD NOT terms (not a soft "may"), and say when a reread
    (``wechat.read``) is actually required, not merely permitted."""
    header = _NOTIFICATION_HEADER_TEMPLATE
    assert (
        "_meta.agent_meta.notifications.persistent.mcp.wechat.messages" in header
    )
    assert "text_truncated" in header
    assert "wechat.read" in header
    # The rule must be an unambiguous prohibition, not the rejected soft "may
    # reply directly ... without an extra read" phrasing.
    assert "SHOULD NOT" in header
    assert "may reply directly" not in header
    # A refresh/worker-recovery lifecycle event alone must not be documented
    # as an unconditional trigger to reread a complete current message.
    assert "reason to reread a complete current message" in header


def test_header_documents_inline_media_paths_not_placeholders_only():
    """Media/file/voice content is already downloaded to a local path inline
    in the landed text, so the header must not send the agent back to
    ``read`` merely because a message is media-bearing."""
    header = _NOTIFICATION_HEADER_TEMPLATE
    assert "media" in header.lower()
    assert "placeholder" in header.lower()


def test_reply_succeeds_with_message_id_from_structured_preview_without_read(
    tmp_path: Path, monkeypatch,
):
    """Regression guard: ``reply`` must accept the exact ``id`` a structured
    ``latest_incoming`` entry already carries, without any prior ``read``
    call landing or refreshing that id. If ``reply`` starts depending on
    ``_handle_read`` having run first, this fails."""
    mgr = _manager(tmp_path)
    user = "wxid_alice@im.wechat"
    asyncio.run(
        mgr._on_incoming(_text_msg(from_user=user, text="hi there", message_id=1))
    )

    _body, metadata = mgr._build_conversation_preview_and_metadata(
        user, current_message_id="",
    )
    latest_incoming = metadata["latest_incoming"]
    assert latest_incoming["text_truncated"] is False
    message_id = latest_incoming["id"]

    # No _handle_read call anywhere above: the id came only from the
    # structured preview, exactly as a current notification would deliver it.
    # _handle_send is mocked so this test never reaches a real transport.
    monkeypatch.setattr(
        mgr,
        "_handle_send",
        lambda args: {"status": "ok", "sent": ["text"], "message_id": "sent-1"},
    )
    result = mgr._handle_reply({"message_id": message_id, "text": "got it"})
    assert result.get("status") == "ok"


def test_structured_message_truncation_boundary_marks_when_read_is_needed(
    tmp_path: Path,
):
    """Exactly-at-cap text is not truncated; one character over is. This is
    the boundary the header's escape hatch relies on to decide whether a
    reread is actually required."""
    mgr = _manager(tmp_path)
    user = "wxid_bob@im.wechat"

    at_cap = "x" * _STRUCTURED_MESSAGE_TEXT_CAP
    msg_dir = mgr._inbox_dir / "at-cap"
    msg_dir.mkdir(parents=True, exist_ok=True)
    (msg_dir / "message.json").write_text(
        json.dumps({
            "id": "at-cap", "from_user_id": user, "body": at_cap,
            "date": "2026-07-06T01:00:00+00:00",
        }),
        encoding="utf-8",
    )

    over_cap = "x" * (_STRUCTURED_MESSAGE_TEXT_CAP + 1)
    msg_dir2 = mgr._inbox_dir / "over-cap"
    msg_dir2.mkdir(parents=True, exist_ok=True)
    (msg_dir2 / "message.json").write_text(
        json.dumps({
            "id": "over-cap", "from_user_id": user, "body": over_cap,
            "date": "2026-07-06T01:01:00+00:00",
        }),
        encoding="utf-8",
    )

    _body, metadata = mgr._build_conversation_preview_and_metadata(
        user, current_message_id="over-cap",
    )
    recent = {m["id"]: m for m in metadata["recent_messages"]}
    assert recent["at-cap"]["text_truncated"] is False
    assert recent["over-cap"]["text_truncated"] is True


def test_schema_action_description_broadens_id_sources_without_reread():
    """The wire-visible action description must not lock the agent into
    ``check/read first`` for every call, and must name the current
    notification as a valid exact-id source alongside check/read/search."""
    schema = _family.wechat_schema()
    action_description = schema["properties"]["action"]["description"]
    assert "reread" in action_description
    assert "current notification" in action_description.lower()
    assert "check/read first" not in action_description
    assert "never to guess" in action_description or "never guess" in action_description


def test_reply_and_user_id_field_descriptions_accept_notification_sourced_ids():
    """Per-field schema descriptions must say a current notification's exact
    ``id`` is a valid ``reply``/``user_id`` input source, not only ``read``."""
    schemas = _family._wechat_input_schemas()
    reply_message_id = schemas["reply"]["properties"]["message_id"]["description"]
    assert "current notification" in reply_message_id
    assert reply_message_id != "Exact inbound message_id returned by read."

    send_user_id = schemas["send"]["properties"]["user_id"]["description"]
    assert "current notification" in send_user_id


def test_public_description_does_not_mandate_check_read_search_first():
    """The top-level tool ``DESCRIPTION`` (server tool listing) must not tell
    the agent to always call check/read/search before acting; a complete
    current notification is sufficient on its own."""
    assert "check/read/search first" not in DESCRIPTION
    assert "current notification" in DESCRIPTION or "CURRENT notification" in DESCRIPTION


def test_skill_and_operations_docs_state_should_not_reread_and_broaden_ids():
    """``SKILL.md`` and ``reference/operations.md`` must state the no-reread
    rule and name the current notification as a valid id source, matching
    the header and schema; a mere lifecycle refresh/recovery event must not
    be documented as an unconditional trigger to read before replying."""
    skill = _SKILL_PATH.read_text(encoding="utf-8")
    operations = _OPERATIONS_PATH.read_text(encoding="utf-8")

    assert "current notification" in skill
    assert "never guess" in skill or "never invent" in skill

    assert "current notification" in operations
    assert "After refresh, worker failure, or recovery, read the merged history before" not in operations
