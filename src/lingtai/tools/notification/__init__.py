"""Official host-plugin family for notification mirrors, hooks, and delay.

This module adapts the strict LTP family only. Notification Core owns producer,
dismissal, delay, and Store state behind the narrow ``notification_state`` port;
the public actions and authorization gates remain unchanged, with read-only
``settings`` before ``manual``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Mapping

from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration

from .schema import (
    ACTION_ENUM_DESCRIPTION,
    DECLARED_INPUT_SCHEMAS,
    NOTIFICATION_DECLARED_ACTIONS,
    get_description as _schema_description,
)
from .settings import notification_settings
from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child

if TYPE_CHECKING:
    from lingtai.kernel.tool_plugin import NotificationStatePort, ToolPluginHost


# Placeholder returned by ``check``.  The turn loop stamps canonical live
# attention/guidance state onto this same dict; this family never rebuilds a
# competing snapshot from disk.
_CHECK_PLACEHOLDER_MESSAGE = (
    "Voluntary notification(action=check) read. The live notification payload "
    "is delivered via the kernel meta-block under the "
    "`_meta.agent_meta.notifications.attention` and "
    "`_meta.agent_meta.guidance.transient` keys on this same result. If those "
    "keys are absent, no notifications are active."
)


def _strip_nulls(action_input: Mapping[str, Any]) -> dict[str, Any]:
    """Restore absence semantics for strict-schema nullable optionals."""
    return {key: value for key, value in action_input.items() if value is not None}


def _check(_state: "NotificationStatePort", _args: dict[str, Any]) -> dict[str, Any]:
    """Return the deliberate check placeholder without touching notification state."""
    return {
        "_notification_placeholder": True,
        "message": _CHECK_PLACEHOLDER_MESSAGE,
    }


def _adapt_manual_result(mcp_result: dict[str, Any]) -> dict[str, Any]:
    """Flatten the dispatched manual child to notification's pinned public shape."""
    flat: dict[str, Any] = {
        "status": mcp_result.get("status", "ok"),
        "notification_manual": mcp_result["content"][0]["text"],
        "manual_path": mcp_result["structuredContent"]["manual_path"],
    }
    if "error" in mcp_result:
        flat["error"] = mcp_result["error"]
    return flat


def _dismiss_channel(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Adapt a whole-channel dismissal; Core owns every policy decision."""
    channel = args.get("channel")
    if channel is None:
        state.log("notification_dismiss_missing_channel")
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
    return state.dismiss(
        channel,
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
    )


def _dismiss_event(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Adapt a targeted system event dismissal; Core owns target policy."""
    event_id = args.get("event_id")
    if not event_id:
        state.log("notification_dismiss_missing_event_id")
        return {
            "status": "error",
            "reason": "missing_event_id",
            "message": (
                "notification(action='dismiss_event') requires "
                "input={'event_id': '<id>', ...}."
            ),
        }
    return state.dismiss(
        args.get("channel", "system"),
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
        event_id=event_id,
    )


def _dismiss_ref(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Adapt a targeted system ref dismissal; Core owns target policy."""
    ref_id = args.get("ref_id")
    if not ref_id:
        state.log("notification_dismiss_missing_ref_id")
        return {
            "status": "error",
            "reason": "missing_ref_id",
            "message": (
                "notification(action='dismiss_ref') requires "
                "input={'ref_id': '<id>', ...}."
            ),
        }
    return state.dismiss(
        args.get("channel", "system"),
        force=bool(args.get("force", False)),
        reason=args.get("reason"),
        ref_id=ref_id,
    )


def _delay(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Apply consumer delay through Core without changing producer state."""
    return state.delay(args.get("channel"), args.get("seconds"))


def _add_hook(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Validate presentation arguments and delegate hook registration to Core."""
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
        return state.add_hook(manifest)
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_manifest",
            "message": str(exc),
        }


def _drop_hook(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Delegate hook removal to Core's persisted registry."""
    try:
        return state.drop_hook(args["name"])
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_manifest",
            "message": str(exc),
        }


def _edit_hook(state: "NotificationStatePort", args: dict[str, Any]) -> dict[str, Any]:
    """Delegate hook update to Core's persisted registry."""
    try:
        name = args["name"]
        fields = {key: value for key, value in args.items() if key != "name" and value is not None}
        return state.edit_hook(name, fields)
    except (KeyError, ValueError) as exc:
        return {
            "status": "error",
            "reason": "invalid_manifest",
            "message": str(exc),
        }


def _list_hooks(state: "NotificationStatePort", _args: dict[str, Any]) -> dict[str, Any]:
    """Read registered hook manifests without bypassing Core's error shape."""
    result = state.list_hooks()
    if isinstance(result, dict) and result.get("status") == "error":
        return result
    return {"status": "ok", "hooks": result}


def _build_family(host: "ToolPluginHost | None") -> ToolFamily:
    """Build the schema-only or bound family from the static declaration."""
    if host is None:
        def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
            raise AssertionError("the module-level schema-only ToolFamily never dispatches")

        children = [
            ChildTool(
                action,
                DECLARATION.input_schemas[action],
                _unused,
                title=f"{action} input",
            )
            for action in DECLARATION.actions
        ]
        children.append(
            ChildTool("manual", DECLARATION.manual_input_schema, _unused, title="manual input")
        )
        return ToolFamily(
            DECLARATION.name,
            children,
            settings_provider=tuple,
        )

    state = host.notification_state
    read_settings = state.read_settings
    handlers: dict[str, Callable[["NotificationStatePort", dict[str, Any]], dict[str, Any]]] = {
        "check": _check,
        "dismiss_channel": _dismiss_channel,
        "dismiss_event": _dismiss_event,
        "dismiss_ref": _dismiss_ref,
        "add": _add_hook,
        "drop": _drop_hook,
        "edit": _edit_hook,
        "list": _list_hooks,
        "delay": _delay,
    }

    def _dispatch(action: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
        handler = handlers[action]

        def bound(action_input: Mapping[str, Any]) -> dict[str, Any]:
            return handler(state, _strip_nulls(action_input))

        return bound

    children = [
        ChildTool(
            action,
            DECLARATION.input_schemas[action],
            _dispatch(action),
            title=f"{action} input",
        )
        for action in DECLARATION.actions
    ]
    children.append(build_manual_child(host.workdir, DECLARATION.manual))
    return ToolFamily(
        DECLARATION.name,
        children,
        settings_provider=lambda: notification_settings(read_settings),
    )


def get_description(lang: str = "en") -> str:
    """Return the canonical language-independent notification description."""
    return _schema_description(lang)


def get_schema(lang: str = "en") -> dict[str, Any]:
    """Return the declaration-derived composed public notification schema."""
    schema = _FAMILY.build_schema()
    schema["properties"]["action"]["description"] = ACTION_ENUM_DESCRIPTION
    return schema


def _bind(host: "ToolPluginHost") -> BoundToolPlugin:
    """Compose notification against its exact declared narrow host facade."""
    family = _build_family(host)

    def handle_notification(args: dict[str, Any]) -> dict[str, Any]:
        # `_tc_id` is old intrinsic plumbing and is never part of the closed
        # public LTP envelope.  Removing it preserves compatibility with a
        # synthesized historic notification call copied into the live surface.
        raw = dict(args or {})
        raw.pop("_tc_id", None)
        action = raw.get("action")
        result = family.handle(raw)
        if action == "manual" and "content" in result:
            return _adapt_manual_result(result)
        if result.get("error_code") == "ACTION_REQUIRED":
            return {"status": "error", "message": f"Unknown notification action: {action}"}
        return result

    return BoundToolPlugin(
        name=DECLARATION.name,
        schema=get_schema(),
        handler=handle_notification,
        description=get_description(),
        glossary_package=__package__,
    )


#: Static official declaration, created before any Agent exists.  Every public
#: identity consumed by the family is read back from this one object.
DECLARATION = ToolPluginDeclaration(
    name="notification",
    actions=NOTIFICATION_DECLARED_ACTIONS,
    input_schemas=DECLARED_INPUT_SCHEMAS,
    manual_input_schema=MANUAL_INPUT_SCHEMA,
    manual="notification",
    description=_schema_description(),
    binder=_bind,
    requires=("workdir", "notification_state"),
    glossary_package=__package__,
    settings=True,
)

# Public compatibility views derived from the declaration. Reserved `settings`
# and `manual` are present once at the end but are not operational actions.
ACTION_ORDER = DECLARATION.public_actions
INPUT_SCHEMAS = DECLARATION.public_input_schemas()

# Import-time schema-only composition catches duplicate reserved-action defects
# before boot. Its children never dispatch or read settings.
_FAMILY = _build_family(None)


def setup(agent, **_ignored) -> None:
    """Mount notification through the kernel's official-plugin registrar."""
    from lingtai.adapters.tool_plugin_host import register_agent_tool_plugins

    register_agent_tool_plugins(agent, [DECLARATION])
