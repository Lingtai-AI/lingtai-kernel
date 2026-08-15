"""LingTai daemon common MCP server.

The model-visible contracts are the cooperative, nonterminal ``checkpoint``
tool and terminal ``finish`` tool. Both use the LTP v2
``action``/``input``/``reasoning`` envelope every curated LingTai MCP tool uses
(``tools/CONTRACT.md``). Checkpoint updates the live RunDir transport; finish
writes the internal completion JSON that the daemon runner validates again
before a backend may mark a run done.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel._fsutil import atomic_write_json
from lingtai.kernel.base_agent.messaging import _enqueue_system_notification
from lingtai.tools.daemon.run_dir import DaemonRunDir

from .._results import json_tool_result as _tool_result
from .._results import unknown_tool_error as _unknown_tool

STATUSES = {"done", "failed", "incomplete"}

_FINISH_ACTIONS = ("finish",)
_CHECKPOINT_ACTIONS = ("checkpoint",)
_MAX_CHECKPOINT_STATE_CHARS = 128
_MAX_CHECKPOINT_SUMMARY_CHARS = 4_000
_MAX_CHECKPOINT_DETAIL_CHARS = 4_000
_MAX_CHECKPOINT_ARTIFACTS = 20
_MAX_CHECKPOINT_ARTIFACT_CHARS = 1_024

_FINISH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": sorted(STATUSES),
            "description": "Terminal daemon status: done, failed, or incomplete.",
        },
        "summary": {
            "type": "string",
            "description": "Short result summary for the parent agent.",
        },
        "reason": {
            "type": "string",
            "description": "Required when status is failed or incomplete.",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional run-dir-relative or absolute artifact paths.",
        },
    },
    "required": ["status"],
    "additionalProperties": False,
}

FINISH_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_FINISH_ACTIONS),
            "description": "Required operation. Must be 'finish'.",
        },
        "input": {
            "description": "Strict action-specific input; re-validated at dispatch.",
            **_FINISH_INPUT_SCHEMA,
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why you are calling this tool (recorded in your diary).",
        },
    },
    "required": ["action", "input", "reasoning"],
    "additionalProperties": False,
}

_CHECKPOINT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "state": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_CHECKPOINT_STATE_CHARS,
            "description": "Short nonterminal worker state, for example implementing or validating.",
        },
        "summary": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MAX_CHECKPOINT_SUMMARY_CHARS,
            "description": "Bounded progress summary for the parent agent.",
        },
        "artifacts": {
            "type": "array",
            "maxItems": _MAX_CHECKPOINT_ARTIFACTS,
            "items": {"type": "string", "minLength": 1, "maxLength": _MAX_CHECKPOINT_ARTIFACT_CHARS},
            "description": "Optional artifact path labels; contents are never read into the notification.",
        },
        "blocker": {
            "type": "string",
            "maxLength": _MAX_CHECKPOINT_DETAIL_CHARS,
            "description": "Optional current blocker.",
        },
        "request": {
            "type": "string",
            "maxLength": _MAX_CHECKPOINT_DETAIL_CHARS,
            "description": "Optional request for the parent to answer at a later checkpoint.",
        },
    },
    "required": ["state", "summary"],
    "additionalProperties": False,
}

CHECKPOINT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": list(_CHECKPOINT_ACTIONS),
            "description": "Required operation. Must be 'checkpoint'.",
        },
        "input": {
            "description": "Strict action-specific input; re-validated at dispatch.",
            **_CHECKPOINT_INPUT_SCHEMA,
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why you are calling this tool (recorded in your diary).",
        },
    },
    "required": ["action", "input", "reasoning"],
    "additionalProperties": False,
}

DESCRIPTION = (
    "Finish this LingTai daemon run. Call exactly once before your final answer: "
    "finish(action='finish', input={'status': ...}, reasoning='...'). "
    "Use status='done' only when the task is complete; use status='failed' or "
    "status='incomplete' when blocked, unvalidated, or unable to finish."
)

CHECKPOINT_DESCRIPTION = (
    "Report bounded nonterminal progress and cooperatively receive parent messages. "
    "Call checkpoint(action='checkpoint', input={'state': ..., 'summary': ...}, "
    "reasoning='...') at useful task boundaries; this does not finish the run."
)


def _completion_path() -> Path:
    raw = os.environ.get("LINGTAI_DAEMON_COMPLETION_FILE")
    if not raw:
        raise RuntimeError("missing LINGTAI_DAEMON_COMPLETION_FILE")
    return Path(raw)


def _run_dir_from_env() -> DaemonRunDir:
    raw = os.environ.get("LINGTAI_DAEMON_RUN_DIR")
    if not raw:
        raise RuntimeError("missing LINGTAI_DAEMON_RUN_DIR")
    run_dir = DaemonRunDir.attach(Path(raw))
    expected = os.environ.get("LINGTAI_DAEMON_RUN_ID")
    if not expected:
        raise RuntimeError("missing LINGTAI_DAEMON_RUN_ID")
    if run_dir.run_id != expected:
        raise RuntimeError("daemon run identity mismatch")
    return run_dir


def _reasoning(arguments: dict[str, Any], tool: str) -> str:
    # A real MCP call carries ``reasoning`` verbatim. A same-process dispatch
    # goes through ``kernel.tool_executor``, which extracts it and re-adds
    # ``_reasoning``. This mirrors ToolFamily's generic envelope tolerance.
    reasoning = arguments.get("reasoning", arguments.get("_reasoning"))
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("reasoning is required")
    if set(arguments) - {"action", "input", "reasoning", "_reasoning"}:
        raise ValueError(f"unsupported {tool} argument")
    return reasoning


def _validate_finish(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("finish arguments must be an object")
    action = arguments.get("action")
    if action not in _FINISH_ACTIONS:
        raise ValueError(f"action must be one of: {', '.join(_FINISH_ACTIONS)}")
    input_ = arguments.get("input")
    if not isinstance(input_, dict):
        raise ValueError("input must be an object")
    _reasoning(arguments, "finish")

    status = input_.get("status")
    if status not in STATUSES:
        raise ValueError("status must be one of: done, failed, incomplete")
    summary = input_.get("summary")
    reason = input_.get("reason")
    artifacts = input_.get("artifacts")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("summary must be a string")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string")
    if artifacts is not None and (
        not isinstance(artifacts, list)
        or not all(isinstance(item, str) for item in artifacts)
    ):
        raise ValueError("artifacts must be an array of strings")
    if status in {"failed", "incomplete"} and not (reason and reason.strip()):
        raise ValueError("reason is required for failed or incomplete status")
    payload = {
        "schema": "lingtai.daemon_completion.v1",
        "status": status,
        "run_id": os.environ.get("LINGTAI_DAEMON_RUN_ID"),
    }
    if summary is not None:
        payload["summary"] = summary
    if reason is not None:
        payload["reason"] = reason
    if artifacts is not None:
        payload["artifacts"] = artifacts
    return payload


def _bounded_string(value: Any, field: str, maximum: int, *, required: bool) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{field} must be a nonempty string" if required else f"{field} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return value


def _validate_checkpoint(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("checkpoint arguments must be an object")
    if arguments.get("action") not in _CHECKPOINT_ACTIONS:
        raise ValueError("action must be checkpoint")
    input_ = arguments.get("input")
    if not isinstance(input_, dict):
        raise ValueError("input must be an object")
    _reasoning(arguments, "checkpoint")
    if set(input_) - {"state", "summary", "artifacts", "blocker", "request"}:
        raise ValueError("unsupported checkpoint input")

    state = _bounded_string(input_.get("state"), "state", _MAX_CHECKPOINT_STATE_CHARS, required=True)
    summary = _bounded_string(input_.get("summary"), "summary", _MAX_CHECKPOINT_SUMMARY_CHARS, required=True)
    artifacts = input_.get("artifacts")
    if artifacts is not None:
        if (
            not isinstance(artifacts, list)
            or len(artifacts) > _MAX_CHECKPOINT_ARTIFACTS
            or not all(
                isinstance(item, str)
                and bool(item)
                and len(item) <= _MAX_CHECKPOINT_ARTIFACT_CHARS
                for item in artifacts
            )
        ):
            raise ValueError("artifacts must be a bounded array of nonempty strings")
    blocker = _bounded_string(
        input_.get("blocker"), "blocker", _MAX_CHECKPOINT_DETAIL_CHARS, required=False
    )
    request = _bounded_string(
        input_.get("request"), "request", _MAX_CHECKPOINT_DETAIL_CHARS, required=False
    )
    payload: dict[str, Any] = {"state": state, "summary": summary}
    if artifacts is not None:
        payload["artifacts"] = artifacts
    if blocker is not None:
        payload["blocker"] = blocker
    if request is not None:
        payload["request"] = request
    return payload


class _CheckpointNotifier:
    """Small process-local adapter for the canonical system-event mutator."""

    def __init__(self, parent_workdir: Path):
        self._notification_store = PosixNotificationStoreAdapter(parent_workdir)

    def _log(self, *_args, **_kwargs) -> None:
        return None

    def _wake_nap(self) -> None:
        return None


def _record_checkpoint(run_dir: DaemonRunDir, arguments: dict[str, Any]) -> dict:
    payload = _validate_checkpoint(arguments)
    recorded = run_dir.record_checkpoint(payload)
    checkpoint = recorded["checkpoint"]
    sequence = checkpoint["sequence"]
    post_record_failure = recorded.get("post_record_failure")
    if post_record_failure:
        operation = post_record_failure["operation"]
        return {
            "status": "error",
            "error": (
                f"checkpoint {sequence} recorded but {operation} failed: "
                f"{post_record_failure['error']}"
            ),
            "error_type": post_record_failure["error_type"],
            "checkpoint_recorded": True,
            "checkpoint_sequence": sequence,
            "messages": recorded["messages"],
            "message": f"daemon checkpoint recorded but {operation} failed",
        }
    key = f"daemon-checkpoint:{run_dir.run_id}:{sequence}"
    body = (
        f"Daemon {run_dir.run_id} checkpoint {sequence} "
        f"(nonterminal; state={checkpoint['state']}): {checkpoint['summary']}"
    )
    if checkpoint.get("blocker"):
        body += f"\nBlocker: {checkpoint['blocker']}"
    try:
        _enqueue_system_notification(
            _CheckpointNotifier(run_dir.path.parent.parent),
            source="daemon",
            ref_id=run_dir.run_id,
            body=body,
            idempotency_key=key,
            skip_if_idempotency_key_exists=True,
            extra={
                "kind": "daemon_checkpoint",
                "terminal": False,
                "checkpoint_sequence": sequence,
                "checkpoint_state": checkpoint["state"],
            },
        )
    except Exception as exc:
        return {
            "status": "error",
            "error": f"checkpoint {sequence} recorded but parent wake failed: {exc}",
            "error_type": type(exc).__name__,
            "checkpoint_recorded": True,
            "checkpoint_sequence": sequence,
            "messages": recorded["messages"],
            "message": "daemon checkpoint recorded but parent wake failed",
        }
    return {
        "status": "ok",
        "checkpoint_sequence": sequence,
        "messages": recorded["messages"],
        "message": "daemon checkpoint recorded",
    }


def build_server() -> Server:
    async def _list_tools(
        _ctx: ServerRequestContext,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="checkpoint",
                    description=CHECKPOINT_DESCRIPTION,
                    input_schema=CHECKPOINT_SCHEMA,
                ),
                types.Tool(
                    name="finish",
                    description=DESCRIPTION,
                    input_schema=FINISH_SCHEMA,
                ),
            ],
        )

    async def _call_tool(
        _ctx: ServerRequestContext,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        if params.name not in {"checkpoint", "finish"}:
            raise _unknown_tool(params.name)
        try:
            if params.name == "checkpoint":
                result = _record_checkpoint(
                    _run_dir_from_env(), params.arguments or {}
                )
            else:
                payload = _validate_finish(params.arguments or {})
                path = _completion_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(path, payload, ensure_ascii=False, indent=2)
                result = {
                    "status": "ok",
                    "completion_status": payload["status"],
                    "message": "daemon completion recorded",
                }
        except Exception as e:
            result = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
        return _tool_result(result)

    server: Server = Server(
        "lingtai-daemon-common",
        instructions=(
            "Use `checkpoint` at useful nonterminal task boundaries to report "
            "bounded progress and cooperatively receive queued parent messages. "
            "Use `finish` exactly once to complete the daemon run; do not rely "
            "on final text alone."
        ),
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )
    return server


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
