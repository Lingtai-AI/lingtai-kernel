"""Secondary nested tool-call policy.

A ``secondary`` call is a small, restricted communication tool invocation
embedded inside a primary tool's arguments.  It exists only so an agent can
reply to a human promptly while starting a potentially long primary action.
The runtime executes it mechanically before the primary handler and reports a
short outcome in the primary tool-result metadata.
"""
from __future__ import annotations

import copy
from typing import Any


SECONDARY_ALLOWED_TOOLS: set[str] = {"email", "telegram", "wechat", "feishu"}
SECONDARY_ALLOWED_ACTIONS: dict[str, set[str]] = {
    "email": {"send", "reply"},
    "telegram": {"send", "reply"},
    "wechat": {"send", "reply"},
    "feishu": {"send", "reply"},
}

_SECONDARY_ARGS_PROPERTIES: dict[str, Any] = {
    "action": {
        "type": "string",
        "enum": ["send", "reply"],
        "description": "Only send/reply are allowed for secondary calls.",
    },
    "text": {"type": "string", "description": "Message text for telegram/wechat/feishu."},
    "message": {"type": "string", "description": "Message body for internal email."},
    "address": {"description": "Internal email recipient for email send."},
    "email_id": {"description": "Internal email id/list for email reply."},
    "chat_id": {"description": "Telegram chat id for telegram send."},
    "user_id": {"type": "string", "description": "WeChat user id for wechat send."},
    "receive_id": {"type": "string", "description": "Feishu receive_id for feishu send."},
    "receive_id_type": {"type": "string", "description": "Feishu receive_id_type for feishu send."},
    "message_id": {"type": "string", "description": "Message id for reply actions."},
    "media_path": {"type": "string", "description": "Optional WeChat media path."},
}

# Primary tools that should not themselves expose ``secondary``.  The
# communication tools are the only allowed secondary targets, so allowing them
# to carry another communication call would create confusing nested sends. IMAP
# is external email and deliberately excluded from the human-reply v0 surface.
SECONDARY_EXCLUDED_PRIMARY_TOOLS: set[str] = SECONDARY_ALLOWED_TOOLS | {"imap", "system", "psyche", "soul"}

SECONDARY_SCHEMA_PROPERTY: dict[str, Any] = {
    "type": "object",
    "description": (
        "Optional nested communication tool call executed by the runtime before "
        "this primary tool starts, only for timely human replies when the primary "
        "tool is expected to take more than a few seconds. Do not use for routine "
        "short calls. Only email/telegram/wechat/feishu are allowed; only "
        "send/reply actions are allowed; nested secondary fields are forbidden. "
        "Secondary failure does not block the primary tool."
    ),
    "additionalProperties": False,
    "properties": {
        "tool": {
            "type": "string",
            "enum": sorted(SECONDARY_ALLOWED_TOOLS),
            "description": "Communication tool to run as the secondary call.",
        },
        "args": {
            "type": "object",
            "description": (
                "Arguments for the communication tool. Must include action=send "
                "or action=reply plus that tool's normal target/message fields. "
                "For example, telegram.send needs chat_id+text. Must not contain "
                "another secondary field."
            ),
            "properties": _SECONDARY_ARGS_PROPERTIES,
            "required": ["action"],
        },
    },
    "required": ["tool", "args"],
}


def is_secondary_primary_eligible(tool_name: str) -> bool:
    """Return True iff a primary tool schema should expose ``secondary``."""
    return tool_name not in SECONDARY_EXCLUDED_PRIMARY_TOOLS


def secondary_schema_property() -> dict[str, Any]:
    """Return a fresh copy of the JSON-schema property for ``secondary``."""
    return copy.deepcopy(SECONDARY_SCHEMA_PROPERTY)
