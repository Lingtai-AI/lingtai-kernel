"""Atomic ownership tests for the process-local stdio MCP overlay."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import lingtai.services.session_mcp as session_mcp
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm import FunctionSchema
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.kernel.session import SessionManager
from lingtai.services.session_mcp import StdioMCPServerConfig
from lingtai.kernel.turn_tool_overlay import (
    bind_turn_tool_overlay, current_turn_tool_overlay, reset_turn_tool_overlay,
)
from lingtai.kernel.base_agent.tools import _build_tool_schemas, _dispatch_tool


class _Agent:
    def __init__(self):
        self._intrinsics = {}
        self._tool_handlers = {"existing": lambda args: args}
        self._tool_schemas = []
        self._mcp_tool_names = set()
        self._mcp_clients_by_tool = {}
        self._mcp_tool_metadata = {}
        self._chat = None
        self._token_decomp_dirty = False

    def _build_tool_schemas(self):
        return self._tool_schemas


class _Client:
    plans = []
    made = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.plan = self.plans.pop(0)
        self.closed = False
        self.made.append(self)

    def start(self):
        if isinstance(self.plan, Exception):
            raise self.plan

    def list_tools(self):
        return self.plan

    def call_tool(self, name, args):
        return {"name": name, "args": args}

    def close(self):
        self.closed = True


class _NoOpToolChat:
    def __init__(self, interface, tools):
        self.interface = interface
        self.tools = list(tools or [])

    def update_tools(self, tools):
        pass


class _SessionService:
    model = "test-model"

    def __init__(self):
        self.sessions = []

    def create_session(self, *, interface=None, tools=None, **kwargs):
        chat = _NoOpToolChat(interface or ChatInterface(), tools)
        self.sessions.append(chat)
        return chat


class _ManagedAgent:
    def __init__(self):
        self._intrinsics = {}
        self._tool_handlers = {"existing": lambda args: args}
        self._tool_schemas = []
        self._mcp_tool_names = set()
        self._mcp_clients_by_tool = {}
        self._mcp_tool_metadata = {}
        self._token_decomp_dirty = False
        self.service = _SessionService()
        self._session = SessionManager(
            llm_service=self.service,
            config=AgentConfig(),
            agent_name="test",
            streaming=False,
            build_system_prompt_fn=lambda: "test prompt",
            build_tool_schemas_fn=self._build_tool_schemas,
            logger_fn=None,
        )

    @property
    def _chat(self):
        return self._session.chat

    def _build_tool_schemas(self):
        return self._tool_schemas


def _config(name="one"):
    return StdioMCPServerConfig(name, "/bin/server", ("--stdio",), (("A", "B"),))


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    _Client.plans = []
    _Client.made = []
    monkeypatch.setattr(session_mcp, "MCPClient", _Client)


def test_session_mcp_publishes_all_tools_and_close_removes_only_owned():
    agent = _Agent()
    _Client.plans = [[{"name": "demo", "schema": {"type": "object"}, "description": "D"}]]
    lease = session_mcp.mount_session_mcp_stdio(agent, (_config(),))
    assert set(agent._tool_handlers) == {"existing", "demo"}
    assert agent._tool_handlers["demo"]({"x": 1})["name"] == "demo"
    assert _Client.made[0].kwargs["env"] == {"A": "B"}
    lease.close()
    lease.close()
    assert set(agent._tool_handlers) == {"existing"}
    assert _Client.made[0].closed


def test_connection_mcp_tool_view_is_turn_local_and_never_published_globally():
    agent = _Agent()
    _Client.plans = [[{
        "name": "puffo_read_inbox", "schema": {"type": "object", "properties": {}},
        "description": "read Puffo inbox",
    }]]
    lease = session_mcp.open_connection_mcp_stdio(agent, (_config("puffo"),))
    assert set(agent._tool_handlers) == {"existing"}
    assert agent._tool_schemas == []
    assert current_turn_tool_overlay(agent) is None
    assert "puffo_read_inbox" not in {s.name for s in _build_tool_schemas(agent)}

    token = bind_turn_tool_overlay(lease)
    try:
        assert current_turn_tool_overlay(agent) is lease
        assert "puffo_read_inbox" in {s.name for s in _build_tool_schemas(agent)}
        result = _dispatch_tool(agent, SimpleNamespace(
            name="puffo_read_inbox", args={"_reasoning": "check"}, id="call-1",
        ))
        assert result == {"name": "puffo_read_inbox", "args": {}}
    finally:
        reset_turn_tool_overlay(token)
    assert "puffo_read_inbox" not in {s.name for s in _build_tool_schemas(agent)}
    lease.close()
    assert _Client.made[0].closed


def test_connection_mcp_rejects_collisions_and_closes_partial_start():
    agent = _Agent()
    _Client.plans = [[{"name": "first", "schema": {}}], [{"name": "existing", "schema": {}}]]
    with pytest.raises(ValueError, match="collision"):
        session_mcp.open_connection_mcp_stdio(agent, (_config("one"), _config("two")))
    assert all(client.closed for client in _Client.made)
    assert set(agent._tool_handlers) == {"existing"}


def test_connection_mcp_closed_or_late_collision_fails_closed():
    agent = _Agent()
    _Client.plans = [[{"name": "puffo_read_inbox", "schema": {}}]]
    lease = session_mcp.open_connection_mcp_stdio(agent, (_config(),))
    token = bind_turn_tool_overlay(lease)
    try:
        agent._tool_handlers["puffo_read_inbox"] = lambda args: args
        with pytest.raises(RuntimeError, match="collides"):
            current_turn_tool_overlay(agent)
        agent._tool_handlers.pop("puffo_read_inbox")
        lease.close()
        with pytest.raises(RuntimeError, match="unavailable"):
            current_turn_tool_overlay(agent)
    finally:
        reset_turn_tool_overlay(token)


def test_session_mcp_rebuilds_managed_noop_chat_on_mount_and_close():
    agent = _ManagedAgent()
    original_chat = agent._session.ensure_session()
    interface = original_chat.interface
    interface.add_user_message("preserve me")
    history = interface.to_dict()
    _Client.plans = [[{"name": "demo", "schema": {"type": "object"}}]]

    lease = session_mcp.mount_session_mcp_stdio(agent, (_config(),))

    mounted_chat = agent._chat
    assert mounted_chat is not original_chat
    assert mounted_chat.interface is interface
    assert [tool.name for tool in mounted_chat.tools] == ["demo"]
    assert interface.to_dict() == history

    lease.close()
    unmounted_chat = agent._chat
    assert unmounted_chat is not mounted_chat
    assert unmounted_chat.interface is interface
    assert unmounted_chat.tools == []
    assert interface.to_dict() == history

    lease.close()
    assert agent._chat is unmounted_chat


@pytest.mark.parametrize("plans", [
    [[{"name": "same", "schema": {}}, {"name": "same", "schema": {}}]],
    [[{"name": "existing", "schema": {}}]],
    [[{"name": "shell", "schema": {}}]],
])
def test_session_mcp_rejects_duplicate_and_existing_tool_collisions(plans):
    agent = _Agent()
    _Client.plans = plans
    with pytest.raises(ValueError):
        session_mcp.mount_session_mcp_stdio(agent, (_config(),))
    assert set(agent._tool_handlers) == {"existing"}
    assert all(client.closed for client in _Client.made)


def test_session_mcp_partial_start_rolls_back_every_client_and_tool():
    agent = _Agent()
    _Client.plans = [[{"name": "first", "schema": {}}], RuntimeError("boom")]
    with pytest.raises(RuntimeError, match="boom"):
        session_mcp.mount_session_mcp_stdio(agent, (_config("one"), _config("two")))
    assert set(agent._tool_handlers) == {"existing"}
    assert len(_Client.made) == 2
    assert all(client.closed for client in _Client.made)


def test_session_mcp_close_still_closes_child_when_live_tool_refresh_fails():
    agent = _Agent()
    _Client.plans = [[{"name": "demo", "schema": {}}]]
    lease = session_mcp.mount_session_mcp_stdio(agent, (_config(),))
    agent._chat = SimpleNamespace(update_tools=lambda tools: (_ for _ in ()).throw(RuntimeError("refresh")))
    lease.close()
    assert _Client.made[0].closed
    assert "demo" not in agent._tool_handlers

def test_session_mcp_mount_refresh_failure_rolls_back_lease_and_children():
    agent = _Agent()
    agent._chat = SimpleNamespace(
        update_tools=lambda tools: (_ for _ in ()).throw(RuntimeError("refresh"))
    )
    _Client.plans = [[{"name": "demo", "schema": {}}]]
    with pytest.raises(RuntimeError, match="refresh"):
        session_mcp.mount_session_mcp_stdio(agent, (_config(),))
    assert "demo" not in agent._tool_handlers
    assert getattr(agent, "_session_mcp_leases", []) == []
    assert _Client.made[0].closed


def test_session_mcp_close_removes_owned_route_but_keeps_later_handler_owner():
    agent = _Agent()
    _Client.plans = [[{"name": "demo", "schema": {}}]]
    lease = session_mcp.mount_session_mcp_stdio(agent, (_config(),))

    replacement_handler = lambda args: {"replacement": args}
    replacement_schema = FunctionSchema(
        name="demo", description="replacement", parameters={"type": "object"}
    )
    agent._tool_handlers["demo"] = replacement_handler
    agent._tool_schemas = [replacement_schema]

    lease.close()
    assert agent._tool_handlers["demo"] is replacement_handler
    assert "demo" not in agent._mcp_clients_by_tool
    assert "demo" not in agent._mcp_tool_metadata
    assert "demo" not in agent._mcp_tool_names
    assert agent._tool_schemas == [replacement_schema]
    assert _Client.made[0].closed
