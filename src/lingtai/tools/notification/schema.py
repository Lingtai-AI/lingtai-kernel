"""Canonical notification input schemas and model-facing English descriptions.

This module owns data only; ``__init__.py`` composes the public family and
``ToolFamily`` validates the closed envelope. ``summarize`` remains a root
post-processing control, not a notification action. Nullable optional fields are
required by strict provider schemas and are stripped back to absent before the
existing handlers apply their defaults.
"""
from __future__ import annotations

from typing import Any

from ..tool_family.manual import MANUAL_INPUT_SCHEMA

LARGE_RESULT_DISMISS_ACTION_NOTE = (
    "Legacy large_tool_result is an escape hatch: prefer "
    "context(action='summarize'); dismissal clears only its mirror."
)

LARGE_RESULT_FORCE_NOTE = ""

# The canonical action order. This is the single source for the schema's
# ``action`` enum order, the ``input`` disclosure/``allOf`` branch order, and the
# child registration order in ``__init__.py`` — one list, not three.
# Read/clear actions keep the pre-existing prefix stable; hook-registry
# management (add/drop/edit/list) is administrative and follows.
NOTIFICATION_DECLARED_ACTIONS = (
    "check",
    "dismiss_channel",
    "dismiss_event",
    "dismiss_ref",
    "add",
    "drop",
    "edit",
    "list",
    "delay",
)

# The official declaration injects the kernel-reserved settings action before
# manual. Keep the full public order available to documentation/import-time
# consumers while leaving both reserved children to generic composition.
ACTION_ORDER = (*NOTIFICATION_DECLARED_ACTIONS, "settings", "manual")

_CHANNEL_DESCRIPTION = (
    "Channel; whole clear requires it, event/ref default to system. Follow the "
    "producer verb first; generic clear is mirror-only."
)

_FORCE_DESCRIPTION = (
    "Optional true only after rereading a confirmed stale mirror; never producer or protected "
    "state. " + LARGE_RESULT_FORCE_NOTE
)

_REASON_DESCRIPTION = "Optional reason; post-molt requires continue|defer|obsolete: ... ."

_CHECK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

_HOOK_NAME_DESCRIPTION = "Hook name in hooks.json."

_HOOK_CHANNEL_DESCRIPTION = "Published stem; registration allowlists it."

_HOOK_STRING_FIELD_DESCRIPTION = "Hook manifest field; null means unchanged."

_ADD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": _HOOK_NAME_DESCRIPTION},
        "channel": {"type": "string", "description": _HOOK_CHANNEL_DESCRIPTION},
        "source": {"type": "string", "description": "Producer identifier."},
        "description": {"type": "string", "description": "Watched source."},
        "how_to_modify": {"type": "string", "description": "How to modify."},
        "how_to_cancel": {"type": "string", "description": "How to stop it; drop never kills it."},
        "version": {"type": ["string", "null"], "description": "Version; default 1.0.0."},
        "instructions": {"type": ["string", "null"], "description": "Handling guidance."},
    },
    "required": [
        "name",
        "channel",
        "source",
        "description",
        "how_to_modify",
        "how_to_cancel",
        "version",
        "instructions",
    ],
    "additionalProperties": False,
}

_DROP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": _HOOK_NAME_DESCRIPTION}
    },
    "required": ["name"],
    "additionalProperties": False,
}

_EDIT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": _HOOK_NAME_DESCRIPTION},
        "version": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
        "source": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
        "description": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
        "channel": {"type": ["string", "null"], "description": _HOOK_CHANNEL_DESCRIPTION},
        "how_to_modify": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
        "how_to_cancel": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
        "instructions": {"type": ["string", "null"], "description": _HOOK_STRING_FIELD_DESCRIPTION},
    },
    "required": ["name", "version", "source", "description", "channel", "how_to_modify", "how_to_cancel", "instructions"],
    "additionalProperties": False,
}

_LIST_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

_DELAY_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "channel": {
            "type": "string",
            "description": "Allowed target; delay-alarm cannot be delayed.",
        },
        "seconds": {
            "type": "integer",
            "minimum": 0,
            "description": "Seconds; 0 cancels, otherwise live cap LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS (default 600).",
        },
    },
    "required": ["channel", "seconds"],
    "additionalProperties": False,
}

_DISMISS_CHANNEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "channel": {"type": "string", "description": _CHANNEL_DESCRIPTION},
        "force": {"type": ["boolean", "null"], "description": _FORCE_DESCRIPTION},
        "reason": {"type": ["string", "null"], "description": _REASON_DESCRIPTION},
    },
    "required": ["channel", "force", "reason"],
    "additionalProperties": False,
}

_DISMISS_EVENT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_id": {
            "type": "string",
            "description": "Remove matching event_id; system by default, daemon also supported.",
        },
        "channel": {"type": ["string", "null"], "description": _CHANNEL_DESCRIPTION},
        "force": {"type": ["boolean", "null"], "description": _FORCE_DESCRIPTION},
        "reason": {"type": ["string", "null"], "description": _REASON_DESCRIPTION},
    },
    "required": ["event_id", "channel", "force", "reason"],
    "additionalProperties": False,
}

_DISMISS_REF_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ref_id": {
            "type": "string",
            "description": "Remove matching ref_id events; system by default, daemon also supported.",
        },
        "channel": {"type": ["string", "null"], "description": _CHANNEL_DESCRIPTION},
        "force": {"type": ["boolean", "null"], "description": _FORCE_DESCRIPTION},
        "reason": {"type": ["string", "null"], "description": _REASON_DESCRIPTION},
    },
    "required": ["ref_id", "channel", "force", "reason"],
    "additionalProperties": False,
}

# Per-action strict schemas for the actions this package itself declares.
# ``settings`` and ``manual`` are deliberately absent: the kernel declaration
# injects their canonical shared schemas and the dispatching family composes the
# matching children. Keeping them absent here prevents package-owned actions
# from drifting into either reserved slot.
DECLARED_INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "add": _ADD_INPUT_SCHEMA,
    "drop": _DROP_INPUT_SCHEMA,
    "edit": _EDIT_INPUT_SCHEMA,
    "list": _LIST_INPUT_SCHEMA,
    "delay": _DELAY_INPUT_SCHEMA,
    "check": _CHECK_INPUT_SCHEMA,
    "dismiss_channel": _DISMISS_CHANNEL_INPUT_SCHEMA,
    "dismiss_event": _DISMISS_EVENT_INPUT_SCHEMA,
    "dismiss_ref": _DISMISS_REF_INPUT_SCHEMA,
}

# Compatibility/readability view of the complete public shape.  The actual
# declaration is built in ``notification.__init__`` from
# ``DECLARED_INPUT_SCHEMAS``; it reuses this imported canonical manual schema,
# rather than treating this map as a second source of truth.
INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    **DECLARED_INPUT_SCHEMAS,
    "settings": dict(_CHECK_INPUT_SCHEMA),
    "manual": MANUAL_INPUT_SCHEMA,
}

ACTION_ENUM_DESCRIPTION = (
    "Choose one strict action; nullable optionals mean absent. "
    "check: read current mirrors. "
    "dismiss_channel: clear one mirror. "
    "dismiss_event: remove an event by event_id; channel defaults to system, daemon also supported. "
    "dismiss_ref: remove matching events by ref_id; channel defaults to system, daemon also supported. "
    "add: register and allowlist a hook. "
    "drop: unregister it; never stop its process. "
    "edit: update a hook and revalidate its channel. "
    "list: show hooks. "
    "delay: hide one consumer channel (0 cancels; nonzero uses the live cap); "
    "producer state is unchanged and expiry emits one non-delayable alarm. "
    "settings: read-only setting rows. "
    "manual: call notification(action='manual', input={}) for installed guidance; "
    "read-only."
) + "\n\n" + LARGE_RESULT_DISMISS_ACTION_NOTE


def get_description(lang: str = "en") -> str:
    return (
        "Notification reads channel mirrors, manages hook registrations, and applies "
        "consumer delay. Use the strict action + input + reasoning envelope; start "
        "with notification(action='check', input={}, reasoning='...'). Live payload: "
        "_meta.agent_meta.notifications.attention (guidance.transient routes handling). Follow "
        "producer instructions before generic dismissal: generic clear affects the "
        "mirror only. Reread after a stale refusal; force=true is only for a "
        "confirmed stale mirror, never producer or protected state. Post-molt needs "
        "continue|defer|obsolete: ...; drop never stops its process; delay is "
        "consumer-only (0 cancels), and delay-alarm cannot be targeted. "
        "notification(action='settings', input={}, reasoning='...') and "
        "notification(action='manual', input={}, reasoning='...') are read-only; "
        "use context(action='summarize') for compaction."
    )


# NOTE: ``get_schema`` is deliberately NOT defined here. The model-facing
# schema is *composed* from the data above by the generic ``ToolFamily``
# infra, and that composition lives next to the child registry it is
# generated from — ``__init__.py::get_schema``. Defining a second one here
# would either duplicate the composition or import the package back into its
# own data module. ``__init__.py`` re-exports the composed ``get_schema``, so
# ``lingtai.tools.notification.get_schema`` (the intrinsic protocol entry
# point ``_build_tool_schemas`` actually calls) is unchanged.
