"""Telegram's independent LTP-v2 tool family.

This module owns only the public Telegram envelope and action branches.  The
manager remains the legacy result/business boundary behind the validated family.
Task Card has a separate family module and registry.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lingtai.tools.tool_family import ChildTool, ToolFamily

from . import updates as tg_updates
from .. import _skill

# Kept local to avoid importing the manager (which consumes this schema).
_SKILL_NAME = "telegram-mcp-manual"
_SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH = _skill.load_skill(
    "lingtai.mcp_servers.telegram"
)

_ACTIONS = (
    "send", "check", "read", "reply", "search", "delete", "edit",
    "contacts", "add_contact", "remove_contact", "accounts", "manual",
)

_RENDERING_MODES = ("plain_text", "HTML", "MarkdownV2", "Markdown", "entities", "rich")


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    one_of: list[dict[str, Any]] | None = None,
    any_of: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    if one_of:
        result["oneOf"] = one_of
    if any_of:
        result["anyOf"] = any_of
    return result


def _chat_id(*, updates: bool = False) -> dict[str, Any]:
    choices: list[dict[str, Any]] = [{"type": "integer"}]
    if updates:
        choices.append({
            "type": "string",
            "enum": [tg_updates.SYNTHETIC_EVENTS_CHAT_ID],
        })
    return {"anyOf": choices}


def _telegram_input_schemas() -> dict[str, dict[str, Any]]:
    media = _object(
        {
            "type": {"type": "string", "enum": ["photo", "document"]},
            "path": {"type": "string"},
        },
        required=["type", "path"],
    )
    fact = _object(
        {"label": {"type": "string"}, "value": {"type": "string"}},
        required=["label", "value"],
    )
    structured_message = _object(
        {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "facts": {"type": "array", "items": fact},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "steps": {"type": "array", "items": {"type": "string"}},
            "code": _object(
                {"text": {"type": "string"}, "language": {"type": "string"}},
                required=["text"],
            ),
            "next": _object(
                {"label": {"type": "string"}, "text": {"type": "string"}},
                required=["label", "text"],
            ),
            "footer": {"type": "string"},
        },
        required=["title"],
    )
    send = _object(
        {
            "account": _nullable({"type": "string"}),
            "chat_id": _chat_id(),
            "text": {"type": "string"},
            "media": _nullable(media),
            "reply_markup": _nullable({"type": "object"}),
            "placeholder": _nullable({"type": "boolean"}),
            "chat_action": _nullable({
                "type": "string",
                "enum": ["typing", "upload_photo", "upload_document", "upload_voice", ""],
            }),
            "rendering_mode": {
                "type": "string", "enum": list(_RENDERING_MODES),
            },
            "entities": _nullable({"type": "array"}),
            "caption_entities": _nullable({"type": "array"}),
            "structured_message": _nullable({
                **structured_message,
                "description": (
                    "System-generated semantic content sent as native Telegram Rich "
                    "Message blocks. Agent-authored emoji are preserved wherever they "
                    "improve scanning; use rendering_mode='rich' and omit text/media."
                ),
            }),
            "link_preview_options": _nullable({"type": "object"}),
            "disable_web_page_preview": _nullable({"type": "boolean"}),
        },
        required=["chat_id", "rendering_mode"],
        any_of=[
            {"required": ["text"]},
            {"required": ["media"]},
            {"required": ["chat_action"]},
            {"required": ["structured_message"]},
        ],
    )
    send["properties"]["media"]["anyOf"][0]["description"] = (
        "Media attachment: {type: 'photo'|'document', path: '/path/to/file'}. "
        "For charts, HTML/SVG/PNG reports, CSVs, PDFs, and other generated artifacts "
        "that should arrive as an intact file, use type='document'. Use type='photo' "
        "only for native inline photo previews; Telegram photo delivery can crop, "
        "compress, thumbnail, or display text-heavy charts poorly. Do not paste local "
        "file paths in message text as a substitute for attaching the file."
    )
    send["properties"]["rendering_mode"]["description"] = (
        "Required rendering choice for every send. Choose plain_text for unformatted "
        "text or chat actions, HTML/Markdown/MarkdownV2 for Telegram parse_mode, "
        "entities when supplying MessageEntity data, or rich for a native structured "
        "message. There is no default; do not combine the modes."
    )
    send["properties"]["entities"]["anyOf"][0]["description"] = (
        "Telegram MessageEntity[] for rendering_mode='entities' on message text."
    )
    send["properties"]["caption_entities"]["anyOf"][0]["description"] = (
        "Telegram MessageEntity[] for rendering_mode='entities' on media captions."
    )
    send["properties"]["chat_action"]["anyOf"][0]["description"] = (
        "For send only. When set and no text/media is provided, sends a chat action "
        "indicator instead of a message. Auto-expires after 5 seconds; omit or pass "
        "an empty string for no chat action."
    )
    empty = _object({})
    return {
        "send": send,
        "check": _object({"account": _nullable({"type": "string"})}),
        "read": _object(
            {
                "account": {"type": "string"},
                "chat_id": _chat_id(updates=True),
                "limit": _nullable({"type": "integer"}),
            },
            required=["chat_id"],
        ),
        "reply": _object(
            {
                "message_id": {"type": "string"},
                "text": {"type": "string"},
                "rendering_mode": {
                    "type": "string", "enum": list(_RENDERING_MODES),
                    "description": (
                        "Required rendering choice: plain_text, HTML, Markdown, "
                        "MarkdownV2, entities, or rich. There is no default."
                    ),
                },
                "entities": _nullable({"type": "array"}),
                "structured_message": _nullable(structured_message),
            },
            required=["message_id", "rendering_mode"],
            any_of=[{"required": ["text"]}, {"required": ["structured_message"]}],
        ),
        "search": _object(
            {
                "query": {"type": "string"},
                "account": _nullable({"type": "string"}),
                "chat_id": _nullable(_chat_id(updates=True)),
            },
            required=["query"],
        ),
        "delete": _object({"message_id": {"type": "string"}}, required=["message_id"]),
        "edit": _object(
            {
                "message_id": {"type": "string"},
                "text": {"type": "string"},
                "reply_markup": _nullable({"type": "object"}),
                "rendering_mode": {
                    "type": "string", "enum": list(_RENDERING_MODES),
                    "description": (
                        "Required rendering choice: plain_text, HTML, Markdown, "
                        "MarkdownV2, entities, or rich. There is no default."
                    ),
                },
                "entities": _nullable({"type": "array"}),
                "structured_message": _nullable(structured_message),
            },
            required=["message_id", "rendering_mode"],
            any_of=[{"required": ["text"]}, {"required": ["structured_message"]}],
        ),
        "contacts": _object({"account": _nullable({"type": "string"})}),
        "add_contact": _object(
            {
                "account": _nullable({"type": "string"}),
                "chat_id": {"type": "integer"},
                "alias": {"type": "string"},
            },
            required=["chat_id", "alias"],
        ),
        "remove_contact": _object(
            {
                "account": _nullable({"type": "string"}),
                "chat_id": {"type": "integer"},
                "alias": {"type": "string"},
            },
            one_of=[{"required": ["alias"]}, {"required": ["chat_id"]}],
        ),
        "accounts": empty,
        "manual": empty,
    }


def _schema_only_family() -> ToolFamily:
    schemas = _telegram_input_schemas()
    return ToolFamily(
        "telegram",
        [
            ChildTool(action, schemas[action], lambda _input: {})
            for action in _ACTIONS
        ],
    )


_SCHEMA_FAMILY = _schema_only_family()


def telegram_schema() -> dict[str, Any]:
    schema = _SCHEMA_FAMILY.build_schema()
    # Telegram has intentionally overlapping optional fields (for example a
    # read with chat_id and a remove_contact with chat_id). The root allOf
    # discriminator still correlates each action to its exact closed branch;
    # use anyOf for the model-discovery list so native JSON-Schema validators do
    # not reject a valid input merely because another action's branch also fits.
    schema["properties"]["input"]["anyOf"] = schema["properties"]["input"].pop("oneOf")
    schema["properties"]["action"]["description"] = (
        "Telegram action. Each action owns a strict input branch. Content-bearing "
        "send/reply/edit calls must provide rendering_mode explicitly: plain_text, "
        "HTML, Markdown, MarkdownV2, entities, or rich; there is no default. For charts, "
        "reports, generated artifacts, and other files the user should open intact, "
        "prefer media.type='document'; use media.type='photo' only for an inline "
        "preview because photo previews may crop or compress text-heavy graphics. "
        "Call manual "
        + _skill.manual_action_description(_SKILL_FRONTMATTER, _SKILL_NAME)
    )
    return schema


def _basic_validate(value: Any, schema: Mapping[str, Any]) -> bool:
    """Small dependency-free validator for the dispatch safety boundary.

    JSON-Schema combinators compose with sibling constraints.  Validate them
    first without returning early, then validate the schema's own type,
    required fields, properties, and bounds.
    """
    if "anyOf" in schema and not any(
        _basic_validate(value, branch) for branch in schema["anyOf"]
    ):
        return False
    if "oneOf" in schema and sum(
        _basic_validate(value, branch) for branch in schema["oneOf"]
    ) != 1:
        return False
    expected = schema.get("type")
    if expected is None:
        required = schema.get("required")
        if required is None:
            return True
        return isinstance(value, Mapping) and all(key in value for key in required)
    if expected == "object":
        if not isinstance(value, Mapping):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        if not all(
            key not in value or _basic_validate(item, child_schema)
            for key, child_schema in properties.items()
            for item in [value.get(key)]
        ):
            return False
        return True
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str) and value in schema.get("enum", [value])
    if expected == "integer":
        return (
            type(value) is int
            and value in schema.get("enum", [value])
            and value >= schema.get("minimum", value)
            and value <= schema.get("maximum", value)
        )
    if expected == "number":
        return (
            type(value) in (int, float)
            and not isinstance(value, bool)
            and value >= schema.get("minimum", value)
            and value <= schema.get("maximum", value)
        )
    if expected == "boolean":
        return type(value) is bool
    if expected == "null":
        return value is None
    return True


def build_telegram_family(manager: Any | None) -> ToolFamily:
    schemas = _telegram_input_schemas()
    children: list[ChildTool] = []
    for action in _ACTIONS:
        if action == "manual":
            handler = (
                (lambda _input: manager.handle({"action": "manual"}))
                if manager is not None
                else lambda _input: _skill.manual_payload(
                    _SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH, _SKILL_NAME
                )
            )
        else:
            handler = (
                (lambda input_, action=action: manager.handle({"action": action, **dict(input_)}))
                if manager is not None else (lambda _input: {})
            )
        children.append(ChildTool(action, schemas[action], handler))
    return ToolFamily("telegram", children)


def handle_telegram(manager: Any | None, args: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(args or {})
    if set(raw) - {"action", "input", "reasoning", "summarize"}:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "unsupported telegram argument"}
    action = raw.get("action")
    if type(action) is not str or action not in _ACTIONS:
        return {"status": "failed", "error_code": "ACTION_REQUIRED", "message": "invalid telegram action"}
    if "input" not in raw or not isinstance(raw.get("input"), Mapping):
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "input must be an object"}
    if type(raw.get("reasoning")) is not str:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "reasoning is required"}
    if "summarize" in raw and type(raw["summarize"]) is not bool:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "summarize must be a boolean"}
    schema = _telegram_input_schemas()[action]
    if not _basic_validate(raw["input"], schema):
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "invalid telegram input"}
    return build_telegram_family(manager).handle(raw)


TELEGRAM_SCHEMA = telegram_schema()
TELEGRAM_ACTIONS = _ACTIONS
