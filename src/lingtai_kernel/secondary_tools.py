"""Secondary nested tool-call policy.

A ``secondary`` call is a small, restricted, **read-only** communication tool
invocation embedded inside a primary tool's arguments.  It exists only so an
agent can pull the full content of a recently-arrived message before acting on
it (when the notification only carried a truncated preview), without spending a
separate turn.  The runtime executes it mechanically before the primary handler
and forwards a bounded slice of the read payload under ``_secondary.result`` in
the primary tool-result metadata.

The channel deliberately does **not** allow human-facing writes (``send`` /
``reply``).  Those remain the exclusive province of the agent's ordinary
top-level communication tools, so normal primary send/reply behaviour is
untouched.  Only the nested ``secondary`` channel is constrained to ``read``.
"""
from __future__ import annotations

import copy
from typing import Any


SECONDARY_ALLOWED_TOOLS: set[str] = {"email", "telegram", "wechat", "feishu", "whatsapp"}
SECONDARY_ALLOWED_ACTIONS: dict[str, set[str]] = {
    "email": {"read"},
    "telegram": {"read"},
    "wechat": {"read"},
    "feishu": {"read"},
    "whatsapp": {"read"},
}

SECONDARY_READ_TARGET_FIELDS: dict[str, str] = {
    "email": "email_id",
    "telegram": "chat_id",
    "wechat": "user_id",
    "feishu": "chat_id",
    "whatsapp": "chat_id",
}

# Maximum serialized size of a ``read`` result body forwarded under
# ``_secondary.result``. The full read response stays in the producer's own
# storage; this is just a preview-into-the-primary slice so the agent does
# not need a separate turn to see what the notification was about.
SECONDARY_READ_RESULT_MAX_BYTES: int = 8_000

_SECONDARY_ARGS_PROPERTIES: dict[str, Any] = {
    "action": {
        "type": "string",
        "enum": ["read"],
        "description": (
            "read pulls the full content of a recently-arrived message before "
            "the primary tool runs. This channel is read-only — it cannot "
            "contact a human."
        ),
    },
    "email_id": {"description": "Internal email id/list to read (used by email read)."},
    "chat_id": {
        "description": "Telegram/Feishu/WhatsApp chat id to read. Telegram chat_id should be an integer; digit strings are normalized at runtime."
    },
    "user_id": {"type": "string", "description": "WeChat user id to read."},
    "limit": {
        "type": "integer",
        "description": (
            "Optional per-thread message-count cap for telegram/wechat/feishu/whatsapp read "
            "(default 10). Ignored by email."
        ),
    },
}

# Primary tools that should not themselves expose ``secondary``.  The
# communication tools are the only allowed secondary targets, so allowing them
# to carry another communication call would create confusing nested reads. IMAP
# is external email and deliberately excluded from the secondary read surface.
SECONDARY_EXCLUDED_PRIMARY_TOOLS: set[str] = SECONDARY_ALLOWED_TOOLS | {"imap", "system", "psyche", "soul"}

SECONDARY_SCHEMA_PROPERTY: dict[str, Any] = {
    "type": "object",
    "description": (
        "Read-only fetch of a just-notified message before this primary runs. "
        "Use this only when the primary call may take >5s and the notification "
        "preview was truncated, so you need the full message body before acting. "
        "Example: before a long bash/daemon/web_search call, when a preview is "
        "truncated, secondary={tool:'telegram', args:{action:'read', "
        "chat_id:..., limit:5}}. The runtime executes the secondary first; "
        "failures never block the primary; the read result returns a bounded "
        "slice under _secondary.result on the primary result. "
        "Do not use for routine short calls. This channel cannot contact a "
        "human — to acknowledge or answer someone, call the communication tool "
        "directly as a normal top-level tool. Only "
        "email/telegram/wechat/feishu/whatsapp are allowed; only the read "
        "action is allowed; nested secondary fields are forbidden."
    ),
    "additionalProperties": False,
    "properties": {
        "tool": {
            "type": "string",
            "enum": sorted(SECONDARY_ALLOWED_TOOLS),
            "description": "Communication tool to run as the secondary read.",
        },
        "args": {
            "type": "object",
            "description": (
                "Arguments for the read. Must include action=read plus that "
                "tool's normal target fields. For example, telegram.read needs "
                "chat_id (+optional limit) and email.read needs email_id. Must "
                "not contain another secondary field."
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


def normalize_secondary_args(tool_name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Return normalized secondary args and an optional validation error.

    The provider-facing secondary schema is intentionally small and shared
    across communication tools, so runtime validation must still enforce the
    target tool/action contract before dispatching to MCP handlers. This keeps
    obviously malformed model output (empty target ids, string Telegram
    ``chat_id`` values, ``limit=0`` placeholders) from surfacing as noisy MCP
    schema errors.
    """

    normalized: dict[str, Any] = {}
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, str) and value == "":
            continue
        # Models often emit optional numeric placeholders as zero; for read
        # limits, omission is safer because channel tools apply their defaults.
        if key == "limit" and value == 0:
            continue
        normalized[key] = value

    action = normalized.get("action")
    if action != "read":
        # The caller already reports disallowed actions. Keep this helper
        # focused on the read contract.
        return normalized, None

    required_field = SECONDARY_READ_TARGET_FIELDS.get(tool_name)
    if required_field and required_field not in normalized:
        return normalized, f"secondary {tool_name}.read requires {required_field}"

    if tool_name == "telegram":
        chat_id = normalized.get("chat_id")
        if isinstance(chat_id, str) and chat_id.isdigit():
            normalized["chat_id"] = int(chat_id)
        elif not isinstance(chat_id, int):
            return normalized, "secondary telegram.read requires integer chat_id"

    return normalized, None
