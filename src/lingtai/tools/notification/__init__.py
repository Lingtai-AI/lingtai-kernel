"""Notification intrinsic — the standalone notification surface.

This intrinsic owns the notification-facing verbs: reading the live
notification surface and clearing notification mirrors.  It is the **only**
agent-callable home for those operations — the ``system`` tool no longer
exposes any notification verb (no ``notification``/``dismiss`` compatibility
alias).  ``system`` retains ``summarize`` (and the lifecycle/karma actions);
``summarize`` is *not* a notification verb and stays under ``system``.

The tool is an LTP v2 family (``../CONTRACT.md``): the model-facing root is
the closed ``action`` + ``input`` + ``reasoning`` + ``summarize`` envelope,
and each action's arguments live in its own strict ``input`` object composed
by the generic ``ToolFamily`` infra (``lingtai.tools.tool_family``) from the
per-action schemas in :mod:`.schema`.  ``action`` values are unchanged, and
the public tool name stays ``notification``.

Dismissal is **atomic**: there is no single kitchen-sink ``dismiss``.  Each
removal target has its own action so the API expresses exactly what is being
cleared:

Actions:
    check          — voluntary read of the live notification surface.  Returns
                     a placeholder dict; the turn loop's meta-block post-hook
                     stamps the canonical ``_meta.agent_meta.notifications.attention`` +
                     ``_meta.agent_meta.guidance.transient`` payload onto this same result.
    dismiss_channel — clear one ``.notification/<channel>.json`` surface whole.
                     Rejects ``event_id``/``ref_id`` (those are atomic-event
                     verbs).  Producer-owned state is never touched; guarded
                     mirrors refuse without ``force``.
    dismiss_event  — remove a single ``system`` event by ``event_id`` from
                     ``.notification/system.json``.
    dismiss_ref    — remove ``system`` event(s) by ``ref_id`` from
                     ``.notification/system.json``.
    manual         — return the installed notification manual body. Read-only;
                     notification and producer state are not touched.

All three dismiss verbs delegate to the single canonical
:func:`lingtai.kernel.notifications.dismiss_channel` with
``invoked_by="notification"``.  The decision logic (allowlist, ``post-molt``
ack-reason, protected channels, generic-dismiss guard, and stale-channel-version
refusal) lives there, so every guard holds through this tool by construction.
Legacy ``large_tool_result`` reminders — the kernel no longer raises these
(large results are ranked under ``_meta.agent_meta.agent_state.current_tool_result_chars``
and compacted via ``context(action="summarize")``) — but any event still present
from before this change (or a pre-molt session) may be dismissed as an escape
hatch; doing so acknowledges the ref_id.  Summarization via
``context(action="summarize")`` still auto-clears any such matching reminder.
"""
from __future__ import annotations

from typing import Any, Mapping

# Schema data — canonical per-action input schemas and registration prose.
# ``get_schema`` is composed below from ``INPUT_SCHEMAS``; ``schema.py``
# deliberately defines no second one.
from .schema import (  # noqa: F401
    ACTION_ENUM_DESCRIPTION,
    ACTION_ORDER,
    INPUT_SCHEMAS,
    get_description,
)
from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import build_manual_child

# Single-source delegate — the canonical dismissal helper.  No notification
# logic is reimplemented here.
from lingtai.kernel.notifications import dismiss_channel


# Placeholder returned by ``check`` — the live payload (``_meta.agent_meta.notifications.attention``
# + ``_meta.agent_meta.guidance.transient``) is stamped onto this same result dict by
# ``attach_active_notifications`` in the turn loop.  Returning a dict (not a
# string) is what makes that stamp possible: the meta-block walks backward for
# the freshest *dict-shaped* tool result.
_CHECK_PLACEHOLDER_MESSAGE = (
    "Voluntary notification(action=check) read. The live notification payload "
    "is delivered via the kernel meta-block under the `_meta.agent_meta.notifications.attention` and "
    "`_meta.agent_meta.guidance.transient` keys on this same result. If those keys are "
    "absent, no notifications are active."
)


# The exact degraded-manual sentence pinned by ``CONTRACT.md`` Port. Kept
# verbatim across the ToolFamily migration — see ``_adapt_manual_result``.
_MANUAL_MISSING_ERROR = (
    "notification manual missing — initializer may have failed or "
    "capability not installed correctly"
)

# The installed intrinsic-skill directory ``manual`` reads. This is the
# ``load_installed_manual`` skill name, not the family name.
_MANUAL_SKILL_NAME = "notification-manual"


def _schema_only_family() -> ToolFamily:
    """Build the module-level ``ToolFamily`` used only to compose the schema.

    Notification is an *intrinsic*: the kernel imports this module once and
    calls ``get_schema()``/``handle(agent, args)`` on the module itself, so
    unlike ``web`` there is no per-Agent manager instance to hang a family
    off.  The real handlers need an ``agent``, which only arrives per call,
    so :func:`handle` builds a per-call family with bound handlers and this
    module-level one never dispatches.  Constructing it at import time is
    still load-bearing: it proves the fixed five-child registry has no
    duplicate and no reserved-name collision on ``manual``
    (``ToolFamilyError`` raises here, at import, rather than shipping
    silently).
    """

    def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("the module-level schema-only ToolFamily never dispatches")

    return ToolFamily(
        "notification",
        [
            ChildTool(action, INPUT_SCHEMAS[action], _unused, title=f"{action} input")
            for action in ACTION_ORDER
        ],
    )


_FAMILY = _schema_only_family()


def get_schema(lang: str = "en") -> dict[str, Any]:
    """Return the composed model-facing ``notification`` family schema.

    Composed by the generic ``ToolFamily`` infra from each action's own
    canonical ``input_schema`` in :mod:`.schema`, rather than hand-assembled.
    ``lang`` is accepted for source compatibility and ignored: schema prose is
    canonical English and language-independent.
    """
    schema = _FAMILY.build_schema()
    # The generic composer writes a neutral "Required operation within the
    # notification family." description. Notification's own per-action prose
    # (behavioral contracts, the channel defaults, the legacy large_tool_result
    # escape hatch) is the model's only in-schema guide to *which* action to
    # pick, so it replaces that placeholder here rather than being lost.
    schema["properties"]["action"]["description"] = ACTION_ENUM_DESCRIPTION
    return schema


def _strip_nulls(action_input: Mapping[str, Any]) -> dict:
    """Drop explicit ``null``s so absent-vs-null defaulting is preserved.

    Strict provider schemas express an optional field as a required nullable
    property, so the model sends ``{"channel": null}`` for "no channel".  The
    pre-existing handlers below distinguish absent from present via
    ``args.get("channel", "system")`` and ``args.get("channel") is None``, so
    null must become *absent* before they run — otherwise ``dismiss_event``
    would pass ``channel=None`` to Core instead of defaulting to ``system``.
    """
    return {key: value for key, value in action_input.items() if value is not None}


def _check(agent, args: dict) -> dict:
    """Voluntary read of the notification surface — returns a placeholder."""
    return {
        "_notification_placeholder": True,
        "message": _CHECK_PLACEHOLDER_MESSAGE,
    }


def _adapt_manual_result(mcp_result: dict) -> dict:
    """Flatten the ManualTool child's canonical result to notification's shape.

    ``ToolFamily.handle()`` has already dispatched to the registered ``manual``
    child (``tool_family.manual.build_manual_child``) and returned its
    canonical result *verbatim* (no double wrap) — full body at
    ``content[0].text``, host-local path at
    ``structuredContent.manual_path``.  Notification's own public result shape
    predates that generic contract and is pinned by
    ``notification/CONTRACT.md`` Port to exactly ``status`` /
    ``notification_manual`` / ``manual_path`` (+ ``error`` when degraded), so
    this Host-owned adapter runs strictly *after* dispatch, in :func:`handle`
    — never inside a registered child.

    The degraded ``error`` sentence is restated here rather than forwarded.
    The shared loader builds it as ``f"{skill_name} manual missing — ..."``
    from the *installed directory* name, which for this family is
    ``notification-manual`` — so forwarding it verbatim would silently change
    the contract-pinned sentence from "notification manual missing" to
    "notification-manual manual missing". Restating the exact pinned text is
    Host presentation of an unchanged fact (the manual is absent), not a
    rewrite of the child's canonical result, which stays canonical and
    untouched. The alternative — renaming the loader's argument or its
    message — would change every other family's error text, which no evidence
    supports.
    """
    flat: dict[str, Any] = {
        "status": mcp_result.get("status", "ok"),
        "notification_manual": mcp_result["content"][0]["text"],
        "manual_path": mcp_result["structuredContent"]["manual_path"],
    }
    if "error" in mcp_result:
        flat["error"] = _MANUAL_MISSING_ERROR
    return flat


def _dismiss_channel(agent, args: dict) -> dict:
    """Clear one notification channel whole.

    Atomic event verbs (``event_id``/``ref_id``) are not accepted here; use
    ``dismiss_event`` / ``dismiss_ref`` for targeted removal.

    Under LTP v2 those two keys are absent from this action's own
    ``input_schema``, so ``ToolFamily.handle()`` rejects them before this
    handler ever runs — a cross-action smuggle now fails earlier, and with no
    notification I/O.  The explicit check below is retained as the inner layer
    of that pair: it still holds for a direct in-process call that bypasses
    the envelope, and removing it would leave the rejection resting on schema
    dispatch alone.
    """
    channel = args.get("channel")
    if channel is None:
        agent._log("notification_dismiss_missing_channel")
        return {
            "status": "error",
            "reason": "missing_channel",
            "message": (
                "notification(action='dismiss_channel') requires "
                "input={'channel': '<name>', ...}."
            ),
        }
    if args.get("event_id") or args.get("ref_id"):
        return {
            "status": "error",
            "reason": "channel_dismiss_rejects_event_target",
            "channel": channel,
            "message": (
                "dismiss_channel clears a whole channel; use dismiss_event "
                "(event_id=...) or dismiss_ref (ref_id=...) for a single "
                "system event."
            ),
        }
    return dismiss_channel(
        agent,
        channel,
        invoked_by="notification",
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
    )


def _dismiss_event(agent, args: dict) -> dict:
    """Remove a single ``system`` event by ``event_id``."""
    event_id = args.get("event_id")
    if not event_id:
        agent._log("notification_dismiss_missing_event_id")
        return {
            "status": "error",
            "reason": "missing_event_id",
            "message": (
                "notification(action='dismiss_event') requires "
                "input={'event_id': '<id>', ...}."
            ),
        }
    return dismiss_channel(
        agent,
        args.get("channel", "system"),
        invoked_by="notification",
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
        event_id=event_id,
    )


def _dismiss_ref(agent, args: dict) -> dict:
    """Remove ``system`` event(s) by ``ref_id``."""
    ref_id = args.get("ref_id")
    if not ref_id:
        agent._log("notification_dismiss_missing_ref_id")
        return {
            "status": "error",
            "reason": "missing_ref_id",
            "message": (
                "notification(action='dismiss_ref') requires "
                "input={'ref_id': '<id>', ...}."
            ),
        }
    return dismiss_channel(
        agent,
        args.get("channel", "system"),
        invoked_by="notification",
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
        ref_id=ref_id,
    )


def _add_hook(agent, args: dict) -> dict:
    """Register an external hook manifest (notification add)."""
    from lingtai.kernel.notifications import add_hook

    try:
        manifest = {
            "name": args["name"],
            "version": args.get("version") or "1.0.0",
            "channel": args["channel"],
            "source": args["source"],
            "description": args["description"],
            "how_to_modify": args["how_to_modify"],
            "how_to_cancel": args["how_to_cancel"],
        }
        if args.get("instructions"):
            manifest["instructions"] = args["instructions"]
        return add_hook(agent, manifest)
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_manifest",
            "message": str(exc),
        }


def _drop_hook(agent, args: dict) -> dict:
    """Unregister a hook by name (notification drop)."""
    from lingtai.kernel.notifications import drop_hook

    try:
        return drop_hook(agent, args["name"])
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_name",
            "message": str(exc),
        }


def _edit_hook(agent, args: dict) -> dict:
    """Update a hook's fields by name (notification edit)."""
    from lingtai.kernel.notifications import edit_hook

    try:
        name = args["name"]
        fields = {
            key: value
            for key, value in args.items()
            if key != "name" and value is not None
        }
        return edit_hook(agent, name, fields)
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_edit",
            "message": str(exc),
        }


def _list_hooks(agent, args: dict) -> dict:
    """Return the registered hook manifests (notification list)."""
    from lingtai.kernel.notifications import list_hooks

    return {"status": "ok", "hooks": list_hooks(agent)}


def _build_family(agent) -> ToolFamily:
    """Build the per-call dispatching family with handlers bound to *agent*.

    Notification is an intrinsic with a module-level ``handle(agent, args)``
    entry point, so ``agent`` is only available per call — there is no
    per-Agent manager to cache a family on, as ``web`` has. Construction is
    cheap (five ``ChildTool`` dataclasses and a name check), and building it
    per call keeps ``agent`` out of module state, which matters because one
    process may serve several agents.

    The reserved ``manual`` child is registered directly and unwrapped, per
    ``tool_family/CONTRACT.md``: ``ToolFamily.handle()`` returns its canonical
    ``content``/``structuredContent`` result verbatim, and
    :func:`_adapt_manual_result` reshapes it afterwards in :func:`handle`.
    """
    dismiss_handlers = {
        "add": _add_hook,
        "drop": _drop_hook,
        "edit": _edit_hook,
        "list": _list_hooks,
        "check": _check,
        "dismiss_channel": _dismiss_channel,
        "dismiss_event": _dismiss_event,
        "dismiss_ref": _dismiss_ref,
    }

    def _bind(action: str):
        handler = dismiss_handlers[action]

        def _dispatch(action_input: Mapping[str, Any]) -> dict:
            return handler(agent, _strip_nulls(action_input))

        return _dispatch

    children = []
    for action in ACTION_ORDER:
        if action == "manual":
            children.append(build_manual_child(agent, _MANUAL_SKILL_NAME))
        else:
            children.append(
                ChildTool(
                    action,
                    INPUT_SCHEMAS[action],
                    _bind(action),
                    title=f"{action} input",
                )
            )
    return ToolFamily("notification", children)


def handle(agent, args: dict) -> dict:
    """Handle the standalone ``notification`` tool.

    The generic ``ToolFamily`` dispatcher validates ``action``, type-checks and
    strips root ``summarize``, rejects unknown root fields, and — crucially for
    a mutating family — rejects ``input`` keys outside the selected action's
    own declared schema *before* any handler runs.  That is what makes a
    cross-action smuggle such as ``action='check', input={'channel': 'goal'}``
    fail with no notification I/O at all, rather than being silently ignored
    downstream.

    ``_tc_id`` is stripped first.  ``base_agent.tools._dispatch_tool`` injects
    it into **every** intrinsic's args (only ``psyche`` molt consumes it), so
    it is kernel plumbing that predates and is invisible to the LTP v2
    envelope.  ``web``, a capability rather than an intrinsic, never sees it —
    which is why the generic ``_ROOT_FIELDS`` set does not admit it and why
    stripping belongs here, at the one boundary where an intrinsic adopts the
    generic dispatcher, rather than in shared infrastructure on the strength
    of a single family's need.

    Unknown/absent actions keep the pre-migration public result exactly
    (``{status: "error", message: "Unknown notification action: <x>"}``), which
    ``CONTRACT.md`` Port pins, so the generic dispatcher's own
    ``ACTION_REQUIRED`` envelope error is normalized here rather than by
    changing that dispatcher's canonical shape — the same seam ``web`` uses.
    """
    raw = dict(args or {})
    raw.pop("_tc_id", None)
    action = raw.get("action")

    result = _build_family(agent).handle(raw)

    if action == "manual" and "content" in result:
        return _adapt_manual_result(result)
    if result.get("error_code") == "ACTION_REQUIRED":
        return {
            "status": "error",
            "message": f"Unknown notification action: {action}",
        }
    return result
