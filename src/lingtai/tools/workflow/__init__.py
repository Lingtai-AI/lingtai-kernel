"""Independent opt-in ordered workflow ToolPlugin composition root."""
from __future__ import annotations

from typing import Any

from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration
from lingtai.tools.tool_family import ChildTool, ToolFamily
from lingtai.tools.tool_family.manual import MANUAL_INPUT_SCHEMA, build_manual_child

from .core import WorkflowManager

_DESCRIPTION = (
    "Run ordered stages of authorized daemon task batches from a temporary JSON file. "
    "Independent opt-in workflow; daemon remains directly available. "
    "Auto-continuation requires file authorization; default pauses. "
    "Terminal/pause/uncertain outcomes notify main; do not poll for progress. "
    "Use check after notification. Keep an ad hoc workflow progress note in the existing "
    "Task Card, updated on meaningful changes. Read manual before unfamiliar use."
)


def _object(properties: dict, required: list[str]) -> dict:
    return dict(type="object", properties=properties, required=required, additionalProperties=False)


_INPUTS = {
    "run": _object({"file": {"type": "string", "minLength": 1},
                    "run_id": {"type": ["string", "null"], "default": None}}, ["file"]),
    "check": _object({"run_id": {"type": "string"}}, ["run_id"]),
    "stop": _object({"run_id": {"type": "string"}}, ["run_id"]),
}


def _family(workdir: Any = None, manager: WorkflowManager | None = None) -> ToolFamily:
    def invoke(action: str, values: dict) -> dict:
        try:
            if manager is None:
                raise RuntimeError("workflow is not bound")
            return getattr(manager, action)(**values)
        except (ValueError, OSError, TypeError) as exc:
            return {"status": "error", "message": str(exc)}

    children = [ChildTool(name=action, input_schema=schema,
                          handler=lambda values, action=action: invoke(action, values))
                for action, schema in _INPUTS.items()]
    children.append(build_manual_child(workdir, "workflow"))
    return ToolFamily("workflow", children)


def _handle(family: ToolFamily, args: dict) -> dict:
    # ToolFamily's legacy flat-field relocation is deliberately NOT workflow input.
    if not isinstance(args, dict) or set(args) - {"action", "input", "reasoning", "_reasoning", "summarize"}:
        return {"status": "error", "message": "workflow requires a strict action/input envelope"}
    return family.handle(args)


def _bind(host) -> BoundToolPlugin:
    from lingtai.adapters.workflow_cli import DaemonCliBatches

    manager = host.workflow_lifecycle.current_manager()
    if manager is None:
        manager = WorkflowManager(host.workdir.path, DaemonCliBatches(host.workdir.path),
                                  host.notifications, host.shutdown)
        host.workflow_lifecycle.retain_manager(manager)
    elif not isinstance(manager, WorkflowManager):
        raise TypeError("workflow lifecycle contains a foreign manager")
    else:
        manager.rebind(host.notifications, host.shutdown)
    family = _family(host.workdir, manager)
    return BoundToolPlugin(name="workflow", schema=family.build_schema(),
                           handler=lambda args: _handle(family, args), description=_DESCRIPTION)


DECLARATION = ToolPluginDeclaration(
    name="workflow", actions=("run", "check", "stop"), input_schemas=_INPUTS,
    manual_input_schema=MANUAL_INPUT_SCHEMA, manual="workflow",
    description=_DESCRIPTION, binder=_bind,
    requires=("workdir", "notifications", "shutdown", "workflow_lifecycle"),
)


def get_schema() -> dict:
    return _family().build_schema()


def get_description() -> str:
    return _DESCRIPTION


def setup(agent) -> WorkflowManager:
    from lingtai.adapters.tool_plugin_host import register_agent_tool_plugins

    register_agent_tool_plugins(agent, [DECLARATION])
    return agent._workflow_manager
