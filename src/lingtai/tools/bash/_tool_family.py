"""ToolFamily envelope for the canonical ``shell`` tool.

The public/model-facing ``shell`` tool is migrated to the LingTai Tool
Protocol v2 action-separated shape (``action``/``input``/``reasoning``/
``summarize``, see ``../CONTRACT.md``) using the generic, optional
``tool_family`` infrastructure (``../tool_family/__init__.py``). ``run``,
``poll``, ``cancel``, and the reserved ``manual`` become four ``ChildTool``s
with their own strict per-action ``input`` schemas — run-only fields
(``command``, ``timeout``, ``working_dir``, ``async``, ``reminder``) live only
in ``run``'s branch; ``job_id`` lives only in ``poll``/``cancel``'s branches.

``ShellManager`` (``__init__.py``) is the unchanged execution engine: its
``handle()`` still accepts the historical flat legacy shape
(``{"action": "run", "command": ..., ...}``, ``action`` defaulting to
``"run"``) and its full async lifecycle (leases, durable state, reminders,
completion notifications, cancellation) is untouched — this module only
translates the new envelope into that flat shape before delegating, so every
existing ``ShellManager`` test keeps exercising the exact same code path. That
flat shape is internal only — this module owns the package's single public
``get_schema``/``get_description`` pair, re-exported from ``__init__.py``.
This is the same division ``web`` uses between
``ToolFamily.handle()`` (envelope validation/dispatch) and its own
Host/presentation adaptation, applied in the opposite direction: here the
*inner* engine is the legacy shape and the *outer* envelope is new.
"""
from __future__ import annotations

import math
import os
from typing import Any, Mapping

from ..tool_family import ChildTool, ToolFamily
from ..tool_family.manual import build_manual_child
from ._shell_dialect import ShellKind

_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_ASYNC_REMINDER_SECONDS = 1800.0

# Hard ceiling for the sync ``run`` ``timeout`` parameter (Jason 2026-08-10
# tool-timeout redesign).  The default timeout stays 30s; a tool call may set
# ``timeout`` at most to this cap; work that needs more must be launched with
# ``async=true``.  The cap is an environment variable (not an arbitrary
# per-config value), so it is enforced uniformly and can be tuned without
# touching the schema.
TIMEOUT_MAX_ENV = "LINGTAI_TOOL_TIMEOUT_MAX_SECONDS"
_DEFAULT_TIMEOUT_MAX_SECONDS = 120.0


def resolve_timeout_max_seconds(environ: Mapping[str, str] | None = None) -> float:
    """Resolve the hard sync-timeout ceiling from the environment.

    Reads ``LINGTAI_TOOL_TIMEOUT_MAX_SECONDS``; missing, empty, non-numeric,
    non-positive, or non-finite values fall back to ``120.0``.  The returned
    ceiling is never below the default sync timeout (30), so an operator who
    sets the variable to e.g. 10 does not disable every default sync run
    (fable r1 BLOCKING-1).  Reads at each ``run`` call (no restart required),
    mirroring the Nudge policy controls.
    """
    raw = (os.environ if environ is None else environ).get(TIMEOUT_MAX_ENV)
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_MAX_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_MAX_SECONDS
    if not math.isfinite(value) or value <= 0:
        return _DEFAULT_TIMEOUT_MAX_SECONDS
    return max(value, float(_DEFAULT_TIMEOUT_SECONDS))

RUN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The shell command to execute",
        },
        "timeout": {
            "type": ["number", "null"],
            "description": (
                "Timeout in seconds, or null for the default 30. Only for sync "
                "execution. Hard ceiling: "
                f"{TIMEOUT_MAX_ENV} (default {_DEFAULT_TIMEOUT_MAX_SECONDS:g}; "
                "the effective ceiling is read from the environment at call "
                "time, floored at 30); a value above the ceiling is refused \u2014 "
                "use async=true instead for work that may need longer."
            ),
            "default": _DEFAULT_TIMEOUT_SECONDS,
        },
        "working_dir": {
            "type": ["string", "null"],
            "description": (
                "Working directory for the command; use null (or an empty "
                "string) to use the agent working directory. Must be inside "
                "the agent working directory sandbox; paths outside it are "
                "rejected. For external repos/paths, keep working_dir at the "
                "agent dir and put an explicit cd in command, e.g. "
                "cd /absolute/path && ..."
            ),
        },
        "async": {
            "type": ["boolean", "null"],
            "description": (
                "Run command in background and return immediately with a "
                "job_id, or null for the default false."
            ),
            "default": False,
        },
        "reminder": {
            "type": ["number", "null"],
            "description": (
                "Last-resort async wake delay in seconds, or null for the "
                "default 1800. Only "
                "meaningful when async is true: if the job is still non-terminal "
                "when the durable deadline expires, publish a system "
                "notification reminding you to poll it; exact completion "
                "suppresses this stale watchdog and publishes the shell "
                "completion wake instead."
            ),
            "default": _DEFAULT_ASYNC_REMINDER_SECONDS,
        },
    },
    # Strict OpenAI schemas express an optional field as a REQUIRED nullable
    # property (the same convention ``web``'s ``browse`` child uses): every
    # run-only field is listed here, and null means "absent" to
    # ``ShellManager``, which then applies its own unchanged runtime default
    # (timeout 30, working_dir = the agent sandbox, async false, reminder
    # 1800). ``command`` is the one genuinely required field and is
    # non-nullable.
    "required": ["command", "timeout", "working_dir", "async", "reminder"],
    "additionalProperties": False,
}

POLL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Job ID for the poll action (returned by an async run)",
        },
    },
    "required": ["job_id"],
    "additionalProperties": False,
}

CANCEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {
            "type": "string",
            "description": "Job ID for the cancel action (returned by an async run)",
        },
    },
    "required": ["job_id"],
    "additionalProperties": False,
}

# Sourced from the shared reserved-``manual`` builder rather than re-declared,
# so the schema the wire advertises and the schema the dispatcher validates
# against are the same object by construction and cannot drift apart.
MANUAL_INPUT_SCHEMA: dict[str, Any] = dict(
    build_manual_child(None, "shell").input_schema
)


def get_description(
    lang: str = "en",
    dialect: str = "posix",
    host_os: str | None = None,
    shell_kind: "ShellKind | str | None" = None,
) -> str:
    host = f" Host OS: {host_os}." if host_os else ""
    kind = ShellKind.coerce(shell_kind) or ShellKind.coerce(dialect)
    shell_prose = (
        f" Active shell: {kind.display_name}. {kind.sequencing_guidance}"
        if kind is not None else ""
    )
    return (
        f"Execute a shell command and return stdout/stderr. Active shell dialect: {dialect}.{shell_prose}{host} "
        "The dialect and host OS are detected at setup time; calls cannot choose them. Any system program — scripts, git, curl, pip, data pipelines. "
        "shell(action='run', input={'command': '...'}, reasoning='...') executes a command; "
        "shell(action='poll', input={'job_id': '...'}, reasoning='...') checks an async job; "
        "shell(action='cancel', input={'job_id': '...'}, reasoning='...') kills an async job; "
        "shell(action='manual', input={}, reasoning='...') returns the installed shell-manual skill. "
        "Returns exit_code, stdout, stderr, plus ok (bool) and command_status ('success'/'failed'). IMPORTANT: top-level status stays 'ok' even when the command FAILS — it only means the shell ran. "
        "Always check exit_code/ok and read the warning field (it names nonzero exits, Python tracebacks, and missing modules); never assume success from status alone. "
        "Avoid broad recursive scans (find … -name, rglob, os.walk, glob('**')) — they time out; prefer `rg --files`. Parse JSONL line-by-line, not as one JSON blob. "
        "Supports async mode (input.async=true → job_id, then poll/cancel). Before ordinary shell work, read the manual — it covers async hygiene and advanced usage; no exceptions. "
        "On Windows, sync runs are contained in a kill-on-close Job Object: a background process started by a sync command (e.g. Start-Process with redirected output) is terminated when the command completes — use input.async=true for work that must outlive the command."
    )


def _schema_only_family() -> ToolFamily:
    # A throwaway ``ToolFamily`` composing only the model-facing schema — the
    # fixed four-child registry (no duplicate/reserved-name collision) is
    # proven once, at import time, exactly as ``web_search`` does. The real
    # per-agent dispatcher below builds its own ``ToolFamily`` with handlers
    # bound to a live ``ShellManager`` instance.
    def _unused(_input: Mapping[str, Any]) -> dict[str, Any]:
        raise AssertionError("the module-level schema-only ToolFamily never dispatches")

    return ToolFamily(
        "shell",
        [
            ChildTool("run", RUN_INPUT_SCHEMA, _unused, title="run input"),
            ChildTool("poll", POLL_INPUT_SCHEMA, _unused, title="poll input"),
            ChildTool("cancel", CANCEL_INPUT_SCHEMA, _unused, title="cancel input"),
            ChildTool("manual", MANUAL_INPUT_SCHEMA, _unused, title="manual input"),
        ],
    )


_FAMILY = _schema_only_family()


def get_schema(lang: str = "en") -> dict[str, Any]:
    """Compose the action-separated public ``shell`` schema.

    Generated purely from ``RUN_INPUT_SCHEMA``/``POLL_INPUT_SCHEMA``/
    ``CANCEL_INPUT_SCHEMA``/``MANUAL_INPUT_SCHEMA`` by the generic
    ``ToolFamily`` infra (root ``allOf`` correlation plus ``input.oneOf``
    disclosure) — this is the schema registered for the public ``shell``
    tool, and the only one the package defines. It is re-exported from
    ``bash/__init__.py`` as that package's canonical ``get_schema``.
    """
    return _FAMILY.build_schema()


class ShellFamilyDispatcher:
    """Adapts the ``action``/``input`` envelope to ``ShellManager``'s legacy flat call shape.

    Built per-agent, bound to one live ``ShellManager``. ``handle()`` is the
    public ``shell`` tool's registered handler: ``ToolFamily.handle()``
    validates the envelope and dispatches to exactly one of the four
    ``ChildTool`` handlers below, each of which flattens its own validated
    ``input`` mapping (injecting the matching ``action`` key, mirroring the
    legacy dispatch contract in ``ShellManager.handle``) and calls
    ``ShellManager.handle()`` unchanged. ``manual`` is registered directly,
    unwrapped, from ``build_manual_child`` — the shared reserved-name
    contract every LingTai family uses — so it returns the canonical
    ``content``/``structuredContent`` shape. It is the family's sole manual
    surface: ``ShellManager.handle`` has no ``action="manual"`` branch, so the
    engine never serves documentation and ``manual`` performs no shell
    operation.
    """

    def __init__(self, manager: Any, agent: Any) -> None:
        self._manager = manager
        self._family = ToolFamily(
            "shell",
            [
                ChildTool("run", RUN_INPUT_SCHEMA, self._dispatch_run, title="run input"),
                ChildTool("poll", POLL_INPUT_SCHEMA, self._dispatch_poll, title="poll input"),
                ChildTool("cancel", CANCEL_INPUT_SCHEMA, self._dispatch_cancel, title="cancel input"),
                build_manual_child(agent, "shell"),
            ],
        )

    @staticmethod
    def _strip_nulls(action_input: Mapping[str, Any]) -> dict[str, Any]:
        # Strict schemas express optional fields as required nullable
        # properties (see ``RUN_INPUT_SCHEMA``'s ``required`` note, and
        # ``web``'s identical ``_strip_nulls``). Null means *absent* to
        # ``ShellManager``, which then applies its own unchanged runtime
        # default — dropping the key is what makes ``args.get("timeout", 30)``
        # / ``args.get("working_dir") or self._working_dir`` behave exactly as
        # they did for a legacy flat caller that simply omitted the field. A
        # falsy-but-present value (``timeout: 0``, ``async: False``,
        # ``working_dir: ""``) is preserved verbatim, never dropped.
        return {key: value for key, value in action_input.items() if value is not None}

    def _dispatch_run(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        flat = self._strip_nulls(action_input)
        flat["action"] = "run"
        flat.setdefault("command", "")
        return self._manager.handle(flat)

    def _dispatch_poll(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        return self._manager.handle({"action": "poll", "job_id": action_input.get("job_id", "")})

    def _dispatch_cancel(self, action_input: Mapping[str, Any]) -> dict[str, Any]:
        return self._manager.handle({"action": "cancel", "job_id": action_input.get("job_id", "")})

    def handle(self, args: Mapping[str, Any] | None) -> dict[str, Any]:
        # ``ToolFamily.handle`` validates the envelope, strips/type-checks
        # root ``summarize``, rejects unknown root fields and cross-action
        # ``input`` keys, then returns the selected child's own raw canonical
        # result verbatim — no double wrap, no Host envelope. The one Host
        # normalization below narrows the generic dispatcher's action list to
        # Shell's exact four; the generic canonical error shape
        # (``status``/``error_code``/``message``) is left untouched.
        result = self._family.handle(args)
        if result.get("error_code") == "ACTION_REQUIRED":
            result["message"] = "action must be one of run, poll, cancel, or manual"
        return result
