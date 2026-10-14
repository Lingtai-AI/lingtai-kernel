"""Claude Agent SDK adapter foundation — staged, not yet the production
runner for ``claude-p`` / ``claude-code``.

This module introduces the first testable slice of the official
``claude_agent_sdk`` adapter: ``backend_options`` argv-to-SDK-kwargs
translation, redaction-safe MCP registration translation, usage
normalization, the verified-human-CLI resolver, ``ClaudeAgentOptions``
construction, and single-turn message draining. It is deliberately NOT
wired into any backend dispatcher, backend-selection table, or preflight
path yet, and it changes no observable daemon behavior: the live
``claude-p``/``claude-code`` implementation remains the existing
``claude --print --output-format stream-json`` subprocess parse loop
(``_run_claude_code_emanation`` in ``daemon/__init__.py``, gated by the
unrelated reserved-flag blocklist ``_validate_claude_backend_argv``). That
parser is not retired by this change — a later change atomically connects
this module's functions to a real run loop and backend-argv preflight, and
retires the old parser and its backend-specific tests in the same change.
See ``daemon/CONTRACT.md`` Capability Invariant 4 and ``daemon/ANATOMY.md``
for the staging plan.

``_resolve_cli_path`` always resolves the verified human-installed ``claude``
CLI from ``PATH``; the SDK's own ``cli_path=None`` default (which would
silently prefer its own bundled binary) is never used.
``_build_options``/``claude_sdk_options_from_argv`` keep three distinct
surfaces separate: LingTai's ``tasks[].tools`` capability names (not handled
here), the ``ClaudeAgentOptions.tools`` native-tool whitelist (an explicit
``[]`` means "no native tools" and is preserved verbatim, distinct from an
absent/``None`` key meaning "unrestricted"), and ``allowed_tools``
auto-approval (not a restriction, not produced by this translator). MCP
registrations are mounted stdio-only and built in memory from the run
capsule — never through a native config file on disk — with
``strict_mcp_config=True``; backend ``env`` overrides and ``resume`` are
passed through explicitly, and the SDK transport merges those overrides
into — rather than replacing — the ambient process environment it retains.

``_drain_turn`` consumes exactly one logical turn off an already-connected,
already-queried SDK client: it captures ``claude_session_id`` from the
earliest message that carries one (for later session persistence), records
assistant text and tool-use dispatch, and — because the real wire delivers
``ToolResultBlock`` wrapped inside a ``UserMessage`` rather than nested in
``AssistantMessage.content`` — clears pending-tool bookkeeping from that
``UserMessage`` branch. A human-origin ``ResultMessage``
(``origin is None or origin["kind"] == "human"``) is returned to the caller
as that turn's answer; an injected background-notification ``ResultMessage``
is recorded but never returned as if it were the pending query's completion,
and draining continues within the same call. ``_normalize_usage`` normalizes
``ResultMessage.usage`` to UI-only totals (never the kernel token ledger),
matching the still-active stream-json normalizer's shape and cached-token
folding.

Excluded from this staged module entirely: the interrupt poller, the
persistent multi-turn run loop, non-terminal turn notifications, and the
emanate/resume runner wrappers. Those land with the atomic production
cutover.
"""
from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lingtai.tools.daemon.run_dir import DaemonRunDir

# claude-agent-sdk's ``cli_path=None`` default looks for a bundled CLI before
# falling back to ``PATH``. The daemon contract requires the verified human
# CLI; never let the SDK silently select its own bundled binary.
_CLI_NOT_FOUND_MESSAGE = (
    "'claude' CLI not found on PATH (required for the claude-p/claude-code "
    "daemon backend; the SDK's own bundled CLI is never used)"
)

#: Flags this backend's option translator (``claude_sdk_options_from_argv``)
#: accepts; each maps 1:1 to a ``ClaudeAgentOptions`` field. Everything else
#: — including harness-owned flags like ``--print``/``--mcp-config``/
#: ``--system-prompt``/``--allowed-tools`` — is meant to be rejected before
#: any run directory is created, by a future preflight call site. That call
#: site does not exist yet: today's live ``claude-p``/``claude-code``
#: backend_argv gate is the unrelated reserved-flag blocklist
#: ``_validate_claude_backend_argv`` in ``daemon/__init__.py``, which this
#: list is not yet connected to.
SUPPORTED_SDK_FLAGS = (
    "--model",
    "--fallback-model",
    "--effort",
    "--max-budget-usd",
    "--add-dir",
    "--disallowed-tools",
)

_EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


class ClaudeTurnError(RuntimeError):
    """A completed SDK turn reported ``is_error``; carries the raw result."""

    def __init__(self, text: str, terminal_reason: str | None):
        detail = text or "claude-p turn ended in an error result"
        if terminal_reason:
            detail = f"{detail} (terminal_reason={terminal_reason})"
        super().__init__(detail)
        self.text = text
        self.terminal_reason = terminal_reason


#: Carries an explicit native tool whitelist (including an explicit empty
#: list, meaning "no native tools") through the ``backend_argv`` channel.
#: Reserved for a future ``_handle_emanate_cli`` injection of
#: ``backend_options.tools`` before the generic argv converter runs; that
#: call site does not exist yet in this staged module, so there is no
#: current caller to point to.
_NATIVE_TOOLS_SENTINEL = "__lingtai_claude_tools"


def claude_sdk_options_from_argv(argv: list[str]) -> dict[str, Any]:
    """Translate ``backend_argv`` tokens into typed SDK kwargs.

    Self-validating: raises ``ValueError`` for any flag outside
    ``SUPPORTED_SDK_FLAGS`` (or the native-tools sentinel), a flag missing
    its value, or a malformed value (e.g. a bad ``effort`` or non-numeric
    ``max-budget-usd``); repeated flags (``--add-dir``,
    ``--disallowed-tools``) accumulate into lists. No caller passes this
    function pre-validated ``backend_argv`` yet — this staged module has no
    preflight call site — so today this is the only gate on these values.
    """
    import json as _json

    options: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        flag = argv[i]
        if flag == _NATIVE_TOOLS_SENTINEL:
            if i + 1 >= len(argv):
                raise ValueError(f"{_NATIVE_TOOLS_SENTINEL} is missing its value")
            try:
                decoded = _json.loads(argv[i + 1])
            except ValueError as exc:
                raise ValueError("backend_options.tools sentinel is not valid JSON") from exc
            if not isinstance(decoded, list) or not all(isinstance(t, str) for t in decoded):
                raise ValueError("backend_options.tools must decode to an array of strings")
            options["tools"] = decoded
            i += 2
            continue
        if flag not in SUPPORTED_SDK_FLAGS:
            raise ValueError(f"unsupported claude-p backend_options flag: {flag}")
        if i + 1 >= len(argv):
            raise ValueError(f"backend_options flag {flag} is missing its value")
        value = argv[i + 1]
        i += 2
        if flag == "--model":
            options["model"] = value
        elif flag == "--fallback-model":
            options["fallback_model"] = value
        elif flag == "--effort":
            if value not in _EFFORT_LEVELS:
                raise ValueError(
                    f"backend_options.effort must be one of {sorted(_EFFORT_LEVELS)}, got {value!r}"
                )
            options["effort"] = value
        elif flag == "--max-budget-usd":
            try:
                options["max_budget_usd"] = float(value)
            except ValueError as exc:
                raise ValueError(
                    f"backend_options.max_budget_usd must be a number, got {value!r}"
                ) from exc
        elif flag == "--add-dir":
            options.setdefault("add_dirs", []).append(value)
        elif flag == "--disallowed-tools":
            options.setdefault("disallowed_tools", []).append(value)
    return options


def _mcp_servers_from_registrations(registrations: list[dict]) -> dict[str, dict]:
    """Translate redaction-safe MCP registrations into ``mcp_servers`` dict.

    Matches the existing backend-support matrix for claude-p: stdio only, HTTP
    omitted (not a verified native loader). No ``daemon_common`` entry is ever
    added here — this backend mounts none.
    """
    servers: dict[str, dict] = {}
    for reg in registrations:
        if not isinstance(reg, dict):
            continue
        if reg.get("transport", reg.get("type", "stdio")) != "stdio":
            continue
        name = reg.get("name")
        command = reg.get("command")
        if not isinstance(name, str) or not name or not isinstance(command, str):
            continue
        server: dict[str, Any] = {"type": "stdio", "command": command}
        args = reg.get("args")
        if isinstance(args, list):
            server["args"] = list(args)
        env = reg.get("env")
        if isinstance(env, dict):
            server["env"] = dict(env)
        servers[name] = server
    return servers


def _normalize_usage(usage: dict | None) -> dict | None:
    """Normalize ``ResultMessage.usage`` to UI totals (never the token ledger).

    Same shape and the same cached-token folding as the still-active
    stream-json normalizer (``_normalize_claude_usage`` in
    ``daemon/__init__.py``): the SDK's ``usage`` dict uses the identical key
    names.
    """
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if (
        not isinstance(input_tokens, int) or isinstance(input_tokens, bool)
        or not isinstance(output_tokens, int) or isinstance(output_tokens, bool)
    ):
        return None
    cache_read = usage.get("cache_read_input_tokens", 0)
    cache_creation = usage.get("cache_creation_input_tokens", 0)
    for value in (cache_read, cache_creation):
        if not isinstance(value, int) or isinstance(value, bool):
            return None
    return {
        "input": input_tokens,
        "output": output_tokens,
        "cached": cache_read + cache_creation,
        "thinking": 0,
    }


def _is_human_origin(origin: object) -> bool:
    if origin is None:
        return True
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    return getattr(origin, "kind", None) == "human"


def _resolve_cli_path() -> str:
    """Resolve the verified installed human Claude CLI; never the SDK bundle."""
    path = shutil.which("claude")
    if not path:
        raise FileNotFoundError(_CLI_NOT_FOUND_MESSAGE)
    return path


def _build_options(
    *,
    cwd: str,
    cli_path: str,
    mcp_servers: dict[str, dict],
    sdk_options: dict[str, Any],
    backend_env: dict[str, str] | None,
    resume: str | None = None,
):
    from claude_agent_sdk import ClaudeAgentOptions

    return ClaudeAgentOptions(
        cwd=cwd,
        cli_path=cli_path,
        permission_mode="bypassPermissions",
        system_prompt={"type": "preset", "preset": "claude_code"},
        mcp_servers=mcp_servers,
        strict_mcp_config=True,
        env=dict(backend_env or {}),
        model=sdk_options.get("model"),
        fallback_model=sdk_options.get("fallback_model"),
        effort=sdk_options.get("effort"),
        max_budget_usd=sdk_options.get("max_budget_usd"),
        add_dirs=list(sdk_options.get("add_dirs") or []),
        disallowed_tools=list(sdk_options.get("disallowed_tools") or []),
        # Native tool whitelist (``ClaudeAgentOptions.tools``) — distinct
        # from both LingTai's `tasks[].tools` (capability names) and
        # `allowed_tools` (auto-approval, not a restriction).
        # ``sdk_options.get("tools")`` is `None` when the caller never
        # passed `backend_options.tools` (unrestricted, today's default
        # behavior); an explicit `[]` is preserved verbatim and disables
        # every native tool, per `claude_sdk_options_from_argv`'s sentinel
        # decoding.
        tools=sdk_options.get("tools"),
        resume=resume,
    )


def _capture_session_id(run_dir: "DaemonRunDir", session_id: object) -> None:
    if isinstance(session_id, str) and session_id:
        run_dir.set_session_id('claude_session_id', session_id, overwrite=True)


async def _drain_turn(client, run_dir: "DaemonRunDir", pending_tools: dict[str, str]) -> Any:
    """Consume messages for exactly one logical turn.

    Returns the ``ResultMessage`` that ends a human-origin turn (``None`` if
    the stream ends first). An injected turn's ``ResultMessage`` is recorded
    (never treated as *the* answer to a pending query) and draining continues
    within the same call, so callers always get back the human-origin result
    they are waiting for, in order.

    Captures ``claude_session_id`` from the earliest message carrying it,
    usually SystemMessage/init. Live asks use the owned connection; terminal
    resume requires this ID to have been successfully persisted.
    """
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
    )

    async for message in client.receive_messages():
        if isinstance(message, SystemMessage):
            data = message.data if isinstance(message.data, dict) else {}
            _capture_session_id(run_dir, data.get("session_id"))
            if message.subtype == 'init':
                tools = data.get('tools')
                run_dir.append_event(
                    'daemon_claude_sdk_init',
                    session_id=data.get('session_id'), model=data.get('model'),
                    tools=[name[:128] for name in tools[:200] if isinstance(name, str)]
                    if isinstance(tools, list) else None,
                )
        elif isinstance(message, AssistantMessage):
            _capture_session_id(run_dir, message.session_id)
            for block in message.content:
                if isinstance(block, TextBlock):
                    if block.text.strip():
                        run_dir.record_cli_output(block.text, stream="stdout")
                elif isinstance(block, ThinkingBlock):
                    continue
                elif isinstance(block, ToolUseBlock):
                    pending_tools[block.id] = block.name
                    try:
                        run_dir.set_current_tool(block.name, block.input or {})
                    except Exception:
                        pass
                # ToolResultBlock never appears on AssistantMessage — the
                # real wire delivers it inside a UserMessage instead (the
                # synthesized "tool results" turn); see the UserMessage
                # branch below. Checking for it here would be dead code.
        elif isinstance(message, UserMessage):
            # The real SDK's ToolResultBlock arrives wrapped in a
            # UserMessage, not nested in AssistantMessage.content — a prior
            # revision of this module only checked the latter, so a tool
            # completion never cleared `current_tool` until the whole turn
            # ended. `UserMessage.content` is `str | list[ContentBlock]`;
            # only the list form can carry tool results.
            content = message.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        pending_tools.pop(block.tool_use_id, None)
                        try:
                            run_dir.clear_current_tool(
                                "error" if block.is_error else "ok"
                            )
                        except Exception:
                            pass
        elif isinstance(message, ResultMessage):
            _capture_session_id(run_dir, message.session_id)
            while pending_tools:
                pending_tools.popitem()
                try:
                    run_dir.clear_current_tool("ok")
                except Exception:
                    pass
            if _is_human_origin(message.origin):
                return message
            # Injected (task-notification) turn: record and keep draining.
            try:
                run_dir.record_cli_output(
                    f"[background notification] {message.result or ''}",
                    stream="stdout",
                )
            except Exception:
                pass
    return None


__all__ = [
    "SUPPORTED_SDK_FLAGS",
    "ClaudeTurnError",
    "claude_sdk_options_from_argv",
]
