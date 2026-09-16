"""Standard-protocol dynamic ``tools/list`` disclosure for curated MCP servers.

Covers the shared primitives in ``lingtai.mcp_servers._disclosure`` directly,
and — parametrized across the three curated servers this slice touches
(Telegram, Feishu, WeChat) — the real behavior over an in-memory SDK v2
``Client``: compact-then-full ``tools/list``, the strict envelope, ordering
(successful manual, no expansion on a rejected manual, inbound-before-wake),
one-time transition, and delivery on both the modern ``subscriptions/listen``
bus and the handshake-era direct notification. IMAP is untouched by this slice
and is not covered here.
"""
from __future__ import annotations

import os
import sys
import threading
from unittest.mock import AsyncMock

import anyio
import mcp.types as types
import pytest
from mcp import Client
from mcp.server.subscriptions import ToolsListChanged

from lingtai.services.mcp import MCPClient

from lingtai.mcp_servers._disclosure import (
    DisclosingServer,
    ToolDisclosure,
    compact_tool_for,
    disclose_after_call,
)
from lingtai.mcp_servers._results import payload_is_error
from lingtai.mcp_servers.feishu.manager import SCHEMA as FEISHU_SCHEMA, DESCRIPTION as FEISHU_DESCRIPTION
from lingtai.mcp_servers.feishu.plugin import FEISHU_PLUGIN
from lingtai.mcp_servers.feishu.server import (
    build_inbound_callback as feishu_inbound_callback,
    build_server as build_feishu,
)
import lingtai.mcp_servers.feishu.server as feishu_server_module
from lingtai.mcp_servers.telegram.manager import SCHEMA as TELEGRAM_SCHEMA, DESCRIPTION as TELEGRAM_DESCRIPTION
from lingtai.mcp_servers.telegram.plugin import TELEGRAM_PLUGIN
from lingtai.mcp_servers.telegram.server import (
    build_inbound_callback as telegram_inbound_callback,
    build_server as build_telegram,
)
import lingtai.mcp_servers.telegram.server as telegram_server_module
from lingtai.mcp_servers.wechat.manager import SCHEMA as WECHAT_SCHEMA, DESCRIPTION as WECHAT_DESCRIPTION
from lingtai.mcp_servers.wechat.plugin import WECHAT_PLUGIN
from lingtai.mcp_servers.wechat.server import (
    build_inbound_callback as wechat_inbound_callback,
    build_server as build_wechat,
)
import lingtai.mcp_servers.wechat.server as wechat_server_module

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


_FULL_SCHEMA_BY_PLUGIN_NAME = {
    TELEGRAM_PLUGIN.name: (TELEGRAM_SCHEMA, TELEGRAM_DESCRIPTION),
    FEISHU_PLUGIN.name: (FEISHU_SCHEMA, FEISHU_DESCRIPTION),
    WECHAT_PLUGIN.name: (WECHAT_SCHEMA, WECHAT_DESCRIPTION),
}


def _fresh_disclosure(plugin) -> ToolDisclosure:
    """A compact-starting disclosure carrying that provider's real full schema."""
    schema, description = _FULL_SCHEMA_BY_PLUGIN_NAME[plugin.name]
    return ToolDisclosure(
        plugin, types.Tool(name=plugin.name, description=description, input_schema=schema),
    )


# ---------------------------------------------------------------------------
# Shared primitives (lingtai.mcp_servers._disclosure) — no SDK client needed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plugin", [TELEGRAM_PLUGIN, FEISHU_PLUGIN, WECHAT_PLUGIN],
    ids=["telegram", "feishu", "wechat"],
)
def test_compact_tool_is_same_name_manual_only_strict_envelope(plugin):
    # The compact schema is derived from the real plugin descriptor's own
    # manual child, not from a hand-authored or provider-specific substitute.
    compact = compact_tool_for(
        plugin,
        types.Tool(name=plugin.name, description=f"{plugin.name} client.", input_schema={}),
    )
    assert compact.name == plugin.name
    schema = compact.input_schema
    assert list(schema["properties"]) == ["action", "input", "reasoning", "summarize"]
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == ["manual"]
    branch = schema["properties"]["input"]
    assert len(branch["oneOf"]) == 1
    assert branch["oneOf"][0]["title"] == "manual input"
    assert branch["oneOf"][0]["type"] == "object"
    assert branch["oneOf"][0]["additionalProperties"] is False
    assert branch["oneOf"][0]["properties"] == {}


def test_disclosure_is_compact_then_full_and_expands_exactly_once():
    full_tool = types.Tool(name=WECHAT_PLUGIN.name, description="d", input_schema={"fake": "full"})
    disclosure = ToolDisclosure(WECHAT_PLUGIN, full_tool)
    assert disclosure.expanded is False
    assert disclosure.tools()[0].input_schema["properties"]["action"]["enum"] == ["manual"]

    assert disclosure.expand("inbound") is True
    assert disclosure.expanded is True
    assert disclosure.reason == "inbound"
    assert disclosure.tools() == [full_tool]

    # Monotonic: a later trigger is a no-op and does not overwrite the reason.
    assert disclosure.expand("manual") is False
    assert disclosure.reason == "inbound"


async def test_disclose_after_call_ignores_non_manual_and_failed_results(monkeypatch):
    disclosure = ToolDisclosure(
        FEISHU_PLUGIN, types.Tool(name=FEISHU_PLUGIN.name, description="d", input_schema={}),
    )
    emit = AsyncMock()
    monkeypatch.setattr(disclosure, "_emit", emit)

    await disclose_after_call(disclosure, {"action": "send"}, {"status": "ok"})
    assert disclosure.expanded is False
    assert disclosure.reason is None
    emit.assert_not_awaited()

    await disclose_after_call(
        disclosure, {"action": "manual"},
        {"status": "failed", "error_code": "INVALID_ARGUMENT"},
    )
    assert disclosure.expanded is False
    assert disclosure.reason is None
    assert payload_is_error({"status": "failed", "error_code": "INVALID_ARGUMENT"})
    emit.assert_not_awaited()

    await disclose_after_call(disclosure, {"action": "manual"}, {"status": "ok"})
    assert disclosure.expanded is True
    assert disclosure.reason == "manual"
    emit.assert_awaited_once_with()

    # Repeated manual and inbound triggers are no-ops: one transition, one signal.
    await disclose_after_call(disclosure, {"action": "manual"}, {"status": "ok"})
    assert disclosure.expand("inbound") is False
    emit.assert_awaited_once_with()


def test_disclosing_server_advertises_handshake_era_tools_changed():
    async def _list_tools(_ctx, _params):
        return types.ListToolsResult(tools=[])

    server = DisclosingServer(TELEGRAM_PLUGIN.server_name, on_list_tools=_list_tools)
    options = server.create_initialization_options()
    assert options.capabilities.tools.list_changed is True


# ---------------------------------------------------------------------------
# Real curated servers over an in-memory SDK v2 Client
# ---------------------------------------------------------------------------

PROVIDERS = [
    pytest.param(
        TELEGRAM_PLUGIN, build_telegram, telegram_inbound_callback,
        telegram_server_module, id="telegram",
    ),
    pytest.param(
        FEISHU_PLUGIN, build_feishu, feishu_inbound_callback,
        feishu_server_module, id="feishu",
    ),
    pytest.param(
        WECHAT_PLUGIN, build_wechat, wechat_inbound_callback,
        wechat_server_module, id="wechat",
    ),
]

_MANUAL_CALL = {"action": "manual", "input": {}, "reasoning": "test"}


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_fresh_session_advertises_compact_tool_with_callable_manual(
    plugin, build_server, _cb, _mod,
):
    disclosure = _fresh_disclosure(plugin)
    async with Client(build_server(None, disclosure=disclosure)) as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_capabilities.tools.list_changed is True

        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == [plugin.name]
        assert tools[0].input_schema["properties"]["action"]["enum"] == ["manual"]

        # The full handler stays dispatchable even while compact: calling the
        # real manual action succeeds and returns the packaged skill.
        result = await client.call_tool(plugin.name, _MANUAL_CALL)
        assert result.is_error is False
        assert result.structured_content["status"] == "ok"
        assert result.structured_content["action"] == "manual"


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_successful_manual_discloses_exact_canonical_tool_on_next_round(
    plugin, build_server, _cb, _mod,
):
    disclosure = _fresh_disclosure(plugin)
    expected_schema, expected_description = _FULL_SCHEMA_BY_PLUGIN_NAME[plugin.name]
    async with Client(build_server(None, disclosure=disclosure)) as client:
        result = await client.call_tool(plugin.name, _MANUAL_CALL)
        assert result.is_error is False
        assert result.structured_content["status"] == "ok"
        assert result.structured_content["action"] == "manual"
        assert disclosure.expanded is True
        assert disclosure.reason == "manual"

        tools = (await client.list_tools(cache_mode="refresh")).tools
        assert [t.name for t in tools] == [plugin.name]
        assert tools[0].description == expected_description
        assert tools[0].input_schema == expected_schema


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_rejected_manual_does_not_expand(plugin, build_server, _cb, _mod):
    disclosure = _fresh_disclosure(plugin)
    async with Client(build_server(None, disclosure=disclosure)) as client:
        bad_call = {"action": "manual", "input": {"unexpected_field": 1}, "reasoning": "test"}
        result = await client.call_tool(plugin.name, bad_call)
        assert result.is_error is True
        assert disclosure.expanded is False, "a rejected manual call must not expand"
        assert disclosure.reason is None

        # A genuinely successful manual call afterward still works normally.
        result = await client.call_tool(plugin.name, _MANUAL_CALL)
        assert result.is_error is False
        assert disclosure.expanded is True


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_modern_listen_stream_receives_the_standard_change_event(
    plugin, build_server, _cb, _mod,
):
    disclosure = _fresh_disclosure(plugin)
    async with Client(build_server(None, disclosure=disclosure)) as client:
        async with client.listen(tools_list_changed=True) as subscription:
            result = await client.call_tool(plugin.name, _MANUAL_CALL)
            assert result.is_error is False
            with anyio.fail_after(5):
                event = await anext(aiter(subscription))
            assert isinstance(event, ToolsListChanged)

        # The client's own response cache is evicted by the notification: a
        # plain (uncached-bypass) relist already reflects the full schema.
        tools = (await client.list_tools()).tools
        actions = tools[0].input_schema["properties"]["action"]["enum"]
        assert len(actions) > 1


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_legacy_connection_receives_the_direct_notification(
    plugin, build_server, _cb, _mod,
):
    disclosure = _fresh_disclosure(plugin)
    received: list[object] = []

    async def _message_handler(message) -> None:
        received.append(message)

    async with Client(
        build_server(None, disclosure=disclosure),
        mode="legacy",
        message_handler=_message_handler,
    ) as client:
        assert client.protocol_version != "2026-07-28"
        assert client.server_capabilities.tools.list_changed is True

        first = await client.call_tool(plugin.name, _MANUAL_CALL)
        second = await client.call_tool(plugin.name, _MANUAL_CALL)
        assert first.is_error is False
        assert second.is_error is False
        assert disclosure.expanded is True

    changed = [
        message for message in received
        if getattr(message, "method", None) == "notifications/tools/list_changed"
    ]
    assert len(changed) == 1, received


@pytest.mark.parametrize(("plugin", "build_server", "build_inbound", "_mod"), PROVIDERS)
async def test_inbound_delivery_discloses_before_the_licc_wake_write(
    plugin, build_server, build_inbound, _mod, monkeypatch,
):
    """Trigger (b), tested at the exact source each server actually uses:
    ``build_inbound_callback`` — no manager, no MCP client needed. Spies on
    the provider's own ``push_inbox_event`` import to prove the disclosure
    flip happens strictly before that LICC write, and that the write itself
    (current provider semantics — reply-on-origin-channel plumbing) still
    receives the exact event payload it always did.
    """
    disclosure = _fresh_disclosure(plugin)
    observed: dict = {}

    def _fake_push(**kwargs):
        observed["expanded_at_write_time"] = disclosure.expanded
        observed["kwargs"] = kwargs
        return True

    monkeypatch.setattr(_mod, "push_inbox_event", _fake_push)
    callback = build_inbound(disclosure)

    assert disclosure.expanded is False
    callback({"from": "alice", "subject": "hi", "body": "hello", "wake": True})

    # Feishu's own callback returns None (current provider semantics,
    # unchanged); Telegram/WeChat return the push result. Only the push
    # itself and its ordering relative to disclosure are asserted here.
    assert observed["expanded_at_write_time"] is True, (
        "disclosure must be expanded before push_inbox_event runs"
    )
    assert observed["kwargs"]["sender"] == "alice"
    assert observed["kwargs"]["body"] == "hello"
    assert disclosure.expanded is True
    assert disclosure.reason == "inbound"


@pytest.mark.parametrize(("plugin", "build_server", "_cb", "_mod"), PROVIDERS)
async def test_new_disclosure_instance_starts_compact_again(
    plugin, build_server, _cb, _mod,
):
    """A new process-local disclosure instance never inherits sibling state."""
    expanded = _fresh_disclosure(plugin)
    expanded.expand("inbound")
    assert expanded.expanded is True

    fresh = _fresh_disclosure(plugin)
    assert fresh.expanded is False
    assert fresh.tools()[0].input_schema["properties"]["action"]["enum"] == ["manual"]


# ---------------------------------------------------------------------------
# Real production stdio entrypoints + the generic host MCP client
# ---------------------------------------------------------------------------

_LIVE_PROVIDER_PLUGINS = [
    pytest.param("telegram", TELEGRAM_PLUGIN, id="telegram"),
    pytest.param("feishu", FEISHU_PLUGIN, id="feishu"),
    pytest.param("wechat", WECHAT_PLUGIN, id="wechat"),
]
_LIVE_CONFIG_ENV = {
    "telegram": "LINGTAI_TELEGRAM_CONFIG",
    "feishu": "LINGTAI_FEISHU_CONFIG",
    "wechat": "LINGTAI_WECHAT_CONFIG",
}


def _assert_compact_record(record: dict, provider: str) -> None:
    assert record["name"] == provider
    schema = record["schema"]
    assert list(schema["properties"]) == ["action", "input", "reasoning", "summarize"]
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == ["manual"]
    branches = schema["properties"]["input"]["oneOf"]
    assert len(branches) == 1
    assert branches[0]["title"] == "manual input"
    assert branches[0]["properties"] == {}
    assert branches[0]["additionalProperties"] is False


def _live_stdio_client(provider: str, agent_dir) -> MCPClient:
    return MCPClient(
        command=sys.executable,
        args=["-m", f"lingtai.mcp_servers.{provider}"],
        name=f"disclosure-stdio-{provider}",
        env={
            "HOME": str(agent_dir),
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": os.pathsep.join(sys.path),
            "LINGTAI_AGENT_DIR": str(agent_dir),
            # Deliberately fail provider startup before any real transport/network
            # work. The packaged manual remains callable in managerless mode.
            _LIVE_CONFIG_ENV[provider]: str(agent_dir / "missing-provider-config.json"),
        },
    )


def _run_production_stdio_disclosure(provider: str, plugin, tmp_path) -> None:
    expected_schema, expected_description = _FULL_SCHEMA_BY_PLUGIN_NAME[plugin.name]
    agent_dir = tmp_path / provider
    agent_dir.mkdir()
    deliveries: list[tuple[list[dict], int]] = []
    changed = threading.Event()

    client = _live_stdio_client(provider, agent_dir)
    client.watch_tools_changed(
        lambda records, epoch: (deliveries.append((records, epoch)), changed.set())
    )
    try:
        initial = client.list_tools(timeout=20)
        assert client.protocol_version == "2026-07-28"
        assert client.catalog_listen_state == "listening"
        assert len(initial) == 1
        _assert_compact_record(initial[0], provider)

        result = client.call_tool(
            provider,
            {"action": "manual", "input": {}, "reasoning": "production stdio test"},
            timeout=20,
        )
        assert result["status"] == "ok"
        assert result["action"] == "manual"
        assert changed.wait(20), (provider, client.catalog_listen_state, deliveries)
        assert len(deliveries) == 1
        records, fetch_epoch = deliveries[0]
        assert fetch_epoch == 0
        assert len(records) == 1
        assert records[0]["name"] == provider
        assert records[0]["description"] == expected_description
        assert records[0]["schema"] == expected_schema
        assert client.list_tools(timeout=20) == records
    finally:
        client.close()

    # A genuinely new OS process starts compact again: disclosure is not
    # persisted and no module-global state leaks across process lifetimes.
    fresh = _live_stdio_client(provider, agent_dir)
    try:
        restarted = fresh.list_tools(timeout=20)
        assert fresh.protocol_version == "2026-07-28"
        assert len(restarted) == 1
        _assert_compact_record(restarted[0], provider)
    finally:
        fresh.close()


@pytest.mark.parametrize(("provider", "plugin"), _LIVE_PROVIDER_PLUGINS)
async def test_production_stdio_discloses_and_new_process_resets(
    provider, plugin, tmp_path,
):
    with anyio.fail_after(60):
        await anyio.to_thread.run_sync(
            _run_production_stdio_disclosure, provider, plugin, tmp_path,
        )
