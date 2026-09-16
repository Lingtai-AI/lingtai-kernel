"""Deferred provider disclosure for the curated messaging-channel tools.

The Telegram, Feishu, and WeChat MCP families advertise large LTP-v2 schemas.
At a fresh session the provider payload carries only a compact manual-only
stand-in for each; the full schema is disclosed on the next provider round
once the tool is called (``action='manual'`` is the only callable action of
the stub) or automatically when that channel delivers an inbound notification.
Handlers and the registered full schema stay in place throughout — only the
provider-visible disclosure is delayed.
"""
from __future__ import annotations

import importlib
import json
import queue
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.agent import Agent
from lingtai.kernel.llm import FunctionSchema
from lingtai.kernel.llm.base import LLMResponse, ToolCall
from lingtai.kernel.llm.interface import ChatInterface, ToolResultBlock
from lingtai.kernel.message import MSG_REQUEST, MSG_TC_WAKE
from lingtai.kernel.state import AgentState
from lingtai.services import mcp as mcp_service
from lingtai.services.mcp import DEFERRED_DISCLOSURE_TOOLS, deferred_tool_stub
from lingtai.services.mcp_inbox import INBOX_DIRNAME, _scan_once, notification_channel
from tests._notification_store_helpers import (
    notification_store_for,
    publish_test_payload,
)
from tests._service_helpers import make_gemini_mock_service as make_mock_service
from tests.test_mcp_inbox import _write_event

CHANNELS = ("telegram", "feishu", "wechat")


def _server_tool(name: str) -> dict:
    """The exact tool record the curated ``<name>`` MCP server advertises."""
    server = importlib.import_module(f"lingtai.mcp_servers.{name}.server")
    return {"name": name, "schema": server.SCHEMA, "description": server.DESCRIPTION}


def _widget_tool() -> dict:
    """A non-target LTP-v2 family that also offers ``manual``."""
    from lingtai.tools.tool_family import ChildTool, ToolFamily

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    family = ToolFamily(
        "widget",
        [
            ChildTool("spin", {**empty, "properties": {"speed": {"type": "integer"}}}, lambda i: {}),
            ChildTool("manual", empty, lambda i: {}),
        ],
    )
    return {"name": "widget", "schema": family.build_schema(), "description": "widget family"}


def _flat_tool() -> dict:
    return {
        "name": "echo",
        "schema": {"type": "object", "properties": {"text": {"type": "string"}}},
        "description": "third-party flat tool",
    }


class _FakeClient:
    """Stdio client double: advertises a fixed catalog, records calls."""

    catalog: list[dict] = []
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict]] = []
        type(self).instances.append(self)

    def start(self):
        pass

    def is_connected(self):
        return True

    def list_tools(self):
        return list(self.catalog)

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return {"status": "ok", "tool": name}

    def close(self):
        pass


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch):
    """A real Agent with every channel family plus two non-target tools mounted."""
    _FakeClient.catalog = [_server_tool(name) for name in CHANNELS] + [_widget_tool(), _flat_tool()]
    _FakeClient.instances = []
    monkeypatch.setattr(mcp_service, "MCPClient", _FakeClient)
    service = make_mock_service()
    agent = Agent(
        service=service,
        agent_name="test",
        working_dir=tmp_path / "agent",
        capabilities={"mcp": {}},
    )
    registered = agent.connect_mcp("fake-server")
    assert set(registered) == set(CHANNELS) | {"widget", "echo"}
    return agent, service


def _visible(agent, name: str) -> FunctionSchema:
    return next(s for s in agent._build_tool_schemas() if s.name == name)


def _registered(agent, name: str) -> FunctionSchema:
    return next(s for s in agent._tool_schemas if s.name == name)


def _is_manual_only_stub(schema: FunctionSchema) -> bool:
    props = schema.parameters["properties"]
    return (
        props["action"]["enum"] == ["manual"]
        and props["input"] == {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
            "description": props["input"]["description"],
        }
        and schema.parameters["required"] == ["action", "input", "reasoning"]
        and schema.parameters["additionalProperties"] is False
    )


# ---------------------------------------------------------------------------
# Stub construction (wrapper MCP boundary)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CHANNELS)
def test_channel_stub_is_a_small_manual_only_family(name: str) -> None:
    tool = _server_tool(name)
    stub = deferred_tool_stub(name, tool["schema"], tool["description"])

    assert isinstance(stub, FunctionSchema)
    assert stub.name == name
    assert _is_manual_only_stub(stub)
    # The stand-in tells the model how to reach the full tool.
    assert "manual" in stub.description
    assert "send" in stub.description  # the deferred action inventory is named
    full_bytes = len(json.dumps({"description": tool["description"], "parameters": tool["schema"]}))
    stub_bytes = len(json.dumps({"description": stub.description, "parameters": stub.parameters}))
    assert stub_bytes < 1200
    # WeChat's family is the smallest (~9 KB); the stub is still a fraction of it.
    assert stub_bytes * 5 < full_bytes


def test_non_target_and_non_family_tools_get_no_stub() -> None:
    widget = _widget_tool()
    assert deferred_tool_stub("widget", widget["schema"], widget["description"]) is None
    flat = _flat_tool()
    assert deferred_tool_stub("telegram", flat["schema"], flat["description"]) is None
    # A target name whose family offers no ``manual`` cannot be collapsed safely.
    telegram = _server_tool("telegram")
    no_manual = json.loads(json.dumps(telegram["schema"]))
    no_manual["properties"]["action"]["enum"] = ["send"]
    assert deferred_tool_stub("telegram", no_manual, telegram["description"]) is None
    assert DEFERRED_DISCLOSURE_TOOLS == frozenset(CHANNELS)


# ---------------------------------------------------------------------------
# Fresh-session visibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CHANNELS)
def test_fresh_session_advertises_stub_and_keeps_full_registration(mounted, name: str) -> None:
    agent, service = mounted
    visible = _visible(agent, name)
    registered = _registered(agent, name)

    assert _is_manual_only_stub(visible)
    assert "reasoning" in visible.parameters["properties"]
    # The registered schema (dispatch/validation source) is the full family.
    assert "send" in registered.parameters["properties"]["action"]["enum"]
    assert registered.stub is not None
    assert registered.disclosure_sources == (notification_channel(name),)
    assert name in agent._tool_handlers

    # The provider session is created with the stub, not the full schema.
    agent._session.ensure_session()
    sent = {s.name: s for s in service.create_session.call_args.kwargs["tools"]}
    assert _is_manual_only_stub(sent[name])


def test_non_target_tools_are_unchanged(mounted) -> None:
    agent, _ = mounted
    for name in ("widget", "echo"):
        assert _registered(agent, name).stub is None
        assert _visible(agent, name).parameters == {
            **_registered(agent, name).parameters,
            "properties": _visible(agent, name).parameters["properties"],
        }
    assert _visible(agent, "widget").parameters["properties"]["action"]["enum"] == ["spin", "manual"]
    assert "text" in _visible(agent, "echo").parameters["properties"]


# ---------------------------------------------------------------------------
# Disclosure triggers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", CHANNELS)
def test_calling_manual_discloses_full_schema_for_next_round(mounted, name: str) -> None:
    agent, service = mounted
    agent._token_decomp_dirty = False
    call = ToolCall(
        name=name,
        args={"action": "manual", "input": {}, "_reasoning": "learn the tool"},
        id="tc-manual",
    )

    result = agent._dispatch_tool(call)

    assert result["status"] == "ok"
    (client,) = _FakeClient.instances
    assert client.calls[-1] == (name, {"action": "manual", "input": {}, "reasoning": "learn the tool"})
    assert name in agent._disclosed_tools
    assert agent._token_decomp_dirty is True
    visible = _visible(agent, name)
    assert visible.parameters == _registered(agent, name).parameters | {
        "properties": visible.parameters["properties"]
    }
    assert "send" in visible.parameters["properties"]["action"]["enum"]
    # Other channels stay collapsed; a session rebuild carries the disclosure.
    for other in CHANNELS:
        if other != name:
            assert _is_manual_only_stub(_visible(agent, other))
    agent._session._rebuild_session(ChatInterface())
    rebuilt = {s.name: s for s in service.create_session.call_args.kwargs["tools"]}
    assert "send" in rebuilt[name].parameters["properties"]["action"]["enum"]


def test_removing_a_disclosed_tool_forgets_its_disclosure(mounted) -> None:
    agent, _ = mounted
    agent._dispatch_tool(ToolCall(name="wechat", args={"action": "manual", "input": {}}, id="t"))
    assert "wechat" in agent._disclosed_tools
    agent.remove_tool("wechat")
    assert "wechat" not in agent._disclosed_tools


class _RecordingChat:
    """Provider-session double that records every ``update_tools`` payload."""

    def __init__(self):
        self.interface = ChatInterface()
        self.tool_updates: list[list[FunctionSchema]] = []

    def update_tools(self, tools):
        self.tool_updates.append(list(tools or []))

    def update_system_prompt(self, prompt):
        pass

    def update_system_prompt_batches(self, batches):
        pass

    def context_window(self):
        return 200_000

    def send(self, message):
        return LLMResponse(text="ok")


def _disclose_via_manual(agent, name: str) -> None:
    agent._dispatch_tool(ToolCall(name=name, args={"action": "manual", "input": {}}, id="t"))


def _disclose_via_licc_event(agent, name: str) -> None:
    from lingtai.kernel.meta_block import attach_active_notifications

    workdir = agent._working_dir
    _write_event(workdir, name, "ev1", {"from": "alice", "subject": "s", "body": "hi"})
    _scan_once(agent, workdir / INBOX_DIRNAME)
    carrier = ToolResultBlock(id="t1", name="echo", content={"ok": True})
    attach_active_notifications(agent, [carrier], prior_holder=None)


@pytest.mark.parametrize(
    "trigger", [_disclose_via_manual, _disclose_via_licc_event], ids=["manual", "notification"]
)
def test_next_session_send_updates_chat_with_disclosed_schema_only(mounted, trigger) -> None:
    """The real ``SessionManager.send`` continuation pushes the effective tool
    list to the provider chat: the disclosed channel arrives in full while the
    other channels are still their stubs."""
    agent, _ = mounted
    chat = _RecordingChat()
    agent._session.chat = chat
    agent._session.send("first round")
    before = {s.name: s for s in chat.tool_updates[-1]}
    assert all(_is_manual_only_stub(before[name]) for name in CHANNELS)

    trigger(agent, "telegram")
    agent._session.send("next round")

    after = {s.name: s for s in chat.tool_updates[-1]}
    assert len(chat.tool_updates) == 2
    assert "send" in after["telegram"].parameters["properties"]["action"]["enum"]
    assert _is_manual_only_stub(after["feishu"])
    assert _is_manual_only_stub(after["wechat"])
    assert after["widget"].parameters["properties"]["action"]["enum"] == ["spin", "manual"]


def test_inbound_licc_event_discloses_only_the_producing_channel(mounted) -> None:
    """ACTIVE delivery: the real LICC producer writes ``mcp.<name>.json`` and the
    tool-result attach path discloses exactly that channel's tool."""
    from lingtai.kernel.meta_block import attach_active_notifications

    agent, _ = mounted
    workdir = agent._working_dir
    _write_event(workdir, "telegram", "ev1", {"from": "alice", "subject": "s", "body": "hi"})
    _scan_once(agent, workdir / INBOX_DIRNAME)
    assert (workdir / ".notification" / "mcp.telegram.json").is_file()

    carrier = ToolResultBlock(id="t1", name="echo", content={"ok": True})
    holder = attach_active_notifications(agent, [carrier], prior_holder=None)

    assert holder is carrier
    assert "mcp.telegram" in carrier.metadata["agent_meta"]["notifications"]["attention"]
    assert agent._disclosed_tools == {"telegram"}
    assert "send" in _visible(agent, "telegram").parameters["properties"]["action"]["enum"]
    assert _is_manual_only_stub(_visible(agent, "feishu"))
    assert _is_manual_only_stub(_visible(agent, "wechat"))


def _stub_kernel_agent(tmp_path: Path, *, state=AgentState.IDLE, inject_ok: bool = True):
    """Minimal BaseAgent double for the IDLE/ASLEEP synthesized-pair sync path.

    ``inject_ok=False`` mirrors ``tests/test_notification_sync.py``'s
    inject-fail fixture: the pair can never be appended, even after heal, so
    the ASLEEP branch falls back to the degraded wake request.
    """
    from lingtai.kernel.base_agent import BaseAgent

    class _Chat:
        def __init__(self):
            self.interface = ChatInterface()

    class _Agent(BaseAgent):
        def __init__(self, workdir):
            self._working_dir = workdir
            self._notification_store = notification_store_for(workdir)
            self._state = state
            self._notification_fp = ()
            self._notification_deferred_log_fp = ()
            self._notification_block_id = None
            self._chat_stub = _Chat()
            self._logs: list = []
            self.agent_name = "stub"
            self.inbox = queue.Queue()
            self._asleep = threading.Event()
            if state == AgentState.ASLEEP:
                self._asleep.set()
            self._cancel_event = threading.Event()
            # ``_token_decomp_dirty`` is a property delegating to the session.
            self._session = SimpleNamespace(token_decomp_dirty=False)
            self._intrinsics = {}
            self._intrinsic_registry = {}
            self._intrinsic_modules = {}
            self._tool_handlers = {}
            self._tool_schemas = []
            for name in CHANNELS:
                tool = _server_tool(name)
                self._tool_schemas.append(
                    FunctionSchema(
                        name=name,
                        description=tool["description"],
                        parameters=tool["schema"],
                        stub=deferred_tool_stub(name, tool["schema"], tool["description"]),
                        disclosure_sources=(notification_channel(name),),
                    )
                )

        @property
        def _chat(self):
            return self._chat_stub

        def _save_chat_history(self, *, ledger_source="main"):
            pass

        def _log(self, evt, **fields):
            self._logs.append((evt, fields))

        def _wake_nap(self, *_a, **_kw):
            pass

        def _set_state(self, new_state, reason=""):
            self._state = new_state

        def _reset_uptime(self):
            pass

    if inject_ok:
        return _Agent(tmp_path)

    class _InjectFailAgent(_Agent):
        def _inject_notification_pair(self, notifications):
            return False

        def _heal_pending_tool_calls(self, *, reason):
            return False

    return _InjectFailAgent(tmp_path)


def _log_index(agent, event: str) -> int:
    return next(i for i, (evt, _) in enumerate(agent._logs) if evt == event)


def _assert_only_feishu_disclosed(agent) -> None:
    assert agent._disclosed_tools == {"feishu"}
    assert "send" in _visible(agent, "feishu").parameters["properties"]["action"]["enum"]
    assert _is_manual_only_stub(_visible(agent, "telegram"))
    assert _is_manual_only_stub(_visible(agent, "wechat"))


@pytest.mark.parametrize("state", [AgentState.IDLE, AgentState.ASLEEP], ids=["idle", "asleep"])
def test_synthesized_pair_discloses_producing_channel_before_wake(tmp_path: Path, state) -> None:
    agent = _stub_kernel_agent(tmp_path, state=state)
    publish_test_payload(tmp_path, "mcp.feishu", {"header": "1 new event", "data": {"count": 1}})
    publish_test_payload(tmp_path, "email", {"count": 1})
    assert all(_is_manual_only_stub(_visible(agent, name)) for name in CHANNELS)

    agent._sync_notifications()

    # The pair is on the wire and the wake turn is queued...
    assert len(agent._chat_stub.interface.entries) == 2
    assert agent.inbox.get_nowait().type == MSG_TC_WAKE
    assert agent._state == AgentState.IDLE
    # ...and the producing channel was disclosed before the pair was recorded,
    # so the wake's send already carries its full tool.
    _assert_only_feishu_disclosed(agent)
    assert _log_index(agent, "tool_schema_disclosed") < _log_index(agent, "notification_pair_injected")


def test_degraded_wake_discloses_producing_channel_before_request(tmp_path: Path) -> None:
    """ASLEEP + injection failing even after heal → the degraded ``MSG_REQUEST``
    still reaches the model with the affected channel's full tool visible."""
    agent = _stub_kernel_agent(tmp_path, state=AgentState.ASLEEP, inject_ok=False)
    publish_test_payload(tmp_path, "mcp.feishu", {"header": "1 new event", "data": {"count": 1}})

    agent._sync_notifications()

    degraded = agent.inbox.get_nowait()
    assert degraded.type == MSG_REQUEST
    assert "mcp.feishu" in degraded.content
    assert agent._state == AgentState.IDLE
    assert len(agent._chat_stub.interface.entries) == 0
    _assert_only_feishu_disclosed(agent)
    assert _log_index(agent, "tool_schema_disclosed") < _log_index(agent, "notification_wake_degraded")
