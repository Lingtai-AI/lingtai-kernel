"""Official MCP Python SDK v2 contract at the LingTai boundary.

Two surfaces are covered here:

* the bundled low-level servers, exercised through an in-memory ``Client`` so
  the assertions are about what a real client negotiates and receives, not
  about how a handler was registered;
* the service-boundary adapters (``_tool_record``, ``preserve_tool_result``,
  ``_list_all_tools``), which are the LingTai-owned translation between typed
  SDK results and the kernel-facing projection.

No external network or credentials: server cases use a ``None`` manager and an
in-memory ``Client`` except for one focused loopback Streamable HTTP assertion,
which verifies the real wire error code and shuts its local server down.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import (
    INVALID_PARAMS,
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
    Tool,
)

from lingtai.mcp_servers.cloud_mail.server import build_server as build_cloud_mail
from lingtai.mcp_servers.daemon_common.server import build_server as build_daemon_common
from lingtai.mcp_servers.feishu.server import build_server as build_feishu
from lingtai.mcp_servers.imap.server import build_server as build_imap
from lingtai.mcp_servers.telegram.server import build_server as build_telegram
from lingtai.mcp_servers.wechat.server import build_server as build_wechat
from lingtai.mcp_servers.whatsapp.server import build_server as build_whatsapp
from lingtai.services.mcp import (
    _decode_tool_result,
    _list_all_tools,
    _tool_record,
    preserve_tool_result,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Bundled servers over an in-memory v2 Client
# ---------------------------------------------------------------------------

TOOL_ONLY_SERVERS = [
    pytest.param(build_daemon_common, ("checkpoint", "finish"), id="daemon-common"),
    pytest.param(lambda: build_cloud_mail(None), "cloud_mail", id="cloud-mail"),
]

RESOURCE_SERVERS = [
    pytest.param(lambda: build_telegram(None), "telegram", id="telegram"),
    pytest.param(lambda: build_feishu(None), "feishu", id="feishu"),
    pytest.param(lambda: build_whatsapp(None), "whatsapp", id="whatsapp"),
    pytest.param(lambda: build_imap(None), "imap", id="imap"),
    # WeChat is the one server whose builder also carries startup-failure
    # detail into its not-initialized tool result.
    pytest.param(
        lambda: build_wechat(
            None, startup_error="poller lock busy", startup_error_type="PollerLockBusy",
        ),
        "wechat",
        id="wechat",
    ),
]


@pytest.mark.parametrize(
    ("build", "tool_name"), TOOL_ONLY_SERVERS + RESOURCE_SERVERS
)
async def test_server_negotiates_2026_protocol_and_lists_its_tool(build, tool_name):
    async with Client(build()) as client:
        assert client.protocol_version == "2026-07-28"

        result = await client.list_tools()

        # A single page: the pagination loop in the service boundary must be
        # able to terminate on this listing. Telegram now advertises only its
        # own public family; the public Task Card root is intrinsic.
        assert result.next_cursor is None
        expected_names = [tool_name] if isinstance(tool_name, str) else list(tool_name)
        assert [tool.name for tool in result.tools] == expected_names
        assert isinstance(result.tools[0].input_schema, dict)


@pytest.mark.parametrize(
    ("build", "tool_name"), TOOL_ONLY_SERVERS + RESOURCE_SERVERS
)
async def test_server_still_serves_a_legacy_handshake_client(build, tool_name):
    """``mode="auto"`` falls back to the pre-2026 handshake on old servers.

    LingTai does not own that negotiation, but it does own whether its servers
    remain usable on the handshake-era wire that the fallback lands on. Pinning
    ``mode="legacy"`` reproduces exactly that connection.
    """
    async with Client(build(), mode="legacy") as client:
        assert client.protocol_version != "2026-07-28"

        tools = (await client.list_tools()).tools

        expected_names = [tool_name] if isinstance(tool_name, str) else list(tool_name)
        assert [tool.name for tool in tools] == expected_names


@pytest.mark.parametrize(("build", "tool_name"), TOOL_ONLY_SERVERS)
async def test_tool_only_server_advertises_no_resource_capability(build, tool_name):
    async with Client(build()) as client:
        capabilities = client.server_capabilities

        # Capabilities follow the registered handlers; a server with no
        # resource handler must not claim resources.
        assert capabilities.tools is not None
        assert capabilities.resources is None
        assert capabilities.prompts is None


@pytest.mark.parametrize(("build", "tool_name"), RESOURCE_SERVERS)
async def test_resource_server_lists_and_reads_a_resource(build, tool_name):
    async with Client(build()) as client:
        assert client.server_capabilities.resources is not None

        listing = await client.list_resources()
        assert listing.resources
        assert all(resource.mime_type for resource in listing.resources)

        contents = (await client.read_resource(str(listing.resources[0].uri))).contents
        assert [type(block) for block in contents] == [TextResourceContents]
        assert isinstance(contents[0].text, str)


async def test_provider_failure_stays_a_readable_tool_error():
    """A not-initialized manager is a recoverable tool error, not -32603."""
    async with Client(build_cloud_mail(None)) as client:
        result = await client.call_tool(
            "cloud_mail",
            {"action": "accounts", "input": {}, "reasoning": "inspect accounts"},
        )

    assert result.is_error is True
    assert result.structured_content["status"] == "error"
    assert json.loads(result.content[0].text) == result.structured_content


async def test_cloud_mail_startup_failure_keeps_operational_behavior_and_strict_settings():
    async with Client(build_cloud_mail(None)) as client:
        operational = await client.call_tool(
            "cloud_mail",
            {"action": "status", "input": {}, "reasoning": "invalid probe"},
        )
        invalid_settings = await client.call_tool(
            "cloud_mail",
            {
                "action": "settings",
                "input": {"set": "config_path"},
                "reasoning": "invalid probe",
            },
        )
        settings = await client.call_tool(
            "cloud_mail",
            {"action": "settings", "input": {}, "reasoning": "diagnose startup"},
        )

    assert operational.is_error is True
    assert operational.structured_content["status"] == "error"
    startup_guidance = operational.structured_content["error"]
    assert "manager not initialized" in startup_guidance
    assert "stderr for the exception class" in startup_guidance
    assert "verify the environment and configuration" in startup_guidance
    assert "underlying exception" not in startup_guidance
    assert invalid_settings.is_error is True
    assert invalid_settings.structured_content == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "invalid cloud_mail input",
    }
    assert settings.is_error is True
    assert settings.structured_content == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


async def test_wechat_startup_failure_detail_survives_into_the_tool_result():
    """WeChat carries concrete remediation detail, not just "check stderr"."""
    server = build_wechat(
        None, startup_error="poller lock busy", startup_error_type="PollerLockBusy",
    )
    async with Client(server) as client:
        result = await client.call_tool("wechat", {"action": "status"})

    assert result.is_error is True
    assert result.structured_content["startup_error_type"] == "PollerLockBusy"
    assert result.structured_content["startup_error"] == "poller lock busy"


async def test_imap_resource_read_carries_its_provider_mime_type():
    """IMAP is the one server whose reads were `ReadResourceContents` in v1."""
    async with Client(build_imap(None)) as client:
        listing = await client.list_resources()
        target = listing.resources[0]
        contents = (await client.read_resource(str(target.uri))).contents

    assert contents[0].mime_type == target.mime_type
    assert isinstance(contents[0].text, str)


@pytest.mark.parametrize(
    ("build", "tool_name"), TOOL_ONLY_SERVERS + RESOURCE_SERVERS
)
async def test_unknown_tool_name_is_invalid_params(build, tool_name):
    """A lookup miss is a caller-fixable parameter error, not a server fault.

    The 2026-07-28 schema lists an unknown tool name under
    ``InvalidParamsError``. Raising an explicit ``MCPError`` is what preserves
    the chosen code: a generic exception would be flattened to
    ``INTERNAL_ERROR`` and read as a server failure the caller cannot fix.
    All seven curated handlers must agree.
    """
    async with Client(build()) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("not_a_real_tool", {})

    assert raised.value.code == INVALID_PARAMS
    assert raised.value.message == "Unknown tool: not_a_real_tool"
    assert raised.value.data == {"requested": "not_a_real_tool"}


async def test_unknown_tool_name_is_invalid_params_over_real_streamable_http():
    """The same classification must survive the real wire, not just in-memory.

    In-memory dispatch and Streamable HTTP take different error paths, so this
    pins one representative server end-to-end over a loopback socket. Scope is
    deliberately one assertion on one server: no credentials, no fixtures
    beyond the socket, and the server is shut down in `finally`.
    """
    import threading

    import anyio
    import uvicorn

    app = build_daemon_common().streamable_http_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        # Wait for the bound socket rather than sleeping a fixed duration.
        with anyio.fail_after(20):
            while not server.started:
                await anyio.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]

        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            with pytest.raises(MCPError) as raised:
                await client.call_tool("not_a_real_tool", {})

        assert raised.value.code == INVALID_PARAMS
        assert raised.value.message == "Unknown tool: not_a_real_tool"
        assert raised.value.data == {"requested": "not_a_real_tool"}
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive(), "loopback uvicorn thread did not stop"


@pytest.mark.parametrize(("build", "tool_name"), RESOURCE_SERVERS)
async def test_unknown_resource_uri_is_invalid_params(build, tool_name):
    """Same classification for an unlisted resource URI (SEP-2164)."""
    async with Client(build()) as client:
        with pytest.raises(MCPError) as raised:
            await client.read_resource("lingtai://definitely-not-a-resource")

    assert raised.value.code == INVALID_PARAMS
    assert raised.value.message.startswith("Unknown resource: ")
    assert raised.value.data == {"uri": "lingtai://definitely-not-a-resource"}


async def test_telegram_task_card_route_is_unknown_after_intrinsic_migration():
    """Telegram no longer owns a public or hidden MCP ``task_card`` route."""
    async with Client(build_telegram(None)) as client:
        listed = {tool.name for tool in (await client.list_tools()).tools}
        assert "task_card" not in listed

        with pytest.raises(MCPError) as raised:
            await client.call_tool("task_card", {})

    assert raised.value.code == INVALID_PARAMS
    assert raised.value.message == "Unknown tool: task_card"
    assert raised.value.data == {"requested": "task_card"}


# ---------------------------------------------------------------------------
# Service-boundary schema adaptation
# ---------------------------------------------------------------------------

def test_tool_record_carries_schema_and_supported_metadata():
    record = _tool_record(
        Tool(
            name="search",
            title="Search the catalog",
            description="Find things.",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"hits": {"type": "integer"}}},
        )
    )

    assert record["name"] == "search"
    assert record["description"] == "Find things."
    assert record["schema"]["properties"] == {"q": {"type": "string"}}
    # Metadata the pre-migration adapter dropped on the floor.
    assert record["title"] == "Search the catalog"
    assert record["output_schema"]["properties"] == {"hits": {"type": "integer"}}


def test_tool_record_defaults_a_missing_description_and_schema():
    record = _tool_record(Tool(name="bare", input_schema={"type": "object"}))

    assert record["description"] == ""
    assert record["schema"] == {"type": "object"}
    assert "output_schema" not in record


class _PagingSession:
    """Two-page ``tools/list``, so the cursor loop has something to follow."""

    def __init__(self):
        self.cursors: list[str | None] = []

    async def list_tools(self, cursor=None):
        self.cursors.append(cursor)
        if cursor is None:
            return _ToolsPage([Tool(name="first", input_schema={})], "page-2")
        return _ToolsPage([Tool(name="second", input_schema={})], None)


class _ToolsPage:
    def __init__(self, tools, next_cursor):
        self.tools = tools
        self.next_cursor = next_cursor


async def test_list_all_tools_follows_cursors_until_exhausted():
    session = _PagingSession()

    records = await _list_all_tools(session)

    assert [record["name"] for record in records] == ["first", "second"]
    assert session.cursors == [None, "page-2"]


# ---------------------------------------------------------------------------
# Rich-result preservation vs. the compatibility projection
# ---------------------------------------------------------------------------

def _rich_result() -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(type="text", text='{"value": "first"}'),
            ImageContent(type="image", data="AA==", mime_type="image/png"),
            AudioContent(type="audio", data="BB==", mime_type="audio/wav"),
            EmbeddedResource(
                type="resource",
                resource=TextResourceContents(
                    uri="lingtai://note", text="embedded", mime_type="text/plain"
                ),
            ),
        ],
        structured_content={"value": "first"},
        is_error=False,
        _meta={"lingtai/trace": "abc123"},
    )


def test_preserve_tool_result_keeps_every_content_block_in_order():
    preserved = preserve_tool_result(_rich_result())

    assert [block["type"] for block in preserved["content"]] == [
        "text",
        "image",
        "audio",
        "resource",
    ]
    # Wire aliases survive serialization even though the attributes are snake_case.
    assert preserved["content"][1]["mimeType"] == "image/png"


def test_preserve_tool_result_keeps_structured_error_bit_and_meta():
    preserved = preserve_tool_result(_rich_result())

    assert preserved["structured_content"] == {"value": "first"}
    assert preserved["is_error"] is False
    assert preserved["meta"]["lingtai/trace"] == "abc123"
    assert preserved["result_type"] == "complete"


def test_preserve_tool_result_keeps_non_dict_structured_content():
    """The legacy projection can only carry a dict; preservation must not."""
    preserved = preserve_tool_result(
        CallToolResult(content=[], structured_content=[1, 2, 3])
    )

    assert preserved["structured_content"] == [1, 2, 3]


def test_compatibility_projection_is_unchanged_for_a_rich_result():
    """The model-facing value stays exactly what it was before the migration."""
    assert _decode_tool_result(_rich_result()) == {"value": "first"}


def test_compatibility_projection_reads_v2_snake_case_error_bit():
    result = CallToolResult(
        content=[TextContent(type="text", text="tool rejected the request")],
        is_error=True,
    )

    assert _decode_tool_result(result) == {
        "status": "error",
        "message": "tool rejected the request",
    }


def test_services_anatomy_pins_fail_closed_stdio_retry_contract():
    """Packaged lifecycle docs must not imply default replay after a stale call."""
    anatomy = (
        Path(__file__).parents[1] / "src/lingtai/services/ANATOMY.md"
    ).read_text(encoding="utf-8")
    retry_line = next(
        line for line in anatomy.splitlines() if "Stale-resource recovery" in line
    )

    assert '`retry_policy="never"`' in retry_line
    assert "without replaying" in retry_line
    assert '`retry_policy="safe"`' in retry_line
    assert "Exactly one replay" in retry_line
