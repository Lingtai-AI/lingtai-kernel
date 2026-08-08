"""Tests for Telegram MCP rich-formatting pass-through (issue #301).

Proves that the in-kernel Telegram MCP manager forwards Bot API
formatting options (rendering_mode / entities / caption_entities /
link_preview_options / disable_web_page_preview) through to the account
wrapper for send / reply / edit / media-caption paths, and that callers
who supply none of these options get exactly the previous behaviour.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.telegram.account import TelegramAccount
from lingtai.mcp_servers.telegram.manager import SCHEMA, TelegramManager
from tests._notification_store_helpers import FakeNotificationStore


class FakeAccount:
    alias = "mybot"

    def __init__(self) -> None:
        self.calls: list = []

    def send_message(
        self,
        chat_id,
        text,
        reply_markup=None,
        reply_to_message_id=None,
        **kwargs,
    ):
        self.calls.append(
            ("send_message", chat_id, text, reply_markup, reply_to_message_id, kwargs)
        )
        return {"message_id": 111}

    def send_photo(
        self,
        chat_id,
        path,
        caption=None,
        reply_to_message_id=None,
        **kwargs,
    ):
        self.calls.append(
            ("send_photo", chat_id, path, caption, reply_to_message_id, kwargs)
        )
        return {"message_id": 333}

    def send_document(
        self,
        chat_id,
        path,
        caption=None,
        reply_to_message_id=None,
        **kwargs,
    ):
        self.calls.append(
            ("send_document", chat_id, path, caption, reply_to_message_id, kwargs)
        )
        return {"message_id": 222}

    def edit_message(self, **kwargs):
        self.calls.append(("edit_message", kwargs))
        return {"ok": True}

    def set_message_reaction(self, *args, **kwargs):
        # Best-effort reaction call in _send; record nothing meaningful.
        return True


class FakeService:
    def __init__(self) -> None:
        self.default_account = FakeAccount()

    def get_account(self, alias):
        assert alias == "mybot"
        return self.default_account

    def list_accounts(self):
        return ["mybot"]


def _manager(tmp_path):
    service = FakeService()
    manager = TelegramManager(
        service,
        working_dir=Path(tmp_path),
        on_inbound=lambda _: None,
        notification_store=FakeNotificationStore(),
    )
    return manager, service.default_account


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _send_schema():
    return next(
        branch for branch in SCHEMA["properties"]["input"].get("oneOf", SCHEMA["properties"]["input"]["anyOf"])
        if branch.get("title") == "send input"
    )


def _schema_enum(schema):
    if "enum" in schema:
        return schema["enum"]
    return [value for branch in schema.get("anyOf", []) for value in branch.get("enum", [])]


def _schema_description(schema):
    if "description" in schema:
        return schema["description"]
    return " ".join(branch.get("description", "") for branch in schema.get("anyOf", []))


def test_schema_exposes_explicit_rendering_fields():
    props = _send_schema()["properties"]
    for field in ("rendering_mode", "entities", "caption_entities", "link_preview_options", "disable_web_page_preview"):
        assert field in props
    assert _schema_enum(props["rendering_mode"]) == ["plain_text", "HTML", "MarkdownV2", "Markdown", "entities", "rich"]
    assert "no default" in _schema_description(props["rendering_mode"])
    assert "parse_mode" not in props


def test_schema_allows_empty_chat_action():
    props = _send_schema()["properties"]
    assert "" in _schema_enum(props["chat_action"])
    assert "empty string" in _schema_description(props["chat_action"])


def test_schema_guides_generated_charts_to_documents():
    props = _send_schema()["properties"]
    media_description = _schema_description(props["media"])
    action_description = SCHEMA["properties"]["action"]["description"]

    assert "charts" in media_description
    assert "generated artifacts" in media_description
    assert "type='document'" in media_description
    assert "type='photo'" in media_description
    assert "crop" in media_description
    assert "compress" in media_description
    assert "charts" in action_description
    assert "media.type='document'" in action_description


def test_skill_file_exists_with_valid_frontmatter():
    """The manual ships as a standard skill file (SKILL.md) in the Telegram MCP
    package folder, with YAML frontmatter exposing at least name + description."""
    from lingtai.mcp_servers.telegram.manager import (
        _SKILL_BODY,
        _SKILL_FRONTMATTER,
        _SKILL_PATH,
    )

    skill_path = Path(_SKILL_PATH)
    assert skill_path.name == "SKILL.md"
    assert skill_path.is_file()
    # parent folder is the Telegram MCP package
    assert skill_path.parent.name == "telegram"

    assert _SKILL_FRONTMATTER.get("name")
    assert _SKILL_FRONTMATTER.get("description")
    # body is the markdown after the frontmatter fence (no leading '---')
    assert _SKILL_BODY.strip()
    assert not _SKILL_BODY.lstrip().startswith("---")


def test_schema_exposes_manual_action():
    props = SCHEMA["properties"]
    assert "manual" in props["action"]["enum"]
    action_description = SCHEMA["properties"]["action"]["description"]
    assert "manual:" in action_description
    assert "progressive-disclosure" in action_description
    # The frontmatter/catalog entry is injected into the schema: the action
    # description advertises the skill name + its description.
    from lingtai.mcp_servers.telegram.manager import _SKILL_FRONTMATTER

    assert _SKILL_FRONTMATTER["name"] in action_description
    # a distinctive phrase from the skill description survives injection
    assert "media.type='document'" in action_description


def test_manual_action_returns_usage_guidance(tmp_path):
    manager, _account = _manager(tmp_path)
    result = manager.handle({"action": "manual"})

    assert result["status"] == "ok"
    manual = result["manual"]
    assert isinstance(manual, str) and manual.strip()

    # Minimal manual contract: return the main SKILL.md body plus its absolute
    # path and parsed metadata. Concrete asset/reference catalogs stay in the
    # SKILL.md text rather than expanding the tool payload/schema.
    assert set(result) == {"status", "action", "skill", "metadata", "path", "manual"}
    assert result["skill"]
    assert result["metadata"].get("name") == result["skill"]
    assert result["metadata"].get("description")
    skill_path = Path(result["path"])
    assert skill_path.is_absolute()
    assert skill_path.name == "SKILL.md"
    assert skill_path.is_file()
    assert "assets" not in result
    assert "references" not in result

    # the returned body is read from SKILL.md, not a hardcoded string
    from lingtai.mcp_servers.telegram.manager import _SKILL_BODY

    assert manual == _SKILL_BODY

    lowered = manual.lower()
    # document / photo / chart guidance
    assert "document" in lowered
    assert "photo" in lowered
    assert "chart" in lowered
    # progressive disclosure framing
    assert "progressive disclosure" in lowered
    # the other facets the manual should cover
    assert "placeholder" in lowered
    assert "reply" in lowered
    assert "rendering_mode" in manual
    assert "parse_mode" in manual  # Bot API mapping remains documented.
    assert "chat_action" in manual
    assert "error" in lowered


# ---------------------------------------------------------------------------
# Manager pass-through (the acceptance-critical rendering path lives here)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rendering_mode", "expected_options"),
    [("plain_text", {}), ("HTML", {"parse_mode": "HTML"}), ("Markdown", {"parse_mode": "Markdown"}), ("MarkdownV2", {"parse_mode": "MarkdownV2"})],
)
def test_send_maps_explicit_rendering_mode(rendering_mode, expected_options, tmp_path):
    manager, account = _manager(tmp_path)
    link_preview_options = {"is_disabled": True}
    result = manager._send({
        "account": "mybot", "chat_id": 123, "text": "formatted link",
        "rendering_mode": rendering_mode, "link_preview_options": link_preview_options,
        "disable_web_page_preview": True,
    })
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][-1] == {**expected_options, "link_preview_options": link_preview_options, "disable_web_page_preview": True}

def test_reply_persists_reply_to_message_id_in_sent_record(tmp_path):
    manager, account = _manager(tmp_path)

    result = manager._reply({
        "message_id": "mybot:123:456",
        "text": "reply text",
        "rendering_mode": "plain_text",
    })

    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][0] == "send_message"
    assert account.calls[0][1] == 123
    assert account.calls[0][4] == 456

    read_result = manager._read({"account": "mybot", "chat_id": 123, "limit": 1})
    assert read_result["status"] == "ok"
    assert read_result["messages"][0]["id"] == "mybot:123:111"
    assert read_result["messages"][0]["reply_to_message_id"] == 456
    assert read_result["messages"][0]["_direction"] == "outgoing"


def test_send_accepts_public_reply_target_fields(tmp_path):
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "text": "reply text",
        "rendering_mode": "plain_text",
        "message_id": "mybot:123:456",
    })

    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][4] == 456

    read_result = manager._read({"account": "mybot", "chat_id": 123, "limit": 1})
    assert read_result["messages"][0]["reply_to_message_id"] == 456


def test_send_accepts_raw_reply_to_message_id(tmp_path):
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "text": "reply text",
        "rendering_mode": "plain_text",
        "reply_to_message_id": 456,
    })

    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][4] == 456

    read_result = manager._read({"account": "mybot", "chat_id": 123, "limit": 1})
    assert read_result["messages"][0]["reply_to_message_id"] == 456


def test_send_reply_target_precedence_prefers_private_field(tmp_path):
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "text": "reply text",
        "rendering_mode": "plain_text",
        "_reply_to_message_id": 111,
        "reply_to_message_id": 222,
        "message_id": "mybot:123:333",
    })

    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][4] == 111

    read_result = manager._read({"account": "mybot", "chat_id": 123, "limit": 1})
    assert read_result["messages"][0]["reply_to_message_id"] == 111


def test_internal_send_without_rendering_mode_keeps_plain_fallback(tmp_path):
    """Manager-owned internal sends retain plain-text compatibility."""
    manager, account = _manager(tmp_path)

    result = manager._send({
        "account": "mybot",
        "chat_id": 123,
        "text": "plain",
    })

    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls == [(
        "send_message",
        123,
        "plain",
        None,
        None,
        {},  # no rich-text options leaked in
    )]


def test_explicit_plain_text_omits_bot_parse_mode(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "plain", "rendering_mode": "plain_text"})
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls == [("send_message", 123, "plain", None, None, {})]
    sent_files = list((Path(tmp_path) / "telegram" / "mybot" / "sent").glob("*/message.json"))
    record = json.loads(sent_files[0].read_text())
    assert record["rendering_mode"] == "plain_text"
    assert record["parse_mode"] is None

def test_explicit_plain_text_with_empty_chat_action_sends_text(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "plain", "rendering_mode": "plain_text", "chat_action": ""})
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls == [("send_message", 123, "plain", None, None, {})]

def test_plain_text_reply_is_forwarded_without_bot_parse_mode(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._reply({"message_id": "mybot:123:456", "text": "plain reply", "rendering_mode": "plain_text"})
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls == [("send_message", 123, "plain reply", None, 456, {})]

def test_reply_passes_rich_formatting_options(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._reply({"message_id": "mybot:123:456", "text": "x", "rendering_mode": "MarkdownV2"})
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls == [("send_message", 123, "x", None, 456, {"parse_mode": "MarkdownV2"})]

def test_reply_entities_mode_passes_entities(tmp_path):
    manager, account = _manager(tmp_path)
    entities = [{"type": "code", "offset": 0, "length": 1}]
    result = manager._reply({"message_id": "mybot:123:456", "text": "x", "rendering_mode": "entities", "entities": entities})
    assert result == {"status": "sent", "message_id": "mybot:123:111"}
    assert account.calls[0][-1] == {"entities": entities}


def test_send_media_passes_caption_entities(tmp_path):
    media_path = tmp_path / "demo.txt"
    media_path.write_text("demo", encoding="utf-8")
    manager, account = _manager(tmp_path)
    caption_entities = [{"type": "italic", "offset": 0, "length": 4}]
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "demo", "media": {"type": "document", "path": str(media_path)}, "rendering_mode": "entities", "caption_entities": caption_entities})
    assert result == {"status": "sent", "message_id": "mybot:123:222"}
    assert account.calls == [("send_document", 123, str(media_path), "demo", None, {"caption_entities": caption_entities})]

def test_edit_text_passes_rendering_mode(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._edit({"message_id": "mybot:123:456", "text": "<b>x</b>", "rendering_mode": "HTML"})
    assert result == {"status": "edited", "message_id": "mybot:123:456"}
    assert account.calls == [("edit_message", {"chat_id": 123, "message_id": 456, "text": "<b>x</b>", "reply_markup": None, "is_caption": False, "parse_mode": "HTML"})]

def test_invalid_rendering_mode_is_rejected(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "hello", "rendering_mode": "BBCode"})
    assert result == {"error": "rendering_mode must be one of: plain_text, HTML, MarkdownV2, Markdown, entities, rich"}
    assert account.calls == []

@pytest.mark.parametrize("rendering_mode", ["plain_text", "HTML", "Markdown", "MarkdownV2"])
def test_entity_data_is_rejected_for_parse_mode_rendering(rendering_mode, tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "hello", "rendering_mode": rendering_mode, "entities": [{"type": "bold", "offset": 0, "length": 5}]})
    assert result == {"error": f"rendering_mode='{rendering_mode}' cannot be combined with entities; choose rendering_mode='entities'"}
    assert account.calls == []


def test_entities_rendering_requires_entity_data(tmp_path):
    manager, account = _manager(tmp_path)
    result = manager._send({"account": "mybot", "chat_id": 123, "text": "hello", "rendering_mode": "entities"})
    assert result == {"error": "rendering_mode='entities' requires entities or caption_entities"}
    assert account.calls == []

class CaptureAccount(TelegramAccount):
    def __init__(self):
        # Bypass TelegramAccount.__init__ — we only exercise payload building.
        pass

    def _request(self, method, **kwargs):
        return {"method": method, "kwargs": kwargs, "message_id": 99}


def test_account_send_message_payload_includes_rich_options():
    acct = CaptureAccount()
    entities = [{"type": "bold", "offset": 0, "length": 4}]
    result = acct.send_message(
        123,
        "bold",
        parse_mode="HTML",
        entities=entities,
        link_preview_options={"is_disabled": True},
        disable_web_page_preview=False,
    )

    assert result["method"] == "sendMessage"
    payload = result["kwargs"]["json"]
    assert payload["parse_mode"] == "HTML"
    assert payload["entities"] == entities
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert payload["disable_web_page_preview"] is False


def test_account_send_message_payload_plain_has_no_rich_options():
    """Backward compatibility at the wrapper layer."""
    acct = CaptureAccount()
    result = acct.send_message(123, "plain")

    payload = result["kwargs"]["json"]
    assert payload == {"chat_id": 123, "text": "plain"}


def test_account_send_document_serializes_caption_entities(tmp_path):
    media_path = tmp_path / "demo.txt"
    media_path.write_text("demo", encoding="utf-8")
    acct = CaptureAccount()
    caption_entities = [{"type": "code", "offset": 0, "length": 4}]

    result = acct.send_document(
        123,
        str(media_path),
        caption="demo",
        parse_mode="HTML",
        caption_entities=caption_entities,
    )

    assert result["method"] == "sendDocument"
    data = result["kwargs"]["data"]
    assert data["parse_mode"] == "HTML"
    # Multipart fields must be strings: caption_entities is JSON-serialized.
    assert json.loads(data["caption_entities"]) == caption_entities


def test_account_edit_caption_payload_includes_parse_mode():
    acct = CaptureAccount()
    result = acct.edit_message(
        123, 456, "new caption", is_caption=True, parse_mode="MarkdownV2",
    )

    assert result["method"] == "editMessageCaption"
    payload = result["kwargs"]["json"]
    assert payload["caption"] == "new caption"
    assert payload["parse_mode"] == "MarkdownV2"
