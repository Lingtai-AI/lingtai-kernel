"""Feishu/Lark logging must not contaminate the MCP stdio protocol."""
from __future__ import annotations

import io
import json
import logging
import os
import selectors
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from lingtai.mcp_servers.feishu import account


_LARK_DIAGNOSTIC = "PR751_PRE_STDIO_LARK_DIAGNOSTIC"


class _PreservedHandler(logging.Handler):
    """A non-stream handler used to prove unrelated logger state is untouched."""

    def emit(self, record: logging.LogRecord) -> None:
        return None



def test_lark_stdout_handlers_are_rerouted_without_changing_logger_state(capsys):
    # Import the real SDK once before arranging the logger. The SDK's own
    # one-time import side effect is then part of the state we restore, while
    # the assertions below exercise both the fresh account cache path and the
    # repeated cached path without allowing a second SDK handler to appear.
    previous_lark = account.lark
    account.lark = None
    account._import_lark()
    account.lark = previous_lark

    logger = logging.getLogger("Lark")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_disabled = logger.disabled
    original_propagate = logger.propagate
    original_filters = logger.filters[:]

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(logging.Formatter("stdout %(message)s"))
    stdout_filter = logging.Filter("Lark")
    stdout_handler.addFilter(stdout_filter)

    original_stdout_handler = logging.StreamHandler(sys.__stdout__)
    original_stdout_handler.setLevel(logging.ERROR)
    original_stdout_handler.setFormatter(logging.Formatter("original %(message)s"))
    original_stdout_filter = logging.Filter("Lark")
    original_stdout_handler.addFilter(original_stdout_filter)

    preserved_handler = _PreservedHandler()
    preserved_handler.setLevel(logging.ERROR)
    preserved_handler.setFormatter(logging.Formatter("preserved %(message)s"))
    preserved_filter = logging.Filter("Lark")
    preserved_handler.addFilter(preserved_filter)
    arranged_handlers = [stdout_handler, preserved_handler, original_stdout_handler]

    try:
        logger.handlers[:] = arranged_handlers
        logger.setLevel(logging.INFO)
        logger.disabled = False
        # Guard the exact #1036 regression: routing must not force propagation off.
        logger.propagate = True
        expected_logger_state = (logger.level, logger.disabled, logger.propagate)
        expected_handler_state = [
            (
                handler,
                handler.level,
                handler.formatter,
                (*handler.filters, account._lark_credential_filter),
            )
            for handler in arranged_handlers
        ]

        previous_lark = account.lark
        account.lark = None
        try:
            # Exercise both the real SDK import path and its cached return path.
            assert account._import_lark() is account._import_lark()
        finally:
            account.lark = previous_lark

        assert logger.handlers == arranged_handlers
        assert len(logger.handlers) == len(arranged_handlers)
        assert [
            (handler, handler.level, handler.formatter, tuple(handler.filters))
            for handler in logger.handlers
        ] == expected_handler_state
        assert (logger.level, logger.disabled, logger.propagate) == expected_logger_state
        assert logger.filters.count(account._lark_credential_filter) == 1
        assert stdout_handler.stream is sys.stderr
        assert original_stdout_handler.stream is sys.stderr

        logger.info("PR751_HANDLER_ROUTING_DIAGNOSTIC")
        captured = capsys.readouterr()
        assert "PR751_HANDLER_ROUTING_DIAGNOSTIC" not in captured.out
        assert "PR751_HANDLER_ROUTING_DIAGNOSTIC" in captured.err
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.disabled = original_disabled
        logger.propagate = original_propagate
        logger.filters[:] = original_filters


def test_lark_credentials_are_redacted_before_handler_rendering():
    logger = logging.getLogger("Lark")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_disabled = logger.disabled
    original_propagate = logger.propagate
    original_filters = logger.filters[:]
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    try:
        logger.handlers[:] = [handler]
        logger.setLevel(logging.INFO)
        logger.disabled = False
        logger.propagate = False

        account._route_lark_stdout_handlers_to_stderr()
        account._route_lark_stdout_handlers_to_stderr()
        logger.info(
            "connected %s",
            "wss://example.invalid/ws?access_key=access-secret"
            "&ticket=ticket-secret&trace=visible",
        )

        rendered = stream.getvalue()
        assert "access-secret" not in rendered
        assert "ticket-secret" not in rendered
        assert "access_key=[REDACTED]" in rendered
        assert "ticket=[REDACTED]" in rendered
        assert "trace=visible" in rendered
        assert handler.filters.count(account._lark_credential_filter) == 1
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.disabled = original_disabled
        logger.propagate = original_propagate
        logger.filters[:] = original_filters


_CHILD = textwrap.dedent(
    f"""
    import asyncio
    import logging

    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    from lingtai.mcp_servers.feishu.account import _import_lark


    async def main():
        # This is the vulnerable interval: the real installed Lark SDK is imported
        # and emits before the real MCP stdio transport claims fd 1.
        _import_lark()
        lark_logger = logging.getLogger("Lark")
        lark_logger.setLevel(logging.INFO)
        lark_logger.info({_LARK_DIAGNOSTIC!r})

        async def list_tools(_ctx, _params):
            return types.ListToolsResult(
                tools=[
                    types.Tool(
                        name="stdio_probe",
                        description="owned no-network probe",
                        input_schema={{"type": "object"}},
                    )
                ]
            )

        async def call_tool(_ctx, _params):
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="ok")]
            )

        server = Server(
            "pr751-stdio-probe",
            on_list_tools=list_tools,
            on_call_tool=call_tool,
        )
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )


    asyncio.run(main())
    """
)


def _read_child_line(
    selector: selectors.BaseSelector,
    stdout_lines: list[str],
    stderr_lines: list[str],
    deadline: float,
) -> None:
    ready = selector.select(max(0.0, deadline - time.monotonic()))
    if not ready:
        return
    for key, _events in ready:
        line = key.fileobj.readline()
        if not line:
            selector.unregister(key.fileobj)
            continue
        decoded = line.decode("utf-8", errors="replace").rstrip("\n")
        if key.data == "stdout":
            stdout_lines.append(decoded)
        else:
            stderr_lines.append(decoded)


def test_real_child_mcp_stdio_has_only_protocol_stdout_and_lark_stderr():
    """The actual Lark import and MCP v2 stdio exchange stay fd-separated."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    # Explicitly select the candidate tree and avoid inherited config/credentials.
    env["PYTHONPATH"] = str(source_root)
    env.pop("LINGTAI_FEISHU_CONFIG", None)
    env.pop("LINGTAI_AGENT_DIR", None)

    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        cwd=str(source_root.parent),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    selector = selectors.DefaultSelector()
    assert child.stdout is not None
    assert child.stderr is not None
    assert child.stdin is not None
    selector.register(child.stdout, selectors.EVENT_READ, "stdout")
    selector.register(child.stderr, selectors.EVENT_READ, "stderr")
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def send(message: dict) -> None:
        child.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        child.stdin.flush()

    initialize_id = 1
    tools_id = 2
    responses: dict[int, dict] = {}
    try:
        send(
            {
                "jsonrpc": "2.0",
                "id": initialize_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "pr751-test", "version": "1"},
                },
            }
        )

        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and initialize_id not in responses:
            before = len(stdout_lines)
            _read_child_line(selector, stdout_lines, stderr_lines, deadline)
            for line in stdout_lines[before:]:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == initialize_id:
                    responses[initialize_id] = message
                    break

        assert initialize_id in responses, (
            "owned child did not return initialize response within timeout; "
            f"stdout={stdout_lines!r} stderr={stderr_lines!r}"
        )
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        send({"jsonrpc": "2.0", "id": tools_id, "method": "tools/list", "params": {}})

        while time.monotonic() < deadline and tools_id not in responses:
            before = len(stdout_lines)
            _read_child_line(selector, stdout_lines, stderr_lines, deadline)
            for line in stdout_lines[before:]:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("id") == tools_id:
                    responses[tools_id] = message
                    break

        assert tools_id in responses, (
            "owned child did not return tools/list response within timeout; "
            f"stdout={stdout_lines!r} stderr={stderr_lines!r}"
        )
        child.stdin.close()
        child.stdin = None
        child.wait(timeout=10.0)

        # The process is owned by this test; drain any final diagnostics without
        # scanning for or touching unrelated processes.
        stdout_lines.extend(
            line.decode("utf-8", errors="replace").rstrip("\n")
            for line in child.stdout.read().splitlines()
        )
        stderr_lines.extend(
            line.decode("utf-8", errors="replace").rstrip("\n")
            for line in child.stderr.read().splitlines()
        )
    finally:
        selector.close()
        if child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5.0)

    assert child.returncode == 0
    parsed_stdout: list[dict] = []
    for line in stdout_lines:
        if not line.strip():
            continue
        message = json.loads(line)
        assert message.get("jsonrpc") == "2.0"
        assert "id" in message and ("result" in message or "error" in message)
        parsed_stdout.append(message)

    by_id = {message["id"]: message for message in parsed_stdout}
    assert by_id[initialize_id]["id"] == initialize_id
    assert by_id[initialize_id].get("result", {}).get("serverInfo", {}).get("name") == (
        "pr751-stdio-probe"
    )
    assert by_id[tools_id]["id"] == tools_id
    assert by_id[tools_id].get("result", {}).get("tools", [])[0]["name"] == (
        "stdio_probe"
    )
    assert _LARK_DIAGNOSTIC not in "\n".join(stdout_lines)
    assert _LARK_DIAGNOSTIC in "\n".join(stderr_lines)
