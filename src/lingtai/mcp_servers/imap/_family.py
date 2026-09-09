"""IMAP's independent LTP-v2 tool family.

This module owns only the public IMAP envelope and action branches. The
manager remains the legacy result/business boundary behind the validated
family — mirrors ``telegram/_family.py``.

Action *composition* belongs to the package's plugin descriptor (`plugin.py`):
this module declares IMAP's own actions and their strict `input` branches, and
`IMAP_PLUGIN` inserts the reserved read-only `settings` action immediately
before the packaged `manual`. Neither reserved action routes through the
business manager.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lingtai.tools.tool_family import ChildTool, ToolFamily

from .plugin import IMAP_ACTIONS, IMAP_DECLARED_ACTIONS, IMAP_PLUGIN
from .settings import imap_setting_rows

# The package's own actions plus plugin-composed ``settings`` and ``manual``.
# Kept local to avoid importing the manager (which consumes this schema).
_DECLARED_ACTIONS = IMAP_DECLARED_ACTIONS
_ACTIONS = IMAP_ACTIONS


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def _address_list() -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ],
    }


def _email_id_list() -> dict[str, Any]:
    return {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
        ],
        "description": "Returned compound account:folder:uid ID(s); reply uses the first.",
    }


def _account_field() -> dict[str, Any]:
    return _nullable({
        "type": "string",
        "description": "Optional account email; blank/whitespace selects the default account.",
    })


def _imap_input_schemas() -> dict[str, dict[str, Any]]:
    send = _object(
        {
            "account": _account_field(),
            "address": _address_list(),
            "subject": {"type": "string", "description": "Email subject line"},
            "message": {"type": "string", "description": "Email body"},
            "cc": _address_list(),
            "bcc": _address_list(),
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Attachment paths; relative to workdir, absolute paths stay inside it.",
            },
        },
        required=["address"],
    )
    send["properties"]["address"]["description"] = "Real recipient(s); verify before delivery."
    send["properties"]["subject"]["description"] = "Subject to verify before delivery"
    send["properties"]["message"]["description"] = "Body to verify; schema permits omission"
    send["properties"]["cc"]["description"] = "CC recipient(s); verify before delivery"
    send["properties"]["bcc"]["description"] = "BCC recipient(s); verify before delivery"

    check = _object({
        "account": _account_field(),
        "folder": _nullable({
            "type": "string",
            "description": "Folder name; blank/whitespace defaults to INBOX.",
        }),
        "n": _nullable({
            "type": "integer",
            "description": "Max recent emails to show (default 10)",
        }),
    })

    read = _object(
        {
            "account": _account_field(),
            "email_id": _email_id_list(),
        },
        required=["email_id"],
    )

    reply = _object(
        {
            "account": _account_field(),
            "email_id": _email_id_list(),
            "subject": {"type": "string", "description": "Email subject line"},
            "message": {"type": "string", "description": "Email body"},
            "cc": _address_list(),
            "attachments": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Attachment paths; relative to workdir, absolute paths stay inside it.",
            },
        },
        required=["email_id", "message"],
    )
    reply["properties"]["email_id"]["description"] = "Returned compound ID; read target first; reply uses first."
    reply["properties"]["subject"]["description"] = "Optional override; otherwise derives from target"
    reply["properties"]["message"]["description"] = "Reply body to verify before delivery"
    reply["properties"]["cc"]["description"] = "CC recipient(s); verify before delivery"

    search = _object(
        {
            "account": _account_field(),
            "query": {
                "type": "string",
                "description": "Server-side DSL, e.g. from:addr subject:text unseen since:YYYY-MM-DD.",
            },
            "folder": _nullable({
                "type": "string",
                "description": "Folder name; blank/whitespace defaults to INBOX.",
            }),
        },
        required=["query"],
    )

    delete = _object(
        {
            "account": _account_field(),
            "email_id": _email_id_list(),
        },
        required=["email_id"],
    )
    delete["properties"]["email_id"]["description"] = "Compound ID(s); verify before changing mailbox state."

    move = _object(
        {
            "account": _account_field(),
            "email_id": _email_id_list(),
            "folder": {
                "type": "string",
                "description": "Non-empty destination; never defaults to INBOX.",
            },
        },
        required=["email_id", "folder"],
    )
    move["properties"]["email_id"]["description"] = "Compound ID(s); verify before changing mailbox state."

    flag = _object(
        {
            "account": _account_field(),
            "email_id": _email_id_list(),
            "flags": {
                "type": "object",
                "description": "Non-empty flag-name-to-bool map; changes mailbox state.",
            },
        },
        required=["email_id", "flags"],
    )
    flag["properties"]["email_id"]["description"] = "Compound ID(s); verify before changing mailbox state."

    folders = _object({"account": _account_field()})
    contacts = _object({"account": _account_field()})

    add_contact = _object(
        {
            "account": _account_field(),
            "address": {"type": "string", "description": "Contact's email address"},
            "name": {
                "type": "string",
                "description": "Contact's human-readable name",
            },
            "note": {"type": "string", "description": "Free-text note about the contact"},
        },
        required=["address", "name"],
    )

    remove_contact = _object(
        {
            "account": _account_field(),
            "address": {"type": "string", "description": "Contact's email address"},
        },
        required=["address"],
    )

    edit_contact = _object(
        {
            "account": _account_field(),
            "address": {"type": "string", "description": "Contact's email address"},
            "name": {
                "type": "string",
                "description": "Contact's human-readable name",
            },
            "note": {"type": "string", "description": "Free-text note about the contact"},
        },
        required=["address"],
    )

    accounts = _object({})
    return IMAP_PLUGIN.action_input_schemas({
        "send": send,
        "check": check,
        "read": read,
        "reply": reply,
        "search": search,
        "delete": delete,
        "move": move,
        "flag": flag,
        "folders": folders,
        "contacts": contacts,
        "add_contact": add_contact,
        "remove_contact": remove_contact,
        "edit_contact": edit_contact,
        "accounts": accounts,
    })


def _schema_only_family() -> ToolFamily:
    schemas = _imap_input_schemas()
    return IMAP_PLUGIN.build_family(
        [
            ChildTool(action, schemas[action], lambda _input: {})
            for action in _DECLARED_ACTIONS
        ],
        settings_provider=lambda: imap_setting_rows(None),
    )


_SCHEMA_FAMILY = _schema_only_family()


def imap_schema() -> dict[str, Any]:
    schema = _SCHEMA_FAMILY.build_schema()
    # IMAP has intentionally overlapping optional fields (for example every
    # action accepts optional account). The root allOf discriminator still
    # correlates each action to its exact closed branch; use anyOf for the
    # model-discovery list so native JSON-Schema validators do not reject a
    # valid input merely because another action's branch also fits.
    input_schema = schema["properties"]["input"]
    if "oneOf" in input_schema:
        input_schema["anyOf"] = input_schema.pop("oneOf")
    schema["properties"]["action"]["description"] = (
        "Strict branches for real IMAP/SMTP. First read: check/search, then read "
        "the returned compound email_id. send/reply deliver real mail: verify "
        "to/cc/bcc and body; external replies need standing policy or confirmation "
        "the sender is the same human who contacted you internally. Blank "
        "check/search folders mean INBOX; move needs a "
        "destination. delete/move/flag mutate state; inspect status/errors. "
        "Configuration is orchestrator-owned; avatars must not configure it. "
        "Read the manual for attachments, settings, and deeper detail. "
        + IMAP_PLUGIN.manual_action_description()
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


def build_imap_family(manager: Any | None) -> ToolFamily:
    """Compose manager actions plus read-only settings and the plugin manual.

    Only IMAP's operational actions are built here. The plugin inserts
    ``settings`` and ``manual``. The manual works without a manager; settings
    returns the generic fixed failure when applied runtime truth is unavailable.
    """
    schemas = _imap_input_schemas()
    children = [
        ChildTool(
            action,
            schemas[action],
            (lambda input_, action=action: manager.handle({"action": action, **dict(input_)}))
            if manager is not None else (lambda _input: {}),
        )
        for action in _DECLARED_ACTIONS
    ]
    return IMAP_PLUGIN.build_family(
        children,
        settings_provider=lambda: imap_setting_rows(manager),
    )


def handle_imap(manager: Any | None, args: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(args or {})
    if set(raw) - {"action", "input", "reasoning", "summarize"}:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "unsupported imap argument"}
    action = raw.get("action")
    if type(action) is not str or action not in _ACTIONS:
        return {"status": "failed", "error_code": "ACTION_REQUIRED", "message": "invalid imap action"}
    if "input" not in raw or not isinstance(raw.get("input"), Mapping):
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "input must be an object"}
    if type(raw.get("reasoning")) is not str:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "reasoning is required"}
    if "summarize" in raw and type(raw["summarize"]) is not bool:
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "summarize must be a boolean"}
    schema = _imap_input_schemas()[action]
    if not _basic_validate(raw["input"], schema):
        return {"status": "failed", "error_code": "INVALID_ARGUMENT", "message": "invalid imap input"}
    return build_imap_family(manager).handle(raw)


IMAP_SCHEMA = imap_schema()
# ``IMAP_ACTIONS`` is re-exported from ``plugin.py`` (imported above) so the
# public action list has exactly one definition.
