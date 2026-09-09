"""Schema data — canonical per-action input schemas and prose for ``system``.

The system tool is an LTP v2 family (``../CONTRACT.md``): the model-facing root
is the closed ``action`` + ``input`` + ``reasoning`` + ``summarize`` envelope,
and each action's arguments live in its own strict ``input`` object.

This module holds only data: each action's own canonical strict
``input_schema`` (:data:`INPUT_SCHEMAS`) and the canonical English prose.
``__init__.py`` composes these into the public model-facing schema via the
generic ``ToolFamily`` infra (``lingtai.tools.tool_family``) — see
``__init__.py::_build_children``/``get_schema``. ``lang`` is accepted on
:func:`get_description` for source compatibility and does not select localized
aliases; schema prose is canonical English, language-independent.

The migration moved the six pre-migration flat sibling fields (``reason``,
``address``, ``preset``, ``revert_preset``, ``rebuild``, ``items``) into the
``input`` object of exactly the actions that read them. Their types, defaults,
and meanings are unchanged; model-visible descriptions are kept concise and
route operational depth to ``system-manual``. No action was added, removed,
renamed, or reordered.

Two fields deserve a note because they are not a straight carry-over of the
pre-migration *schema*:

* ``sleep.force`` was always read by ``karma._sleep`` (the kernel#112 escape
  hatch) but never advertised in the flat schema. A strict child ``input``
  must declare every key its handler accepts, or dispatch would reject a call
  that succeeds today, so it is declared here. This surfaces an existing
  behavior; it does not add one.
* ``notification_threshold_chars`` is deliberately still absent. It is
  config-only (``manifest.summarize_notification_threshold`` + refresh), and
  ``summarize._summarize``'s loud runtime-mutation refusal is retained as the
  inner layer for direct in-process callers that bypass the envelope.

Optional fields are declared in the provider-compatible nullable
representation (``"type": ["string", "null"]`` plus membership in
``required``) per ``tools/CONTRACT.md`` "Envelope": strict OpenAI schemas have
no other way to express an optional field. ``__init__.py`` strips those nulls
back to *absent* before the pre-existing handlers run, so
``args.get("reason", "")``-style defaulting is preserved exactly.
"""
from __future__ import annotations

from typing import Any

from ..tool_family.manual import MANUAL_INPUT_SCHEMA
from .plugin import SYSTEM_DECLARED_ACTIONS

# Canonical compatibility order for the eleven operational actions plus
# ``manual``. The ToolPlugin declaration owns operational child registration;
# ToolFamily's reserved settings-provider opt-in mechanically inserts
# ``settings`` immediately before ``manual`` in the final model-facing family.
ACTION_ORDER = (*SYSTEM_DECLARED_ACTIONS, "manual")

# --- Concise descriptions for shared fields with unchanged runtime meaning ---

_REASON_DESCRIPTION = (
    "Optional reason, recorded in the event log; for clear it becomes the "
    "recovery source tag."
)

_ADDRESS_DESCRIPTION = (
    "Target agent working-directory address; required by this peer-control "
    "action."
)

_PRESET_DESCRIPTION = (
    "Exact preset path from the allowed `presets` result. Refresh refuses an "
    "unauthorized path or one whose context limit cannot hold this conversation; "
    "read the system-manual pre-check first."
)

_REVERT_PRESET_DESCRIPTION = (
    "With refresh, return to `manifest.preset.default`. Mutually exclusive with "
    "a non-empty preset; errors when no default is configured."
)

_FORCE_DESCRIPTION = (
    "Sleep despite pending notifications. The default false refuses the "
    "transition; use true only when that pending attention is intentionally deferred."
)

def _address_input_schema() -> dict[str, Any]:
    """Build the shared input shape for the six address-taking verbs.

    ``lull``/``interrupt``/``suspend``/``cpr``/``clear``/``nirvana`` take
    exactly the same two fields, so they are generated rather than restated
    six times. Each call returns a fresh dict so no two children share a
    mutable schema container.
    """
    return {
        "type": "object",
        "properties": {
            "address": {"type": "string", "description": _ADDRESS_DESCRIPTION},
            "reason": {
                "type": ["string", "null"],
                "description": _REASON_DESCRIPTION,
            },
        },
        "required": ["address", "reason"],
        "additionalProperties": False,
    }


_REFRESH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": ["string", "null"], "description": _REASON_DESCRIPTION},
        "preset": {"type": ["string", "null"], "description": _PRESET_DESCRIPTION},
        "revert_preset": {
            "type": ["boolean", "null"],
            "description": _REVERT_PRESET_DESCRIPTION,
        },
    },
    "required": ["reason", "preset", "revert_preset"],
    "additionalProperties": False,
}

_SLEEP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": ["string", "null"], "description": _REASON_DESCRIPTION},
        "force": {"type": ["boolean", "null"], "description": _FORCE_DESCRIPTION},
        "delay": {
            "type": ["number", "null"],
            "exclusiveMinimum": 0,
            "description": (
                "Finite positive seconds for the one-shot system alarm; use only "
                "as a last-resort when async work has no reliable completion "
                "notification. Normal waiting is IDLE. Null omits it; an early "
                "wake does not cancel it and a later delay replaces it."
            ),
        },
    },
    "required": ["reason", "force", "delay"],
    "additionalProperties": False,
}

_PRESETS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}

# The two name actions, moved here from the dissolved ``psyche`` family. Both
# take exactly one field, ``content``, matching the pre-move shape and
# semantics. They are identity *runtime state* (live in-memory identity, the
# persisted ``.agent.json``, and the protected prompt ``identity`` section) —
# NOT raw init/config editing, and NOT the physical agent address/workdir
# rename, which remains the operator migration workflow in ``system-manual``.
_NAME_SET_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": (
                "True name (真名), set once and immutable. Use name_nickname for a "
                "changeable display name; a second name_set is refused."
            ),
        },
    },
    "required": ["content"],
    "additionalProperties": False,
}

_NAME_NICKNAME_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "content": {
            "type": "string",
            "description": (
                "Mutable nickname (别名), unlike the true name; an empty string "
                "clears it."
            ),
        },
    },
    "required": ["content"],
    "additionalProperties": False,
}

INPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "refresh": _REFRESH_INPUT_SCHEMA,
    "sleep": _SLEEP_INPUT_SCHEMA,
    "lull": _address_input_schema(),
    "interrupt": _address_input_schema(),
    "suspend": _address_input_schema(),
    "cpr": _address_input_schema(),
    "clear": _address_input_schema(),
    "nirvana": _address_input_schema(),
    "presets": _PRESETS_INPUT_SCHEMA,
    "name_set": _NAME_SET_INPUT_SCHEMA,
    "name_nickname": _NAME_NICKNAME_INPUT_SCHEMA,
    # Referenced, not restated: ``build_manual_child`` owns this literal, so
    # the schema-only family and the dispatching family cannot drift
    # (``tool_family/CONTRACT.md`` Contract rules).
    "manual": MANUAL_INPUT_SCHEMA,
}

# Concise per-action prose the model reads to choose an action. Runtime meaning
# and argument ownership stay unchanged; operational depth routes to
# ``system-manual`` instead of remaining in the resident declaration.
ACTION_ENUM_DESCRIPTION = (
    "Choose one action; put only that action's fields in input.\n"
    "Self actions need no karma; peer controls name another agent.\n"
    "refresh: reload the existing runtime from init.json; a preset must be an "
    "exact allowed path, and revert_preset returns to the configured default. "
    "Read system-manual's refresh pre-check first.\n"
    "presets: list the allowed preset paths and current metadata.\n"
    "sleep: sleep yourself; normal waiting is IDLE. Use a positive delay only "
    "as a last-resort alarm when async work has no reliable completion notification; "
    "pending notifications may require force.\n"
    "lull: put another agent to sleep; requires admin.karma=True.\n"
    "interrupt: cancel another agent's current turn; requires admin.karma=True.\n"
    "suspend: stop another agent; requires admin.karma=True.\n"
    "cpr: resuscitate another agent; requires admin.karma=True.\n"
    "clear: force another agent's molt for recovery; requires admin.karma=True.\n"
    "nirvana: permanently destroy the exact target; requires admin.karma=True and "
    "admin.nirvana=True; read the peer-recovery procedure in system-manual and "
    "verify the exact target first.\n"
    "name_set: set the immutable true name once. name_nickname: set or clear "
    "the mutable nickname; neither renames the address/workdir.\n"
    "settings: complete read-only SHOW (input={}). manual: return the installed "
    "system-manual (input={}) without changing runtime state.\n"
    "Notification reads/dismissals belong to notification; context hygiene "
    "belongs to context(action='summarize'|'rebuild'|'molt')."
)


def get_description(lang: str = "en") -> str:
    return (
        "System owns runtime lifecycle, identity, presets, settings, and peer "
        "management. Use the closed action + input envelope; each action accepts "
        "only its own fields. Self actions need no karma; peer controls require "
        "admin.karma=True and nirvana additionally requires admin.nirvana=True. "
        "Availability never proves authority. Settings is read-only; follow its "
        "comment pointer for meaning and authorized changes. Context and "
        "notification own their respective actions. Call system(action='manual', "
        "input={}) for the installed manual."
    )
