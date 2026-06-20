"""Schema — tool registration for the standalone ``notification`` tool.

The notification tool exposes only the notification-facing verbs: ``check``
plus the three atomic dismiss verbs (``dismiss_channel``, ``dismiss_event``,
``dismiss_ref``).  ``summarize`` is *not* here — it remains a ``system`` action.

Where a parameter is shared in spirit with the ``system`` tool (``channel``,
``force``, ``event_id``, ``ref_id``, ``reason``), a notification-owned i18n
string is used so the notification tool documents its own behavior.
"""
from __future__ import annotations


def get_description(lang: str = "en") -> str:
    from ...i18n import t
    return t(lang, "notification_tool.description")


def get_schema(lang: str = "en") -> dict:
    from ...i18n import t
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["check", "dismiss_channel", "dismiss_event", "dismiss_ref"],
                "description": t(lang, "notification_tool.action_description"),
            },
            "channel": {
                "type": "string",
                "description": t(lang, "notification_tool.channel_description"),
            },
            "force": {
                "type": "boolean",
                "description": t(lang, "notification_tool.force_description"),
            },
            "event_id": {
                "type": "string",
                "description": t(lang, "notification_tool.event_id_description"),
            },
            "ref_id": {
                "type": "string",
                "description": t(lang, "notification_tool.ref_id_description"),
            },
            "reason": {
                "type": "string",
                "description": t(lang, "notification_tool.reason_description"),
            },
        },
        "required": ["action"],
    }
