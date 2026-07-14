"""Stale-resource recovery for stdio MCPClient — regressions for issue #104.

A revived agent kept a Telegram MCP tool registered, but every call returned
``{"status": "error", "message": ""}``. The underlying exception was anyio's
``ClosedResourceError`` (empty ``str(e)``) raised against a dead stdio stream
whose session object still looked "connected".

These tests use fakes/monkeypatching only — no real MCP subprocess, network, or
credentials. They cover issue-104 transport recovery while requiring opaque
calls to fail closed when the first attempt's remote commit point is ambiguous.
"""
from __future__ import annotations

import inspect

import pytest

from lingtai.services.mcp import HTTPMCPClient, MCPClient


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class ClosedResourceError(Exception):
    """Stand-in for anyio.ClosedResourceError — same name, empty ``str()``."""


class _FakeFuture:
    """Minimal ``concurrent.futures.Future`` stand-in used by ``call_tool``."""

    def __init__(self, value=None, exc=None):
        self._value = value
        self._exc = exc

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._value


def _install_fake_loop(client):
    """Make either MCP client look connected without a subprocess or network."""
    client._session = object()

    class _Loop:
        def is_running(self):
            return True

    client._loop = _Loop()
    client._closed = False


# ---------------------------------------------------------------------------
# Exception formatting and stale detection
# ---------------------------------------------------------------------------

def test_format_exception_empty_message_uses_class_name():
    msg = MCPClient._format_exception(ClosedResourceError())
    assert msg == "ClosedResourceError"
    assert msg.strip() != ""


def test_format_exception_with_message_includes_class_and_message():
    assert MCPClient._format_exception(ValueError("boom")) == "ValueError: boom"


def test_is_stale_resource_error_detects_closed_resource_by_class_name():
    assert MCPClient._is_stale_resource_error(ClosedResourceError()) is True


def test_is_stale_resource_error_detects_closed_substrings():
    assert MCPClient._is_stale_resource_error(
        RuntimeError("the stream was closed")
    ) is True


def test_is_stale_resource_error_false_for_unrelated_errors():
    assert MCPClient._is_stale_resource_error(ValueError("bad arg")) is False


# ---------------------------------------------------------------------------
# restart() resets startup state
# ---------------------------------------------------------------------------

def test_restart_resets_startup_state_so_start_cannot_lie(monkeypatch):
    client = MCPClient(command="/bin/true")
    client._ready.set()
    client._error = "old startup error"
    client._closed = True
    client._session = object()
    client._stdio_cm = object()
    client._session_cm = object()

    closed = {"n": 0}
    started = {"n": 0}

    monkeypatch.setattr(
        client, "close", lambda: closed.__setitem__("n", closed["n"] + 1)
    )
    monkeypatch.setattr(
        client, "start", lambda: started.__setitem__("n", started["n"] + 1)
    )

    client.restart()

    assert closed["n"] == 1
    assert started["n"] == 1
    assert not client._ready.is_set()
    assert client._error is None
    assert client._closed is False
    assert client._session is None
    assert client._stdio_cm is None
    assert client._session_cm is None


# ---------------------------------------------------------------------------
# call_tool: stale outcome is fail-closed unless replay is explicitly safe
# ---------------------------------------------------------------------------

def test_call_tool_fails_closed_on_stale_error_by_default(monkeypatch):
    """Recover the transport, but never replay an opaque default call."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}
    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        return _FakeFuture(exc=ClosedResourceError())

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client,
        "restart",
        lambda: restarts.__setitem__("n", restarts["n"] + 1),
    )

    result = client.call_tool("wechat", {"action": "send"})

    assert attempts["n"] == 1
    assert restarts["n"] == 1
    assert result["status"] == "error"
    assert result["outcome"] == "ambiguous"
    assert result["retryable"] is False
    assert "not retried" in result["message"]
    assert "future call" in result["message"]


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("wechat", {"action": "send"}),
        ("wechat", {"action": "get_unread"}),
        ("get_messages", {"action": "send"}),
        ("send_message", {"action": "get"}),
    ],
)
def test_default_policy_does_not_infer_safety_from_name_or_action(
    monkeypatch, name, args
):
    """Names and ``args.action`` values never enable replay implicitly."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        return _FakeFuture(exc=ClosedResourceError())

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", lambda: None)

    result = client.call_tool(name, args)

    assert attempts["n"] == 1
    assert result["outcome"] == "ambiguous"
    assert result["retryable"] is False


def test_explicit_safe_policy_restarts_and_retries_once(monkeypatch):
    """A trusted caller can retain issue-104 one-restart/one-retry recovery."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}
    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeFuture(exc=ClosedResourceError())
        return _FakeFuture(value={"status": "success", "text": "pong"})

    def fake_restart():
        restarts["n"] += 1
        _install_fake_loop(client)

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", fake_restart)

    result = client.call_tool(
        "known_read_only_operation", {}, retry_policy="safe"
    )

    assert attempts["n"] == 2
    assert restarts["n"] == 1
    assert result == {"status": "success", "text": "pong"}


def test_default_stale_restart_failure_is_ambiguous_without_replay(monkeypatch):
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        return _FakeFuture(exc=ClosedResourceError())

    def failed_restart():
        raise RuntimeError("cannot reconnect")

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", failed_restart)

    result = client.call_tool("opaque_write", {"value": 1})

    assert attempts["n"] == 1
    assert result["status"] == "error"
    assert result["outcome"] == "ambiguous"
    assert result["retryable"] is False
    assert "restart failed" in result["message"]
    assert "RuntimeError: cannot reconnect" in result["message"]


def test_explicit_safe_retry_failure_is_ambiguous_and_non_retryable(monkeypatch):
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        return _FakeFuture(exc=ClosedResourceError())

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", lambda: _install_fake_loop(client))

    result = client.call_tool("known_safe_read", {}, retry_policy="safe")

    assert attempts["n"] == 2
    assert result["status"] == "error"
    assert result["outcome"] == "ambiguous"
    assert result["retryable"] is False
    assert "ClosedResourceError" in result["message"]
    assert "retry failed" in result["message"]


def test_explicit_safe_restart_failure_never_attempts_replay(monkeypatch):
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    attempts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        return _FakeFuture(exc=ClosedResourceError())

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client,
        "restart",
        lambda: (_ for _ in ()).throw(RuntimeError("restart unavailable")),
    )

    result = client.call_tool("known_safe_read", {}, retry_policy="safe")

    assert attempts["n"] == 1
    assert result["outcome"] == "ambiguous"
    assert result["retryable"] is False
    assert "restart failed" in result["message"]


@pytest.mark.parametrize("invalid_policy", ["", "blind", "idempotent"])
def test_call_tool_rejects_unsupported_retry_policy_before_call(
    monkeypatch, invalid_policy
):
    """No unverifiable generic idempotency label or misspelling is accepted."""
    client = MCPClient(command="/bin/true")
    starts = {"n": 0}
    monkeypatch.setattr(
        client, "start", lambda: starts.__setitem__("n", starts["n"] + 1)
    )

    with pytest.raises(ValueError, match="retry_policy"):
        client.call_tool("read_message", {}, retry_policy=invalid_policy)

    assert starts["n"] == 0
    assert client.get_activity_log() == []


def test_call_tool_non_stale_empty_error_surfaces_class_without_restart(monkeypatch):
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)

    class WeirdEmptyError(Exception):
        pass

    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(exc=WeirdEmptyError())

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client,
        "restart",
        lambda: restarts.__setitem__("n", restarts["n"] + 1),
    )

    result = client.call_tool("send_message", {"text": "hi"})

    assert result == {"status": "error", "message": "WeirdEmptyError"}
    assert restarts["n"] == 0


def test_call_tool_success_passes_through_unchanged(monkeypatch):
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)
    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(value={"status": "success", "text": "ok"})

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client,
        "restart",
        lambda: restarts.__setitem__("n", restarts["n"] + 1),
    )

    result = client.call_tool("send_message", {"text": "hi"})

    assert result == {"status": "success", "text": "ok"}
    assert restarts["n"] == 0


def test_http_call_tool_signature_and_success_path_are_unchanged(monkeypatch):
    """WB-01 is stdio-only; HTTP gets no inert replay-policy parameter."""
    assert "retry_policy" not in inspect.signature(HTTPMCPClient.call_tool).parameters

    client = HTTPMCPClient(url="https://invalid.example.test/mcp")
    _install_fake_loop(client)

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(value={"status": "success", "text": "http-ok"})

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)

    result = client.call_tool("read", {})

    assert result == {"status": "success", "text": "http-ok"}
