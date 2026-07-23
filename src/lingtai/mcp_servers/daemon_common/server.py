"""LingTai daemon common MCP server.

The model-visible contract consists of ``finish`` and ``ask_human``. The JSON
files they write are internal daemon transports and are validated again by the
daemon runner before it changes daemon state.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from lingtai.kernel._fsutil import atomic_write_json

STATUSES = {"done", "failed", "incomplete"}

FINISH_SCHEMA = {
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

ASK_HUMAN_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "Question for the parent or human.",
        },
        "choices": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional short answer choices.",
        },
        "default": {"type": "string", "description": "Optional default answer."},
        "reason": {
            "type": "string",
            "description": "Optional explanation of why input is required.",
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

DESCRIPTION = (
    "Finish this LingTai daemon run. Call exactly once before your final answer. "
    "Use status='done' only when the task is complete; use status='failed' or "
    "status='incomplete' when blocked, unvalidated, or unable to finish."
)

ASK_HUMAN_DESCRIPTION = (
    "Pause this LingTai daemon run and request parent or human input. Use this "
    "when clarification is required instead of asking only in final prose. "
    "After calling it, stop work and end the current CLI turn; do not also "
    "call finish."
)


def _completion_path() -> Path:
    raw = os.environ.get("LINGTAI_DAEMON_COMPLETION_FILE")
    if not raw:
        raise RuntimeError("missing LINGTAI_DAEMON_COMPLETION_FILE")
    return Path(raw)


def _input_request_path() -> Path:
    raw = os.environ.get("LINGTAI_DAEMON_INPUT_REQUEST_FILE")
    if not raw:
        raise RuntimeError("missing LINGTAI_DAEMON_INPUT_REQUEST_FILE")
    return Path(raw)


def _validate_finish(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("finish arguments must be an object")
    status = arguments.get("status")
    if status not in STATUSES:
        raise ValueError("status must be one of: done, failed, incomplete")
    summary = arguments.get("summary")
    reason = arguments.get("reason")
    artifacts = arguments.get("artifacts")
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


def _validate_ask_human(arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("ask_human arguments must be an object")
    question = arguments.get("question")
    choices = arguments.get("choices")
    default = arguments.get("default")
    reason = arguments.get("reason")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if choices is not None and (
        not isinstance(choices, list)
        or not choices
        or not all(isinstance(item, str) and item.strip() for item in choices)
    ):
        raise ValueError("choices must be a non-empty array of non-empty strings")
    if default is not None and not isinstance(default, str):
        raise ValueError("default must be a string")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string")
    payload: dict[str, Any] = {
        "schema": "lingtai.daemon_input_request.v1",
        "run_id": os.environ.get("LINGTAI_DAEMON_RUN_ID"),
        "question": question.strip(),
    }
    if choices is not None:
        payload["choices"] = choices
    if default is not None:
        payload["default"] = default
    if reason is not None:
        payload["reason"] = reason
    return payload


def build_server() -> Server:
    server: Server = Server(
        "lingtai-daemon-common",
        instructions=(
            "Use `finish` to explicitly complete the daemon run. When blocked "
            "on clarification, use `ask_human` instead of asking in final text; "
            "then stop the current turn without calling `finish`."
        ),
    )

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="finish",
                description=DESCRIPTION,
                inputSchema=FINISH_SCHEMA,
            ),
            types.Tool(
                name="ask_human",
                description=ASK_HUMAN_DESCRIPTION,
                inputSchema=ASK_HUMAN_SCHEMA,
            ),
        ]

    @server.call_tool()
    async def _call_tool(
        name: str, arguments: dict[str, Any],
    ) -> list[types.TextContent]:
        if name not in {"finish", "ask_human"}:
            raise ValueError(f"unknown tool: {name!r}")
        try:
            if name == "finish":
                payload = _validate_finish(arguments or {})
                path = _completion_path()
            else:
                payload = _validate_ask_human(arguments or {})
                path = _input_request_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            result = {"status": "ok"}
            if name == "finish":
                result.update(
                    completion_status=payload["status"],
                    message="daemon completion recorded",
                )
            else:
                result.update(
                    state="waiting_input",
                    message="human input request recorded; stop this turn",
                )
        except Exception as e:
            result = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
        return [types.TextContent(
            type="text", text=json.dumps(result, ensure_ascii=False),
        )]

    return server


async def serve() -> None:
    server = build_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
