"""Standard MCP dynamic tool-catalog reconciliation.

An MCP server may change its ``tools/list`` while connected and announce it
with the standard ``notifications/tools/list_changed`` — delivered on a
``subscriptions/listen`` stream at protocol 2026-07-28, or spontaneously on
earlier negotiated versions. The kernel owns nothing about *what* changed: the
client refetches the complete paginated catalog (bypassing the SDK response
cache) off the delivery thread, and the Agent atomically replaces exactly the
tools that client owns. Every other MCP client, native tool, and intrinsic is
untouched; an invalid or colliding catalog leaves the last good one mounted.

Three layers, each pinned separately:

* a **real stdio SDK server** (``tests/_mcp_catalog_server.py``) whose catalog
  changes after a tool call and which publishes the standard event through the
  SDK's ``ListenHandler`` — the production-shaped proof;
* the **client watch** (both transports) driven with a fake session on a real
  event loop — legacy direct notification, coalescing, cache bypass, failure
  keeps last good, listen feature detection, teardown;
* the **Agent reconcile** with fake clients — add/replace/remove per owner,
  handler identity, rejection cases, stale delivery, provider-session refresh.
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.agent import Agent
from lingtai.kernel.base_agent.turn import _LiveKnownTools
from lingtai.kernel.llm.base import ToolCall
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.services import mcp as mcp_service
from lingtai.services.mcp import HTTPMCPClient, MCPClient
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests.test_deep_refresh import _make_agent, _make_init

CATALOG_SERVER_ARGS = ["-m", "tests._mcp_catalog_server"]
LIST_CHANGED = "notifications/tools/list_changed"


def _mk_agent(tmp_path: Path) -> Agent:
    return Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=tmp_path / "agent",
        capabilities={"mcp": {}},
    )


def _wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _schema_names(agent) -> list[str]:
    return [s.name for s in agent._tool_schemas]


def _mcp_names(agent) -> list[str]:
    """MCP-owned tool names in surface order (capability tools excluded)."""
    return [s.name for s in agent._tool_schemas if s.name in agent._mcp_clients_by_tool]


# ---------------------------------------------------------------------------
# Real stdio SDK server: catalog changes, standard listen event, reconcile
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog_agent(tmp_path):
    agent = _mk_agent(tmp_path)
    baseline = set(_schema_names(agent))  # capability tools mounted by the Agent itself
    agent.add_tool(
        "native",
        schema={"type": "object", "properties": {}},
        handler=lambda args: {"ok": True},
        description="native tool",
    )
    intrinsics_before = set(agent._intrinsics)
    registered = agent.connect_mcp(
        sys.executable,
        CATALOG_SERVER_ARGS,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": ":".join(sys.path)},
    )
    try:
        yield agent, registered, intrinsics_before, baseline
    finally:
        agent.stop(timeout=5)


def test_real_stdio_server_catalog_change_reconciles_the_agent_surface(catalog_agent):
    agent, registered, intrinsics_before, baseline = catalog_agent
    assert registered == ["echo", "old", "bump"]
    client = agent._mcp_clients[-1]
    assert isinstance(client, MCPClient)
    assert client.protocol_version == "2026-07-28"
    assert client.server_capabilities.tools.list_changed is True
    assert _wait(lambda: client.catalog_listen_state == "listening")

    events: list[tuple[str, dict]] = []
    agent._log = lambda event, **fields: events.append((event, fields))
    echo_before = agent._tool_handlers["echo"]
    bump_before = agent._tool_handlers["bump"]
    native_before = agent._tool_handlers["native"]
    chat = _RecordingChat()
    agent._session.chat = chat

    result = agent._dispatch_tool(ToolCall(name="bump", args={}, id="t-bump"))

    assert result["status"] == "ok"
    assert _wait(lambda: "pong" in agent._tool_handlers and "old" not in agent._tool_handlers)
    # Surface: removed / replaced / added, in stable order, others untouched.
    assert [n for n in _schema_names(agent) if n not in baseline] == ["native", "echo", "bump", "pong"]
    assert baseline <= set(_schema_names(agent))
    echo = next(s for s in agent._tool_schemas if s.name == "echo")
    assert echo.description == "echo v2"
    assert "count" in echo.parameters["properties"]
    assert agent._tool_handlers["echo"] is not echo_before
    assert agent._tool_handlers["bump"] is bump_before  # unchanged record keeps identity
    assert agent._tool_handlers["native"] is native_before
    assert set(agent._intrinsics) == intrinsics_before
    # Ownership, metadata sidecar, and provider-facing catalog follow.
    assert agent._mcp_tool_names == {"echo", "bump", "pong"}
    assert agent._mcp_clients_by_tool["pong"] is client
    assert "old" not in agent._mcp_clients_by_tool
    assert agent.mcp_tool_metadata("echo") == {"title": "Echo v2"}
    assert agent.mcp_tool_metadata("old") is None
    visible = [s.name for s in agent._build_tool_schemas()]
    assert "pong" in visible and "old" not in visible
    assert [s.name for s in chat.tool_updates[-1]] == visible
    # The new tool routes to the live client; the executor's live view knows it.
    pong = agent._dispatch_tool(ToolCall(name="pong", args={"n": 1}, id="t-pong"))
    assert pong == {"status": "ok", "tool": "pong", "args": {"n": 1}}
    known = _LiveKnownTools(agent)
    assert "pong" in known and "old" not in known and "native" in known
    reconciled = [f for e, f in events if e == "mcp_catalog_reconciled"]
    assert reconciled and reconciled[-1]["added"] == ["pong"]
    assert reconciled[-1]["replaced"] == ["echo"] and reconciled[-1]["removed"] == ["old"]

    agent.stop(timeout=5)
    assert not client.is_connected()
    assert client._thread is not None and not client._thread.is_alive()
    assert client._catalog_deliver_pool is None
    assert client.request_tool_catalog_refetch("after-close") is False


def test_first_change_immediately_after_mount_is_not_missed(catalog_agent):
    """Regression: the listen stream must be acknowledged before the client is
    published as connected. Otherwise a catalog change announced by the very
    first tool call — issued right after mount, with no wait — is published to
    no subscriber and, since listen events are never replayed, the catalog
    would stay stale forever."""
    agent, registered, _, _ = catalog_agent
    client = agent._mcp_clients[-1]
    # No test-side wait: readiness itself carried the acknowledged stream.
    assert client.catalog_listen_state == "listening"
    assert registered == ["echo", "old", "bump"]

    assert agent._dispatch_tool(ToolCall(name="bump", args={}, id="t-first"))["status"] == "ok"

    assert _wait(lambda: "pong" in agent._tool_handlers and "old" not in agent._tool_handlers)
    assert agent._mcp_tool_names == {"echo", "bump", "pong"}


# ---------------------------------------------------------------------------
# Client watch on a real loop with a fake session (both transports)
# ---------------------------------------------------------------------------


class _Tool:
    def __init__(self, name: str, schema: dict):
        self.name = name
        self.description = f"{name} description"
        self.input_schema = schema


class _Page:
    def __init__(self, tools, next_cursor):
        self.tools = tools
        self.next_cursor = next_cursor


class _FakeSession:
    """Two-page ``tools/list`` with an optional gate and failure switch."""

    def __init__(self):
        self.version = 1
        self.calls: list[tuple[str | None, str | None]] = []
        self.gate: asyncio.Event | None = None
        self.fail = False
        self.protocol_version = "2025-11-25"
        self.server_capabilities = None

    async def list_tools(self, cursor=None, cache_mode=None):
        self.calls.append((cursor, cache_mode))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("tools/list unavailable")
        if cursor is None:
            return _Page([_Tool("first", {"type": "object", "v": self.version})], "page-2")
        return _Page([_Tool("second", {"type": "object"})], None)


class _LoopHarness:
    """A connected-looking client whose loop is real but whose session is fake."""

    def __init__(self, client, session):
        self.client = client
        self.session = session
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        client._loop = self.loop
        client._thread = self.thread
        client._session = session
        client._client = SimpleNamespace()  # no ``listen`` attribute
        client._closed = False
        self.deliveries: list[tuple[list[dict], str]] = []
        client.watch_tools_changed(
            lambda records, fetch_epoch: self.deliveries.append(
                (records, threading.current_thread().name)
            )
        )

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=5)

    def notify(self, method=LIST_CHANGED):
        self.run(self.client._on_server_message(SimpleNamespace(method=method)))

    def close(self):
        self.client.close()
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


def _make_client(kind: str):
    if kind == "stdio":
        return MCPClient(command="/bin/true")
    return HTTPMCPClient(url="https://example.invalid/mcp")


@pytest.fixture(params=["stdio", "http"])
def harness(request):
    h = _LoopHarness(_make_client(request.param), _FakeSession())
    try:
        yield h
    finally:
        h.close()


def test_direct_notification_refetches_full_catalog_bypassing_cache_off_loop(harness):
    harness.notify()

    assert _wait(lambda: len(harness.deliveries) == 1)
    records, thread_name = harness.deliveries[0]
    assert [r["name"] for r in records] == ["first", "second"]
    assert harness.session.calls == [(None, "refresh"), ("page-2", "refresh")]
    assert thread_name.startswith("mcp-catalog")
    assert thread_name != harness.thread.name
    # Unrelated notifications are ignored.
    harness.notify("notifications/progress")
    time.sleep(0.1)
    assert len(harness.session.calls) == 2 and len(harness.deliveries) == 1


def test_change_burst_coalesces_and_delivers_only_the_newest_catalog(harness):
    gate = harness.run(_make_event())
    harness.session.gate = gate
    for _ in range(5):
        harness.notify()
    harness.session.version = 2
    harness.loop.call_soon_threadsafe(gate.set)

    assert _wait(lambda: len(harness.deliveries) == 2)
    time.sleep(0.1)
    # One refetch was in flight; the other four signals collapsed into one more.
    assert len(harness.session.calls) == 4
    assert len(harness.deliveries) == 2
    assert harness.deliveries[-1][0][0]["schema"] == {"type": "object", "v": 2}


async def _make_event() -> asyncio.Event:
    return asyncio.Event()


def test_failed_refetch_delivers_nothing_and_a_later_signal_recovers(harness):
    harness.session.fail = True
    harness.notify()
    time.sleep(0.15)
    assert harness.deliveries == []
    harness.session.fail = False
    harness.notify()
    assert _wait(lambda: len(harness.deliveries) == 1)


def test_refetch_request_is_ignored_once_closed_and_without_a_listener(harness):
    assert harness.client.request_tool_catalog_refetch("restart") is True
    assert _wait(lambda: len(harness.deliveries) == 1)
    harness.client._catalog_listener = None
    assert harness.client.request_tool_catalog_refetch("no-listener") is False
    harness.client.watch_tools_changed(
        lambda records, fetch_epoch: harness.deliveries.append((records, "x"))
    )
    harness.client.close()
    assert harness.client.request_tool_catalog_refetch("closed") is False
    assert harness.client._catalog_deliver_pool is None
    assert len(harness.deliveries) == 1


class _FakeListen:
    """Fake ``Client.listen`` producing scripted events, a named failure, or a
    delayed acknowledgment (``ack_delay`` seconds before entering returns)."""

    def __init__(
        self,
        events=(),
        raise_named: str | None = None,
        ack_delay: float = 0.0,
        hold: float = 0.0,
    ):
        self.events = list(events)
        self.raise_named = raise_named
        self.ack_delay = ack_delay
        self.hold = hold  # keep the stream open this long after the last event
        self.calls = 0

    def __call__(self, *, tools_list_changed: bool):
        assert tools_list_changed is True
        self.calls += 1
        fake = self

        class _Sub:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if fake.events:
                    return fake.events.pop(0)
                if fake.hold:
                    await asyncio.sleep(fake.hold)
                raise StopAsyncIteration

        class _Ctx:
            async def __aenter__(self):
                if fake.ack_delay:
                    await asyncio.sleep(fake.ack_delay)
                if fake.raise_named:
                    raise type(fake.raise_named, (RuntimeError,), {})("nope")
                return _Sub()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.parametrize(
    "advertised, listen, expected_state, expected_deliveries",
    [
        (False, _FakeListen(), "unsupported", 0),
        (True, _FakeListen(raise_named="ListenNotSupportedError"), "legacy", 0),
        (True, _FakeListen(events=[type("ToolsListChanged", (), {})()]), "closed", 1),
    ],
    ids=["no-capability", "older-protocol", "modern-event-then-close"],
)
def test_listen_is_feature_detected_and_drives_refetch(
    advertised, listen, expected_state, expected_deliveries
):
    h = _LoopHarness(_make_client("stdio"), _FakeSession())
    try:
        h.session.server_capabilities = SimpleNamespace(
            tools=SimpleNamespace(list_changed=advertised)
        )
        h.client._client = SimpleNamespace(listen=listen)
        h.loop.call_soon_threadsafe(h.client._arm_catalog_listen)
        assert _wait(lambda: h.client.catalog_listen_state == expected_state)
        if expected_deliveries:
            assert _wait(lambda: len(h.deliveries) >= 1)
        time.sleep(0.1)
        # A modern event and the graceful close coalesce into one refetch each
        # at most; nothing is delivered when listen is unavailable.
        assert (len(h.deliveries) >= 1) is bool(expected_deliveries)
        assert listen.calls == (1 if advertised else 0)
    finally:
        h.close()


@pytest.mark.parametrize(
    "advertised, listen, expected_state, bounded",
    [
        (True, _FakeListen(ack_delay=0.05, hold=2.0), "listening", False),
        (True, _FakeListen(raise_named="ListenNotSupportedError"), "legacy", False),
        (False, _FakeListen(), "unsupported", False),
        (True, _FakeListen(ack_delay=0.8, hold=2.0), "arming", True),
    ],
    ids=["acknowledged", "older-protocol", "no-capability", "never-acknowledged"],
)
def test_startup_readiness_waits_for_listen_resolution_but_is_bounded(
    monkeypatch, advertised, listen, expected_state, bounded
):
    """``_arm_catalog_listen_and_wait`` (the step both connect paths run before
    ``_ready.set()``) returns only once the route is acknowledged or has
    conclusively resolved — and, for a server that never acknowledges, returns
    after the bounded timeout with the driver still running."""
    monkeypatch.setattr(mcp_service, "_CATALOG_LISTEN_ACK_TIMEOUT", 0.3)
    h = _LoopHarness(_make_client("http"), _FakeSession())
    try:
        h.session.server_capabilities = SimpleNamespace(
            tools=SimpleNamespace(list_changed=advertised)
        )
        h.client._client = SimpleNamespace(listen=listen)
        started = time.monotonic()
        h.run(h.client._arm_catalog_listen_and_wait())
        elapsed = time.monotonic() - started
        assert h.client.catalog_listen_state == expected_state
        if bounded:
            assert 0.3 <= elapsed < 0.7
            assert not h.client._catalog_listen_task.done()
            # The late acknowledgment still lands afterwards.
            assert _wait(lambda: h.client.catalog_listen_state == "listening", timeout=2)
        else:
            assert elapsed < 0.3
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Agent reconcile with fake clients
# ---------------------------------------------------------------------------


def _record(name: str, version: int = 1, **meta) -> dict:
    return {
        "name": name,
        "description": f"{name} v{version}",
        "schema": {"type": "object", "properties": {"v": {"const": version}}},
        **meta,
    }


class _FakeClient:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.records = list(type(self).catalog)
        self.closed = False
        self.calls: list[tuple[str, dict]] = []
        self.listener = None
        # Mirrors the real clients' starting epoch, so tests can reference
        # ``client._restart_epoch`` as the second positional argument every
        # registered listener now requires, exactly as production code does.
        self._restart_epoch = 0
        type(self).instances.append(self)

    catalog: list[dict] = []

    @property
    def name(self):
        return self.kwargs.get("command")

    @property
    def url(self):
        # ``connect_mcp_http``'s dedup check reads ``.url``, not ``.name``;
        # this fake stands in for both ``MCPClient`` and ``HTTPMCPClient``.
        return self.kwargs.get("url")

    def start(self):
        pass

    def is_connected(self):
        return not self.closed

    def list_tools(self):
        return [dict(r) for r in self.records]

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"status": "ok", "tool": name}

    def close(self):
        self.closed = True

    def watch_tools_changed(self, listener):
        self.listener = listener


class _FailingListToolsClient(_FakeClient):
    """Starts and appends successfully, then fails MCP preflight (``list_tools``).

    Models the ``connect_mcp*`` preflight-failure branch: the client is
    already published into ``_mcp_clients`` by the time this raises, so the
    except-branch must discard it through the locked ``_discard_mcp_client``
    path.
    """

    def list_tools(self):
        raise RuntimeError("boom: server rejected tools/list")


class _RecordingChat:
    def __init__(self):
        self.interface = ChatInterface()
        self.tool_updates: list[list] = []

    def update_tools(self, tools):
        self.tool_updates.append(list(tools or []))

    def update_system_prompt(self, prompt):
        pass

    def update_system_prompt_batches(self, batches):
        pass


@pytest.fixture
def two_clients(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_service, "MCPClient", _FakeClient)
    _FakeClient.instances = []
    agent = _mk_agent(tmp_path)
    _FakeClient.catalog = [_record("a1"), _record("a2", title="A2")]
    assert agent.connect_mcp("server-a") == ["a1", "a2"]
    _FakeClient.catalog = [_record("b1")]
    assert agent.connect_mcp("server-b") == ["b1"]
    client_a, client_b = _FakeClient.instances
    assert client_a.listener is not None and client_b.listener is not None
    return agent, client_a, client_b


def test_reconcile_replaces_only_the_owning_clients_tools(two_clients):
    agent, client_a, client_b = two_clients
    a1_before = agent._tool_handlers["a1"]
    b1_before = agent._tool_handlers["b1"]
    chat = _RecordingChat()
    agent._session.chat = chat

    report = client_a.listener(
        [_record("a1", version=2, title="A1v2"), _record("a3")], client_a._restart_epoch
    )

    assert report["status"] == "ok"
    assert report["added"] == ["a3"] and report["replaced"] == ["a1"] and report["removed"] == ["a2"]
    assert _mcp_names(agent) == ["a1", "b1", "a3"]
    assert agent._tool_handlers["a1"] is not a1_before
    assert agent._tool_handlers["b1"] is b1_before
    assert "a2" not in agent._tool_handlers
    assert agent._mcp_clients_by_tool == {"a1": client_a, "a3": client_a, "b1": client_b}
    assert agent._mcp_tool_names == {"a1", "a3", "b1"}
    assert agent.mcp_tool_metadata("a1") == {"title": "A1v2"}
    assert agent.mcp_tool_metadata("a2") is None
    assert agent.mcp_tool_metadata("a3") == {}
    assert agent._tool_handlers["a3"]({"x": 1}) == {"status": "ok", "tool": "a3"}
    assert client_a.calls[-1] == ("a3", {"x": 1})
    assert [s.name for s in chat.tool_updates[-1]] == [s.name for s in agent._build_tool_schemas()]
    # An identical redelivery is a no-op that keeps handler identity.
    a3_handler = agent._tool_handlers["a3"]
    again = client_a.listener(
        [_record("a1", version=2, title="A1v2"), _record("a3")], client_a._restart_epoch
    )
    assert again["added"] == [] and again["replaced"] == [] and again["removed"] == []
    assert again["unchanged"] == 2
    assert agent._tool_handlers["a3"] is a3_handler


@pytest.mark.parametrize(
    "catalog, reason",
    [
        ([_record("a1"), _record("a1")], "duplicate_tool_name"),
        ([_record("a1"), _record("b1")], "tool_name_collision"),
        ([_record("a1"), {"name": "", "schema": {}}], "tool_name_invalid"),
        ([_record("a1"), {"name": "x", "schema": "not-a-dict"}], "malformed_tool_record"),
        ("not-a-list", "catalog_not_a_list"),
    ],
    ids=["duplicate", "other-client-name", "empty-name", "malformed", "not-a-list"],
)
def test_invalid_or_colliding_catalog_keeps_the_last_good_one(two_clients, catalog, reason):
    agent, client_a, _ = two_clients
    handlers_before = dict(agent._tool_handlers)
    schemas_before = list(agent._tool_schemas)
    routes_before = dict(agent._mcp_clients_by_tool)
    metadata_before = dict(agent._mcp_tool_metadata)

    report = client_a.listener(catalog, client_a._restart_epoch)

    assert report == {"status": "rejected", "reason": reason} | {
        k: v for k, v in report.items() if k not in ("status", "reason")
    }
    assert report["reason"] == reason
    assert agent._tool_handlers == handlers_before
    assert agent._tool_schemas == schemas_before
    assert agent._mcp_clients_by_tool == routes_before
    assert agent._mcp_tool_metadata == metadata_before


def test_catalog_colliding_with_an_intrinsic_or_native_tool_is_rejected(two_clients):
    agent, client_a, _ = two_clients
    intrinsic = next(iter(agent._intrinsics))
    assert (
        client_a.listener([_record("a1"), _record(intrinsic)], client_a._restart_epoch)["reason"]
        == "tool_name_collision"
    )
    agent._tool_handlers["native"] = lambda args: {}
    assert (
        client_a.listener([_record("a1"), _record("native")], client_a._restart_epoch)["reason"]
        == "tool_name_collision"
    )
    assert _mcp_names(agent) == ["a1", "a2", "b1"]


def test_stale_delivery_after_client_teardown_is_ignored(two_clients):
    agent, client_a, client_b = two_clients
    agent._discard_mcp_client(client_a)
    assert (
        client_a.listener([_record("a9")], client_a._restart_epoch)["reason"] == "client_not_live"
    )
    assert "a9" not in agent._tool_handlers
    # A client that is still tracked but no longer connected is likewise stale.
    client_b.closed = True
    assert (
        client_b.listener([_record("b9")], client_b._restart_epoch)["reason"] == "client_not_live"
    )
    assert "b9" not in agent._tool_handlers


def test_live_known_tools_tracks_the_surface_without_a_snapshot(two_clients):
    agent, client_a, _ = two_clients
    view = _LiveKnownTools(agent)
    assert "a1" in view and "a3" not in view
    before = len(view)
    client_a.listener([_record("a3")], client_a._restart_epoch)  # a1 + a2 replaced by a3
    assert "a3" in view and "a1" not in view and "a2" not in view
    assert len(view) == before - 1
    assert "a3" in sorted(view)


# ---------------------------------------------------------------------------
# Teardown races an in-flight reconcile: stop/refresh must serialize with
# ``_reconcile_mcp_client_tools`` under the shared surface lock, so neither a
# lost race nor a won one can leave a stale/partial surface behind.
# ---------------------------------------------------------------------------


def _gate_on_agent_log(agent, event_name: str) -> tuple[threading.Event, threading.Event]:
    """Pause ``agent._log(event_name, ...)`` until released.

    ``_reconcile_mcp_client_tools`` logs ``mcp_catalog_reconciled`` as the
    last statement inside its ``with _surface_lock(self):`` block — gating
    here pauses the reconcile call *after* its full mutation has committed
    but *while it still holds the lock*, which is exactly the window a
    concurrent, unlocked teardown used to be able to interleave with.
    """
    entered = threading.Event()
    release = threading.Event()
    original_log = agent._log

    def gated_log(event, **fields):
        if event == event_name:
            entered.set()
            release.wait(timeout=5)
        return original_log(event, **fields)

    agent._log = gated_log
    return entered, release


def test_stop_teardown_waits_for_an_in_flight_reconcile_then_finalizes_it(two_clients):
    """Reconcile wins the race: it must fully apply before stop tears down.

    Without the shared lock around ``_close_agent_owned_services_after_quiescence``,
    stop's client-closing/metadata-clearing could interleave with a reconcile
    still mutating ``_tool_handlers``/``_tool_schemas``/``_mcp_clients_by_tool``,
    leaving those three surfaces disagreeing with each other. Serializing them
    means stop can only ever observe the reconcile's fully-committed result.
    """
    agent, client_a, client_b = two_clients
    entered, release = _gate_on_agent_log(agent, "mcp_catalog_reconciled")

    reconcile_result: dict = {}

    def run_reconcile():
        reconcile_result["value"] = client_a.listener([_record("a3")], client_a._restart_epoch)

    t_reconcile = threading.Thread(target=run_reconcile)
    t_reconcile.start()
    assert entered.wait(timeout=5), "reconcile never reached its held-lock gate"

    stop_done = threading.Event()

    def run_stop():
        agent.stop(timeout=5)
        stop_done.set()

    t_stop = threading.Thread(target=run_stop)
    t_stop.start()
    time.sleep(0.2)
    assert not stop_done.is_set(), "stop must block behind the reconcile's held surface lock"

    release.set()
    t_reconcile.join(timeout=5)
    t_stop.join(timeout=5)
    assert stop_done.is_set()

    # The reconcile committed atomically before stop tore anything down: its
    # tool, schema, and route entries for "a3" are all present together, never
    # partially applied.
    assert reconcile_result["value"]["status"] == "ok"
    assert "a3" in agent._tool_handlers
    assert "a3" in [s.name for s in agent._tool_schemas]
    assert agent._mcp_clients_by_tool["a3"] is client_a
    # Stop then ran, strictly after, and closed every client.
    assert client_a.closed and client_b.closed
    assert agent._mcp_tool_metadata == {}


def test_stop_teardown_racing_an_arriving_reconcile_rejects_it_as_not_live(two_clients):
    """Stop wins the race: the reconcile must see the client as dead and reject.

    Gates ``client_a.close()`` (called inside stop's locked teardown) so a
    reconcile arriving concurrently is forced to wait on the same lock. Once
    stop finishes and releases it, the reconcile's own liveness check —
    itself taken under that lock — must observe the now-closed client and
    make no mutation: no dead-client handler is resurrected.
    """
    agent, client_a, client_b = two_clients
    entered = threading.Event()
    release = threading.Event()
    original_close = client_a.close

    def gated_close():
        entered.set()
        release.wait(timeout=5)
        original_close()

    client_a.close = gated_close

    stop_done = threading.Event()

    def run_stop():
        agent.stop(timeout=5)
        stop_done.set()

    t_stop = threading.Thread(target=run_stop)
    t_stop.start()
    assert entered.wait(timeout=5), "stop never reached the gated close()"

    reconcile_result: dict = {}
    reconcile_done = threading.Event()

    def run_reconcile():
        reconcile_result["value"] = client_a.listener([_record("a3")], client_a._restart_epoch)
        reconcile_done.set()

    t_reconcile = threading.Thread(target=run_reconcile)
    t_reconcile.start()
    time.sleep(0.2)
    assert not reconcile_done.is_set(), "reconcile must block behind stop's held surface lock"

    release.set()
    t_stop.join(timeout=5)
    t_reconcile.join(timeout=5)
    assert stop_done.is_set() and reconcile_done.is_set()

    assert reconcile_result["value"]["reason"] == "client_not_live"
    assert "a3" not in agent._tool_handlers
    assert "a3" not in agent._mcp_clients_by_tool
    assert agent._mcp_tool_metadata == {}


def test_refresh_teardown_waits_for_an_in_flight_reconcile_then_wipes_it(tmp_path, monkeypatch):
    """Refresh must never keep a raced-in tool from a client it is discarding.

    Mirrors the stop-side race above for ``_setup_from_init``'s teardown: a
    reconcile that wins the race commits fully, but refresh's full-surface
    clear (also under the shared lock) always runs after it and wipes the
    result along with everything else — a rebuilt agent must never retain a
    dead client's tools.
    """
    monkeypatch.setattr(mcp_service, "MCPClient", _FakeClient)
    _FakeClient.instances = []
    agent = _make_agent(tmp_path, _make_init(capabilities={}))
    _FakeClient.catalog = [_record("a1"), _record("a2")]
    assert agent.connect_mcp("server-a") == ["a1", "a2"]
    # Model a started agent only after setup-time tool registration is complete.
    agent._sealed = True
    client_a = _FakeClient.instances[-1]
    assert client_a.listener is not None

    entered, release = _gate_on_agent_log(agent, "mcp_catalog_reconciled")

    reconcile_result: dict = {}

    def run_reconcile():
        reconcile_result["value"] = client_a.listener([_record("a3")], client_a._restart_epoch)

    t_reconcile = threading.Thread(target=run_reconcile)
    t_reconcile.start()
    assert entered.wait(timeout=5), "reconcile never reached its held-lock gate"

    refresh_done = threading.Event()

    def run_refresh():
        agent._setup_from_init()
        refresh_done.set()

    t_refresh = threading.Thread(target=run_refresh)
    t_refresh.start()
    time.sleep(0.2)
    assert not refresh_done.is_set(), "refresh must block behind the reconcile's held surface lock"

    release.set()
    t_reconcile.join(timeout=5)
    t_refresh.join(timeout=5)
    assert refresh_done.is_set()

    assert reconcile_result["value"]["status"] == "ok"
    # Refresh's full clear ran strictly after the reconcile committed, so the
    # raced-in "a3" never survives into the rebuilt agent.
    assert "a3" not in agent._tool_handlers
    assert "a3" not in [s.name for s in agent._tool_schemas]
    assert agent._mcp_tool_metadata == {}
    assert client_a.closed is True


# ---------------------------------------------------------------------------
# Retry and preflight cleanup race reconciliation too: `_retry_failed_mcps`'s
# dead-client/disconnected-replacement teardown and `connect_mcp*`'s
# preflight-failure discard must be exactly as mutually exclusive with
# `_reconcile_mcp_client_tools` as mount/stop/refresh already are above.
# ---------------------------------------------------------------------------


def test_retry_cleanup_of_a_dead_client_is_mutually_exclusive_with_a_concurrent_reconcile(
    tmp_path, monkeypatch
):
    """`_retry_failed_mcps` closing/removing/forgetting a dead client must
    never interleave with a concurrent `tools/list_changed` reconciliation for
    a *different* live client mutating the same `_mcp_clients_by_tool` /
    `_mcp_tool_metadata` / `_mcp_tool_collisions` structures.

    Without locking that cleanup, gating the dead client's `close()` (the last
    step of `_discard_mcp_client`'s teardown) would not block a concurrent
    reconcile at all: the two would run fully interleaved. Gating it here and
    asserting the reconcile blocks until release is therefore a genuine
    regression for the unlocked path — the isolated cleanup half only:
    `connect_mcp*` is stubbed so the test does not depend on that unrelated
    reconnect machinery.
    """
    monkeypatch.setattr(mcp_service, "MCPClient", _FakeClient)
    _FakeClient.instances = []
    agent = _mk_agent(tmp_path)
    _FakeClient.catalog = [_record("a1")]
    assert agent.connect_mcp("server-a") == ["a1"]
    _FakeClient.catalog = [_record("b1")]
    assert agent.connect_mcp("server-b") == ["b1"]
    client_a, client_b = _FakeClient.instances

    client_a.closed = True  # unhealthy: is_connected() is False
    agent._mcp_init_specs = {
        "server-a": {
            "cfg": {"command": "server-a"},
            "source": "init.json:mcp",
            "client": client_a,
        },
    }
    monkeypatch.setattr(agent, "connect_mcp", lambda *a, **kw: [])
    monkeypatch.setattr(agent, "connect_mcp_http", lambda *a, **kw: [])

    entered = threading.Event()
    release = threading.Event()
    original_close = client_a.close

    def gated_close():
        entered.set()
        release.wait(timeout=5)
        original_close()

    client_a.close = gated_close

    retry_result: dict = {}
    retry_done = threading.Event()

    def run_retry():
        retry_result["value"] = agent._retry_failed_mcps()
        retry_done.set()

    t_retry = threading.Thread(target=run_retry)
    t_retry.start()
    assert entered.wait(timeout=5), "retry never reached the gated close()"

    reconcile_result: dict = {}
    reconcile_done = threading.Event()

    def run_reconcile():
        reconcile_result["value"] = client_b.listener([_record("b2")], client_b._restart_epoch)
        reconcile_done.set()

    t_reconcile = threading.Thread(target=run_reconcile)
    t_reconcile.start()
    time.sleep(0.2)
    assert not reconcile_done.is_set(), "reconcile must block behind retry's held surface lock"

    release.set()
    t_retry.join(timeout=5)
    t_reconcile.join(timeout=5)
    assert retry_done.is_set() and reconcile_done.is_set()

    assert reconcile_result["value"]["status"] == "ok"
    assert "b2" in agent._tool_handlers
    assert agent._mcp_clients_by_tool["b2"] is client_b
    # The dead client's routes/handlers/metadata were fully forgotten and it
    # was dropped from `_mcp_clients`, uncorrupted by the concurrent reconcile.
    assert "a1" not in agent._tool_handlers
    assert "a1" not in agent._mcp_clients_by_tool
    assert client_a not in agent._mcp_clients
    assert retry_result["value"]["still_failed"] == ["server-a"]


@pytest.mark.parametrize(
    "kind, connect_kwargs",
    [
        ("stdio", {"command": "server-fail"}),
        ("http", {"url": "https://example.invalid/fail"}),
    ],
    ids=["stdio", "http"],
)
def test_preflight_discard_after_failed_list_tools_is_mutually_exclusive_with_reconcile(
    tmp_path, monkeypatch, kind, connect_kwargs
):
    """`connect_mcp`/`connect_mcp_http`'s preflight-failure branch — `list_tools()`
    raised after the just-started client was already published into
    `_mcp_clients` — discards that client through the locked
    `_discard_mcp_client` path, so it can never interleave with a concurrent
    `tools/list_changed` reconciliation for an already-mounted client mutating
    the same `_mcp_clients_by_tool` / `_mcp_tool_metadata` dicts. Parametrized
    over both transports: `connect_mcp_http` shares the identical locked
    dedup/append/discard shape as `connect_mcp`.
    """
    monkeypatch.setattr(mcp_service, "MCPClient", _FakeClient)
    _FakeClient.instances = []
    agent = _mk_agent(tmp_path)
    _FakeClient.catalog = [_record("a1")]
    assert agent.connect_mcp("server-a") == ["a1"]
    client_a = _FakeClient.instances[-1]

    entered, release = _gate_on_agent_log(agent, "mcp_catalog_reconciled")

    reconcile_result: dict = {}

    def run_reconcile():
        reconcile_result["value"] = client_a.listener([_record("a2")], client_a._restart_epoch)

    t_reconcile = threading.Thread(target=run_reconcile)
    t_reconcile.start()
    assert entered.wait(timeout=5), "reconcile never reached its held-lock gate"

    failing_attr = "MCPClient" if kind == "stdio" else "HTTPMCPClient"
    monkeypatch.setattr(mcp_service, failing_attr, _FailingListToolsClient)
    connect_fn = agent.connect_mcp if kind == "stdio" else agent.connect_mcp_http
    connect_done = threading.Event()
    connect_error: dict = {}

    def run_connect():
        try:
            connect_fn(**connect_kwargs)
        except Exception as exc:
            connect_error["value"] = exc
        connect_done.set()

    t_connect = threading.Thread(target=run_connect)
    t_connect.start()
    time.sleep(0.2)
    assert not connect_done.is_set(), (
        "the failing connect (publication and/or preflight discard) must "
        "block behind the reconcile's held surface lock"
    )

    release.set()
    t_reconcile.join(timeout=5)
    t_connect.join(timeout=5)
    assert connect_done.is_set()

    assert reconcile_result["value"]["status"] == "ok"
    assert "a2" in agent._tool_handlers
    assert isinstance(connect_error.get("value"), RuntimeError)
    failing_client = _FailingListToolsClient.instances[-1]
    assert failing_client not in agent._mcp_clients
    assert failing_client.closed is True
    assert all(owner is not failing_client for owner in agent._mcp_clients_by_tool.values())
    # The reconciled client itself is untouched by the unrelated failure.
    assert client_a.closed is False
    assert client_a in agent._mcp_clients


# ---------------------------------------------------------------------------
# Restart epoch fence: a catalog fetched before ``restart()`` must never
# reconcile after it, even via a detached delivery-pool task or a listener
# call blocked on the surface lock across the restart.
# ---------------------------------------------------------------------------


def test_deliver_catalog_drops_a_refetch_stamped_before_a_restart(harness):
    """Unit-level fence: ``_deliver_catalog`` itself rejects a stale epoch.

    ``close()``'s delivery-pool shutdown is ``wait=False``, so a refetch that
    already finished (and was submitted to the pool) before a restart can
    still run afterward, on a worker thread the new connection no longer
    owns. The generation counter alone does not catch this: it is never
    reset by restart, so a queued stale generation can still be numerically
    higher than whatever the fresh post-restart delivery has reached so far.
    """
    client = harness.client
    stale_epoch = client._restart_epoch
    client._restart_epoch += 1  # simulate a restart that has already begun

    client._deliver_catalog(client._catalog_generation + 1, [{"name": "stale"}], stale_epoch)
    assert harness.deliveries == []

    # A delivery stamped with the *current* epoch still applies normally.
    client._deliver_catalog(client._catalog_generation + 1, [{"name": "fresh"}], client._restart_epoch)
    assert len(harness.deliveries) == 1
    assert harness.deliveries[0][0] == [{"name": "fresh"}]


def test_reconcile_rejects_a_delivery_whose_fetch_epoch_predates_a_restart(two_clients):
    """Agent-level fence: a delivery blocked on the surface lock across a
    restart must still be rejected once it finally acquires the lock.

    ``fetch_epoch`` here plays the role of the value ``_deliver_catalog``
    already validated the fetch against and forwarded, unmodified, all the
    way through the wiring lambda into this method (see
    ``_mount_mcp_tools_locked`` and ``MCPClient._deliver_catalog``) — never a
    value reread from ``client._restart_epoch`` at any point in that chain.
    """
    agent, client_a, _ = two_clients
    client_a._restart_epoch = 0
    handlers_before = dict(agent._tool_handlers)
    schemas_before = list(agent._tool_schemas)
    routes_before = dict(agent._mcp_clients_by_tool)

    stale_epoch = client_a._restart_epoch
    client_a._restart_epoch += 1  # a restart completed while this was "in flight"

    report = agent._reconcile_mcp_client_tools(client_a, [_record("a3")], stale_epoch)

    assert report == {"status": "rejected", "reason": "client_restarted"}
    assert agent._tool_handlers == handlers_before
    assert agent._tool_schemas == schemas_before
    assert agent._mcp_clients_by_tool == routes_before

    # The same delivery, stamped with the current epoch, applies normally.
    current_report = agent._reconcile_mcp_client_tools(
        client_a, [_record("a3")], client_a._restart_epoch
    )
    assert current_report["status"] == "ok"


def test_wired_listener_forwards_the_delivered_epoch_without_rereading_it(two_clients):
    """Regression: the production listener wired by ``_mount_mcp_tools_locked``
    must forward the ``fetch_epoch`` it is called with *verbatim* into
    ``_reconcile_mcp_client_tools`` — never reread ``client._restart_epoch``
    at call time.

    ``_deliver_catalog`` validates a fetch's epoch against the client's
    current ``_restart_epoch`` and, only having passed that check, invokes the
    listener with that same already-validated value. ``restart()`` runs
    synchronously on whatever arbitrary thread calls it (e.g. ``call_tool``'s
    stale-resource recovery), while delivery runs on the client's own pool
    thread, so a restart can genuinely land in the handoff between that check
    and the listener call. A listener that re-derived its own epoch from the
    client instead of using the one it was handed would, in exactly that
    window, observe the *post-restart* value — stamping stale records
    (fetched under the old connection) with an epoch that now matches the
    client's current one, and defeating ``_reconcile_mcp_client_tools``'s own
    fence entirely.

    Calling the actual registered listener (``client_a.listener``, exactly
    the closure ``_mount_mcp_tools_locked`` built) with an explicit, already-
    stale ``fetch_epoch`` — while the client's own epoch has since moved on,
    exactly as it would after such a race — proves the wiring does not
    rederive its own value: a listener that ignored its second argument in
    favor of a fresh read would report this delivery as accepted, not
    rejected.
    """
    agent, client_a, _ = two_clients
    stale_epoch = client_a._restart_epoch  # what _deliver_catalog validated against
    client_a._restart_epoch += 1  # a restart lands in the check-to-listener handoff

    # The registered listener, invoked exactly as _deliver_catalog invokes it:
    # with the fetch's own already-validated epoch, not a fresh read.
    report = client_a.listener([_record("a3")], stale_epoch)

    assert report == {"status": "rejected", "reason": "client_restarted"}
    assert "a3" not in agent._tool_handlers
    assert "a3" not in [s.name for s in agent._tool_schemas]
    assert "a3" not in agent._mcp_clients_by_tool

    # The same delivery, carrying the client's now-current epoch — exactly
    # what a genuinely fresh post-restart fetch would be stamped with —
    # applies normally through the same wiring.
    current_report = client_a.listener([_record("a3")], client_a._restart_epoch)
    assert current_report["status"] == "ok"
    assert "a3" in agent._tool_handlers


# ---------------------------------------------------------------------------
# ToolExecutor must not freeze a momentarily-empty live known-tools view.
# ---------------------------------------------------------------------------


def test_tool_executor_preserves_a_momentarily_empty_live_known_tools_view():
    """``known_tools or set()`` called the live view's ``__len__``: a live
    surface that happened to be empty at construction time (no intrinsics,
    no MCP/native handlers mounted yet) was silently swapped for a frozen
    empty set, and a tool added moments later could never become known to
    that executor for the rest of the turn."""
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.tool_executor import ToolExecutor

    fake_agent = SimpleNamespace(_intrinsics={}, _tool_handlers={})
    view = _LiveKnownTools(fake_agent)
    assert len(view) == 0

    executor = ToolExecutor(
        dispatch_fn=lambda tc: {},
        make_tool_result_fn=lambda name, result, **kw: result,
        guard=LoopGuard(),
        known_tools=view,
    )
    assert executor._known_tools is view

    fake_agent._tool_handlers["late_tool"] = lambda args: {}
    assert "late_tool" in executor._known_tools
    assert len(executor._known_tools) == 1
