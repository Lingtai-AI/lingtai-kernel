"""MCP structured-result decoding shared by stdio and HTTP transports."""

from __future__ import annotations

import asyncio

import pytest
from mcp.types import CallToolResult, ImageContent, TextContent

from lingtai.services.mcp import HTTPMCPClient, MCPClient


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self, timeout=None):
        return self._value


class _RunningLoop:
    def is_running(self):
        return True


class _Session:
    def __init__(self, result):
        self._result = result

    async def call_tool(self, **kwargs):
        return self._result


def _result(*, is_error, structured=None, text=None):
    content = [] if text is None else [TextContent(type="text", text=text)]
    return CallToolResult(
        isError=is_error,
        structuredContent=structured,
        content=content,
    )


@pytest.fixture(params=["stdio", "http"])
def client(request, monkeypatch):
    if request.param == "stdio":
        instance = MCPClient(command="/bin/true")
    else:
        instance = HTTPMCPClient(url="https://example.invalid/mcp")

    instance._loop = _RunningLoop()
    instance._closed = False

    real_asyncio_run = asyncio.run

    def run_now(coro, loop):
        return _ImmediateFuture(real_asyncio_run(coro))

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", run_now)
    return instance


def _call(client, result):
    client._session = _Session(result)
    return client.call_tool("example", {"value": 1})


def test_structured_error_has_priority_and_keeps_fields(client):
    structured = {
        "status": "success",
        "code": "RestoreInProgress",
        "message": "restore is still preparing",
        "retryable": True,
        "details": {"operation_id": "restore-123"},
    }
    result = _call(
        client,
        _result(
            is_error=True,
            structured=structured,
            text='{"code":"WrongTextFallback","message":"wrong"}',
        ),
    )

    assert result == {**structured, "status": "error"}


@pytest.mark.parametrize(
    ("structured", "text", "expected_message"),
    [
        (
            {"code": "RestoreProtocolMismatch", "retryable": True},
            "metadata owner returned an inconsistent outcome",
            "metadata owner returned an inconsistent outcome",
        ),
        (
            {
                "code": "RestoreProtocolMismatch",
                "message": "",
                "retryable": True,
            },
            None,
            "Unknown MCP error",
        ),
    ],
    ids=["missing-message-uses-text", "empty-message-uses-default"],
)
def test_structured_error_fills_missing_or_empty_message(
    client,
    structured,
    text,
    expected_message,
):
    result = _call(
        client,
        _result(is_error=True, structured=structured, text=text),
    )

    assert result == {
        **structured,
        "status": "error",
        "message": expected_message,
    }


def test_json_object_error_is_promoted_to_top_level(client):
    result = _call(
        client,
        _result(
            is_error=True,
            text=(
                '{"code":"SnapshotLeaseExpired",'
                '"message":"checkpoint expired","retryable":false,'
                '"details":{"snapshot_id":42},'
                '"error":{"kind":"checkpoint"}}'
            ),
        ),
    )

    assert result == {
        "status": "error",
        "code": "SnapshotLeaseExpired",
        "message": "checkpoint expired",
        "retryable": False,
        "details": {"snapshot_id": 42},
        "error": {"kind": "checkpoint"},
    }


def test_plain_text_error_keeps_legacy_envelope(client):
    result = _call(
        client,
        _result(is_error=True, text="tool rejected the request"),
    )

    assert result == {
        "status": "error",
        "message": "tool rejected the request",
    }


def test_empty_error_keeps_nonempty_fallback(client):
    result = _call(client, _result(is_error=True))

    assert result == {"status": "error", "message": "Unknown MCP error"}


def test_structured_success_is_preferred_over_text(client):
    result = _call(
        client,
        _result(
            is_error=False,
            structured={"status": "success", "value": 7},
            text='{"status":"success","value":8}',
        ),
    )

    assert result == {"status": "success", "value": 7}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true", True),
        ("42", 42),
        ('[1,"two",null]', [1, "two", None]),
        ("null", None),
    ],
    ids=["bool", "number", "list", "null"],
)
def test_json_non_object_success_preserves_legacy_result(client, text, expected):
    result = _call(client, _result(is_error=False, text=text))

    assert result == expected


def test_empty_success_text_keeps_legacy_envelope(client):
    result = _call(client, _result(is_error=False, text=""))

    assert result == {"status": "success", "text": ""}


@pytest.mark.parametrize(
    "text",
    ["ordinary success text", "{malformed-json"],
    ids=["plain-text", "malformed-json"],
)
def test_non_json_success_keeps_legacy_envelope(client, text):
    result = _call(client, _result(is_error=False, text=text))

    assert result == {"status": "success", "text": text}


def test_first_text_block_wins_after_non_text_content(client):
    result = _call(
        client,
        CallToolResult(
            isError=False,
            content=[
                ImageContent(type="image", data="AA==", mimeType="image/png"),
                TextContent(type="text", text='{"value":"first"}'),
                TextContent(type="text", text='{"value":"second"}'),
            ],
        ),
    )

    assert result == {"value": "first"}
