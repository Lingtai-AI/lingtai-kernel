"""MCP v2 metadata sidecars and real transport lifecycle.

`FunctionSchema` has fields only for name/description/parameters, so the v2
metadata a server advertises (title, output schema, annotations, icons,
execution, meta) is retained beside the registered tool instead of being forced
into the provider wire schema. These tests pin that the sidecar exists, is
copied rather than aliased, and dies with the clients it describes.

The stdio lifecycle tests here start a **real** subprocess MCP server (a
bundled one, over the local SDK) rather than a fake, because the fakes in
`tests/test_mcp_closed_resource_restart.py` deliberately never spawn a process
and so cannot prove start/close/restart against a live transport.
"""

from __future__ import annotations

import sys

import pytest

from lingtai.services.mcp import HTTPMCPClient, MCPClient, tool_metadata
from tests._daemon_helpers import make_daemon_agent

# A bundled, dependency-free stdio server: `finish` needs only a writable
# completion path, so it starts anywhere the test suite runs.
_REAL_SERVER_ARGS = ["-m", "lingtai.mcp_servers.daemon_common"]


def _record(name: str = "demo_tool", **extra) -> dict:
    record = {
        "name": name,
        "description": "Demo tool",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    record.update(extra)
    return record


def _full_record(name: str = "demo_tool") -> dict:
    return _record(
        name,
        title="Demo Tool",
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        annotations={"readOnlyHint": True},
        icons=[{"src": "https://example.invalid/i.png"}],
        execution={"taskSupport": "optional"},
        meta={"vendor/trace": "abc"},
    )


class _FakeClient:
    """Minimal stand-in for either service client."""

    def __init__(self, records=None, **_kw):
        self._records = records if records is not None else [_full_record()]
        self.closed = False

    def start(self):
        pass

    def list_tools(self):
        return [dict(record) for record in self._records]

    def call_tool(self, name, args):
        return {"called": name, "args": args}

    def close(self):
        self.closed = True

    def is_connected(self):
        return not self.closed


# ---------------------------------------------------------------------------
# The shared service helper
# ---------------------------------------------------------------------------

def test_tool_metadata_extracts_only_unrepresentable_fields():
    metadata = tool_metadata(_full_record())

    assert set(metadata) == {
        "title", "output_schema", "annotations", "icons", "execution", "meta",
    }
    # name/description/schema stay with FunctionSchema, not the sidecar.
    assert "name" not in metadata and "schema" not in metadata


def test_tool_metadata_omits_fields_the_server_did_not_advertise():
    assert tool_metadata(_record()) == {}


def test_tool_metadata_deep_copies_so_the_sidecar_never_aliases_the_record():
    record = _full_record()
    metadata = tool_metadata(record)

    metadata["annotations"]["readOnlyHint"] = False

    assert record["annotations"]["readOnlyHint"] is True


# ---------------------------------------------------------------------------
# Agent — both transports
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(tmp_path):
    return make_daemon_agent(tmp_path, ["daemon"])


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_agent_retains_advertised_metadata_for_registered_tools(
    agent, monkeypatch, transport,
):
    import lingtai.services.mcp as mcp_mod

    if transport == "stdio":
        monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
        agent.connect_mcp("python", ["-m", "demo"])
    else:
        monkeypatch.setattr(mcp_mod, "HTTPMCPClient", _FakeClient)
        agent.connect_mcp_http("https://example.invalid/mcp")

    metadata = agent.mcp_tool_metadata("demo_tool")

    assert metadata["title"] == "Demo Tool"
    assert metadata["output_schema"]["properties"] == {"ok": {"type": "boolean"}}
    assert metadata["annotations"] == {"readOnlyHint": True}
    assert metadata["execution"] == {"taskSupport": "optional"}
    assert metadata["meta"] == {"vendor/trace": "abc"}


def test_agent_metadata_is_absent_for_an_unregistered_tool(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    agent.connect_mcp("python", ["-m", "demo"])

    assert agent.mcp_tool_metadata("never_registered") is None


def test_agent_metadata_read_returns_a_copy_not_the_stored_record(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    agent.connect_mcp("python", ["-m", "demo"])

    agent.mcp_tool_metadata("demo_tool")["title"] = "MUTATED"

    assert agent.mcp_tool_metadata("demo_tool")["title"] == "Demo Tool"


def test_agent_registration_does_not_mutate_the_client_record(agent, monkeypatch):
    """The `additionalProperties` normalization must not reach the sidecar."""
    import lingtai.services.mcp as mcp_mod
    records = [_full_record()]
    monkeypatch.setattr(
        mcp_mod, "MCPClient", lambda **kw: _FakeClient(records=records),
    )

    agent.connect_mcp("python", ["-m", "demo"])

    assert records[0]["schema"]["additionalProperties"] is False


def test_agent_stop_drops_metadata_for_closed_clients(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    agent.connect_mcp("python", ["-m", "demo"])
    assert agent.mcp_tool_metadata("demo_tool") is not None

    agent.stop()

    assert agent.mcp_tool_metadata("demo_tool") is None


def test_agent_reregistration_replaces_rather_than_merges(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    agent.connect_mcp("python", ["-m", "demo"])

    monkeypatch.setattr(
        mcp_mod, "MCPClient", lambda **kw: _FakeClient(records=[_record()]),
    )
    agent.connect_mcp("python", ["-m", "demo2"])

    # The second server advertises no metadata; the first server's title must
    # not survive under the same tool name.
    assert agent.mcp_tool_metadata("demo_tool") == {}


# ---------------------------------------------------------------------------
# Task daemon
# ---------------------------------------------------------------------------

def _stdio_registration() -> dict:
    return {
        "name": "demo-mcp",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "demo"],
    }


def _http_registration() -> dict:
    return {
        "name": "demo-mcp",
        "transport": "http",
        "url": "https://example.invalid/mcp",
    }


def test_task_daemon_retains_and_copies_metadata(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    manager = agent.get_capability("daemon")

    _schemas, _handlers, clients = manager._connect_task_mcp_registrations(
        [_stdio_registration()]
    )
    try:
        metadata = manager.task_mcp_tool_metadata("demo_tool")
        assert metadata["title"] == "Demo Tool"
        assert metadata["annotations"] == {"readOnlyHint": True}

        manager.task_mcp_tool_metadata("demo_tool")["title"] = "MUTATED"
        assert manager.task_mcp_tool_metadata("demo_tool")["title"] == "Demo Tool"
    finally:
        manager._close_task_mcp_clients(clients)


def test_task_daemon_close_clears_metadata(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    manager = agent.get_capability("daemon")

    _schemas, _handlers, clients = manager._connect_task_mcp_registrations(
        [_stdio_registration()]
    )
    assert manager.task_mcp_tool_metadata("demo_tool") is not None

    manager._close_task_mcp_clients(clients)

    assert manager.task_mcp_tool_metadata("demo_tool") is None


def test_task_daemon_failed_connect_publishes_no_metadata(agent, monkeypatch):
    """A duplicate tool name aborts the surface; nothing may remain visible."""
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(
        mcp_mod,
        "MCPClient",
        lambda **kw: _FakeClient(records=[_full_record(), _full_record()]),
    )
    manager = agent.get_capability("daemon")

    with pytest.raises(ValueError, match="duplicate MCP tool name"):
        manager._connect_task_mcp_registrations([_stdio_registration()])

    assert manager.task_mcp_tool_metadata("demo_tool") is None


def test_task_daemon_empty_registrations_clear_prior_metadata(agent, monkeypatch):
    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _FakeClient)
    manager = agent.get_capability("daemon")
    _s, _h, clients = manager._connect_task_mcp_registrations([_stdio_registration()])
    assert manager.task_mcp_tool_metadata("demo_tool") is not None

    manager._connect_task_mcp_registrations([])

    assert manager.task_mcp_tool_metadata("demo_tool") is None
    manager._close_task_mcp_clients(clients)


@pytest.mark.parametrize("transport", ["stdio", "http"])
@pytest.mark.parametrize(
    ("schema", "arguments", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "additionalProperties": False,
            },
            {
                "value": "business",
                "unknown_business_field": "server validates this",
                "_reasoning": "kernel audit metadata",
            },
            {
                "value": "business",
                "unknown_business_field": "server validates this",
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "input": {"type": "object"},
                    "reasoning": {"type": "string"},
                    "summarize": {"type": "boolean"},
                },
                "required": ["action", "input", "reasoning"],
                "additionalProperties": False,
            },
            {
                "action": "accounts",
                "input": {},
                "_reasoning": "strict family rationale",
            },
            {
                "action": "accounts",
                "input": {},
                "reasoning": "strict family rationale",
            },
        ),
        (
            {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "_reasoning": {"type": "string"},
                },
                "additionalProperties": False,
            },
            {"value": "declared", "_reasoning": "server-owned field"},
            {"value": "declared", "_reasoning": "server-owned field"},
        ),
    ],
    ids=["undeclared-private", "strict-ltp-v2", "declared-private"],
)
def test_task_daemon_adapts_host_private_arguments_at_mcp_boundary(
    agent, monkeypatch, transport, schema, arguments, expected
):
    import lingtai.services.mcp as mcp_mod

    records = [_record(schema=schema)]
    client_class = "MCPClient" if transport == "stdio" else "HTTPMCPClient"
    monkeypatch.setattr(
        mcp_mod,
        client_class,
        lambda **kw: _FakeClient(records=records),
    )
    manager = agent.get_capability("daemon")
    original = dict(arguments)
    registration = (
        _stdio_registration() if transport == "stdio" else _http_registration()
    )

    _schemas, handlers, clients = manager._connect_task_mcp_registrations(
        [registration]
    )
    try:
        result = handlers["demo_tool"](arguments)
    finally:
        manager._close_task_mcp_clients(clients)

    assert arguments == original
    assert result == {"called": "demo_tool", "args": expected}


# ---------------------------------------------------------------------------
# Real stdio transport lifecycle (subprocess; no network, no credentials)
# ---------------------------------------------------------------------------

@pytest.fixture
def real_stdio_client(tmp_path):
    client = MCPClient(
        command=sys.executable,
        args=_REAL_SERVER_ARGS,
        env={
            "LINGTAI_DAEMON_COMPLETION_FILE": str(tmp_path / "completion.json"),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": ":".join(sys.path),
        },
    )
    try:
        yield client
    finally:
        client.close()


def test_real_stdio_start_lists_tools_over_a_live_subprocess(real_stdio_client):
    real_stdio_client.start()

    assert real_stdio_client.is_connected()
    assert [t["name"] for t in real_stdio_client.list_tools()] == ["checkpoint", "finish"]
    assert real_stdio_client.protocol_version == "2026-07-28"


def test_real_stdio_call_tool_round_trips(real_stdio_client, tmp_path):
    result = real_stdio_client.call_tool(
        "finish",
        {
            "action": "finish",
            "input": {"status": "done", "summary": "lifecycle probe"},
            "reasoning": "lifecycle probe",
        },
    )

    assert result["status"] == "ok"
    assert (tmp_path / "completion.json").exists()


def test_real_stdio_close_then_reuse_is_refused(real_stdio_client):
    real_stdio_client.start()
    real_stdio_client.close()

    assert not real_stdio_client.is_connected()
    with pytest.raises(RuntimeError, match="closed"):
        real_stdio_client.call_tool("finish", {"status": "done"})


def test_real_stdio_start_after_close_refuses_and_spawns_nothing(real_stdio_client):
    """A closed client must not silently acquire a second subprocess.

    `close()` early-returns on an already-closed client, so a transport
    acquired after close could never be shut down.
    """
    real_stdio_client.start()
    real_stdio_client.close()
    thread_before = real_stdio_client._thread

    with pytest.raises(RuntimeError, match="closed"):
        real_stdio_client.start()

    assert real_stdio_client._thread is thread_before
    assert not real_stdio_client.is_connected()


def test_real_stdio_list_tools_after_close_refuses(real_stdio_client):
    """`list_tools` lazily starts, so it must refuse too."""
    real_stdio_client.start()
    real_stdio_client.close()
    thread_before = real_stdio_client._thread

    with pytest.raises(RuntimeError, match="closed"):
        real_stdio_client.list_tools()

    assert real_stdio_client._thread is thread_before


def test_real_stdio_repeated_close_is_safe(real_stdio_client):
    real_stdio_client.start()
    real_stdio_client.close()
    real_stdio_client.close()

    assert not real_stdio_client.is_connected()


def test_real_stdio_restart_reconnects_a_closed_client(real_stdio_client):
    """`restart()` remains the supported way back: it clears `_closed` first."""
    real_stdio_client.start()
    real_stdio_client.close()

    real_stdio_client.restart()

    assert real_stdio_client.is_connected()
    assert [t["name"] for t in real_stdio_client.list_tools()] == ["checkpoint", "finish"]


def test_real_stdio_failed_connect_reports_the_failure(tmp_path):
    """A command that exits immediately must surface, not hang or look live."""
    client = MCPClient(command=sys.executable, args=["-c", "raise SystemExit(1)"])
    try:
        with pytest.raises(RuntimeError, match="MCP server failed to start"):
            client.start()
        assert not client.is_connected()
    finally:
        client.close()


# ---------------------------------------------------------------------------
# HTTP: supplied httpx2.AsyncClient ownership
# ---------------------------------------------------------------------------

def test_http_client_owns_and_closes_its_httpx2_client(monkeypatch):
    """The SDK does not manage a supplied client, so this module must."""
    import httpx2

    created = []
    real_async_client = httpx2.AsyncClient

    class _TrackedAsyncClient(real_async_client):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.exited = False
            created.append(self)

        async def __aexit__(self, *exc):
            self.exited = True
            return await super().__aexit__(*exc)

    monkeypatch.setattr(httpx2, "AsyncClient", _TrackedAsyncClient)

    client = HTTPMCPClient(url="http://127.0.0.1:1/mcp")
    # Connecting to a closed port fails; the point is that teardown still runs
    # the httpx2 client's __aexit__ rather than leaking the connection pool.
    with pytest.raises(RuntimeError):
        client.start()
    client.close()

    assert created, "HTTPMCPClient must build its own httpx2.AsyncClient"
    assert created[0].exited is True


def test_http_call_tool_exposes_no_retry_policy():
    """HTTP never replays; the absence of the knob is the contract."""
    import inspect

    assert "retry_policy" not in inspect.signature(HTTPMCPClient.call_tool).parameters


@pytest.fixture
def counted_httpx2(monkeypatch):
    """Count every `httpx2.AsyncClient` the HTTP client builds."""
    import httpx2

    created = []
    real_async_client = httpx2.AsyncClient

    class _CountedAsyncClient(real_async_client):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created.append(self)

    monkeypatch.setattr(httpx2, "AsyncClient", _CountedAsyncClient)
    return created


def _closed_http_client(counted):
    """An HTTP client that attempted one connect and was then closed."""
    client = HTTPMCPClient(url="http://127.0.0.1:1/mcp")
    with pytest.raises(RuntimeError):
        client.start()
    client.close()
    return client, len(counted)


def test_http_start_after_close_refuses_and_builds_no_new_client(counted_httpx2):
    """HTTP has no `restart()`, so it stays one-shot after close.

    Reviving it would build a second AsyncClient that the early-returning
    `close()` could never shut down, leaking the connection pool.
    """
    client, count_at_close = _closed_http_client(counted_httpx2)

    with pytest.raises(RuntimeError, match="closed"):
        client.start()

    assert len(counted_httpx2) == count_at_close


def test_http_list_tools_after_close_refuses_and_builds_no_new_client(counted_httpx2):
    client, count_at_close = _closed_http_client(counted_httpx2)

    with pytest.raises(RuntimeError, match="closed"):
        client.list_tools()

    assert len(counted_httpx2) == count_at_close


def test_http_repeated_close_is_safe(counted_httpx2):
    client, count_at_close = _closed_http_client(counted_httpx2)

    client.close()

    assert not client.is_connected()
    assert len(counted_httpx2) == count_at_close


# ---------------------------------------------------------------------------
# mode="auto" -> legacy fallback against a discover-rejecting peer
# ---------------------------------------------------------------------------

def test_auto_mode_falls_back_to_initialize_against_a_legacy_peer():
    """LingTai's clients never pass `mode`, so they inherit `mode="auto"`.

    The prior contract test pinned the legacy wire by forcing `mode="legacy"`,
    which does not prove the *fallback* happens. This drives a scripted peer
    that rejects `server/discover`, using the SDK's own in-memory stream pair,
    and asserts the client still lands on a handshake-era version.
    """
    import anyio
    import mcp.types as types
    from contextlib import asynccontextmanager
    from mcp import Client
    from mcp.shared.memory import create_client_server_memory_streams
    from mcp.shared.message import SessionMessage
    from mcp.types import ServerCapabilities
    from mcp_types.version import LATEST_HANDSHAKE_VERSION

    methods_seen: list[str] = []

    async def scripted_legacy_server(streams):
        server_read, server_write = streams
        async for message in server_read:
            frame = message.message
            methods_seen.append(frame.method)
            if isinstance(frame, types.JSONRPCNotification):
                continue
            if frame.method == "server/discover":
                # A pre-2026 server has no such method.
                await server_write.send(SessionMessage(types.JSONRPCError(
                    jsonrpc="2.0",
                    id=frame.id,
                    error=types.ErrorData(
                        code=types.METHOD_NOT_FOUND, message="unknown method",
                    ),
                )))
            elif frame.method == "initialize":
                result = types.InitializeResult(
                    protocol_version=LATEST_HANDSHAKE_VERSION,
                    capabilities=ServerCapabilities(),
                    server_info=types.Implementation(
                        name="legacy-only", version="0.0.1",
                    ),
                )
                await server_write.send(SessionMessage(types.JSONRPCResponse(
                    jsonrpc="2.0",
                    id=frame.id,
                    result=result.model_dump(
                        by_alias=True, mode="json", exclude_none=True,
                    ),
                )))

    @asynccontextmanager
    async def scripted_transport():
        async with (
            create_client_server_memory_streams() as (
                (client_read, client_write), server_streams,
            ),
            anyio.create_task_group() as tg,
        ):
            tg.start_soon(scripted_legacy_server, server_streams)
            yield client_read, client_write
            tg.cancel_scope.cancel()

    async def _run():
        # No `mode=`: exactly what services/mcp.py constructs.
        with anyio.fail_after(10):
            async with Client(scripted_transport()) as client:
                return client.protocol_version, client.server_info.name

    version, server_name = anyio.run(_run)

    assert version == LATEST_HANDSHAKE_VERSION
    assert server_name == "legacy-only"
    # The probe was attempted and then abandoned for the handshake.
    assert methods_seen[:2] == ["server/discover", "initialize"]
