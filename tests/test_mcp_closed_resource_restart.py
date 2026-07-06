"""Stale-resource recovery for stdio MCPClient — regression for Lingtai-AI/lingtai#104.

A revived agent kept a Telegram MCP tool registered, but every call returned
``{"status": "error", "message": ""}``. The underlying exception was anyio's
``ClosedResourceError`` (empty ``str(e)``) raised against a dead stdio stream
whose session object still looked "connected". ``refresh``/``clear`` could not
repair it because ``call_tool`` never tore down and re-spawned the subprocess.

These tests use fakes/monkeypatching only — no real MCP subprocess, no Telegram
credentials. They exercise:
  - empty-message exceptions surface the class name (no blank errors)
  - a stale ``ClosedResourceError`` triggers exactly one restart + retry
  - a successful retry returns the normal tool result
  - a failed retry returns a helpful error mentioning the class and that
    restart/retry failed (never blank)
  - non-stale exceptions surface a useful error, no restart attempted
  - ``restart()`` resets startup state so ``start()`` cannot lie
"""
from __future__ import annotations

import pytest

from lingtai.services.mcp import HTTPMCPClient, MCPClient


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class ClosedResourceError(Exception):
    """Stand-in for anyio.ClosedResourceError — same class name, empty str()."""


class _FakeFuture:
    """Mimics concurrent.futures.Future enough for call_tool: result() either
    returns a value or raises the staged exception."""

    def __init__(self, value=None, exc=None):
        self._value = value
        self._exc = exc

    def result(self, timeout=None):
        if self._exc is not None:
            raise self._exc
        return self._value


def _install_fake_loop(client):
    """Make the client look connected without a real subprocess/event loop.

    Works for both MCPClient and HTTPMCPClient — they share the
    ``_session`` / ``_loop`` / ``_closed`` triple that ``is_connected()`` reads.
    """
    client._session = object()

    class _Loop:
        def is_running(self):
            return True

    client._loop = _Loop()
    client._closed = False


# ---------------------------------------------------------------------------
# Exception formatting
# ---------------------------------------------------------------------------

def test_format_exception_empty_message_uses_class_name():
    """An exception whose str() is empty must surface its class name."""
    msg = MCPClient._format_exception(ClosedResourceError())
    assert msg == "ClosedResourceError"
    assert msg.strip() != ""


def test_format_exception_with_message_includes_class_and_message():
    msg = MCPClient._format_exception(ValueError("boom"))
    assert msg == "ValueError: boom"


# ---------------------------------------------------------------------------
# Stale-resource detection
# ---------------------------------------------------------------------------

def test_is_stale_resource_error_detects_closed_resource_by_class_name():
    assert MCPClient._is_stale_resource_error(ClosedResourceError()) is True


def test_is_stale_resource_error_detects_closed_substrings():
    assert MCPClient._is_stale_resource_error(
        RuntimeError("the stream was closed")) is True


def test_is_stale_resource_error_false_for_unrelated_errors():
    assert MCPClient._is_stale_resource_error(ValueError("bad arg")) is False


# ---------------------------------------------------------------------------
# restart() resets startup state
# ---------------------------------------------------------------------------

def test_restart_resets_startup_state_so_start_cannot_lie(monkeypatch):
    """After a first start, _ready is set and _error may linger. restart()
    must clear _ready/_error, reset _closed, drop stale session/loop/thread/cm
    so the next start() actually reconnects instead of early-returning."""
    client = MCPClient(command="/bin/true")

    # Simulate a prior (now-dead) start: ready latched, error latched, closed.
    client._ready.set()
    client._error = "old startup error"
    client._closed = True
    client._session = object()
    client._stdio_cm = object()
    client._session_cm = object()

    closed = {"n": 0}
    started = {"n": 0}

    def fake_close():
        closed["n"] += 1

    def fake_start():
        started["n"] += 1

    monkeypatch.setattr(client, "close", fake_close)
    monkeypatch.setattr(client, "start", fake_start)

    client.restart()

    assert closed["n"] == 1
    assert started["n"] == 1
    # State reset so a real start() would not early-return or raise on stale error.
    assert not client._ready.is_set()
    assert client._error is None
    assert client._closed is False
    assert client._session is None
    assert client._stdio_cm is None
    assert client._session_cm is None


# ---------------------------------------------------------------------------
# call_tool: stale ClosedResourceError → restart + retry once
# ---------------------------------------------------------------------------

def test_call_tool_restarts_and_retries_once_on_stale_error(monkeypatch):
    """First attempt raises stale ClosedResourceError → client restarts and
    retries exactly once → retry succeeds → normal result returned."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)

    attempts = {"n": 0}
    restarts = {"n": 0}

    def fake_run(coro, loop):
        # The coroutine is created but never awaited in this fake; close it to
        # avoid "coroutine was never awaited" warnings.
        coro.close()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeFuture(exc=ClosedResourceError())
        return _FakeFuture(value={"status": "success", "text": "pong"})

    monkeypatch.setattr(
        "asyncio.run_coroutine_threadsafe", fake_run)

    def fake_restart():
        restarts["n"] += 1
        _install_fake_loop(client)

    monkeypatch.setattr(client, "restart", fake_restart)

    result = client.call_tool("send_message", {"text": "hi"})

    assert attempts["n"] == 2          # original + one retry
    assert restarts["n"] == 1          # restarted exactly once
    assert result == {"status": "success", "text": "pong"}


def test_call_tool_failed_retry_returns_helpful_error_not_blank(monkeypatch):
    """If the retry also fails, return a helpful error mentioning the class
    name and that restart/retry failed — never a blank message."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(exc=ClosedResourceError())  # always stale

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", lambda: _install_fake_loop(client))

    result = client.call_tool("send_message", {"text": "hi"})

    assert result["status"] == "error"
    assert result["message"]                       # not blank
    assert "ClosedResourceError" in result["message"]
    assert "retry" in result["message"].lower()


def test_call_tool_non_stale_empty_error_surfaces_class_name(monkeypatch):
    """A non-stale exception with an empty str() must surface the class name,
    not a blank message, and must NOT trigger a restart."""
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
        client, "restart", lambda: restarts.__setitem__("n", restarts["n"] + 1))

    result = client.call_tool("send_message", {"text": "hi"})

    assert result["status"] == "error"
    assert "WeirdEmptyError" in result["message"]
    assert restarts["n"] == 0           # non-stale → no restart


def test_call_tool_success_passes_through_unchanged(monkeypatch):
    """The happy path is untouched: a successful call returns its result and
    never restarts."""
    client = MCPClient(command="/bin/true")
    _install_fake_loop(client)

    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(value={"status": "success", "text": "ok"})

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client, "restart", lambda: restarts.__setitem__("n", restarts["n"] + 1))

    result = client.call_tool("send_message", {"text": "hi"})

    assert result == {"status": "success", "text": "ok"}
    assert restarts["n"] == 0


# ---------------------------------------------------------------------------
# HTTPMCPClient parity — regression for Lingtai-AI/lingtai-kernel#740
#
# The HTTP sibling shipped without the #104 recovery: a dropped remote
# HTTP/SSE stream raised a raw (often blank) ClosedResourceError, poisoned the
# long-lived client permanently (is_connected() still True), and left no trace
# in the activity log. These mirror the stdio tests above for HTTPMCPClient.
# ---------------------------------------------------------------------------

def _http_client() -> HTTPMCPClient:
    return HTTPMCPClient(url="https://example.invalid/mcp")


def test_http_restart_resets_startup_state_so_start_cannot_lie(monkeypatch):
    """HTTPMCPClient.restart() must clear all latched startup/session fields,
    including the HTTP-specific _transport_cm/_read_stream/_write_stream, so the
    next start() actually reconnects instead of early-returning or re-raising."""
    client = _http_client()

    client._ready.set()
    client._error = "old connect error"
    client._closed = True
    client._session = object()
    client._read_stream = object()
    client._write_stream = object()
    client._transport_cm = object()
    client._session_cm = object()

    closed = {"n": 0}
    started = {"n": 0}
    monkeypatch.setattr(client, "close", lambda: closed.__setitem__("n", closed["n"] + 1))
    monkeypatch.setattr(client, "start", lambda: started.__setitem__("n", started["n"] + 1))

    client.restart()

    assert closed["n"] == 1
    assert started["n"] == 1
    assert not client._ready.is_set()
    assert client._error is None
    assert client._closed is False
    assert client._session is None
    assert client._read_stream is None
    assert client._write_stream is None
    assert client._transport_cm is None
    assert client._session_cm is None


def test_http_call_tool_restarts_and_retries_once_on_stale_error(monkeypatch):
    """First attempt raises a stale ClosedResourceError → the HTTP client
    restarts exactly once and the successful retry result is returned."""
    client = _http_client()
    _install_fake_loop(client)

    attempts = {"n": 0}
    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _FakeFuture(exc=ClosedResourceError())
        return _FakeFuture(value={"status": "success", "text": "pong"})

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)

    def fake_restart():
        restarts["n"] += 1
        _install_fake_loop(client)

    monkeypatch.setattr(client, "restart", fake_restart)

    result = client.call_tool("web_search_prime", {"search_query": "hi"})

    assert attempts["n"] == 2          # original + one retry
    assert restarts["n"] == 1          # restarted exactly once
    assert result == {"status": "success", "text": "pong"}


def test_http_call_tool_empty_message_error_surfaces_class_name(monkeypatch):
    """A blank-str() stale error must surface the class name (the #104 blank-
    error regression, now guarded for the HTTP client) and never propagate."""
    client = _http_client()
    _install_fake_loop(client)

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(exc=ClosedResourceError())  # always stale, empty str()

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(client, "restart", lambda: _install_fake_loop(client))

    result = client.call_tool("web_search_prime", {"search_query": "hi"})

    assert result["status"] == "error"
    assert result["message"]                       # not blank
    assert "ClosedResourceError" in result["message"]
    assert "retry" in result["message"].lower()


def test_http_call_tool_non_stale_error_returns_formatted_dict_not_raise(monkeypatch):
    """A non-stale ValueError returns a formatted error dict (never raised) and
    does NOT restart the connection."""
    client = _http_client()
    _install_fake_loop(client)

    restarts = {"n": 0}

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(exc=ValueError("boom"))

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)
    monkeypatch.setattr(
        client, "restart", lambda: restarts.__setitem__("n", restarts["n"] + 1))

    result = client.call_tool("web_search_prime", {"search_query": "hi"})

    assert result == {"status": "error", "message": "ValueError: boom"}
    assert restarts["n"] == 0           # non-stale → no restart


def test_http_call_tool_failure_recorded_in_activity_log(monkeypatch):
    """A failed HTTP call must be converted to an error dict BEFORE the activity
    log append, so get_activity_log() records it (the observability fix)."""
    client = _http_client()
    _install_fake_loop(client)

    def fake_run(coro, loop):
        coro.close()
        return _FakeFuture(exc=ValueError("boom"))

    monkeypatch.setattr("asyncio.run_coroutine_threadsafe", fake_run)

    client.call_tool("web_search_prime", {"search_query": "hi"})

    log = client.get_activity_log()
    assert len(log) == 1
    assert log[0]["tool"] == "web_search_prime"
    assert log[0]["result"]["status"] == "error"
