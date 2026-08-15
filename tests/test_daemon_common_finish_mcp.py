"""Tests for the daemon_common MCP server's ``finish`` tool.

``finish`` moved to the LTP v2 ``action``/``input``/``reasoning`` envelope
every curated LingTai MCP tool uses (``tools/CONTRACT.md``), rather than
bare MCP params. The on-disk completion contract (schema
``lingtai.daemon_completion.v1``, validated again by the daemon runner) is
unchanged — only the model-facing call shape moved.
"""
from __future__ import annotations

import json

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from lingtai.mcp_servers.daemon_common.server import DESCRIPTION, FINISH_SCHEMA, build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _env(monkeypatch, tmp_path):
    completion_file = tmp_path / "daemon_completion.json"
    monkeypatch.setenv("LINGTAI_DAEMON_COMPLETION_FILE", str(completion_file))
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", "run-123")
    return completion_file


async def test_lists_ltp_v2_envelope_schema():
    async with Client(build_server()) as client:
        result = await client.list_tools()
        assert [t.name for t in result.tools] == ["checkpoint", "finish"]
        by_name = {tool.name: tool.input_schema for tool in result.tools}
        finish_schema = by_name["finish"]
        checkpoint_schema = by_name["checkpoint"]
        assert finish_schema["required"] == ["action", "input", "reasoning"]
        assert finish_schema["properties"]["action"]["enum"] == ["finish"]
        assert finish_schema["properties"]["input"]["required"] == ["status"]
        assert checkpoint_schema["required"] == ["action", "input", "reasoning"]
        assert checkpoint_schema["properties"]["action"]["enum"] == ["checkpoint"]
        assert checkpoint_schema["properties"]["input"]["required"] == [
            "state", "summary"
        ]
    assert FINISH_SCHEMA == finish_schema
    assert "finish(action='finish'" in DESCRIPTION


async def test_finish_via_envelope_writes_completion_file(monkeypatch, tmp_path):
    completion_file = _env(monkeypatch, tmp_path)
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "finish",
            {
                "action": "finish",
                "input": {"status": "done", "summary": "all good"},
                "reasoning": "task complete",
            },
        )
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "ok"
    assert payload["completion_status"] == "done"
    assert result.is_error is False

    written = json.loads(completion_file.read_text())
    assert written["schema"] == "lingtai.daemon_completion.v1"
    assert written["status"] == "done"
    assert written["summary"] == "all good"
    assert written["run_id"] == "run-123"


async def test_finish_failed_requires_reason(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "finish",
            {"action": "finish", "input": {"status": "failed"}, "reasoning": "why"},
        )
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"
    assert result.is_error is True


async def test_finish_rejects_old_flat_argument_shape(monkeypatch, tmp_path):
    """The pre-LTP-v2 bare {"status": ...} shape is no longer accepted."""
    _env(monkeypatch, tmp_path)
    async with Client(build_server()) as client:
        result = await client.call_tool("finish", {"status": "done"})
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"
    assert result.is_error is True


async def test_finish_requires_reasoning(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    async with Client(build_server()) as client:
        result = await client.call_tool(
            "finish", {"action": "finish", "input": {"status": "done"}},
        )
    payload = json.loads(result.content[0].text)
    assert payload["status"] == "error"


async def test_unknown_tool_is_invalid_params():
    async with Client(build_server()) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("not_finish", {})
        assert raised.value.code == INVALID_PARAMS
