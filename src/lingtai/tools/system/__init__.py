"""System's official declared host-plugin slice.

``system`` remains one LTP family with eleven operational actions plus the
reserved ``settings`` and ``manual`` actions (thirteen total).  The public
handler is now a static ``ToolPluginDeclaration`` bound
only to three narrow ports: its workdir for manual/addressed documents, a
runtime/lifecycle vocabulary, and durable naming identity.  The legacy
``handle(agent, args)`` entry point remains solely as a compatibility adapter
for direct in-process callers; normal ``lingtai.Agent`` composition mounts the
bound declaration through the kernel registrar.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Mapping

from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration
from lingtai.kernel.notifications import clear as clear_notification, submit as publish_notification

from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child
from .karma import (
    _KARMA_ACTIONS,
    _NIRVANA_ACTIONS,
    _check_karma_gate,
    _clear,
    _cpr,
    _interrupt,
    _lull,
    _target_refresh,
    _nirvana,
    _sleep,
    sleep_use_case,
    _suspend,
)
from .name import _name_nickname, _name_set
from .plugin import SYSTEM_DECLARED_ACTIONS
from .preset import _check_context_fits, _preset_ref_in, _presets, _refresh
from .schema import ACTION_ENUM_DESCRIPTION, ACTION_ORDER, INPUT_SCHEMAS, get_description
from .settings import system_settings_provider
from .summarize import SUMMARIZE_MARKER, _summarize

if TYPE_CHECKING:
    from lingtai.kernel.base_agent import BaseAgent
    from lingtai.kernel.tool_plugin import ToolPluginHost

__all__ = [
    "DECLARATION", "SYSTEM_DECLARED_ACTIONS", "ACTION_ORDER", "INPUT_SCHEMAS",
    "get_description", "get_schema", "handle", "boot", "setup", "SUMMARIZE_MARKER",
    "_summarize", "publish_notification", "clear_notification",
]

_DESCRIPTION = get_description()
_ACTION_HANDLERS = {
    "refresh": _refresh,
    "target_refresh": _target_refresh,
    "sleep": _sleep,
    "lull": _lull,
    "interrupt": _interrupt,
    "suspend": _suspend,
    "cpr": _cpr,
    "clear": _clear,
    "nirvana": _nirvana,
    "presets": _presets,
    "name_set": _name_set,
    "name_nickname": _name_nickname,
}


def _strip_nulls(action_input: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve established absent/null handler semantics."""
    return {key: value for key, value in action_input.items() if value is not None}


class _SystemHandlerHost:
    """Compatibility-shaped façade assembled only from System's granted ports.

    The retained handlers predate the declared-host contract and use a compact
    Agent-like vocabulary.  This private bridge maps that vocabulary to the
    three explicit ports; it never stores or exposes an Agent.
    """

    __slots__ = ("_workdir", "_runtime", "_identity")

    def __init__(self, host: "ToolPluginHost") -> None:
        self._workdir = host.workdir
        self._runtime = host.system_runtime
        self._identity = host.identity

    @property
    def _working_dir(self):
        return self._workdir.path

    @property
    def _admin(self):
        return self._runtime.admin

    @property
    def _config(self):
        return SimpleNamespace(language=self._runtime.language)

    @property
    def agent_name(self):
        return self._identity.name

    def _log(self, event: str, **fields: Any) -> None:
        self._runtime.log(event, **fields)

    def get_token_usage(self):
        return self._runtime.token_usage()

    def load_preset(self, name: str):
        return self._runtime.load_preset(name)

    def _activate_preset(self, name: str) -> None:
        self._runtime.activate_preset(name)

    def _activate_default_preset(self) -> None:
        self._runtime.activate_default_preset()

    def _retry_failed_mcps(self):
        return self._runtime.retry_failed_mcps()

    def _perform_refresh(self) -> None:
        self._runtime.perform_refresh()

    def _cpr_agent(self, address: str):
        return self._runtime.resuscitate(address)

    @property
    def _system_sleep_port(self):
        # The granted SystemRuntimePort carries exactly the SystemSleepPort
        # evidence/effects vocabulary; ``karma._sleep`` runs the one
        # System-owned ``sleep_use_case`` over it. No sleep policy lives on
        # this bridge or in the host adapter.
        return self._runtime

    def set_name(self, name: str) -> None:
        self._identity.set_name(name)

    def set_nickname(self, nickname: str) -> None:
        self._identity.set_nickname(nickname)


def _build_children(subject: Any, manual_source: Any = None) -> list[ChildTool]:
    """Build declaration-derived children; retained for direct test callers."""
    if manual_source is None:
        manual_source = subject

    def unused(_input: Mapping[str, Any]) -> dict:
        raise AssertionError("the schema-only System family never dispatches")

    children: list[ChildTool] = []
    for action in DECLARATION.actions:
        handler = _ACTION_HANDLERS[action]
        if subject is None:
            dispatch = unused
        else:
            def dispatch(action_input: Mapping[str, Any], handler=handler) -> dict:
                return handler(subject, _strip_nulls(action_input))
        children.append(ChildTool(
            action, DECLARATION.input_schemas[action], dispatch, title=f"{action} input",
        ))
    if subject is None:
        children.append(ChildTool("manual", DECLARATION.manual_input_schema, unused, title="manual input"))
    else:
        children.append(build_manual_child(manual_source, DECLARATION.manual))
    return children


def _settings_workdir(source: Any) -> Path | None:
    if source is None:
        return None
    value = getattr(source, "path", None)
    if value is None:
        value = getattr(source, "_working_dir", None)
    if value is None:
        raise ValueError("System settings require a workdir")
    return Path(value)


def _build_family(subject: Any, manual_source: Any = None) -> ToolFamily:
    """Compose the one System family from declaration-derived children."""
    owner_source = manual_source if manual_source is not None else subject
    return ToolFamily(
        DECLARATION.name,
        _build_children(subject, manual_source),
        settings_provider=system_settings_provider(_settings_workdir(owner_source)),
    )


def _adapt_manual_result(result: dict) -> dict:
    flat = {
        "status": result.get("status", "ok"),
        "manual": result["content"][0]["text"],
        "manual_path": result["structuredContent"]["manual_path"],
    }
    if "error" in result:
        flat["error"] = result["error"]
    return flat


def _dispatch(family: ToolFamily, args: Mapping[str, Any] | None) -> dict:
    raw = dict(args or {})
    raw.pop("_tc_id", None)
    action = raw.get("action")
    # Membership against a tuple keeps malformed unhashable action values an
    # ordinary unknown-action receipt rather than leaking TypeError.
    if action not in family.child_names:
        return {"status": "error", "message": f"Unknown system action: {action}"}
    result = family.handle(raw)
    if action == "manual" and "content" in result:
        return _adapt_manual_result(result)
    if result.get("error_code") == "ACTION_REQUIRED":
        return {"status": "error", "message": f"Unknown system action: {action}"}
    return result


def get_schema(lang: str = "en") -> dict[str, Any]:
    schema = _FAMILY.build_schema()
    schema["properties"]["action"]["description"] = ACTION_ENUM_DESCRIPTION
    return schema


def _bind(host: "ToolPluginHost") -> BoundToolPlugin:
    """Purely bind System to its workdir/runtime/identity host ports."""
    bridge = _SystemHandlerHost(host)
    family = _build_family(bridge, host.workdir)
    return BoundToolPlugin(
        name=DECLARATION.name,
        schema=get_schema(),
        handler=lambda args: _dispatch(family, args),
        description=DECLARATION.description,
        glossary_package=__package__,
    )


DECLARATION = ToolPluginDeclaration(
    name="system",
    actions=SYSTEM_DECLARED_ACTIONS,
    input_schemas={action: INPUT_SCHEMAS[action] for action in SYSTEM_DECLARED_ACTIONS},
    manual_input_schema=MANUAL_INPUT_SCHEMA,
    # Existing system-manual remains the installed router bundle; this is the
    # single source consumed by the family-owned manual child.
    manual="system-manual",
    description=_DESCRIPTION,
    binder=_bind,
    requires=("workdir", "system_runtime", "identity"),
    glossary_package=__package__,
    settings=True,
)

# Static schema composition has no Agent and validates the declaration's
# public action inventory at import.
_FAMILY = _build_family(None, None)


def handle(agent: Any, args: dict) -> dict:
    """Legacy direct adapter; official Agent mounting never passes an Agent here."""
    return _dispatch(_build_family(agent, agent), args)


def boot(agent: "BaseAgent") -> None:
    """Register the static System declaration through the controlled host path.

    ``BaseAgent._boot_official_intrinsics`` invokes this on construction and
    on every refresh because the injected intrinsic registry marks ``system``
    with ``official_plugin``.  A minimal test double without the official
    claim surface is left untouched, mirroring the other official families.
    """
    if not hasattr(agent, "official_tool_plugins"):
        return
    from lingtai.adapters.tool_plugin_host import register_agent_tool_plugins

    register_agent_tool_plugins(agent, [DECLARATION])


def setup(agent: "BaseAgent", **_ignored: Any) -> None:
    """Compatibility alias for direct callers; the official route is ``boot``."""
    boot(agent)
