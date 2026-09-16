"""MCP clients — async-to-sync bridges for MCP servers.

MCPClient: stdio subprocess servers (e.g., uvx minimax-coding-plan-mcp).
HTTPMCPClient: remote HTTP/SSE servers (e.g., api.z.ai/api/mcp/...).

Both provide the same basic synchronous call_tool() interface. A background
daemon thread runs the async event loop; the public API is thread-safe. Both
clients recover a dropped transport inside call_tool: they restart the session
so a future call can succeed, and they return a non-blank error dict instead
of raising. The stdio client additionally accepts an explicit stale-resource
replay policy; the HTTP client never replays (see HTTPMCPClient.call_tool).

Both are built on the official MCP Python SDK v2 first-class ``mcp.Client``
in its default ``mode="auto"``: the SDK probes ``server/discover`` and falls
back to the pre-2026 ``initialize`` handshake on legacy servers, so one client
speaks to both eras without LingTai owning any negotiation code. The SDK owns
JSON-RPC framing, transport, and typed protocol validation; this module owns
process/session lifecycle, the stale-resource replay policy, and the
kernel-facing projection of typed results.
"""
from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from lingtai.kernel.logging import get_logger

logger = get_logger()

_JSON_PARSE_FAILED = object()
_HOST_PRIVATE_TOOL_ARGUMENTS = frozenset({"_reasoning"})
_LTP_V2_FAMILY_ROOT_FIELDS = frozenset(
    {"action", "input", "reasoning", "summarize"}
)
_LTP_V2_FAMILY_REQUIRED_FIELDS = frozenset({"action", "input", "reasoning"})


def is_strict_ltp_v2_family_schema(schema: Any) -> bool:
    """Recognize the canonical closed root used by native MCP ToolFamilies."""
    if not isinstance(schema, dict):
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    return (
        schema.get("type") == "object"
        and schema.get("additionalProperties") is False
        and isinstance(properties, dict)
        and set(properties) == _LTP_V2_FAMILY_ROOT_FIELDS
        and isinstance(required, list)
        and set(required) == _LTP_V2_FAMILY_REQUIRED_FIELDS
        and isinstance(properties.get("reasoning"), dict)
        and properties["reasoning"].get("type") == "string"
    )


def prepare_mcp_tool_arguments(
    arguments: dict[str, Any] | None,
    input_schema: Any,
) -> dict[str, Any]:
    """Adapt host-private arguments to one MCP server's advertised schema.

    ``ToolExecutor`` retains model rationale under ``_reasoning`` for kernel
    logging. That private key must not cross the MCP boundary unless the server
    explicitly declares it. Native strict LTP-v2 ToolFamilies are the existing
    exception: their closed public envelope requires ``reasoning``, so restore
    that key before dispatch. Other unknown arguments are intentionally left
    untouched for server-side schema validation.

    A fresh dictionary is always returned so adapter normalization cannot
    mutate the ToolCall or a caller-owned argument mapping.
    """
    prepared = dict(arguments or {})
    if "_reasoning" not in prepared:
        return prepared

    if is_strict_ltp_v2_family_schema(input_schema):
        reasoning = prepared.pop("_reasoning")
        prepared.setdefault("reasoning", reasoning)
        return prepared

    properties = (
        input_schema.get("properties") if isinstance(input_schema, dict) else None
    )
    declared = properties if isinstance(properties, dict) else {}
    for field in _HOST_PRIVATE_TOOL_ARGUMENTS - declared.keys():
        prepared.pop(field, None)
    return prepared


def _first_text_content(result: Any) -> str | None:
    """Return the first text block carried by an MCP tool result."""
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            return text
    return None


def _error_payload(payload: dict[str, Any], fallback_message: str) -> dict[str, Any]:
    """Keep a structured MCP error while retaining the legacy envelope."""
    error = dict(payload)
    # The protocol's isError bit is authoritative. Do not let a malformed or
    # hostile payload disguise a failed tool call as success.
    error["status"] = "error"
    message = error.get("message")
    if not isinstance(message, str) or not message:
        error["message"] = fallback_message
    return error


def _result_is_error(result: Any) -> bool:
    """Read the protocol error bit from an SDK v2 ``CallToolResult``."""
    return bool(getattr(result, "is_error", False))


def _result_structured(result: Any) -> Any:
    """Read the SDK v2 ``structured_content`` member."""
    return getattr(result, "structured_content", None)


def _dump_block(block: Any) -> Any:
    """Serialize one content block using its MCP wire names.

    ``by_alias=True`` is what keeps ``mimeType``/``_meta`` on the wire spelling
    while the Python attributes stay snake_case (SDK v2 renamed the attributes,
    not the protocol).
    """
    dump = getattr(block, "model_dump", None)
    if dump is None:
        return block
    return dump(by_alias=True, exclude_none=True, mode="json")


def preserve_tool_result(result: Any) -> dict[str, Any]:
    """Preserve a typed MCP tool result without losing anything.

    The compatibility projection below (``_decode_tool_result``) intentionally
    reduces a result to one legacy value for the model-facing tool contract.
    This function is the non-lossy half of that boundary: it keeps the full
    ordered content union (text, image, audio, resource links, embedded
    resources), the structured payload whatever its JSON type, the protocol
    error bit, the result type, and ``_meta`` — so a caller that wants the real
    protocol result can have it rather than only the projection.
    """
    content = [_dump_block(block) for block in getattr(result, "content", None) or []]
    preserved: dict[str, Any] = {
        "content": content,
        "structured_content": _result_structured(result),
        "is_error": _result_is_error(result),
    }
    result_type = getattr(result, "result_type", None)
    if result_type is not None:
        preserved["result_type"] = result_type
    meta = getattr(result, "meta", None)
    if meta is not None:
        preserved["meta"] = meta
    return preserved


def _decode_tool_result(result: Any) -> Any:
    """Project an MCP result into the legacy model-facing value.

    This is an explicit **compatibility projection**, not the protocol result:
    kernel tool handlers have always received one decoded value, and every
    intrinsic tool family's public result shape depends on that. MCP transports
    may return the same object in ``structured_content`` and in a JSON text
    block, so prefer the structured form, then a JSON object in text.
    Plain-text errors retain the historical ``status``/``message`` envelope.
    The full typed result stays reachable through ``preserve_tool_result`` and
    the clients' ``last_tool_result`` property; nothing here is the only copy.

    This decoder is intentionally protocol-generic and is shared by stdio and
    HTTP clients.
    """
    text = _first_text_content(result)
    structured = _result_structured(result)

    if isinstance(structured, dict):
        if _result_is_error(result):
            return _error_payload(
                structured,
                text if text else "Unknown MCP error",
            )
        return dict(structured)

    if text is not None:
        try:
            decoded = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            decoded = _JSON_PARSE_FAILED
        if isinstance(decoded, dict):
            if _result_is_error(result):
                return _error_payload(decoded, text)
            return decoded
        if not _result_is_error(result) and decoded is not _JSON_PARSE_FAILED:
            # Preserve the pre-existing behavior for successful JSON values,
            # even though MCP tool handlers conventionally return objects.
            return decoded

    if _result_is_error(result):
        return {"status": "error", "message": text or "Unknown MCP error"}
    return {"status": "success", "text": text or ""}


def _tool_record(tool: Any) -> dict[str, Any]:
    """Convert one SDK v2 ``Tool`` into the kernel-facing tool record.

    ``name``/``description``/``schema`` are the historical keys every caller
    already reads. The remaining supported metadata (title, output schema,
    annotations, icons, ``_meta``) is carried alongside instead of being
    dropped at this boundary; consumers that cannot represent a field simply
    ignore it.
    """
    raw_schema = getattr(tool, "input_schema", None)
    schema = raw_schema if isinstance(raw_schema, dict) else {}
    record: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description or "",
        "schema": schema,
    }
    output_schema = getattr(tool, "output_schema", None)
    if isinstance(output_schema, dict):
        record["output_schema"] = output_schema
    for attribute in ("title", "annotations", "icons", "execution", "meta"):
        value = getattr(tool, attribute, None)
        if value is None:
            continue
        dump = getattr(value, "model_dump", None)
        record[attribute] = (
            dump(by_alias=True, exclude_none=True, mode="json") if dump else value
        )
    return record


#: Keys of an MCP tool record that ``FunctionSchema`` cannot represent. These
#: are the v2 fields a server advertises beyond name/description/input schema;
#: adapters retain them in a sidecar instead of forcing them into the provider
#: wire schema.
MCP_TOOL_METADATA_KEYS = (
    "title",
    "output_schema",
    "annotations",
    "icons",
    "execution",
    "meta",
)


def tool_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of the advertised metadata carried by a tool record.

    Deep-copied on the way in so a stored sidecar can never alias the caller's
    record, and copied again on the way out by the adapters' read seams, so a
    consumer cannot mutate stored metadata through a returned reference.
    """
    return deepcopy(
        {key: record[key] for key in MCP_TOOL_METADATA_KEYS if key in record}
    )


async def _list_all_tools(client: Any) -> list[dict[str, Any]]:
    """Page through ``tools/list`` until the server stops handing back a cursor.

    Every SDK v2 ``list_*`` result carries ``next_cursor``; a single call is
    only the first page. Looping here is what makes the returned catalog the
    server's whole tool surface.
    """
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        result = await client.list_tools(cursor=cursor)
        tools.extend(_tool_record(tool) for tool in result.tools)
        cursor = getattr(result, "next_cursor", None)
        if cursor is None:
            return tools


# A stale stdio response has an unknowable remote commit point. Replay is
# therefore opt-in and limited to callers that can justify that it is safe.
_STALE_RETRY_POLICIES = frozenset({"never", "safe"})


def _snapshot_process_table(
    timeout: float = 20.0,
    *,
    strict: bool = False,
) -> list[dict]:
    """Return [{pid, ppid, cmdline}] for every visible process.

    stdlib-only, cross-platform. The MCP server may be a wrapper that spawns
    a real interpreter child (Windows venv shim -> base python), so callers
    that need to kill a server must walk the whole descendant tree, not just
    direct children. ``strict`` propagates collection failures when a caller
    needs process absence to be positive evidence rather than a best-effort
    empty result.
    """
    rows: list[dict] = []
    try:
        if os.name == "nt":
            import json
            import subprocess

            script = (
                "Get-CimInstance Win32_Process | "
                "Select-Object ProcessId,ParentProcessId,CommandLine | "
                "ConvertTo-Json -Compress"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                text=True,
                timeout=max(0.0, timeout),
                errors="replace",
            ).strip()
            if not out:
                return rows
            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                rows.append(
                    {
                        "pid": int(item.get("ProcessId") or 0),
                        "ppid": int(item.get("ParentProcessId") or 0),
                        "cmdline": (item.get("CommandLine") or "").lower(),
                    }
                )
        else:
            import subprocess

            out = subprocess.check_output(
                ["ps", "-axo", "pid=,ppid=,command="],
                text=True,
                timeout=max(0.0, timeout),
                errors="replace",
            )
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 2)
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[0])
                    ppid = int(parts[1])
                except ValueError:
                    continue
                cmdline = (parts[2] if len(parts) > 2 else "").lower()
                rows.append({"pid": pid, "ppid": ppid, "cmdline": cmdline})
    except Exception:
        if strict:
            raise
        # Best effort: if the snapshot fails we simply cannot clean up by pid.
        return rows
    return rows


def _descendant_pids(root_pid: int, table: list[dict]) -> list[int]:
    """Return every pid in the descendant closure of ``root_pid``."""
    children: dict[int, list[int]] = {}
    for row in table:
        children.setdefault(row["ppid"], []).append(row["pid"])
    found: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def _pid_exited(pid: int) -> bool:
    """Reap an owned POSIX child when possible, then verify PID absence."""
    try:
        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            return True
    except ChildProcessError:
        pass  # A captured descendant need not be our direct child.
    except ProcessLookupError:
        return True
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def _wait_for_processes_exit(
    pids: list[int],
    timeout: float,
    *,
    deadline: float | None = None,
) -> bool:
    """Boundedly reap/verify every captured PID."""
    remaining = {candidate for candidate in pids if candidate > 0}
    if deadline is None:
        deadline = time.monotonic() + max(0.0, timeout)
    while remaining:
        if os.name == "nt":
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                return False
            try:
                table = _snapshot_process_table(
                    timeout=remaining_time,
                    strict=True,
                )
            except Exception:
                # Snapshot failure is not evidence that a captured PID exited.
                return False
            present = {row.get("pid") for row in table}
            remaining.intersection_update(present)
        else:
            remaining = {
                candidate for candidate in remaining if not _pid_exited(candidate)
            }
        if not remaining:
            return True
        remaining_time = deadline - time.monotonic()
        if remaining_time <= 0:
            return False
        time.sleep(min(0.05, remaining_time))
    return True


def _kill_process_tree(pid: int, timeout: float = 3.0) -> bool:
    """Terminate a process and all its descendants; return True if gone."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import subprocess

            deadline = time.monotonic() + max(0.0, timeout)
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                return False
            captured_tree_is_known = True
            try:
                table = _snapshot_process_table(
                    timeout=remaining_time,
                    strict=True,
                )
            except Exception:
                table = []
                captured_tree_is_known = False
            captured_pids = list(
                dict.fromkeys([pid, *_descendant_pids(pid, table)])
            )
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                return False
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=remaining_time,
            )
            if not captured_tree_is_known:
                return False
            return _wait_for_processes_exit(
                captured_pids,
                timeout,
                deadline=deadline,
            )
        else:
            import signal

            captured_pids = list(
                dict.fromkeys(
                    [pid, *_descendant_pids(pid, _snapshot_process_table())]
                )
            )
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            if _wait_for_processes_exit(captured_pids, timeout):
                return True
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            return _wait_for_processes_exit(captured_pids, timeout)
    except Exception:
        return False


class MCPClient:
    """Async-to-sync bridge for any MCP stdio server.

    Args:
        command: Executable to run (e.g., "uvx").
        args: Arguments to the command (e.g., ["minimax-coding-plan-mcp", "-y"]).
        env: Environment variables for the subprocess. If None, inherits
            the current process environment.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        name: str | None = None,
    ):
        self._command = command
        self._args = args or []
        self._env = env
        self._name = name or self._default_name(command, self._args)

        # Child process tree owned by this client (captured after start so
        # close() can terminate it even on platforms where EOF does not exit
        # the child, e.g. Windows venv shim -> real interpreter).
        self._child_pids: list[int] = []

        self._session: Any = None
        self._read_stream: Any = None
        self._write_stream: Any = None
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._stdio_cm: Any = None
        self._session_cm: Any = None
        # Official SDK v2 first-class client; `_session` stays the object the
        # call/list paths talk to so the lifecycle code below is unchanged.
        self._client: Any = None
        self._last_result: dict[str, Any] | None = None

        # Activity log for debugging — last 50 calls
        self._activity_log: list[dict[str, Any]] = []
        self._activity_lock = threading.Lock()

    @staticmethod
    def _default_name(command: str, args: list[str]) -> str:
        """Derive a stable identity from the spawn command for dedup."""
        base = os.path.basename(command).lower()
        tokens = [base]
        for a in args:
            tok = a.lower().strip()
            if len(tok) >= 5 and not tok.startswith("-"):
                tokens.append(tok)
        return "|".join(tokens)

    @property
    def name(self) -> str:
        """Stable identity for dedup (explicit name or derived from command)."""
        return self._name

    def _capture_child_pids(self) -> None:
        """Record descendant PIDs matching this server's command signature."""
        try:
            signature_tokens = [t for t in self._name.split("|") if t]
            table = _snapshot_process_table()
            matches = []
            for pid in _descendant_pids(os.getpid(), table):
                row = next((r for r in table if r["pid"] == pid), None)
                if row and row["cmdline"]:
                    if any(tok in row["cmdline"] for tok in signature_tokens):
                        matches.append(pid)
            if matches:
                self._child_pids = matches
        except Exception:
            pass

    def _kill_children(self) -> None:
        """Terminate the captured child process tree (best effort)."""
        if not self._child_pids:
            return
        for pid in self._child_pids:
            try:
                _kill_process_tree(pid)
            except Exception:
                continue

    # ------------------------------------------------------------------
    # Exception helpers — never surface a blank error (issue #104)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_exception(exc: BaseException) -> str:
        """Render an exception as ``ClassName: message``.

        Some MCP/anyio exceptions (notably ``ClosedResourceError``) have an
        empty ``str()``. Falling through to that empty string is what produced
        the blank ``{"status": "error", "message": ""}`` in issue #104. When the
        message is empty we fall back to the class name alone.
        """
        cls = type(exc).__name__
        msg = str(exc).strip()
        return f"{cls}: {msg}" if msg else cls

    @staticmethod
    def _validate_retry_policy(retry_policy: str) -> None:
        if retry_policy not in _STALE_RETRY_POLICIES:
            allowed = ", ".join(sorted(_STALE_RETRY_POLICIES))
            raise ValueError(
                f"retry_policy must be one of: {allowed}; got {retry_policy!r}"
            )

    @staticmethod
    def _ambiguous_call_error(message: str) -> dict:
        """Return a non-retryable error for an unknowable remote outcome."""
        return {
            "status": "error",
            "message": message,
            "outcome": "ambiguous",
            "retryable": False,
        }

    @staticmethod
    def _is_stale_resource_error(exc: BaseException) -> bool:
        """Detect a dead/closed MCP transport that warrants a restart.

        Primary signal is the exception class name ``ClosedResourceError``
        (anyio) — matched by name so we need not import anyio. As a secondary
        signal we look for closed-stream substrings in the message.
        """
        if type(exc).__name__ == "ClosedResourceError":
            return True
        text = str(exc).lower()
        return any(
            marker in text
            for marker in ("closed", "broken pipe", "stream", "transport")
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background thread and connect to the MCP server.

        Called automatically by call_tool() and list_tools() if not yet
        connected.

        Raises:
            RuntimeError: If this client was explicitly closed. A closed client
                must not silently acquire a fresh subprocess: the caller kept
                no handle to it and ``close()`` would early-return, so the new
                transport could never be shut down. ``restart()`` is the
                supported way back, and it clears ``_closed`` first.
        """
        if self._closed:
            raise RuntimeError("MCP client has been closed")
        if self.is_connected():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._error:
            raise RuntimeError(f"MCP server failed to start: {self._error}")
        # The subprocess is now alive; record its tree so close() can
        # terminate it on platforms where closing stdio does not exit it.
        self._capture_child_pids()

    def close(self) -> None:
        """Shut down the MCP session, background thread, and subprocess tree.

        Stopping the asyncio loop and joining the thread is enough on POSIX,
        where closing the stdio pipes delivers EOF and the child exits. On
        Windows that is not reliable (a venv launcher shim spawns a real
        interpreter as a child that survives), so we additionally terminate
        the captured subprocess tree. Harmless on POSIX, where the tree is
        already gone.
        """
        if self._closed:
            return
        self._closed = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        self._kill_children()

    def restart(self) -> None:
        """Tear down a (possibly stale) session and reconnect from scratch.

        ``start()`` early-returns when it believes it is connected and never
        clears latched startup state, so a stale ``_ready``/``_error`` or a
        ``_closed`` flag from a prior ``close()`` would make a fresh ``start()``
        lie (return immediately, or raise on the *old* error). This resets all
        startup/session fields so the subsequent ``start()`` is a real reconnect.
        Used by ``call_tool`` to recover from a closed stdio resource (issue #104).
        """
        self.close()
        self._ready.clear()
        self._error = None
        self._closed = False
        self._session = None
        self._read_stream = None
        self._write_stream = None
        self._loop = None
        self._thread = None
        self._stdio_cm = None
        self._session_cm = None
        self._client = None
        self.start()

    def is_connected(self) -> bool:
        """Check if the client has an active session."""
        return (
            self._session is not None
            and self._loop is not None
            and self._loop.is_running()
            and not self._closed
        )

    # ------------------------------------------------------------------
    # Tool calls
    # ------------------------------------------------------------------

    def call_tool(
        self,
        name: str,
        args: dict,
        timeout: float = 120,
        *,
        retry_policy: str = "never",
    ) -> Any:
        """Call an MCP tool synchronously.

        Starts the connection lazily if not yet connected.

        Args:
            name: Tool name (e.g., "web_search").
            args: Tool arguments dict.
            timeout: Timeout in seconds.
            retry_policy: Stale-resource replay policy. ``"never"`` (the
                default) fails closed because the remote commit point is
                ambiguous. ``"safe"`` is an explicit caller assertion that a
                replay cannot create a duplicate externally visible effect.
                This client does not infer safety from tool names or arguments.

        Returns:
            Decoded MCP result. JSON scalars, arrays, and ``null`` retain
            their protocol value; structured objects remain dictionaries.
            A default-policy stale failure returns an error with
            ``outcome="ambiguous"`` and ``retryable=False`` after attempting
            transport recovery without replaying the call. A timeout after the
            call has been submitted is also returned as ambiguous and
            non-retryable; the remote may have committed even though no
            response reached this caller.

        Raises:
            RuntimeError: If the client is closed or connection fails.
            ValueError: If ``retry_policy`` is not supported.
        """
        import asyncio

        self._validate_retry_policy(retry_policy)

        if self._closed:
            raise RuntimeError("MCP client has been closed")

        # Lazy start
        if not self.is_connected():
            self.start()

        if self._session is None or self._loop is None:
            raise RuntimeError("MCP client not connected")

        def _attempt() -> Any:
            async def _call():
                # SDK v2 timeouts are float seconds, not timedelta.
                result = await self._session.call_tool(
                    name=name,
                    arguments=args,
                    read_timeout_seconds=float(timeout),
                )
                self._last_result = preserve_tool_result(result)
                return _decode_tool_result(result)

            future = asyncio.run_coroutine_threadsafe(_call(), self._loop)
            try:
                return future.result(timeout=timeout)
            except TimeoutError as exc:
                # The coroutine crossed the submission boundary before this
                # timeout. The server may therefore have committed the call
                # even though this caller never received its response. Cancel
                # local work if possible, but never advertise a blind retry.
                future.cancel()
                formatted = self._format_exception(exc)
                logger.warning(
                    "MCP tool %s timed out after submission (%s); remote "
                    "outcome is ambiguous; not retrying",
                    name,
                    formatted,
                )
                return self._ambiguous_call_error(
                    f"{formatted}: MCP call was submitted but completion timed "
                    "out; remote outcome is ambiguous; call was not retried to "
                    "avoid duplicate side effects"
                )

        try:
            result = _attempt()
        except Exception as exc:
            formatted = self._format_exception(exc)
            if not self._is_stale_resource_error(exc):
                # Non-stale failure: surface the class name so the error is
                # never blank (issue #104), but don't churn the subprocess.
                result = {"status": "error", "message": formatted}
            else:
                if retry_policy == "never":
                    # Recover the transport for a future independent call, but
                    # do not replay this call: the server may have committed it
                    # before the stale response path failed.
                    logger.warning(
                        "MCP tool %s hit stale resource (%s); restarting "
                        "transport without replay",
                        name,
                        formatted,
                    )
                    try:
                        self.restart()
                    except Exception as restart_exc:
                        result = self._ambiguous_call_error(
                            f"{formatted}: MCP session closed; remote outcome is "
                            "ambiguous; call was not retried to avoid duplicate "
                            "side effects; transport restart failed: "
                            f"{self._format_exception(restart_exc)}"
                        )
                    else:
                        result = self._ambiguous_call_error(
                            f"{formatted}: MCP session closed; remote outcome is "
                            "ambiguous; call was not retried to avoid duplicate "
                            "side effects; transport restarted for a future call"
                        )
                else:
                    # ``safe`` is a trusted caller attestation. This generic
                    # client cannot verify the operation's replay semantics.
                    logger.warning(
                        "MCP tool %s hit stale resource (%s); explicit safe "
                        "policy; restarting and retrying once",
                        name,
                        formatted,
                    )
                    try:
                        self.restart()
                    except Exception as restart_exc:
                        result = self._ambiguous_call_error(
                            f"{formatted}: MCP session closed; explicit safe "
                            "replay requested but transport restart failed: "
                            f"{self._format_exception(restart_exc)}; first "
                            "attempt outcome remains ambiguous"
                        )
                    else:
                        try:
                            result = _attempt()
                        except Exception as retry_exc:
                            result = self._ambiguous_call_error(
                                f"{self._format_exception(retry_exc)}: MCP "
                                "session closed; restarted once but explicit "
                                "safe retry failed; outcome remains ambiguous"
                            )

        # Log activity
        with self._activity_lock:
            self._activity_log.append({
                "tool": name,
                "args": args,
                "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if len(self._activity_log) > 50:
                self._activity_log[:] = self._activity_log[-50:]

        return result

    def list_tools(self, timeout: float = 10) -> list[dict]:
        """List available tools from the MCP server.

        Returns a list of dicts with 'name', 'description', and 'schema' keys,
        plus any supported tool metadata the server advertised. The listing is
        paginated until ``next_cursor`` is ``None``, so the catalog is complete
        even against a server that pages.
        """
        import asyncio

        # Explicit, so a closed client reports why rather than surfacing the
        # same message from inside the lazy start below.
        if self._closed:
            raise RuntimeError("MCP client has been closed")
        if not self.is_connected():
            self.start()

        if self._session is None or self._loop is None:
            raise RuntimeError("MCP client not connected")

        future = asyncio.run_coroutine_threadsafe(
            _list_all_tools(self._session), self._loop
        )
        return future.result(timeout=timeout)

    # ------------------------------------------------------------------
    # Negotiation diagnostics (read-only)
    # ------------------------------------------------------------------

    @property
    def protocol_version(self) -> str | None:
        """Protocol version the SDK negotiated, or ``None`` before connect."""
        return getattr(self._session, "protocol_version", None)

    @property
    def server_info(self) -> Any:
        """Server identity as reported at connect (may be ``None``)."""
        return getattr(self._session, "server_info", None)

    @property
    def server_capabilities(self) -> Any:
        """Capabilities the connected server advertised."""
        return getattr(self._session, "server_capabilities", None)

    @property
    def instructions(self) -> str | None:
        """Server-provided instructions string, if it sent one."""
        return getattr(self._session, "instructions", None)

    @property
    def last_tool_result(self) -> dict[str, Any] | None:
        """Full typed result of the most recent ``call_tool``.

        ``call_tool`` returns the legacy compatibility projection; this keeps
        the ordered content union, structured payload, error bit, result type
        and ``_meta`` of that same call available without a second round trip.
        """
        return self._last_result

    def get_activity_log(self) -> list[dict[str, Any]]:
        """Get recent MCP tool calls for debugging."""
        with self._activity_lock:
            return list(self._activity_log)

    # ------------------------------------------------------------------
    # Internal — background thread
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """Background thread: run the async event loop with the MCP session."""
        import asyncio

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_connect())
            loop.run_forever()
        except Exception as e:
            # Preserve the class name so a blank str(e) (e.g. ClosedResourceError)
            # does not produce "MCP server failed to start: " (issue #104).
            self._error = self._format_exception(e)
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(self._async_cleanup())
            except Exception:
                pass
            loop.close()

    async def _async_connect(self) -> None:
        """Establish the MCP stdio connection (runs in background thread).

        The official ``stdio_client`` transport is handed to the SDK v2
        ``Client``, whose default ``mode="auto"`` probes ``server/discover``
        and falls back to the legacy ``initialize`` handshake. Entering the
        client is the whole handshake: LingTai performs no negotiation itself.
        """
        from mcp import Client
        from mcp.client.stdio import stdio_client, StdioServerParameters

        server_params = StdioServerParameters(
            command=self._command,
            args=self._args,
            env=self._env,
        )

        self._client = Client(stdio_client(server_params))
        self._session = await self._client.__aenter__()

        self._ready.set()

    async def _async_cleanup(self) -> None:
        """Clean up the MCP client and its stdio transport."""
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass


class HTTPMCPClient:
    """Async-to-sync bridge for remote HTTP MCP servers.

    Connects to a remote MCP server via streamable HTTP transport.
    Same call_tool() interface as MCPClient.

    Args:
        url: HTTP endpoint of the MCP server (e.g., "https://api.z.ai/api/mcp/web_search_prime/mcp").
        headers: HTTP headers (e.g., {"Authorization": "Bearer ..."}).
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ):
        self._url = url
        self._headers = headers or {}

        self._session: Any = None
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._transport_cm: Any = None
        self._session_cm: Any = None
        self._client: Any = None
        self._http_client: Any = None
        self._last_result: dict[str, Any] | None = None

        self._activity_log: list[dict[str, Any]] = []
        self._activity_lock = threading.Lock()

    def start(self) -> None:
        """Connect to the remote MCP server.

        Raises:
            RuntimeError: If this client was explicitly closed. ``restart()``
                is the supported way back: it clears ``_closed`` first and
                rebuilds the transport (including the owning
                ``httpx2.AsyncClient``), so the subsequent ``start()`` is a
                real reconnect rather than an early return.
        """
        if self._closed:
            raise RuntimeError("HTTP MCP client has been closed")
        if self.is_connected():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=30)
        if self._error:
            raise RuntimeError(f"HTTP MCP server failed to connect: {self._error}")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    def restart(self) -> None:
        """Tear down a (possibly stale) session and reconnect from scratch.

        ``start()`` early-returns when it believes it is connected and never
        clears latched startup state, so a stale ``_ready``/``_error`` or a
        ``_closed`` flag from a prior ``close()`` would make a fresh
        ``start()`` lie (return immediately, or raise on the *old* error).
        This resets all startup/session fields — including the SDK client and
        its owning ``httpx2.AsyncClient`` — so the subsequent ``start()`` is
        a real reconnect. Used by ``call_tool`` to recover from a dropped
        HTTP/SSE stream (issue #740, the HTTP half of issue #104).
        """
        self.close()
        self._ready.clear()
        self._error = None
        self._closed = False
        self._session = None
        self._loop = None
        self._thread = None
        self._client = None
        self._http_client = None
        self._transport_cm = None
        self._session_cm = None
        self.start()

    def is_connected(self) -> bool:
        return (
            self._session is not None
            and self._loop is not None
            and self._loop.is_running()
            and not self._closed
        )

    def call_tool(self, name: str, args: dict, timeout: float = 120) -> Any:
        """Call an MCP tool synchronously. Same interface as MCPClient.

        Recovers a dropped HTTP/SSE stream (issue #740): on a stale transport
        error the client restarts its connection so a *future* call can
        succeed, and returns an ambiguous, non-retryable error dict for the
        current one. The submitted call is never replayed — the remote commit
        point is unknowable — matching the stdio client's default fail-closed
        policy. There is deliberately no ``retry_policy`` parameter here; that
        HTTP non-retry contract is stated policy, not an oversight.

        Non-stale failures return a non-blank
        ``{"status": "error", "message": ...}`` dict instead of raising, so a
        message-less exception (e.g. anyio's ``ClosedResourceError``) can never
        surface as a blank error again (issue #104). All outcomes, including
        failures, are recorded in ``_activity_log``.
        """
        import asyncio

        if self._closed:
            raise RuntimeError("HTTP MCP client has been closed")
        if not self.is_connected():
            self.start()
        if self._session is None or self._loop is None:
            raise RuntimeError("HTTP MCP client not connected")

        def _attempt() -> Any:
            async def _call():
                # SDK v2 timeouts are float seconds, not timedelta.
                result = await self._session.call_tool(
                    name=name,
                    arguments=args,
                    read_timeout_seconds=float(timeout),
                )
                self._last_result = preserve_tool_result(result)
                return _decode_tool_result(result)

            future = asyncio.run_coroutine_threadsafe(_call(), self._loop)
            try:
                return future.result(timeout=timeout)
            except TimeoutError as exc:
                # The coroutine crossed the submission boundary before this
                # timeout. The server may therefore have committed the call
                # even though this caller never received its response. Cancel
                # local work if possible, but never advertise a blind retry.
                future.cancel()
                formatted = MCPClient._format_exception(exc)
                logger.warning(
                    "HTTP MCP tool %s timed out after submission (%s); remote "
                    "outcome is ambiguous; not retrying",
                    name,
                    formatted,
                )
                return MCPClient._ambiguous_call_error(
                    f"{formatted}: HTTP MCP call was submitted but completion "
                    "timed out; remote outcome is ambiguous; call was not "
                    "retried to avoid duplicate side effects"
                )

        try:
            result = _attempt()
        except Exception as exc:
            formatted = MCPClient._format_exception(exc)
            if not MCPClient._is_stale_resource_error(exc):
                # Non-stale failure: surface the class name so the error is
                # never blank (issue #104), but don't churn the connection.
                result = {"status": "error", "message": formatted}
            else:
                # Recover the transport for a future independent call, but do
                # not replay this call: the server may have committed it before
                # the stale response path failed.
                logger.warning(
                    "HTTP MCP tool %s hit stale resource (%s); restarting "
                    "transport without replay",
                    name,
                    formatted,
                )
                try:
                    self.restart()
                except Exception as restart_exc:
                    result = MCPClient._ambiguous_call_error(
                        f"{formatted}: HTTP MCP session closed; remote outcome "
                        "is ambiguous; call was not retried to avoid duplicate "
                        "side effects; transport restart failed: "
                        f"{MCPClient._format_exception(restart_exc)}"
                    )
                else:
                    result = MCPClient._ambiguous_call_error(
                        f"{formatted}: HTTP MCP session closed; remote outcome "
                        "is ambiguous; call was not retried to avoid duplicate "
                        "side effects; transport restarted for a future call"
                    )

        # Log activity (failures included, so failed calls are debuggable).
        with self._activity_lock:
            self._activity_log.append({
                "tool": name,
                "args": args,
                "result": result,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            if len(self._activity_log) > 50:
                self._activity_log[:] = self._activity_log[-50:]

        return result

    def list_tools(self, timeout: float = 10) -> list[dict]:
        """List available tools from the MCP server (paginated to completion)."""
        import asyncio

        # Explicit, matching the stdio client and `call_tool` below.
        if self._closed:
            raise RuntimeError("HTTP MCP client has been closed")
        if not self.is_connected():
            self.start()
        if self._session is None or self._loop is None:
            raise RuntimeError("HTTP MCP client not connected")

        future = asyncio.run_coroutine_threadsafe(
            _list_all_tools(self._session), self._loop
        )
        return future.result(timeout=timeout)

    # ------------------------------------------------------------------
    # Negotiation diagnostics (read-only)
    # ------------------------------------------------------------------

    @property
    def protocol_version(self) -> str | None:
        """Protocol version the SDK negotiated, or ``None`` before connect."""
        return getattr(self._session, "protocol_version", None)

    @property
    def server_info(self) -> Any:
        """Server identity as reported at connect (may be ``None``)."""
        return getattr(self._session, "server_info", None)

    @property
    def server_capabilities(self) -> Any:
        """Capabilities the connected server advertised."""
        return getattr(self._session, "server_capabilities", None)

    @property
    def instructions(self) -> str | None:
        """Server-provided instructions string, if it sent one."""
        return getattr(self._session, "instructions", None)

    @property
    def last_tool_result(self) -> dict[str, Any] | None:
        """Full typed result of the most recent ``call_tool``."""
        return self._last_result

    def get_activity_log(self) -> list[dict[str, Any]]:
        """Get recent MCP tool calls for debugging (failures included)."""
        with self._activity_lock:
            return list(self._activity_log)

    def _run_loop(self) -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_connect())
            loop.run_forever()
        except Exception as e:
            # Preserve the class name so a blank str(e) is not surfaced as an
            # empty connect error (issue #104).
            self._error = MCPClient._format_exception(e)
            self._ready.set()
        finally:
            try:
                loop.run_until_complete(self._async_cleanup())
            except Exception:
                pass
            loop.close()

    async def _async_connect(self) -> None:
        """Connect over official Streamable HTTP (SDK v2).

        v1's ``streamablehttp_client`` alias is gone. ``streamable_http_client``
        keeps only ``url``/``http_client``, so headers and timeouts move onto an
        ``httpx2.AsyncClient`` we own and must close ourselves. The timeout and
        ``follow_redirects`` values reproduce what v1's internal client used.
        """
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        self._http_client = httpx2.AsyncClient(
            headers=self._headers,
            timeout=httpx2.Timeout(30, read=300),
            follow_redirects=True,
        )
        await self._http_client.__aenter__()

        self._client = Client(
            streamable_http_client(url=self._url, http_client=self._http_client)
        )
        self._session = await self._client.__aenter__()
        self._ready.set()

    async def _async_cleanup(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
        if self._http_client:
            try:
                await self._http_client.__aexit__(None, None, None)
            except Exception:
                pass
