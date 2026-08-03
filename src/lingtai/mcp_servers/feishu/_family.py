"""Feishu's independent LTP-v2 tool family.

This module owns only the public Feishu envelope and action branches. The
manager remains the legacy result/business boundary behind the validated
family, matching the Telegram MCP's split (``../telegram/_family.py``).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lingtai.tools.tool_family import ChildTool, ToolFamily

from .. import _skill
from ._errors import failure_result

# Kept local to avoid importing the manager (which consumes this schema).
_SKILL_NAME = "feishu-mcp-manual"
_SKILL_FRONTMATTER, _SKILL_BODY, _SKILL_PATH = _skill.load_skill(
    "lingtai.mcp_servers.feishu"
)

_ACTIONS = (
    "send", "check", "read", "reply", "react", "search", "delete", "edit",
    "contacts", "add_contact", "remove_contact", "accounts", "manual",
)


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


def _media_source_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _object(
                {
                    "type": {"type": "string", "enum": ["path"]},
                    "path": {"type": "string", "minLength": 1},
                },
                required=["type", "path"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["key"]},
                    "key": {"type": "string", "minLength": 1},
                },
                required=["type", "key"],
            ),
        ]
    }


def _outbound_content_schema(*, include_media: bool = True) -> dict[str, Any]:
    """Strict outbound union; media may be excluded for edit."""
    post_value = {"type": "object", "minProperties": 1}
    card_value = {
        "type": "object",
        "properties": {
            "schema": {"type": "string", "enum": ["2.0"]},
        },
        "required": ["schema"],
        "minProperties": 2,
    }
    branches = [
        _object(
            {
                "type": {"type": "string", "enum": ["text"]},
                "text": {"type": "string", "minLength": 1},
            },
            required=["type", "text"],
        ),
        _object(
            {
                "type": {"type": "string", "enum": ["markdown"]},
                "markdown": {"type": "string", "minLength": 1},
            },
            required=["type", "markdown"],
        ),
        _object(
            {
                "type": {"type": "string", "enum": ["post"]},
                "post": post_value,
            },
            required=["type", "post"],
        ),
        _object(
            {
                "type": {"type": "string", "enum": ["card"]},
                "card": card_value,
            },
            required=["type", "card"],
        ),
    ]
    if include_media:
        source = _media_source_schema()
        branches.extend([
            _object(
                {
                    "type": {"type": "string", "enum": ["image"]},
                    "source": source,
                    "caption": {"type": "string"},
                },
                required=["type", "source"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["file"]},
                    "source": source,
                    "file_name": {"type": "string", "minLength": 1},
                },
                required=["type", "source"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["audio"]},
                    "source": source,
                },
                required=["type", "source"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["video"]},
                    "source": source,
                    "caption": {"type": "string"},
                },
                required=["type", "source"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["share_chat"]},
                    "chat_id": {"type": "string", "minLength": 1},
                },
                required=["type", "chat_id"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["share_user"]},
                    "user_id": {"type": "string", "minLength": 1},
                },
                required=["type", "user_id"],
            ),
            _object(
                {
                    "type": {"type": "string", "enum": ["sticker"]},
                    "file_key": {"type": "string", "minLength": 1},
                },
                required=["type", "file_key"],
            ),
        ])
    return {"oneOf": branches}


def _feishu_input_schemas() -> dict[str, dict[str, Any]]:
    content = _outbound_content_schema()
    editable_content = _outbound_content_schema(include_media=False)
    send = _object(
        {
            "account": _nullable({"type": "string"}),
            "receive_id": {"type": "string"},
            "receive_id_type": _nullable({
                "type": "string",
                "enum": ["open_id", "user_id", "email", "chat_id", "union_id"],
            }),
            "text": {"type": "string"},
            "content": content,
            "placeholder": _nullable({"type": "boolean"}),
        },
        required=["receive_id"],
        one_of=[{"required": ["text"]}, {"required": ["content"]}],
    )
    send["properties"]["receive_id_type"]["anyOf"][0]["description"] = (
        "Type of receive_id. Use 'open_id' for individual users "
        "(format: ou_xxx), 'chat_id' for group chats (format: oc_xxx). "
        "Defaults to 'open_id'."
    )
    send["properties"]["placeholder"]["anyOf"][0]["description"] = (
        "send only — wrap text, markdown, or post content in a native "
        "schema-2.0 progress card and return its compound message_id. "
        "Edit that card only at meaningful phase changes; send the final "
        "answer separately with send or reply."
    )
    empty = _object({})
    return {
        "send": send,
        "check": _object({"account": _nullable({"type": "string"})}),
        "read": _object(
            {
                "account": _nullable({"type": "string"}),
                "chat_id": {"type": "string"},
                "limit": _nullable({"type": "integer"}),
            },
            required=["chat_id"],
        ),
        "reply": _object(
            {
                "message_id": {"type": "string"},
                "text": {"type": "string"},
                "content": content,
                "reply_in_thread": {"type": "boolean"},
            },
            required=["message_id"],
            one_of=[{"required": ["text"]}, {"required": ["content"]}],
        ),
        "react": _object(
            {
                "message_id": {"type": "string", "minLength": 1},
                "operation": {
                    "type": "string", "enum": ["add", "remove"],
                },
                "emoji_type": {"type": "string", "minLength": 1},
                "reaction_id": {"type": "string", "minLength": 1},
            },
            required=["message_id", "operation"],
            one_of=[
                {
                    "properties": {
                        "operation": {"type": "string", "enum": ["add"]},
                    },
                    "required": ["emoji_type"],
                    "not": {"required": ["reaction_id"]},
                },
                {
                    "properties": {
                        "operation": {"type": "string", "enum": ["remove"]},
                    },
                    "required": ["reaction_id"],
                    "not": {"required": ["emoji_type"]},
                },
            ],
        ),
        "search": _object(
            {
                "query": {"type": "string"},
                "account": _nullable({"type": "string"}),
                "chat_id": _nullable({"type": "string"}),
            },
            required=["query"],
        ),
        "delete": _object({
            "message_id": {
                "type": "string",
                "description": (
                    "Any compound ID returned for a logical bot message; "
                    "all persisted physical chunks are deleted."
                ),
            },
        }, required=["message_id"]),
        "edit": _object(
            {
                "message_id": {
                    "type": "string",
                    "description": (
                        "Any compound ID returned for a logical bot message; "
                        "all persisted physical chunks are edited."
                    ),
                },
                "text": {"type": "string"},
                "content": editable_content,
            },
            required=["message_id"],
            one_of=[{"required": ["text"]}, {"required": ["content"]}],
        ),
        "contacts": _object({"account": _nullable({"type": "string"})}),
        "add_contact": _object(
            {
                "account": _nullable({"type": "string"}),
                "open_id": {"type": "string"},
                "alias": {"type": "string"},
                "name": _nullable({"type": "string"}),
                "chat_id": _nullable({"type": "string"}),
            },
            required=["open_id", "alias"],
        ),
        "remove_contact": _object(
            {
                "account": _nullable({"type": "string"}),
                "open_id": {"type": "string"},
                "alias": {"type": "string"},
            },
            one_of=[{"required": ["alias"]}, {"required": ["open_id"]}],
        ),
        "accounts": empty,
        "manual": empty,
    }


def _schema_only_family() -> ToolFamily:
    schemas = _feishu_input_schemas()
    return ToolFamily(
        "feishu",
        [
            ChildTool(action, schemas[action], lambda _input: {})
            for action in _ACTIONS
        ],
    )


_SCHEMA_FAMILY = _schema_only_family()


def feishu_schema() -> dict[str, Any]:
    schema = _SCHEMA_FAMILY.build_schema()
    # check/contacts/accounts/manual all share the empty-object branch, so a
    # strict oneOf here is ambiguous ({} matches more than one branch). The
    # root allOf discriminator still correlates each action to its exact
    # closed branch; use anyOf for the model-discovery list so native
    # JSON-Schema validators do not reject a valid input merely because
    # another action's branch also fits.
    schema["properties"]["input"]["anyOf"] = schema["properties"]["input"].pop("oneOf")
    schema["properties"]["action"]["description"] = (
        "send: send text, markdown, post, card, media, share, or sticker content "
        "to a user or chat "
        "(receive_id, receive_id_type, exactly one of text/content; "
        "optional account, placeholder). "
        "If placeholder is true, sends a native progress card immediately; "
        "edit it only at meaningful phase changes and send the final answer "
        "separately with send or reply. "
        "check: list recent conversations with unread counts "
        "(optional account). "
        "read: read messages from a specific chat "
        "(chat_id; optional limit, account). "
        "reply: reply to a specific message with text, markdown, post, card, "
        "media, share, or sticker content "
        "(message_id from read results, exactly one of text/content; "
        "optional reply_in_thread). "
        "react: add or remove a reaction on a message "
        "(message_id, operation='add' + emoji_type, or "
        "operation='remove' + reaction_id). "
        "search: search inbox messages by regex "
        "(query; optional account, chat_id). "
        "delete: delete every physical chunk of a logical bot message "
        "using any returned compound message_id. "
        "edit: edit every physical chunk of a bot text/post/card message "
        "(message_id, exactly one of text/content). "
        "contacts: list saved contacts (optional account). "
        "add_contact: save a contact "
        "(open_id, alias; optional name, chat_id). "
        "remove_contact: remove a contact (alias or open_id). "
        "accounts: list configured app accounts. "
        + _skill.manual_action_description(_SKILL_FRONTMATTER, _SKILL_NAME)
    )
    return schema


def _basic_validate(value: Any, schema: Mapping[str, Any]) -> bool:
    """Small dependency-free validator for the dispatch safety boundary.

    JSON-Schema combinators compose with sibling constraints. Validate them
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
        if len(value) < schema.get("minProperties", 0):
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
        return (
            isinstance(value, str)
            and value in schema.get("enum", [value])
            and len(value) >= schema.get("minLength", 0)
        )
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


def build_feishu_family(manager: Any | None) -> ToolFamily:
    schemas = _feishu_input_schemas()
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
    return ToolFamily("feishu", children)


def handle_feishu(manager: Any | None, args: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(args or {})
    if set(raw) - {"action", "input", "reasoning", "summarize"}:
        return failure_result(
            "unsupported feishu argument", error_code="INVALID_ARGUMENT",
        )
    action = raw.get("action")
    if type(action) is not str or action not in _ACTIONS:
        return failure_result(
            "invalid feishu action", error_code="ACTION_REQUIRED",
        )
    if "input" not in raw or not isinstance(raw.get("input"), Mapping):
        return failure_result(
            "input must be an object", error_code="INVALID_ARGUMENT",
        )
    if type(raw.get("reasoning")) is not str:
        return failure_result(
            "reasoning is required", error_code="INVALID_ARGUMENT",
        )
    if "summarize" in raw and type(raw["summarize"]) is not bool:
        return failure_result(
            "summarize must be a boolean", error_code="INVALID_ARGUMENT",
        )
    schema = _feishu_input_schemas()[action]
    if not _basic_validate(raw["input"], schema):
        return failure_result(
            "invalid feishu input", error_code="INVALID_ARGUMENT",
        )
    if action == "react":
        input_ = raw["input"]
        operation = input_.get("operation")
        expected = "emoji_type" if operation == "add" else "reaction_id"
        unexpected = "reaction_id" if operation == "add" else "emoji_type"
        if operation not in {"add", "remove"} or not input_.get(expected) or input_.get(
            unexpected
        ):
            return failure_result(
                "invalid feishu input", error_code="INVALID_ARGUMENT",
            )
    if manager is None and action != "manual":
        return failure_result(
            "Feishu manager is not initialized",
            error_code="NOT_CONNECTED",
        )
    return build_feishu_family(manager).handle(raw)


FEISHU_SCHEMA = feishu_schema()
FEISHU_ACTIONS = _ACTIONS
