"""Claude Agent SDK runner: the one execution substrate for ``claude-p`` /
``claude-code``.

Replaces the retired ``claude --print --output-format stream-json`` subprocess
parse loop with the official ``claude_agent_sdk.ClaudeSDKClient``. Runs inside
the existing per-run detached execution child (see
``kernel/daemon_supervisor/CONTRACT.md`` and ``execution_host.py``); it owns no
process/control/notification machinery of its own — it drives one asyncio
event loop around one persistent SDK connection and reuses the existing
``DaemonRunDir`` hooks (``record_cli_output``, ``set_current_tool``,
``clear_current_tool``, ``record_cli_tokens``, ``set_session_id``,
``enqueue_live_followup`` / ``drain_followups_or_finalize``, ``mark_done`` /
``mark_failed`` / ``mark_cancelled`` / ``mark_timeout``) for persisted run truth,
so ``daemon(check)`` reuses the existing artifact/state projection.

Sequential, non-overlapping ``receive_messages()`` readers each drain one
query turn on the same connection. A human-origin ``ResultMessage``
(``origin is None or origin["kind"] == "human"``) closes the pending query;
an injected background-notification result is recorded, never matched as
that answer. Live ``ask`` acceptance uses atomic ``enqueue_live_followup``;
``drain_followups_or_finalize`` orders queue drain with terminal commit.
Queued messages reach ``client.query()`` at turn boundaries. An optional
interrupt request targets only the query window bounded by ``begin_live_turn``
and ``end_live_turn``. The per-turn poller calls ``client.interrupt()``;
acceptance is not immediate delivery, and ``daemon_interrupt_delivered``
proves the call ran. Between-turn asks stay queued, and poller teardown
completes before any successor query.

No ``daemon_common`` MCP is mounted for this backend: there is no mandatory
``finish``/``checkpoint`` tool. The model's ordinary reply ends the turn; the
per-turn ``ResultMessage`` (``is_error``, ``terminal_reason``, ``result``) is
the only completion signal, matching the authorized decision that ordinary
assistant replies plus parent-filtered events are sufficient acknowledgement.
"""
from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING, Any

from .runtime import mark_cancelled_or_timeout

if TYPE_CHECKING:
    from lingtai.tools.daemon.run_dir import DaemonRunDir

# Mirrors ``_CLAUDE_CODE_STRIP_ENV`` in ``daemon/__init__.py``. Kept as an
# independent literal (not a cross-import) so this module never participates
# in the package's `__init__` import cycle; the underlying auth-hygiene
# contract (GH #107 / GH Lingtai-AI/lingtai#189) is identical.
_CLAUDE_CODE_STRIP_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

# claude-agent-sdk's ``cli_path=None`` default looks for a bundled CLI before
# falling back to ``PATH``. The daemon contract requires the verified human
# CLI; never let the SDK silently select its own bundled binary.
_CLI_NOT_FOUND_MESSAGE = (
    "'claude' CLI not found on PATH (required for the claude-p/claude-code "
    "daemon backend; the SDK's own bundled CLI is never used)"
)

#: Flags this backend's option translator accepts. Values map 1:1 to
#: ``ClaudeAgentOptions`` fields; everything else (including harness-owned
#: flags such as ``--print``/``--mcp-config``/``--system-prompt``/
#: ``--allowed-tools``) is rejected at preflight, before any run directory is
#: created — see ``_validate_claude_sdk_backend_argv`` in ``daemon/__init__.py``.
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
#: Injected by ``_handle_emanate_cli`` from ``backend_options.tools`` before
#: the generic argv converter runs — see the comment at that call site for
#: why this cannot be a plain ``--tools`` flag through the generic path.
_NATIVE_TOOLS_SENTINEL = "__lingtai_claude_tools"


def claude_sdk_options_from_argv(argv: list[str]) -> dict[str, Any]:
    """Translate validated ``backend_argv`` tokens into typed SDK kwargs.

    ``argv`` has already passed ``_validate_claude_sdk_backend_argv`` (every
    token's flag is in ``SUPPORTED_SDK_FLAGS``, or is the native-tools
    sentinel); this function only parses values and accumulates repeated
    flags (``--add-dir``, ``--disallowed-tools``) into lists. Raises
    ``ValueError`` on a malformed value (e.g. a bad ``effort`` or non-numeric
    ``max-budget-usd``) so the whole batch fails at preflight, before any run
    directory exists.
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

    Same shape and the same cached-token folding as the retired stream-json
    normalizer (``_normalize_claude_usage`` in ``daemon/__init__.py``): the
    SDK's ``usage`` dict uses the identical key names.
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


async def _poll_live_interrupt(client, run_dir: "DaemonRunDir", stop: "asyncio.Event") -> None:
    """Poll accepted current-query interrupt requests without competing with
    the turn's `_drain_turn` reader. Runs only during one turn; teardown is
    awaited before any successor query. An ask accepted between turns stays
    queued and cannot target a successor; acceptance never guarantees that
    ``client.interrupt()`` will run immediately.

    ``daemon(ask, interrupt=true)``'s own response can only ever report that
    an interrupt was *requested* — the poller runs concurrently, up to 0.5s
    later, in a different call entirely. This records the one observable
    event that distinguishes an actual delivered ``client.interrupt()`` call
    (``daemon_interrupt_delivered``) from a request that was consumed but
    failed to send (``daemon_interrupt_delivery_failed``), using the
    existing bounded event log rather than a new control protocol.
    """
    while not stop.is_set():
        if run_dir.consume_live_interrupt_request():
            try:
                await client.interrupt()
            except Exception as exc:
                try:
                    run_dir.append_event(
                        "daemon_interrupt_delivery_failed",
                        error_type=type(exc).__name__,
                    )
                except Exception:
                    pass
                return
            try:
                run_dir.append_event("daemon_interrupt_delivered")
            except Exception:
                pass
            return
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.5)
        except asyncio.TimeoutError:
            pass


async def _drain_turn_with_interrupt(client, run_dir: "DaemonRunDir", pending_tools: dict) -> Any:
    """Run `_drain_turn` with a concurrent interrupt poller for its duration."""
    stop = asyncio.Event()
    poller = asyncio.create_task(_poll_live_interrupt(client, run_dir, stop))
    try:
        return await _drain_turn(client, run_dir, pending_tools)
    finally:
        stop.set()
        poller.cancel()
        try:
            run_dir.end_live_turn()
        finally:
            try:
                await poller
            except (asyncio.CancelledError, Exception):
                pass


def _publish_turn_notification(
    manager, run_dir: "DaemonRunDir", *, turn: int, text: str,
    is_error: bool, turn_path: str,
) -> None:
    """Publish one non-terminal daemon-channel notice for a completed turn.

    Reuses the exact store/idempotency/body-building machinery the terminal
    receipt and ``daemon_checkpoint``/``daemon_followup`` events already
    share (``supervisor_runtime._publish_daemon_notification``), typed
    ``kind="daemon_turn_result"`` so no consumer mistakes it for this run's
    terminal outcome (``_is_terminal_daemon_event`` only matches
    ``daemon_terminal`` kinds / ``daemon-terminal:`` idempotency keys).

    If ``record_turn_result`` already failed to durably save this turn
    (``turn_path`` falsy), this never claims success: it records one typed
    ``daemon_turn_persistence_failed`` event instead of publishing a notice
    that implies the save succeeded. A publish failure — whether the store
    raises or just returns ``False`` — is likewise recorded as a typed,
    retryable event rather than silently treated as success; either way, a
    lost non-terminal wake never aborts an otherwise-successful continuing
    run.
    """
    if not turn_path:
        try:
            run_dir.append_event(
                "daemon_turn_persistence_failed", turn=turn, retryable=True,
            )
        except Exception:
            pass
        return
    try:
        from .supervisor_runtime import _publish_daemon_notification

        manifest = getattr(manager, "_manifest", None)
        if isinstance(manifest, dict) and "parent_working_dir" in manifest:
            parent_working_dir = manifest["parent_working_dir"]
            task = manifest.get("task")
        else:
            # Always present in production (``execution_host`` sets it before
            # the runner ever runs); this branch only matters for tests that
            # call the runner directly on a non-detached ``DaemonManager``
            # (no ``_manifest`` attribute at all), which still has `_workdir`.
            workdir = getattr(manager, "_workdir", None)
            if workdir is None:
                return
            parent_working_dir = str(workdir.path)
            task = None
        manifest = {"parent_working_dir": parent_working_dir, "task": task}
        # Same redaction `record_turn_result` already applied to the turn
        # file itself (`run_dir._durable_value`) — a raw, unredacted `text`
        # here would leak an ephemeral secret into the notification store
        # even though the on-disk turn file is properly scrubbed.
        safe_text = run_dir._durable_value(text or "")[:500]
        state = {
            "result_path": turn_path,
            "result_preview": safe_text,
            "task": manifest.get("task"),
        }
        if is_error:
            state["error"] = {"type": "ClaudeTurnError", "message": safe_text}
        published = _publish_daemon_notification(
            run_dir, manifest,
            status="turn_result", state=state,
            idempotency_key=f"daemon-turn:{run_dir.run_id}:{turn}",
            kind="daemon_turn_result",
        )
        if not published:
            run_dir.append_event(
                "daemon_notification_error", kind="daemon_turn_result",
                turn=turn, error_type="PublishReturnedFalse", retryable=True,
            )
    except Exception as exc:
        try:
            run_dir.append_event(
                "daemon_notification_error", kind="daemon_turn_result",
                turn=turn, error_type=type(exc).__name__, retryable=True,
            )
        except Exception:
            pass


async def _run_async(
    manager, run_dir: "DaemonRunDir", task: str,
    cancel_event, timeout_event, mcp_servers: dict[str, dict],
    sdk_options: dict[str, Any], backend_env: dict[str, str] | None,
) -> str:
    from claude_agent_sdk import ClaudeSDKClient, ResultError

    stripped = [key for key in _CLAUDE_CODE_STRIP_ENV if key in os.environ]
    for key in stripped:
        os.environ.pop(key, None)
    if stripped:
        try:
            run_dir.append_event("daemon_claude_code_env_stripped", stripped=stripped)
        except Exception:
            pass

    cli_path = _resolve_cli_path()
    options = _build_options(
        cwd=str(manager._workdir.path), cli_path=cli_path,
        mcp_servers=mcp_servers, sdk_options=sdk_options,
        backend_env=backend_env,
    )
    client = ClaudeSDKClient(options=options)
    await client.connect()
    final_text = ""
    turn_seq = 0
    try:
        run_dir.begin_live_turn()
        await client.query(task)
        pending_tools: dict[str, str] = {}
        while True:
            if cancel_event.is_set():
                try:
                    await client.interrupt()
                except Exception:
                    pass
                return mark_cancelled_or_timeout(run_dir, timeout_event)

            try:
                result = await _drain_turn_with_interrupt(client, run_dir, pending_tools)
            except ResultError as exc:
                turn_exc = ClaudeTurnError(str(exc), getattr(exc, "terminal_reason", None))
                run_dir.mark_failed(turn_exc)
                return final_text
            if result is None:
                # The connection ended without ever producing a result for
                # our own turn (crash, auth failure, unexpected EOF) — never
                # treat a silent stream end as success.
                run_dir.mark_failed(
                    RuntimeError("claude SDK stream ended without a result for this turn")
                )
                return final_text

            sid = getattr(result, "session_id", None)
            if isinstance(sid, str) and sid:
                run_dir.set_session_id("claude_session_id", sid, overwrite=True)
            final_text = result.result or ""
            turn_seq += 1
            usage = _normalize_usage(result.usage)
            if usage is not None:
                try:
                    run_dir.record_cli_tokens(
                        input=usage["input"], output=usage["output"],
                        cached=usage["cached"], thinking=usage["thinking"],
                    )
                except Exception:
                    pass

            # Durably save this turn's complete result BEFORE deciding
            # whether the run continues or finalizes — a parent molt/
            # reconnect, or a crash immediately after this point, must still
            # find the full result on disk rather than depending on the
            # run's eventual terminal state to persist it.
            turn_path = run_dir.record_turn_result(
                turn=turn_seq, text=final_text, is_error=bool(result.is_error),
                session_id=sid if isinstance(sid, str) else None,
            )
            if not turn_path:
                # The durability promise this backend makes ("every Result
                # is saved before the run continues or completes") cannot be
                # honestly kept for this turn. Stop here, before sending any
                # next query — not after, and not silently continuing as if
                # nothing happened. Record the typed failure unconditionally
                # (previously this was only recorded when the run went on to
                # a follow-up; a write failure on the *final* turn left no
                # trace at all, confirmed by a parent-injected reproduction).
                # `pending_followups` is deliberately left untouched rather
                # than drained: any already-accepted message stays visible
                # on disk for forensic inspection instead of being silently
                # discarded along with the result that couldn't be saved.
                try:
                    run_dir.append_event(
                        "daemon_turn_persistence_failed", turn=turn_seq, retryable=True,
                    )
                except Exception:
                    pass
                run_dir.mark_failed(
                    RuntimeError(f"turn {turn_seq} result could not be durably saved")
                )
                return final_text

            if cancel_event.is_set():
                try:
                    await client.interrupt()
                except Exception:
                    pass
                return mark_cancelled_or_timeout(run_dir, timeout_event)

            # Drain a racing follow-up and finalize in one atomic
            # transaction (see `DaemonRunDir.drain_followups_or_finalize`):
            # a separate drain-then-finalize pair leaves a real window in
            # which an `ask`'s `enqueue_live_followup` lands between this
            # runner's own empty drain and its terminal write, stranding an
            # already-accepted message forever once the run is terminal —
            # confirmed by a deterministic reproduction, not a theoretical
            # concern. Terminal receipt (the supervisor's own
            # terminal-notification publish, triggered by the
            # mark_done/mark_failed-equivalent state transition inside this
            # call) already covers this turn's result when it finalizes;
            # no separate non-terminal notice is needed for that case.
            followup = run_dir.drain_followups_or_finalize(
                status="failed" if result.is_error else "done",
                text=final_text,
                error=(
                    ClaudeTurnError(final_text, result.terminal_reason)
                    if result.is_error else None
                ),
            )
            if followup is None:
                return final_text

            # The run continues past this turn: this Result is not the
            # run's terminal outcome, so publish it as a compact
            # non-terminal notice through the existing daemon-channel
            # notification path (reusing the same store/idempotency
            # machinery as every other non-terminal daemon event) before
            # sending the next query — the parent must be able to read
            # this turn's conclusion even if it never sees this run's
            # eventual terminal receipt (crash, timeout on a later turn,
            # reclaim, ...).
            _publish_turn_notification(
                manager, run_dir, turn=turn_seq, text=final_text,
                is_error=bool(result.is_error), turn_path=turn_path,
            )
            run_dir.begin_live_turn()
            await client.query(followup)
            continue
    finally:
        try:
            run_dir.end_live_turn()
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass


def run_claude_agent_sdk_emanation(
    manager, em_id: str, run_dir: "DaemonRunDir", task: str,
    cancel_event, timeout_event=None,
    backend_argv: list[str] | None = None,
    backend_env: dict[str, str] | None = None,
) -> str:
    """Entry point matching every other CLI backend's ``runner_attr`` shape.

    Called synchronously from inside the detached execution child
    (``execution_host.DetachedDaemonExecutionHost.run()``); owns its own
    asyncio loop for the life of the run. All terminal-state transitions
    (``mark_done``/``mark_failed``/cancelled/timeout) happen inside
    ``_run_async`` at the exact point each is decided, matching every other
    backend runner's structure.
    """
    if cancel_event.is_set():
        return mark_cancelled_or_timeout(run_dir, timeout_event)

    try:
        sdk_options = claude_sdk_options_from_argv(list(backend_argv or []))
    except ValueError as exc:
        run_dir.mark_failed(exc)
        raise
    capsule = getattr(manager, "_capsule", None)
    raw_mcp = capsule.get("mcp") if isinstance(capsule, dict) else None
    mcp_servers = _mcp_servers_from_registrations(raw_mcp if isinstance(raw_mcp, list) else [])

    try:
        return asyncio.run(
            _run_async(
                manager, run_dir, task, cancel_event, timeout_event,
                mcp_servers, sdk_options, backend_env,
            )
        )
    except FileNotFoundError as exc:
        run_dir.mark_failed(exc)
        raise RuntimeError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - run loop boundary, same as every other backend
        run_dir.mark_failed(exc)
        raise


def run_claude_agent_sdk_resume(manager, em_id: str, entry: dict, message: str) -> dict:
    """Terminal follow-up: resume the session in a fresh connection.

    Matches ``_handle_ask_cli``'s calling convention and *async dispatch
    contract* exactly: ``execution_host.run_resume`` calls this, then reads
    ``entry["ask_future"]`` and blocks on ``future.result(timeout=...)``
    itself to decide the generation's ``record_followup`` receipt. This
    function's job is therefore only to (a) surface a synchronous rejection
    (busy / missing session / missing CLI) by returning a plain dict with no
    future set, or (b) submit the actual connect/query/drain work to
    ``manager._ask_pool`` (constructed by ``run_resume`` immediately before
    calling this), set ``entry["ask_future"]``, and return
    ``{"status": "sent", "async": True}`` immediately — never block this
    call on the SDK round trip itself.
    """
    run_dir = entry.get("run_dir")
    if run_dir is None:
        return {"status": "error", "message": f"emanation {em_id} has no run_dir"}
    state = run_dir.state_snapshot()
    session_id = state.get("claude_session_id")
    if not session_id:
        return {
            "status": "error",
            "message": f"No claude session ID found for {em_id}. "
                       "The emanation may still be initializing — wait a moment and retry.",
        }

    with entry["followup_lock"]:
        if entry.get("ask_in_flight"):
            return {"status": "busy", "id": em_id,
                    "message": f"a previous ask on {em_id} is still running"}
        entry["ask_in_flight"] = True

    try:
        cli_path = _resolve_cli_path()
    except FileNotFoundError as exc:
        with entry["followup_lock"]:
            entry["ask_in_flight"] = False
        return {"status": "error", "id": em_id, "message": str(exc)}

    backend_argv = list(getattr(manager, "_manifest", {}).get("backend_argv") or [])
    try:
        sdk_options = claude_sdk_options_from_argv(backend_argv)
    except ValueError as exc:
        with entry["followup_lock"]:
            entry["ask_in_flight"] = False
        return {"status": "error", "id": em_id, "message": str(exc)}
    backend_env_fn = getattr(manager, "_runtime_backend_env", None)
    backend_env = backend_env_fn() if callable(backend_env_fn) else {}
    capsule = getattr(manager, "_capsule", None)
    raw_mcp = capsule.get("mcp") if isinstance(capsule, dict) else None
    mcp_servers = _mcp_servers_from_registrations(raw_mcp if isinstance(raw_mcp, list) else [])

    def _resume_worker() -> dict:
        try:
            output = asyncio.run(
                _resume_async(
                    run_dir=run_dir, message=message, session_id=session_id,
                    cwd=str(manager._workdir.path), cli_path=cli_path,
                    mcp_servers=mcp_servers, sdk_options=sdk_options,
                    backend_env=backend_env,
                )
            )
        except ClaudeTurnError as exc:
            return {"status": "error", "id": em_id, "message": str(exc)}
        except Exception as exc:  # noqa: BLE001 - resume-owner boundary
            return {"status": "error", "id": em_id, "message": f"{type(exc).__name__}: {exc}"}
        finally:
            with entry["followup_lock"]:
                entry["ask_in_flight"] = False
        return {"status": "sent", "id": em_id, "output": output}

    ask_pool = getattr(manager, "_ask_pool", None)
    if ask_pool is None:
        with entry["followup_lock"]:
            entry["ask_in_flight"] = False
        return {"status": "error", "id": em_id, "message": "no ask executor available"}
    entry["ask_future"] = ask_pool.submit(_resume_worker)
    return {"status": "sent", "id": em_id, "async": True,
            "message": "resume dispatched; check daemon(action='check', "
                       f"id='{em_id}') for progress and final reply"}


async def _resume_async(
    *, run_dir: "DaemonRunDir", message: str, session_id: str,
    cwd: str, cli_path: str, mcp_servers: dict[str, dict],
    sdk_options: dict[str, Any], backend_env: dict[str, str] | None,
) -> str:
    from claude_agent_sdk import ClaudeSDKClient

    stripped = [key for key in _CLAUDE_CODE_STRIP_ENV if key in os.environ]
    for key in stripped:
        os.environ.pop(key, None)
    if stripped:
        try:
            run_dir.append_event("daemon_claude_code_env_stripped", stripped=stripped)
        except Exception:
            pass
    options = _build_options(
        cwd=cwd, cli_path=cli_path, mcp_servers=mcp_servers,
        sdk_options=sdk_options, backend_env=backend_env, resume=session_id,
    )
    client = ClaudeSDKClient(options=options)
    await client.connect()
    try:
        await client.query(message)
        pending_tools: dict[str, str] = {}
        result = await _drain_turn_with_interrupt(client, run_dir, pending_tools)
        if result is None:
            raise ClaudeTurnError(
                "claude SDK resume stream ended without a result", None
            )
        if isinstance(getattr(result, "session_id", None), str) and result.session_id:
            run_dir.set_session_id("claude_session_id", result.session_id, overwrite=True)
        usage = _normalize_usage(result.usage)
        if usage is not None:
            try:
                run_dir.record_cli_tokens(**usage)
            except Exception:
                pass
        if result.is_error:
            raise ClaudeTurnError(result.result or "", result.terminal_reason)
        return result.result or ""
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


__all__ = [
    "SUPPORTED_SDK_FLAGS",
    "ClaudeTurnError",
    "claude_sdk_options_from_argv",
    "run_claude_agent_sdk_emanation",
    "run_claude_agent_sdk_resume",
]
