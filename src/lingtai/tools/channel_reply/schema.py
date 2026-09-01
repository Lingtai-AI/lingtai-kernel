"""Schema data for the channel_reply intrinsic."""
from __future__ import annotations

from typing import Any

from lingtai.kernel.channel_reply import MAX_REPLY_TEXT_CHARS, PROTOCOL_VERSION
from lingtai.tools.tool_family.manual import MANUAL_INPUT_SCHEMA

ACTION_ORDER = ("submit", "manual")

SUBMIT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "enum": [PROTOCOL_VERSION]},
        "grant_ref": {
            "type": "string",
            "description": "Opaque owner-provided reply grant reference.",
        },
        "request_id": {
            "type": "string",
            "description": "Target-local idempotency key for this reply request.",
        },
        "created_at": {
            "type": "string",
            "description": (
                "Target-authored current UTC timestamp generated when this concrete "
                "submit request is made; never copy the grant's issuance/route time."
            ),
        },
        "text": {
            "type": "string",
            "maxLength": MAX_REPLY_TEXT_CHARS,
            "description": "Plain text for one anchored channel reply.",
        },
        "proof": {
            "type": "string",
            "description": "Narrow bearer proof paired with the opaque grant.",
        },
    },
    "required": ["version", "grant_ref", "request_id", "created_at", "text", "proof"],
    "additionalProperties": False,
}

INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "submit": SUBMIT_INPUT_SCHEMA,
    "manual": MANUAL_INPUT_SCHEMA,
}

ACTION_ENUM_DESCRIPTION = (
    "submit: request one plain-text reply through an opaque owner grant. The "
    "target provides only version, grant_ref, request_id, created_at, text, "
    "and proof. Destination, account, chat, user, message, rendering, media, "
    "and retry fields are never accepted.\n\n"
    "manual: return the channel_reply manual. Read-only."
)


def get_description(lang: str = "en") -> str:
    return (
        "Channel reply — normally closed, grant-bound plain-text replies "
        "through the owning channel adapter. This tool does not expose channel "
        "destinations or credentials."
    )
