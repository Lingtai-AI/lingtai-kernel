# tests/test_daemon.py
"""Tests for the daemon (神識) capability — subagent system."""
import json
import os
import queue
import re
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.base import ChatSession, FunctionSchema, LLMResponse, ToolCall, UsageMetadata
from lingtai.kernel.llm.interface import ChatInterface, TextBlock, ToolCallBlock, ToolResultBlock
from lingtai.kernel.tool_call_guard import GuardDecision, ToolCallGuard
from lingtai.tools import daemon as daemon_tool

from tests._daemon_helpers import make_daemon_agent as _make_agent
from tests._daemon_helpers import make_daemon_run_dir as _make_run_dir
from tests._notification_store_helpers import store_agent_for
from lingtai.tools.daemon.process_port import DaemonProcessCommand
from lingtai.tools.daemon.run_dir import DaemonRunDir


def _reuse_parent_service(monkeypatch, agent):
    """Make daemon-scoped ``LLMService(...)`` construction return the parent mock.

    No-preset daemon runs now always build a fresh daemon-scoped service instead
    of reusing ``agent.service`` directly. Tests that exercise the tool loop via a
    mock parent service patch ``LLMService`` so the freshly-built service is the
    same mock, keeping ``agent.service.create_session`` the session under test.
    """
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **kwargs: agent.service)
    return agent.service



def _assert_compact_daemon_id(em_id: str) -> None:
    assert re.fullmatch(r"em-[0-9a-f]{4}(?:-\d+)?", em_id), em_id


def _poll_daemon_terminal(run_dir, expected: str = "done", timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = DaemonRunDir.read_state_from_disk(run_dir.path)
        if state.get("state") == expected:
            return state
        if state.get("state") in {"failed", "cancelled", "timeout"}:
            raise AssertionError(f"daemon reached unexpected state: {state}")
        time.sleep(0.05)
    raise AssertionError(f"daemon did not reach {expected}: {run_dir.path}")


def _enable_detached_fake_llm(agent, monkeypatch, *, scenario: str | None = None) -> None:
    agent.service.provider = "lingtai-supervisor-test-fake"
    agent.service.model = "fake-model"
    agent.service.api_key = "detached-test-key"
    agent.service._base_url = None
    agent.service._provider_defaults = {}
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM", "1")
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH", "1")
    if scenario is not None:
        monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_SCENARIO", scenario)
    tests_dir = str(Path(__file__).parent)
    src_dir = str(Path(__file__).resolve().parents[1] / "src")
    existing = os.environ.get("PYTHONPATH", "")
    parts = [tests_dir, src_dir]
    parts.extend(p for p in existing.split(os.pathsep) if p)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(dict.fromkeys(parts)))


class _CanonicalFakeSession(ChatSession):
    def __init__(self, responses, *, system_prompt: str, interface: ChatInterface | None = None):
        self._interface = interface or ChatInterface()
        if interface is None:
            self._interface.add_system(system_prompt)
        self._responses = list(responses)
        self.sent_messages = []
        self.request_snapshots = []

    @property
    def interface(self) -> ChatInterface:
        return self._interface

    def send(self, message):
        self.sent_messages.append(message)
        if isinstance(message, str):
            self.interface.add_user_message(message)
        elif isinstance(message, list):
            self.interface.add_tool_results(message)
        else:
            raise TypeError(f"unsupported fake message: {type(message)!r}")
        self.request_snapshots.append(self.interface.to_dict())
        if not self._responses:
            raise AssertionError("fake session has no queued response")
        response = self._responses.pop(0)
        blocks = []
        if response.text:
            blocks.append(TextBlock(response.text))
        for tc in response.tool_calls or []:
            blocks.append(ToolCallBlock(id=tc.id or "", name=tc.name, args=dict(tc.args or {})))
        if blocks:
            self.interface.add_assistant_message(blocks)
        return response


class _CanonicalFakeService:
    provider = "mock"
    model = "mock-model"
    api_key = "fake"
    _base_url = None
    _provider_defaults = {}

    def __init__(self, session_responses):
        self._session_responses = [list(responses) for responses in session_responses]
        self.sessions = []
        self.make_tool_result_calls = []

    def create_session(self, *, system_prompt, interface=None, **_kwargs):
        if not self._session_responses:
            raise AssertionError("fake service has no queued session")
        session = _CanonicalFakeSession(
            self._session_responses.pop(0),
            system_prompt=system_prompt,
            interface=interface,
        )
        self.sessions.append(session)
        return session

    def make_tool_result(self, tool_name, result, *, tool_call_id=None, provider=None):
        self.make_tool_result_calls.append((tool_name, result, tool_call_id, provider))
        return ToolResultBlock(id=tool_call_id or "", name=tool_name, content=result)


def _resp(text="", tool_calls=None):
    return LLMResponse(text=text, tool_calls=list(tool_calls or []), usage=None)


def _write_daemon_json(tmp_path, run_id, **overrides):
    daemon_dir = tmp_path / "daemon-agent" / "daemons" / run_id
    daemon_dir.mkdir(parents=True)
    data = {
        "handle": "em-test",
        "run_id": run_id,
        "parent_pid": 12345,
        "state": "running",
        "finished_at": None,
        "current_tool": "read",
        "error": None,
        "unrelated": {"preserved": True},
    }
    data.update(overrides)
    daemon_json = daemon_dir / "daemon.json"
    daemon_json.write_text(json.dumps(data), encoding="utf-8")
    return daemon_json


def test_daemon_registers_tool(tmp_path):
    agent = _make_agent(tmp_path, ["daemon"])
    tool_names = {s.name for s in agent._tool_schemas}
    assert "daemon" in tool_names


def test_daemon_default_max_emanations_is_100(tmp_path):
    """Default concurrency ceiling is 100 when init.json gives no override."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    assert mgr._max_emanations == 100
    assert mgr._handle_list()["max_emanations"] == 100


def test_daemon_max_emanations_override_reaches_manager(tmp_path):
    """A kwargs override on the daemon capability reaches DaemonManager."""
    agent = _make_agent(tmp_path, {"daemon": {"max_emanations": 30}})
    mgr = agent.get_capability("daemon")
    assert mgr._max_emanations == 30
    assert mgr._handle_list()["max_emanations"] == 30


def test_daemon_setup_reaps_dead_parent_running_record(tmp_path, monkeypatch):
    """Startup marks stale running daemon.json records failed."""
    from lingtai.tools import daemon as daemon_mod

    stale_pid = 987654
    daemon_json = _write_daemon_json(
        tmp_path,
        "em-dead-20260101-000000-abcdef",
        parent_pid=stale_pid,
    )

    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if pid == stale_pid:
            raise ProcessLookupError

    monkeypatch.setattr(daemon_mod.os, "kill", fake_kill)

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    data = json.loads(daemon_json.read_text(encoding="utf-8"))
    assert data["state"] == "failed"
    assert data["finished_at"]
    assert data["current_tool"] is None
    assert data["error"] == {
        "type": "DaemonOrphaned",
        "message": (
            "Reaped running daemon record because recorded parent_pid "
            f"{stale_pid} is no longer alive after daemon manager startup."
        ),
    }
    assert data["unrelated"] == {"preserved": True}
    assert (stale_pid, 0) in calls
    assert mgr._emanations == {}


def test_daemon_setup_keeps_current_and_live_parent_records(tmp_path, monkeypatch):
    """Startup skips current PID and records whose parent PID is still alive."""
    from lingtai.tools import daemon as daemon_mod

    current_pid = os.getpid()
    live_pid = current_pid + 100000
    current_json = _write_daemon_json(
        tmp_path,
        "em-current-20260101-000000-abcdef",
        parent_pid=current_pid,
        state="active",
    )
    live_json = _write_daemon_json(
        tmp_path,
        "em-live-20260101-000000-abcdef",
        parent_pid=live_pid,
    )

    calls = []

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        return None

    monkeypatch.setattr(daemon_mod.os, "kill", fake_kill)

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    current_data = json.loads(current_json.read_text(encoding="utf-8"))
    live_data = json.loads(live_json.read_text(encoding="utf-8"))
    assert current_data["state"] == "active"
    assert current_data["current_tool"] == "read"
    assert current_data["error"] is None
    assert live_data["state"] == "running"
    assert live_data["current_tool"] == "read"
    assert live_data["error"] is None
    assert (current_pid, 0) not in calls
    assert (live_pid, 0) in calls
    assert mgr._emanations == {}


def test_build_tool_surface_expands_groups(tmp_path):
    """'file' group expands to read/write/edit/glob/grep."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, dispatch = mgr._build_tool_surface(["file"])
    names = {s.name for s in schemas}
    assert "read" in names
    assert "write" in names
    assert "edit" in names
    assert "glob" in names
    assert "grep" in names


def test_build_tool_surface_blacklist(tmp_path):
    """Blacklisted tools are silently excluded."""
    agent = _make_agent(tmp_path, ["file", "daemon", "avatar"])
    mgr = agent.get_capability("daemon")
    schemas, dispatch = mgr._build_tool_surface([
        "file",
        "avatar",
        "daemon",
    ])
    names = {s.name for s in schemas}
    assert "daemon" not in names
    assert "avatar" not in names
    assert "avatar" not in dispatch
    assert "read" in names


def test_build_tool_surface_unknown_tool(tmp_path):
    """Unknown tool name raises ValueError."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    try:
        mgr._build_tool_surface(["nonexistent"])
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "nonexistent" in str(e)


def test_build_tool_surface_requires_task_mcp_surface(tmp_path):
    """MCP tools are present only when supplied by task-scoped registration."""
    agent = _make_agent(tmp_path, ["daemon"])
    # Simulate a parent MCP tool registered on the parent. It must not leak into
    # daemon surface unless this task's own mcp registration produced it.
    agent._sealed = False
    agent.add_tool("parent_mcp_tool", schema={"type": "object", "properties": {}},
                   handler=lambda args: {"ok": True}, description="Parent MCP tool")
    agent._sealed = True
    mgr = agent.get_capability("daemon")

    agent._mcp_tool_names = {"parent_mcp_tool"}
    schemas, dispatch = mgr._build_tool_surface([])
    names = {s.name for s in schemas}
    assert "parent_mcp_tool" not in names
    assert "parent_mcp_tool" not in dispatch

    try:
        mgr._build_tool_surface(["parent_mcp_tool"])
        assert False, "parent MCP tool name should not be accepted via tools"
    except ValueError as e:
        assert "task mcp registrations" in str(e)

    mcp_schema = FunctionSchema(
        name="task_mcp_tool",
        description="Task MCP tool",
        parameters={"type": "object", "properties": {}},
    )
    schemas, dispatch = mgr._build_tool_surface(
        [],
        mcp_surface=({"task_mcp_tool": mcp_schema}, {"task_mcp_tool": lambda args: {"ok": True}}),
    )
    names = {s.name for s in schemas}
    assert "task_mcp_tool" in names
    assert "task_mcp_tool" in dispatch
    assert "parent_mcp_tool" not in names

    # A task-scoped MCP registration may expose the same tool name as a parent
    # MCP. The task-scoped handler should be used; parent MCP still is not
    # inherited through tools.
    replacement_schema = FunctionSchema(
        name="parent_mcp_tool",
        description="Replacement task MCP tool",
        parameters={"type": "object", "properties": {}},
    )
    schemas, dispatch = mgr._build_tool_surface(
        [],
        mcp_surface=({"parent_mcp_tool": replacement_schema}, {"parent_mcp_tool": lambda args: {"task": True}}),
    )
    names = {s.name for s in schemas}
    assert "parent_mcp_tool" in names
    assert dispatch["parent_mcp_tool"]({}) == {"task": True}


def test_build_tool_surface_rejects_task_mcp_name_collision(tmp_path):
    """Task-scoped MCP tools must not shadow parent or daemon tool names."""
    agent = _make_agent(tmp_path, ["daemon", "file"])
    mgr = agent.get_capability("daemon")
    read_schema = FunctionSchema(
        name="read",
        description="Conflicting MCP read",
        parameters={"type": "object", "properties": {}},
    )

    try:
        mgr._build_tool_surface([], mcp_surface=({"read": read_schema}, {"read": lambda args: {}}))
        assert False, "Should reject MCP tool collision with parent read tool"
    except ValueError as e:
        assert "collide" in str(e)


def test_task_mcp_registrations_accept_full_objects_and_redact_secrets(tmp_path):
    """Task mcp is full registration YAML context, not name-based selection."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    registrations, catalog = mgr._task_mcp_registrations({
        "task": "x",
        "tools": [],
        "mcp": [{
            "name": "demo-mcp",
            "type": "stdio",
            "command": "python",
            "args": ["-m", "demo"],
            "env": {"TOKEN": "secret"},
        }],
    })

    assert registrations == [{
        "name": "demo-mcp",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "demo"],
        "env": {"TOKEN": "secret"},
    }]
    assert catalog is not None
    assert "## Parent-provided MCP registrations" not in catalog
    assert "demo-mcp" in catalog
    assert "command: python" in catalog
    assert "TOKEN: <redacted>" in catalog
    assert "secret" not in catalog


def test_connect_task_mcp_registrations_builds_surface_and_closes(tmp_path, monkeypatch):
    """LingTai backend starts task-scoped MCP clients from full registrations."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    created = []

    class FakeMCPClient:
        def __init__(self, command, args=None, env=None):
            self.command = command
            self.args = args
            self.env = env
            self.closed = False
            created.append(self)
        def start(self):
            pass
        def list_tools(self):
            return [{
                "name": "demo_tool",
                "description": "Demo tool",
                "schema": {"type": "object", "properties": {}, "additionalProperties": False},
            }]
        def call_tool(self, name, args):
            return {"called": name, "args": args}
        def close(self):
            self.closed = True

    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", FakeMCPClient)

    schemas, handlers, clients = mgr._connect_task_mcp_registrations([{
        "name": "demo-mcp",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "demo"],
        "env": {"TOKEN": "secret"},
    }])

    assert set(schemas) == {"demo_tool"}
    assert handlers["demo_tool"]({"q": "x"}) == {"called": "demo_tool", "args": {"q": "x"}}
    assert created[0].env["LINGTAI_AGENT_DIR"] == str(agent._working_dir)
    assert created[0].env["LINGTAI_MCP_NAME"] == "demo-mcp"
    assert created[0].env["TOKEN"] == "secret"
    mgr._close_task_mcp_clients(clients)
    assert created[0].closed


def test_daemon_schema_exposes_prompt_and_removes_system_prompt():
    """Task items expose LingTai first-user prompt and reject the old field."""
    from lingtai.tools.daemon import get_schema

    task_props = get_schema("en")["properties"]["tasks"]["items"]["properties"]
    assert "prompt" in task_props
    assert task_props["prompt"]["type"] == "string"
    assert "system_prompt" not in task_props
    assert "skills" in task_props
    assert task_props["skills"]["type"] == "array"
    assert task_props["skills"]["items"]["type"] == "string"
    assert "mcp" in task_props
    assert task_props["mcp"]["type"] == "array"
    assert task_props["mcp"]["items"]["type"] == "object"
    assert "custom_system_prompt" not in task_props


def test_cli_backend_serializes_task_mcp_context(tmp_path, monkeypatch):
    """CLI backends receive serialized MCP registrations instead of rejecting mcp."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    captured = {}

    def fake_run(em_id, run_dir, task, cancel_event, timeout_event=None, backend_argv=None):
        captured["task"] = task
        captured["prompt"] = run_dir.prompt_path.read_text(encoding="utf-8")
        run_dir.mark_done("ok")
        return "ok"

    monkeypatch.setattr(mgr, "_run_codex_emanation", fake_run)
    result = mgr.handle({
        "action": "emanate",
        "backend": "codex",
        "tasks": [{
            "task": "x",
            "tools": [],
            "mcp": [{
                "name": "demo-mcp",
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "demo"],
                "env": {"TOKEN": "secret"},
            }],
        }],
    })

    assert result["status"] == "dispatched"
    # Detached CLI ownership has no parent future.  The durable prompt is the
    # product-visible replacement for the old monkeypatched runner capture.
    run_dir = mgr._emanations[result["ids"][0]]["run_dir"]
    captured["prompt"] = run_dir.prompt_path.read_text(encoding="utf-8")
    assert "## Parent-provided MCP registrations" in captured["prompt"]
    assert "demo-mcp" in captured["prompt"]
    assert "TOKEN: <redacted>" in captured["prompt"]
    assert "secret" not in captured["prompt"]


def test_build_tool_surface_requires_explicit_email_tool(tmp_path):
    """Result-only tools=[] daemons must not receive communication tools."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    schemas, dispatch = mgr._build_tool_surface([])

    names = {s.name for s in schemas}
    assert "compact" in names
    assert "email" not in names
    assert "email" not in dispatch

    schemas, dispatch = mgr._build_tool_surface(["email"])
    names = {s.name for s in schemas}
    assert "compact" in names
    assert "email" in names
    assert "email" in dispatch


def test_compact_is_auto_present_on_lingtai_surfaces(tmp_path):
    """LingTai daemon self-compact is automatic and idempotent."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    preset_schema = FunctionSchema(
        name="bash",
        description="Preset-provided bash",
        parameters={"type": "object", "properties": {}},
    )

    default_schemas, _ = mgr._build_tool_surface([])
    explicit_schemas, _ = mgr._build_tool_surface(["compact"])
    preset_schemas, _ = mgr._build_tool_surface(
        [], preset_surface=({"bash": preset_schema}, {"bash": lambda args: {}})
    )

    assert [s.name for s in default_schemas].count("compact") == 1
    assert [s.name for s in explicit_schemas].count("compact") == 1
    assert [s.name for s in preset_schemas].count("compact") == 1


def test_compact_schema_requires_explicit_run_or_manual_action(tmp_path):
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    schema = next(s for s in mgr._build_tool_surface([])[0] if s.name == "compact")

    assert schema.parameters["properties"]["action"]["enum"] == ["run", "manual"]
    assert schema.parameters["required"] == ["action"]
    run_branch, manual_branch = schema.parameters["anyOf"]
    assert run_branch["properties"]["action"]["const"] == "run"
    assert run_branch["required"] == ["_reason"]
    assert manual_branch["properties"]["action"]["const"] == "manual"
    assert "read-only" in schema.description
    assert "Action is required" in schema.description
    assert "default" not in schema.parameters["properties"]["action"]["description"]


def test_daemon_context_countdown_enters_ticks_and_resets():
    state = daemon_tool._DaemonMetaState("em-countdown", "run-countdown", max_turns=10, context_window=100)

    class Session:
        def context_window(self):
            return 100

    session = Session()
    for round_id, input_tokens in enumerate((89, 90, 90, 89, 90), start=1):
        response = LLMResponse(usage=UsageMetadata(input_tokens=input_tokens, output_tokens=1))
        state.note_response(response, session)
        snapshot = state.snapshot(session)
        context = snapshot["context"]
        if input_tokens < 90:
            assert "compact_countdown" not in context
            assert not state.compact_due
        elif round_id in (2, 5):
            assert context["compact_countdown"] == 9
            countdown_warning = context["compact_countdown_warning"].lower()
            assert "daemon context is at or above 90%" in countdown_warning
            assert "9 proactive round(s) remain" in countdown_warning
            assert 'compact(action="run", _reason="...")' in countdown_warning
        else:
            assert context["compact_countdown"] == 8

    assert state.snapshot(session)["context"]["compact_countdown"] == 9
    assert not state.compact_due
    state.note_compact_reset(session)
    assert "compact_countdown" not in state.snapshot(session)["context"]
    assert not state.compact_due

    expiry_state = daemon_tool._DaemonMetaState(
        "em-expiry", "run-expiry", max_turns=10, context_window=100
    )
    high_response = LLMResponse(
        usage=UsageMetadata(input_tokens=90, output_tokens=1)
    )
    for _ in range(daemon_tool.DAEMON_CONTEXT_COUNTDOWN_ROUNDS):
        expiry_state.note_response(high_response, session)
    assert expiry_state.snapshot(session)["context"]["compact_countdown"] == 1
    assert not expiry_state.compact_due
    expiry_state.note_response(high_response, session)
    assert expiry_state.snapshot(session)["context"]["compact_countdown"] == 1
    assert expiry_state.compact_due
    expiry_state.note_response(
        LLMResponse(usage=UsageMetadata(input_tokens=89, output_tokens=1)), session
    )
    assert "compact_countdown" not in expiry_state.snapshot(session)["context"]
    assert not expiry_state.compact_due


def test_daemon_mechanical_compact_counts_down_and_requires_recovery(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    responses = [
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id=f"manual-{i}")],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )
        for i in range(1, 11)
    ]
    responses.append(LLMResponse(text="recovered", usage=UsageMetadata(input_tokens=20, output_tokens=1)))
    recovery_tool = ToolCall(name="compact", args={"action": "manual"}, id="recovery-tool")
    service = _CanonicalFakeService([
        responses[:-1],
        [
            LLMResponse(tool_calls=[recovery_tool], usage=UsageMetadata(input_tokens=20, output_tokens=1)),
            responses[-1],
        ],
    ])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, em_id="em-mechanical", task="task")
    mgr._emanations["em-mechanical"] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    assert mgr._run_emanation(
        "em-mechanical", run_dir, *mgr._build_tool_surface([]), "task", threading.Event(),
        max_turns=10,
    ) == "recovered"
    assert len(service.sessions) == 2

    first_session = service.sessions[0]
    # The final ordinary continuation request carries countdown=1.  The
    # response from that request is the one whose non-compact tool turn is
    # mechanically compacted; it must not be skipped by the force boundary.
    assert len(first_session.request_snapshots) == 10  # kickoff + nine ordinary continuations
    value_one_request = first_session.request_snapshots[-1]
    value_one_result = value_one_request[-1]["content"][0]
    assert value_one_request[-1]["role"] == "user"
    assert value_one_result["type"] == "tool_result"
    assert value_one_result["metadata"]["agent_meta"]["agent_state"]["context"]["compact_countdown"] == 1

    ordinary_countdowns = [
        request[-1]["content"][0]["metadata"]["agent_meta"]["agent_state"]["context"]["compact_countdown"]
        for request in first_session.request_snapshots[1:]
    ]
    assert ordinary_countdowns == [*range(9, 0, -1)]

    # The mechanically compacted result is attached after the final ordinary
    # request; its duplicate value=1 is retained for explicit recovery but was
    # not another provider continuation opportunity.
    countdowns = []
    first_countdown_result = None
    for entry in first_session.interface.entries:
        for block in entry.content:
            if isinstance(block, ToolResultBlock):
                agent_meta = block.metadata["agent_meta"]["agent_state"]
                context = agent_meta["context"]
                if "compact_countdown" in context:
                    countdowns.append(context["compact_countdown"])
                    first_countdown_result = first_countdown_result or block
    assert countdowns == [*range(9, 0, -1), 1]
    from lingtai.llm.interface_converters import _project_tool_result
    projected = _project_tool_result(first_countdown_result)
    assert projected["_meta"]["agent_meta"]["agent_state"]["context"]["compact_countdown"] == 9

    recovery_snapshot = service.sessions[1].request_snapshots[0]
    recovery_user = recovery_snapshot[-1]["content"][0]["text"]
    assert "mechanically compacted" in recovery_user
    assert "recovery" in recovery_user.lower()
    assert "Before continuing" in recovery_user
    assert len(recovery_snapshot) == 4  # system + preserved assistant/result pair + recovery user


def test_daemon_value_one_response_can_proactively_compact(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    responses = [
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id=f"manual-{i}")],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )
        for i in range(1, 10)
    ]
    responses.append(
        LLMResponse(
            tool_calls=[
                ToolCall(
                    name="compact",
                    args={"action": "run", "_reason": "proactive value-one handoff"},
                    id="compact-proactive",
                )
            ],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )
    )
    service = _CanonicalFakeService([responses, [_resp("done after proactive compact")]])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-proactive-value-one"
    run_dir = _make_run_dir(agent, em_id=em_id, task="task")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    assert mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]), "task", threading.Event()
    ) == "done after proactive compact"
    assert len(service.sessions) == 2

    # The proactive response was generated only after this ordinary request
    # carried the final visible warning; it therefore prevents mechanics.
    value_one_request = service.sessions[0].request_snapshots[-1]
    value_one_result = value_one_request[-1]["content"][0]
    assert value_one_result["metadata"]["agent_meta"]["agent_state"]["context"]["compact_countdown"] == 1
    retained_request = service.sessions[1].request_snapshots[0]
    assert [entry["role"] for entry in retained_request] == ["system", "assistant", "user"]
    assert retained_request[1]["content"][0]["id"] == "compact-proactive"
    assert retained_request[2]["content"][0]["content"]["status"] == "success"
    proactive_context = retained_request[2]["content"][0]["metadata"]["agent_meta"]["agent_state"]["context"]
    assert "compact_countdown" not in proactive_context
    assert "warning" not in proactive_context
    assert not any(
        entry["role"] == "user"
        and entry["content"][0].get("type") == "text"
        and "mechanically compacted" in entry["content"][0].get("text", "")
        for entry in retained_request
    )


def test_daemon_expired_text_response_recovers_before_queued_followup(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    first_responses = [
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id=f"manual-{i}")],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )
        for i in range(1, 10)
    ]
    first_responses.append(
        LLMResponse(text="expired text", usage=UsageMetadata(input_tokens=90, output_tokens=1))
    )
    recovery_tool = ToolCall(name="compact", args={"action": "manual"}, id="recovery-tool")
    service = _CanonicalFakeService([
        first_responses,
        [
            LLMResponse(tool_calls=[recovery_tool], usage=UsageMetadata(input_tokens=20, output_tokens=1)),
            LLMResponse(text="recovery complete", usage=UsageMetadata(input_tokens=20, output_tokens=1)),
            LLMResponse(text="followup complete", usage=UsageMetadata(input_tokens=20, output_tokens=1)),
        ],
    ])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        if len(service.sessions) == 1:
            original_send = session.send

            def send_and_queue(message):
                response = original_send(message)
                if response.text == "expired text":
                    mgr._emanations[em_id]["followup_buffer"] = "queued followup"
                return response

            session.send = send_and_queue
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-expired-text-followup"
    run_dir = _make_run_dir(agent, em_id=em_id, task="task")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    assert mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]), "task", threading.Event()
    ) == "followup complete"

    assert len(service.sessions) == 2
    first_session, fresh_session = service.sessions
    assert all(message != "queued followup" for message in first_session.sent_messages)
    assert fresh_session.sent_messages[0].startswith("The runtime mechanically compacted")
    assert isinstance(fresh_session.sent_messages[1], list)
    assert fresh_session.sent_messages[2] == "queued followup"
    assert fresh_session.sent_messages.index("queued followup") > 1
    assert fresh_session.request_snapshots[0][-2]["role"] == "user"
    assert fresh_session.request_snapshots[0][-2]["content"][0]["type"] == "tool_result"
    assert fresh_session.request_snapshots[0][-1]["content"][0]["type"] == "text"

    durable_entries = [
        json.loads(line)
        for line in run_dir.chat_path.read_text(encoding="utf-8").splitlines()
    ]
    recovery_user_index = next(
        index
        for index, entry in enumerate(durable_entries)
        if entry.get("kind") == "mechanical_compact_recovery"
    )
    recovery_index = recovery_user_index + 1
    assert durable_entries[recovery_index]["role"] == "assistant"
    assert durable_entries[recovery_index]["text"] == ""
    tool_result_index = next(
        index
        for index, entry in enumerate(durable_entries[recovery_index + 1 :], recovery_index + 1)
        if entry.get("kind") == "tool_results"
    )
    followup_index = next(
        index
        for index, entry in enumerate(durable_entries[tool_result_index + 1 :], tool_result_index + 1)
        if entry.get("kind") == "followup"
    )
    assert recovery_index < tool_result_index < followup_index
    assert json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))["turn"] == durable_entries[-1]["turn"]


def test_daemon_mechanical_compact_failure_is_terminal(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    responses = [
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id=f"manual-{i}")],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )
        for i in range(1, 11)
    ]

    class FailingService(_CanonicalFakeService):
        def create_session(self, **kwargs):
            if self.sessions:
                raise RuntimeError("mechanical compact failure")
            return super().create_session(**kwargs)

    service = FailingService([responses])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, em_id="em-mechanical-failure", task="task")
    mgr._emanations["em-mechanical-failure"] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    try:
        mgr._run_emanation(
            "em-mechanical-failure",
            run_dir,
            *mgr._build_tool_surface([]),
            "task",
            threading.Event(),
        )
    except RuntimeError as exc:
        assert str(exc) == "mechanical compact failure"
    else:
        raise AssertionError("mechanical compact failure was swallowed")
    assert run_dir.state_snapshot()["state"] == "failed"


def test_daemon_agent_meta_is_local_and_warning_tracks_current_usage(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    responses = [
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id="manual-1")],
            usage=UsageMetadata(input_tokens=89, output_tokens=1),
        ),
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id="manual-2")],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        ),
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id="manual-3")],
            usage=UsageMetadata(input_tokens=95, output_tokens=1),
        ),
        LLMResponse(
            tool_calls=[ToolCall(name="compact", args={"action": "manual"}, id="manual-4")],
        ),
        LLMResponse(text="done", usage=UsageMetadata(input_tokens=21, output_tokens=1)),
    ]
    service = _CanonicalFakeService([responses])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-meta"
    run_dir = _make_run_dir(agent, em_id=em_id, task="task")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    assert mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]), "task", threading.Event()
    ) == "done"
    assert len(service.sessions) == 1, "manual action must not compact/reset the session"

    snapshots = service.sessions[0].request_snapshots
    first_meta = snapshots[1][-1]["content"][0]["metadata"]["agent_meta"]
    second_result = snapshots[2][-1]["content"][0]
    second_meta = second_result["metadata"]["agent_meta"]
    third_meta = snapshots[3][-1]["content"][0]["metadata"]["agent_meta"]
    fourth_meta = snapshots[4][-1]["content"][0]["metadata"]["agent_meta"]
    assert "notifications" not in first_meta
    assert "guidance" not in first_meta
    assert "warning" not in first_meta["agent_state"]["context"]
    assert second_meta["agent_state"]["context"]["context_usage"] == 0.9
    assert second_meta["agent_state"]["context"]["warning"] == (
        "Daemon context is at or above 90%. 9 proactive round(s) remain "
        "before runtime mechanical compact; call compact(action=\"run\", "
        "_reason=\"...\") now to compact with your own handoff."
    )
    assert second_meta["agent_state"]["context"]["compact_countdown_warning"] == (
        second_meta["agent_state"]["context"]["warning"]
    )
    assert third_meta["agent_state"]["context"]["warning"] == (
        "Daemon context is at or above 90%. 8 proactive round(s) remain "
        "before runtime mechanical compact; call compact(action=\"run\", "
        "_reason=\"...\") now to compact with your own handoff."
    )
    assert third_meta["agent_state"]["context"]["compact_countdown_warning"] == (
        third_meta["agent_state"]["context"]["warning"]
    )
    assert fourth_meta["agent_state"]["context"]["context_usage"] == 0.2
    assert fourth_meta["agent_state"]["token_usage"]["current_call"]["input"] == 0
    assert "warning" not in fourth_meta["agent_state"]["context"]
    assert second_meta["agent_state"]["token_usage"]["current_call"]["input"] == 90
    assert second_result["content"]["action"] == "manual"
    assert second_result["content"]["read_only"] is True

    # Exercise canonical finalization and provider projection against real
    # ToolResultBlocks, not only the ChatInterface snapshots.
    from lingtai.kernel.meta_block import finalize_two_axis_sidecars
    from lingtai.llm.interface_converters import _project_tool_result
    blocks = [
        block
        for entry in service.sessions[0].interface.entries
        for block in entry.content
        if isinstance(block, ToolResultBlock)
    ]
    finalize_two_axis_sidecars([blocks[-1]])
    projected_high = _project_tool_result(blocks[-2])
    projected_low = _project_tool_result(blocks[-1])
    assert projected_high["_meta"]["agent_meta"]["agent_state"]["context"]["warning"] == (
        "Daemon context is at or above 90%. 8 proactive round(s) remain "
        "before runtime mechanical compact; call compact(action=\"run\", "
        "_reason=\"...\") now to compact with your own handoff."
    )
    assert "warning" not in projected_low["_meta"]["agent_meta"]["agent_state"]["context"]
    assert "notifications" not in projected_low["_meta"]["agent_meta"]
    assert "guidance" not in projected_low["_meta"]["agent_meta"]


def test_build_tool_surface_preset_requires_explicit_email_tool(tmp_path):
    """Preset-path daemon must still gate email behind explicit ``tools`` (#584).

    Mirrors the default-path contract test above but exercises the
    ``preset_surface`` branch of ``_build_tool_surface``: a result-only
    ``tools=[]`` daemon running under a preset must not receive the internal
    ``email`` tool, while ``tools=["email"]`` explicitly opts in.
    """
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    # Minimal preset surface: a single tool the preset's child LLM owns. The
    # mere presence of ``preset_surface`` selects the preset-driven branch of
    # ``_build_tool_surface`` rather than the default parent-surface path.
    preset_schema = FunctionSchema(
        name="bash",
        description="Preset-provided bash",
        parameters={"type": "object", "properties": {}},
    )
    preset_surface = ({"bash": preset_schema}, {"bash": lambda args: {}})

    # A preset-driven daemon that does not request email (result-only) must not
    # receive communication tools.
    schemas, dispatch = mgr._build_tool_surface([], preset_surface=preset_surface)
    names = {s.name for s in schemas}
    assert "compact" in names
    assert "email" not in names
    assert "email" not in dispatch

    # Explicitly requesting email on the preset path opts in.
    schemas, dispatch = mgr._build_tool_surface(
        ["email"], preset_surface=preset_surface
    )
    names = {s.name for s in schemas}
    assert "compact" in names
    assert "email" in names
    assert "email" in dispatch


def test_build_emanation_prompt_includes_oneshot_system_prompt(tmp_path):
    """Parent-provided daemon prompt is appended before the task."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, _ = mgr._build_tool_surface(["file"])

    prompt = mgr._build_emanation_prompt(
        "Find all TODOs",
        schemas,
        system_prompt="Only inspect Python files and write no files.",
    )

    assert "Parent-provided daemon context" in prompt
    assert "Only inspect Python files" in prompt
    assert prompt.index("Only inspect Python files") < prompt.index("Your task:")




def test_task_prompt_defaults_and_preserves_nonblank_whitespace(tmp_path):
    """Blank prompt defaults; nonblank prompt is preserved byte-for-byte."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    assert mgr._task_first_prompt({"task": "x", "tools": []}) == "Begin the assigned daemon task."
    assert mgr._task_first_prompt({"task": "x", "tools": [], "prompt": ""}) == "Begin the assigned daemon task."
    assert mgr._task_first_prompt({"task": "x", "tools": [], "prompt": "   "}) == "Begin the assigned daemon task."
    assert mgr._task_first_prompt({"task": "x", "tools": [], "prompt": "  start here  "}) == "  start here  "


def test_obsolete_system_prompt_fails_before_run_dir(tmp_path):
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "tasks": [{"task": "x", "tools": [], "system_prompt": "old"}],
    })

    assert result["status"] == "error"
    assert "system_prompt is obsolete" in result["message"]
    assert "complete daemon system instruction" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_external_cli_prompt_fails_before_run_dir(tmp_path):
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    result = mgr.handle({
        "action": "emanate",
        "backend": "codex",
        "tasks": [{"task": "x", "tools": [], "prompt": "first user"}],
    })

    assert result["status"] == "error"
    assert "prompt is supported only for backend='lingtai'" in result["message"]
    assert not (agent._working_dir / "daemons").exists()


def test_handle_emanate_maps_prompt_default_and_preserves_whitespace_before_detach(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    captured = []

    def fake_spawn(run_dir, **kwargs):
        captured.append((run_dir, kwargs))
        run_dir.mark_done("ok")

    monkeypatch.setattr(mgr, "_spawn_detached_lingtai_run", fake_spawn)
    result = mgr.handle({
        "action": "emanate",
        "tasks": [
            {"task": "system task one", "tools": [], "prompt": "  first user  "},
            {"task": "system task two", "tools": [], "prompt": "   "},
        ],
    })

    assert result["status"] == "dispatched"
    assert [row[1]["prompt"] for row in captured] == [
        "  first user  ",
        "Begin the assigned daemon task.",
    ]
    prompts = [row[0].prompt_path.read_text(encoding="utf-8") for row in captured]
    assert "system task one" in prompts[0]
    assert "  first user  " not in prompts[0]
    assert "system task two" in prompts[1]


def test_task_skills_render_compact_catalog_from_dir_and_file(tmp_path):
    """Task skills accept either a skill directory or a direct SKILL.md path."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    skill_dir = agent._working_dir / "local-skills" / "demo"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: demo-skill\n"
        "description: >\n"
        "  Demo workflow for daemon skill injection.\n"
        "version: 1.0.0\n"
        "---\n"
        "# Demo\n",
        encoding="utf-8",
    )

    from_dir = mgr._task_skill_catalog({"task": "x", "tools": [], "skills": ["local-skills/demo"]})
    from_file = mgr._task_skill_catalog({"task": "x", "tools": [], "skills": [str(skill_file)]})

    for rendered in (from_dir, from_file):
        assert rendered is not None
        assert "skills:" in rendered
        assert "- name: demo-skill" in rendered
        assert f"location: {skill_file}" in rendered
        assert "Demo workflow for daemon skill injection." in rendered


def test_task_skills_reject_invalid_path(tmp_path):
    """Task skills fail before scheduling when a path is not a skill."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    try:
        mgr._task_skill_catalog({"task": "x", "tools": [], "skills": ["missing-skill"]})
    except ValueError as e:
        assert "skill path does not resolve to a file" in str(e)
    else:  # pragma: no cover - defensive
        raise AssertionError("missing skill path should fail")


def test_task_skills_reject_null_frontmatter_fields(tmp_path):
    """Null YAML name/description values are treated as missing, not stringified."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    skill_dir = agent._working_dir / "local-skills" / "null-field"
    skill_dir.mkdir(parents=True)

    cases = [
        ("name:\ndescription: Has description.\n", "name"),
        ("name: null-field\ndescription:\n", "description"),
    ]
    for frontmatter, missing_field in cases:
        (skill_dir / "SKILL.md").write_text(
            "---\n" + frontmatter + "---\n# Null field\n",
            encoding="utf-8",
        )
        try:
            mgr._task_skill_catalog({"task": "x", "tools": [], "skills": ["local-skills/null-field"]})
        except ValueError as e:
            assert f"missing required frontmatter field: {missing_field}" in str(e)
        else:  # pragma: no cover - defensive
            raise AssertionError(f"null {missing_field} should fail")


def test_task_skills_deduplicates_canonical_paths(tmp_path):
    """Different spellings of the same selected skill render only one catalog row."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    skill_dir = agent._working_dir / "local-skills" / "dedup"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: dedup-skill\n"
        "description: Dedup selected skill paths.\n"
        "---\n",
        encoding="utf-8",
    )

    rendered = mgr._task_skill_catalog(
        {"task": "x", "tools": [], "skills": ["local-skills/dedup", str(skill_file)]}
    )

    assert rendered is not None
    assert rendered.count("- name: dedup-skill") == 1


def test_build_emanation_prompt_includes_selected_skills(tmp_path):
    """Selected skills are rendered into the daemon prompt before the task."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    skill_dir = agent._working_dir / "local-skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: review-skill\n"
        "description: Review daemon outputs carefully.\n"
        "---\n",
        encoding="utf-8",
    )
    schemas, _ = mgr._build_tool_surface(["file"])
    context = mgr._combine_oneshot_context(
        "Stay read-only.",
        mgr._task_skill_catalog({"task": "x", "tools": [], "skills": ["local-skills/review"]}),
    )

    prompt = mgr._build_emanation_prompt("Review the report", schemas, system_prompt=context)

    assert "Stay read-only." in prompt
    assert "## Parent-selected skills" in prompt
    assert "- name: review-skill" in prompt
    assert prompt.index("- name: review-skill") < prompt.index("Your task:")


def test_build_emanation_prompt_includes_selected_mcp_context(tmp_path):
    """Selected MCP registrations are rendered into the daemon prompt before the task."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, _ = mgr._build_tool_surface(["file"])
    context = mgr._combine_oneshot_context(
        "Use the selected external tools only if needed.",
        None,
        "The parent provided these MCP registrations for this daemon run.\nmcp:\n  - name: demo-mcp\n    transport: stdio\n    command: python",
    )

    prompt = mgr._build_emanation_prompt("Review the report", schemas, system_prompt=context)

    assert "Use the selected external tools only if needed." in prompt
    assert "## Parent-provided MCP registrations" in prompt
    assert "- name: demo-mcp" in prompt
    assert "transport: stdio" in prompt
    assert prompt.index("## Parent-provided MCP registrations") < prompt.index("Your task:")


def test_build_emanation_prompt_includes_task(tmp_path):
    """System prompt includes the task description."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, _ = mgr._build_tool_surface(["file"])
    prompt = mgr._build_emanation_prompt("Find all TODOs", schemas)
    assert "Find all TODOs" in prompt
    assert "daemon emanation" in prompt.lower() or "分神" in prompt


def test_build_emanation_prompt_teaches_bounded_tool_use(tmp_path):
    """Daemon prompt teaches manuals, summarized results, compact, and finish."""
    from lingtai.tools.daemon.system_prompt import DAEMON_SYSTEM_PROMPT_BUDGET_CHARS

    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, _ = mgr._build_tool_surface(["file"])
    schemas[0].description = "FULL TOOL DESCRIPTION MUST NOT BE DUPLICATED"

    prompt = mgr._build_emanation_prompt("Inspect one file", schemas)

    assert len(prompt) <= DAEMON_SYSTEM_PROMPT_BUDGET_CHARS == 20_000
    assert "Before first using a tool or workflow that has a manual" in prompt
    assert "summary=true" in prompt
    assert "You do not have the parent agent's `system.summarize`" in prompt
    assert 'compact(action="run", _reason="...")' in prompt
    assert "call `finish` exactly once" in prompt
    assert "FULL TOOL DESCRIPTION MUST NOT BE DUPLICATED" not in prompt
    assert f"`{schemas[0].name}`" in prompt


def test_build_emanation_prompt_rejects_over_budget_without_truncation(tmp_path):
    """Oversized task/context fails loud instead of losing parent constraints."""
    import pytest

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    schemas, _ = mgr._build_tool_surface([])
    task = "x" * 20_000

    with pytest.raises(ValueError, match="20,000-character budget"):
        mgr._build_emanation_prompt(task, schemas)


def test_run_emanation_returns_text(tmp_path, monkeypatch):
    """Emanation runs a tool loop and returns final text."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _reuse_parent_service(monkeypatch, agent)
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Task done. Found 3 files."
    mock_response.tool_calls = []
    mock_response.usage = MagicMock(input_tokens=0, output_tokens=0,
                                    thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_response)
    agent.service.create_session = MagicMock(return_value=mock_session)

    cancel = threading.Event()
    em_id = "em-test"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch,
                                "find stuff", cancel)
    assert "Found 3 files" in result



def test_run_emanation_codex_parent_gets_daemon_cache_anchor(tmp_path, monkeypatch):
    """Builtin Codex daemon runs get a per-run cache anchor, not parent service."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "codex"
    agent.service.model = "gpt-5.5"
    agent.service._base_url = "https://chatgpt.com/backend-api/codex"
    agent.service._context_window = 123456
    agent.service._key_resolver = lambda provider: "token"
    agent.service._api_key = "token"
    agent.service.api_key = "token"
    agent.service._provider_defaults = {
        "codex": {
            "max_rpm": 7,
            "codex_session_anchor": str((agent._working_dir / "init.json").resolve()),
        }
    }

    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]

        def create_session(self, **kwargs):
            captured["session"] = kwargs
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "daemon done"
            mock_response.tool_calls = []
            mock_response.usage = MagicMock(
                input_tokens=0,
                output_tokens=0,
                thinking_tokens=0,
                cached_tokens=0,
            )
            mock_session.send = MagicMock(return_value=mock_response)
            return mock_session

    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", FakeService)

    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    em_id = "em-codex"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch, "x", cancel)

    assert result == "daemon done"
    agent.service.create_session.assert_not_called()
    assert captured["init"]["provider"] == "codex"
    assert captured["init"]["model"] == "gpt-5.5"
    assert captured["init"]["base_url"] == "https://chatgpt.com/backend-api/codex"
    assert captured["init"]["context_window"] == 123456
    defaults = captured["init"]["provider_defaults"]
    assert defaults["codex"]["max_rpm"] == 7
    assert defaults["codex"]["codex_session_anchor"] == str((run_dir.path / "daemon.json").resolve())


def test_run_emanation_non_codex_parent_builds_fresh_daemon_service(tmp_path, monkeypatch):
    """Builtin non-Codex daemon runs build a fresh service, not the parent one.

    The daemon-scoped service must mirror the parent (provider/model/base_url/
    key_resolver/context_window) and preserve the parent's provider defaults,
    without any Codex-only cache anchor.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "anthropic"
    agent.service.model = "claude-opus-4-8"
    agent.service._base_url = "https://api.anthropic.com"
    agent.service._context_window = 200000
    sentinel_resolver = lambda provider: "token"
    agent.service._key_resolver = sentinel_resolver
    # A real LLMService built with no direct api_key records the resolver result
    # as its effective ``api_key``; the mock models that so the daemon inherits
    # the same credential a real parent would expose.
    agent.service._api_key = "token"
    agent.service.api_key = "token"
    agent.service._provider_defaults = {
        "anthropic": {"max_rpm": 5, "default_headers": {"x-test": "1"}}
    }

    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]

        def create_session(self, **kwargs):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "daemon done"
            mock_response.tool_calls = []
            mock_response.usage = MagicMock(
                input_tokens=0,
                output_tokens=0,
                thinking_tokens=0,
                cached_tokens=0,
            )
            mock_session.send = MagicMock(return_value=mock_response)
            return mock_session

    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", FakeService)

    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    em_id = "em-anthropic"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch, "x", cancel)

    assert result == "daemon done"
    # A fresh daemon-scoped service is constructed; the parent service is not reused.
    agent.service.create_session.assert_not_called()
    assert captured["init"]["provider"] == "anthropic"
    assert captured["init"]["model"] == "claude-opus-4-8"
    assert captured["init"]["base_url"] == "https://api.anthropic.com"
    assert captured["init"]["context_window"] == 200000
    assert captured["init"]["api_key"] == "token"
    assert captured["init"]["key_resolver"] is sentinel_resolver
    defaults = captured["init"]["provider_defaults"]
    # Parent provider defaults are preserved verbatim; no Codex cache anchor.
    assert defaults == {"anthropic": {"max_rpm": 5, "default_headers": {"x-test": "1"}}}
    assert "codex_session_anchor" not in defaults["anthropic"]


def test_run_emanation_inherits_parent_noncanonical_api_key(tmp_path, monkeypatch):
    """No-preset daemon inherits the parent's resolved key, not the canonical slot.

    Regression (Lingtai-AI/lingtai): a parent on provider=custom resolves its
    api_key from a noncanonical env slot (api_key_env=LLM_API_KEY) and holds it
    as its effective key. The parent's default key_resolver only ever reads the
    canonical CUSTOM_API_KEY, which is absent here. The fresh daemon-scoped
    service must still be constructed with a *present* api_key (the inherited
    one), not None.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "custom"
    agent.service.model = "glm-5.1"
    agent.service._base_url = "https://proxy.example/v1"
    agent.service._context_window = 200000
    # Canonical-only resolver: CUSTOM_API_KEY is absent, so it returns None.
    canonical_only_resolver = lambda provider: os.environ.get(f"{provider.upper()}_API_KEY")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
    agent.service._key_resolver = canonical_only_resolver
    # The parent remembers the key it was actually constructed with (resolved
    # from the noncanonical LLM_API_KEY at boot). On a real LLMService this is
    # the ``api_key`` property backed by ``_api_key``; the mock models both.
    agent.service._api_key = "sk-from-LLM_API_KEY"
    agent.service.api_key = "sk-from-LLM_API_KEY"
    agent.service._provider_defaults = {"custom": {"api_compat": "openai"}}

    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]

        def create_session(self, **kwargs):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "daemon done"
            mock_response.tool_calls = []
            mock_response.usage = MagicMock(
                input_tokens=0, output_tokens=0,
                thinking_tokens=0, cached_tokens=0,
            )
            mock_session.send = MagicMock(return_value=mock_response)
            return mock_session

    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", FakeService)

    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    em_id = "em-custom"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch, "x", cancel)

    assert result == "daemon done"
    agent.service.create_session.assert_not_called()
    # The whole point: the daemon-scoped service gets a present, correct key.
    assert captured["init"]["api_key"] == "sk-from-LLM_API_KEY"
    assert captured["init"]["provider"] == "custom"
    assert captured["init"]["base_url"] == "https://proxy.example/v1"
    # Provider defaults still inherited verbatim (no Codex anchor for custom).
    assert captured["init"]["provider_defaults"] == {"custom": {"api_compat": "openai"}}


def test_run_emanation_codex_preset_gets_daemon_cache_anchor(tmp_path, monkeypatch):
    """Codex preset daemons pass daemon-scoped provider defaults too."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    captured = {}

    class FakeService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]

        def create_session(self, **kwargs):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.text = "preset daemon done"
            mock_response.tool_calls = []
            mock_response.usage = MagicMock(
                input_tokens=0,
                output_tokens=0,
                thinking_tokens=0,
                cached_tokens=0,
            )
            mock_session.send = MagicMock(return_value=mock_response)
            return mock_session

    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", FakeService)

    cancel = threading.Event()
    em_id = "em-preset-codex"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id,
        run_dir,
        schemas,
        dispatch,
        "x",
        cancel,
        preset_llm={
            "provider": "codex",
            "model": "gpt-5.5",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_key": "ignored-by-fake",
            "max_rpm": 11,
            "compact_threshold": None,
        },
    )

    assert result == "preset daemon done"
    assert captured["init"]["provider"] == "codex"
    assert captured["init"]["model"] == "gpt-5.5"
    assert captured["init"]["base_url"] == "https://chatgpt.com/backend-api/codex"
    defaults = captured["init"]["provider_defaults"]
    assert defaults["codex"]["max_rpm"] == 11
    assert defaults["codex"]["compact_threshold"] is None
    assert defaults["codex"]["codex_session_anchor"] == str((run_dir.path / "daemon.json").resolve())


def test_run_emanation_dispatches_tools(tmp_path, monkeypatch):
    """Emanation dispatches tool calls and feeds results back."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _reuse_parent_service(monkeypatch, agent)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_handler = MagicMock(return_value={"content": "file text"})
    agent._tool_handlers["read"] = mock_handler

    tc = ToolCall(name="read", args={"file_path": "/tmp/x"}, id="tc-1")
    resp1 = MagicMock()
    resp1.text = ""
    resp1.tool_calls = [tc]
    resp1.usage = MagicMock(input_tokens=0, output_tokens=0,
                            thinking_tokens=0, cached_tokens=0)
    resp2 = MagicMock()
    resp2.text = "Task done. Read the file."
    resp2.tool_calls = []
    resp2.usage = MagicMock(input_tokens=0, output_tokens=0,
                            thinking_tokens=0, cached_tokens=0)

    mock_session = MagicMock()
    mock_session.send = MagicMock(side_effect=[resp1, resp2])
    agent.service.create_session = MagicMock(return_value=mock_session)
    agent.service.make_tool_result = MagicMock(return_value="mock_result")

    cancel = threading.Event()
    em_id = "em-test"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch,
                                "read a file", cancel)
    assert "Read the file" in result
    assert mock_handler.called


def test_run_emanation_uses_prompt_as_first_user_without_task_duplication(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    service = _CanonicalFakeService([[ _resp("done") ]])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id, task="system objective")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]),
        "system objective", threading.Event(), prompt="  custom first user  ",
    )

    assert result == "done"
    assert service.sessions[0].sent_messages[0] == "  custom first user  "
    first_request = service.sessions[0].request_snapshots[0]
    user_entries = [e for e in first_request if e["role"] == "user"]
    assert [e["content"][0]["text"] for e in user_entries] == ["  custom first user  "]
    assert all("system objective" not in e["content"][0]["text"] for e in user_entries)


def test_compact_blank_reason_does_not_reset_context(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    compact_call = ToolCall(name="compact", args={"action": "run", "_reason": "   "}, id="compact-blank")
    service = _CanonicalFakeService([[
        _resp(tool_calls=[compact_call]),
        _resp("done after blank"),
    ]])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]),
        "task", threading.Event(),
    )

    assert result == "done after blank"
    assert len(service.sessions) == 1
    second_request = service.sessions[0].request_snapshots[1]
    assert any(e["role"] == "user" and e["content"][0]["type"] == "text" for e in second_request)
    result_entry = second_request[-1]
    assert result_entry["role"] == "user"
    result_content = result_entry["content"][0]["content"]
    assert result_content["status"] == "error"
    assert "context was not reset" in result_content["message"]


def test_compact_missing_action_is_refused_without_reset(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    compact_call = ToolCall(
        name="compact",
        args={"_reason": "handoff must not imply run"},
        id="compact-missing-action",
    )
    service = _CanonicalFakeService([[
        _resp(tool_calls=[compact_call]),
        _resp("done after refusal"),
    ]])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]),
        "task", threading.Event(),
    )

    assert result == "done after refusal"
    assert len(service.sessions) == 1
    result_content = service.sessions[0].request_snapshots[1][-1]["content"][0]["content"]
    assert result_content["status"] == "error"
    assert "action is required" in result_content["message"]
    assert "context was not reset" in result_content["message"]


def test_compact_success_prunes_to_system_call_and_result(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    compact_call = ToolCall(
        name="compact",
        args={"action": "run", "_reason": "handoff: continue"},
        id="compact-1",
    )
    service = _CanonicalFakeService([
        [LLMResponse(
            tool_calls=[compact_call],
            usage=UsageMetadata(input_tokens=90, output_tokens=1),
        )],
        [_resp("done after compact")],
    ])
    original_create_session = service.create_session

    def create_session_with_window(**kwargs):
        session = original_create_session(**kwargs)
        session.context_window = lambda: 100
        session.interface.estimate_context_tokens = lambda: 20
        return session

    service.create_session = create_session_with_window
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id, task="task")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]),
        "task", threading.Event(),
    )

    assert result == "done after compact"
    assert len(service.sessions) == 2
    retained_request = service.sessions[1].request_snapshots[0]
    assert [e["role"] for e in retained_request] == ["system", "assistant", "user"]
    assert retained_request[1]["content"][0] == {
        "type": "tool_call",
        "id": "compact-1",
        "name": "compact",
        "args": {"action": "run", "_reason": "handoff: continue"},
    }
    result_block = retained_request[2]["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["id"] == "compact-1"
    assert result_block["name"] == "compact"
    result_meta = result_block["metadata"]["agent_meta"]
    assert result_meta["agent_state"]["context"]["context_usage"] == 0.2
    assert "warning" not in result_meta["agent_state"]["context"]
    assert result_meta["agent_state"]["token_usage"]["current_call"]["input"] == 0
    assert result_meta["agent_state"]["token_usage"]["session"]["input_tokens"] == 90
    payload = result_block["content"]
    assert payload["status"] == "success"
    assert "surviving compact call _reason" in payload["instruction"]
    assert "handoff" not in payload
    assert payload["recovery"] == {
        "run_directory": str(run_dir.path),
        "state": str(run_dir.daemon_json_path),
        "chat_history": str(run_dir.chat_path),
        "event_log": str(run_dir.events_path),
    }
    assert not any(
        e["role"] == "user"
        and e["content"][0].get("type") == "text"
        for e in retained_request
    )
    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert state["run_id"] == run_dir.run_id
    assert state["state"] == "done"


def test_compact_is_repeatable_same_run(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    first = ToolCall(name="compact", args={"action": "run", "_reason": "first handoff"}, id="compact-1")
    second = ToolCall(name="compact", args={"action": "run", "_reason": "second handoff"}, id="compact-2")
    service = _CanonicalFakeService([
        [_resp(tool_calls=[first])],
        [_resp(tool_calls=[second])],
        [_resp("done after repeat")],
    ])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id, task="task")
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface([]),
        "task", threading.Event(),
    )

    assert result == "done after repeat"
    assert len(service.sessions) == 3
    second_retained = service.sessions[2].request_snapshots[0]
    assert [e["role"] for e in second_retained] == ["system", "assistant", "user"]
    assert second_retained[1]["content"][0]["id"] == "compact-2"
    assert DaemonRunDir.read_state_from_disk(run_dir.path)["run_id"] == run_dir.run_id


def test_compact_mixed_batch_does_not_dispatch_siblings_and_pairs_all_calls(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    sibling_handler = MagicMock(return_value={"content": "should not run"})
    agent._tool_handlers["read"] = sibling_handler
    calls = [
        ToolCall(name="compact", args={"action": "run", "_reason": "handoff"}, id="compact-mixed"),
        ToolCall(name="read", args={"file_path": "/tmp/x"}, id="read-mixed"),
    ]
    service = _CanonicalFakeService([[
        _resp(tool_calls=calls),
        _resp("done after mixed error"),
    ]])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    mgr = agent.get_capability("daemon")
    em_id = "em-test"
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(
        em_id, run_dir, *mgr._build_tool_surface(["file"]),
        "task", threading.Event(),
    )

    assert result == "done after mixed error"
    sibling_handler.assert_not_called()
    assert len(service.sessions) == 1
    tool_result_entry = service.sessions[0].request_snapshots[1][-1]
    assert tool_result_entry["role"] == "user"
    paired = tool_result_entry["content"]
    assert [block["id"] for block in paired] == ["compact-mixed", "read-mixed"]
    assert all(block["content"]["status"] == "error" for block in paired)
    assert "no tools in this batch were executed" in paired[0]["content"]["message"]


def test_run_emanation_uses_tool_call_guard_before_dispatch(tmp_path, monkeypatch):
    """Daemon tool calls go through ToolExecutor/ToolCallGuard before handler dispatch."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _reuse_parent_service(monkeypatch, agent)
    agent.inbox = queue.Queue()

    def deny_read(proposal):
        if proposal.tool_name == "read":
            return GuardDecision.deny(
                check_name="deny_read",
                reason="daemon read blocked by policy",
            )
        return None

    agent._tool_call_guard = ToolCallGuard([deny_read])
    mgr = agent.get_capability("daemon")

    mock_handler = MagicMock(return_value={"content": "file text"})
    agent._tool_handlers["read"] = mock_handler

    tc = ToolCall(name="read", args={"file_path": "/tmp/x"}, id="tc-guard")
    resp1 = MagicMock()
    resp1.text = ""
    resp1.tool_calls = [tc]
    resp1.usage = MagicMock(input_tokens=0, output_tokens=0,
                            thinking_tokens=0, cached_tokens=0)
    resp2 = MagicMock()
    resp2.text = "Task done. Guard denial observed."
    resp2.tool_calls = []
    resp2.usage = MagicMock(input_tokens=0, output_tokens=0,
                            thinking_tokens=0, cached_tokens=0)

    mock_session = MagicMock()
    mock_session.send = MagicMock(side_effect=[resp1, resp2])
    agent.service.create_session = MagicMock(return_value=mock_session)
    agent.service.make_tool_result = MagicMock(return_value="mock_guard_result")

    cancel = threading.Event()
    em_id = "em-test"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch,
                                "read a file", cancel)

    assert "Guard denial observed" in result
    mock_handler.assert_not_called()
    payload = agent.service.make_tool_result.call_args.args[1]
    assert payload["error_type"] == "ToolCallGuardDenied"
    assert payload["guard_check"] == "deny_read"


def test_run_emanation_respects_cancel_before_first_send(tmp_path):
    """Emanation exits immediately if pre-cancelled (before first LLM call)."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    agent.service.create_session = MagicMock(return_value=mock_session)

    cancel = threading.Event()
    cancel.set()
    em_id = "em-test"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch,
                                "do stuff", cancel)
    assert result == "[cancelled]"
    mock_session.send.assert_not_called()


def test_handle_emanate_dispatches_and_returns_ids(tmp_path, monkeypatch):
    """emanate dispatches tasks and returns compact unique IDs."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _enable_detached_fake_llm(agent, monkeypatch)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "task A", "tools": ["file"]},
        {"task": "task B", "tools": ["file"]},
    ]})
    assert result["status"] == "dispatched"
    assert result["handoff"] == (
        "While waiting, go idle or call system(action='sleep'); the terminal result "
        "will arrive and wake you as a notification; read daemon-manual and "
        "notification-manual for details. If Telegram is connected and a Task Card "
        "is available for the current turn, use it to report progress; call "
        "`telegram(action='manual')` and follow its `Programmable Task Card` "
        "section for details."
    )
    assert result["count"] == 2
    ids = result["ids"]
    assert len(ids) == 2
    assert len(set(ids)) == 2
    for em_id in ids:
        _assert_compact_daemon_id(em_id)
    assert re.fullmatch(r"dg-\d{8}-\d{6}-[0-9a-f]{6}", result["group_id"])

    group_ids = []
    for em_id in ids:
        run_dir = mgr._emanations[em_id]["run_dir"]
        data = json.loads(run_dir.daemon_json_path.read_text())
        group_ids.append(data["group_id"])
    assert group_ids == [result["group_id"], result["group_id"]]

    list_result = mgr._handle_list()
    listed_groups = {item["id"]: item.get("group_id") for item in list_result["emanations"]}
    assert listed_groups == {em_id: result["group_id"] for em_id in ids}

    for em_id in ids:
        _poll_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    from tests._notification_store_helpers import snapshot_notifications

    assert agent.inbox.empty()
    notification_deadline = time.monotonic() + 5.0
    events = []
    while time.monotonic() < notification_deadline:
        system = snapshot_notifications(agent._working_dir).get("system", {})
        events = system.get("data", {}).get("events", [])
        if {e.get("ref_id") for e in events} == set(ids):
            break
        time.sleep(0.05)
    assert len(events) == 2
    assert {e["source"] for e in events} == {"daemon"}
    assert {e["ref_id"] for e in events} == set(ids)
    assert all("[daemon:em-" not in e["body"] for e in events)


def test_handle_emanate_allows_concurrent(tmp_path):
    """emanate succeeds even with existing emanations (no limit)."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")

    mgr._emanations["em-0"] = {"future": MagicMock(done=MagicMock(return_value=False)), "run_dir": None}
    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "x", "tools": ["file"]},
    ]})
    # No limit enforced — should succeed
    assert result["status"] == "dispatched"
    assert len(result["ids"]) == 1


def test_handle_list_shows_status(tmp_path):
    """list returns emanation statuses."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")

    done_future = MagicMock()
    done_future.done.return_value = True
    done_future.exception.return_value = None
    running_future = MagicMock()
    running_future.done.return_value = False

    mgr._emanations = {
        "em-1": {"future": done_future, "task": "task A", "start_time": time.time() - 10, "cancel_event": threading.Event(), "run_dir": None},
        "em-2": {"future": running_future, "task": "task B", "start_time": time.time() - 5, "cancel_event": threading.Event(), "run_dir": None},
    }
    result = mgr._handle_list()
    assert len(result["emanations"]) == 2
    statuses = {e["id"]: e["status"] for e in result["emanations"]}
    assert statuses["em-1"] == "done"
    assert statuses["em-2"] == "running"


def test_handle_list_includes_historical_done_run_dirs(tmp_path):
    """list scans daemon run dirs so completed daemons remain discoverable."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(
        agent,
        em_id="em-history",
        task="summarize alpha history",
        tools=["file", "bash"],
        system_prompt="custom system prompt alpha",
        call_parameters={
            "task": "summarize alpha history",
            "tools": ["file", "bash"],
            "skills": ["daemon-manual"],
            "mcp": [{"name": "docs", "transport": "stdio", "env": {"TOKEN": "<redacted>"}}],
            "system_prompt": "custom system prompt alpha",
        },
    )
    rd.mark_done("needle result with artifact path reports/alpha.md")

    # Simulate a later manager/session: no live _emanations, only run-dir files.
    listing = mgr._handle_list()
    matches = [e for e in listing["emanations"] if e.get("run_id") == rd.run_id]
    assert len(matches) == 1
    em = matches[0]
    assert listing["history_included"] is True
    assert listing["index"] == "daemon_run_dirs"
    assert em["id"] == "em-history"
    assert em["status"] == "done"
    assert em["path"].endswith(rd.run_id)
    assert em["result_path"].endswith("result.txt")
    assert "needle result" in em["result_preview"]
    assert em["system_prompt_path"].endswith(".prompt")
    assert "custom system prompt alpha" in em["system_prompt_preview"]
    assert em["call_parameters"]["skills"] == ["daemon-manual"]
    assert em["call_parameters"]["mcp"][0]["env"] == {"TOKEN": "<redacted>"}


def test_handle_list_filters_history_by_contains_status_and_last(tmp_path):
    """list supports lightweight progressive-disclosure search over run metadata."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    alpha = _make_run_dir(agent, em_id="em-alpha", task="alpha task")
    alpha.mark_done("alpha result contains unique-needle")
    beta = _make_run_dir(agent, em_id="em-beta", task="beta task")
    beta.mark_failed(RuntimeError("beta failed unique-needle"))

    done_listing = mgr._handle_list(contains="unique-needle", status_filter="done")
    assert done_listing["total_matches"] == 1
    assert done_listing["emanations"][0]["run_id"] == alpha.run_id

    failed_listing = mgr._handle_list(contains="unique-needle", status_filter="failed", limit=1)
    assert failed_listing["total_matches"] == 1
    assert failed_listing["showing"] == 1
    assert failed_listing["emanations"][0]["run_id"] == beta.run_id
    assert "beta failed" in str(failed_listing["emanations"][0]["error"])

    hidden_history = mgr._handle_list(include_done=False)
    assert all(e.get("run_id") not in {alpha.run_id, beta.run_id} for e in hidden_history["emanations"])
    assert hidden_history["history_included"] is False


def test_daemon_run_dir_writes_current_data_version(tmp_path):
    """New daemon.json records carry a version for future lazy migration."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    rd = _make_run_dir(agent, em_id="em-version")
    state = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    assert state["data_version"] == DaemonRunDir.DATA_VERSION


def test_handle_list_rebuilds_missing_daemon_json_best_effort(tmp_path):
    """list lazily rebuilds a minimal daemon.json when a run folder lost it."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    run_path = agent._working_dir / "daemons" / "em-7-20260102-030405-abcdef"
    (run_path / "logs").mkdir(parents=True)
    (run_path / "history").mkdir()
    (run_path / ".prompt").write_text(
        "daemon prompt\n\nYour task:\nrecover missing daemon json task",
        encoding="utf-8",
    )
    (run_path / "result.txt").write_text("recovered result body", encoding="utf-8")
    (run_path / "logs" / "events.jsonl").write_text(
        json.dumps({"event": "daemon_done", "ts": "2026-01-02T03:05:00Z"}) + "\n",
        encoding="utf-8",
    )

    listing = mgr._handle_list(contains="recover missing")
    assert listing["total_matches"] == 1
    em = listing["emanations"][0]
    assert em["run_id"] == "em-7-20260102-030405-abcdef"
    assert em["id"] == "em-7"
    assert em["status"] == "done"
    assert em["task"] == "recover missing daemon json task"
    assert em["data_version"] == 1
    assert em["migration"]["reason"] == "daemon_json_missing"
    assert "recovered result" in em["result_preview"]

    rebuilt = json.loads((run_path / "daemon.json").read_text(encoding="utf-8"))
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    assert rebuilt["data_version"] == DaemonRunDir.DATA_VERSION
    assert rebuilt["migration"]["reason"] == "daemon_json_missing"
    assert rebuilt["task"] == "recover missing daemon json task"
    assert rebuilt["result_path"].endswith("result.txt")


def test_handle_list_rebuilds_invalid_daemon_json_best_effort(tmp_path):
    """list also rebuilds corrupt daemon.json files instead of dropping the run."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    run_path = agent._working_dir / "daemons" / "em-8-20260102-030405-fedcba"
    (run_path / "logs").mkdir(parents=True)
    (run_path / "history").mkdir()
    (run_path / ".prompt").write_text("daemon prompt\n\nYour task:\nrecover corrupt json task", encoding="utf-8")
    (run_path / "daemon.json").write_text("{not-json", encoding="utf-8")

    listing = mgr._handle_list(contains="corrupt json")
    assert listing["total_matches"] == 1
    em = listing["emanations"][0]
    assert em["run_id"] == "em-8-20260102-030405-fedcba"
    assert em["migration"]["reason"] == "daemon_json_invalid"

    rebuilt = json.loads((run_path / "daemon.json").read_text(encoding="utf-8"))
    assert rebuilt["task"] == "recover corrupt json task"
    assert rebuilt["migration"]["reason"] == "daemon_json_invalid"


def test_handle_list_rebuilds_non_utf8_daemon_json(tmp_path):
    """A non-UTF-8 daemon.json is treated as invalid and rebuilt best-effort."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    run_path = agent._working_dir / "daemons" / "em-9-20260102-030405-a1b2c3"
    (run_path / "logs").mkdir(parents=True)
    (run_path / "history").mkdir()
    (run_path / ".prompt").write_text("daemon prompt\n\nYour task:\nrecover non utf daemon json", encoding="utf-8")
    (run_path / "result.txt").write_text("non utf rebuilt result", encoding="utf-8")
    (run_path / "daemon.json").write_bytes(b"\xff\xfe\x00bad-json")

    listing = mgr._handle_list(contains="non utf")
    assert listing["total_matches"] == 1
    em = listing["emanations"][0]
    assert em["status"] == "done"
    assert em["migration"]["reason"] == "daemon_json_invalid"

    rebuilt = json.loads((run_path / "daemon.json").read_text(encoding="utf-8"))
    assert rebuilt["task"] == "recover non utf daemon json"
    assert rebuilt["migration"]["reason"] == "daemon_json_invalid"


def test_handle_list_rebuild_reads_only_events_tail(tmp_path):
    """Best-effort rebuild can infer terminal state from the tail of a large event log."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    run_path = agent._working_dir / "daemons" / "em-10-20260102-030405-1a2b3c"
    (run_path / "logs").mkdir(parents=True)
    (run_path / "history").mkdir()
    (run_path / ".prompt").write_text("daemon prompt\n\nYour task:\nlarge events tail task", encoding="utf-8")
    events_path = run_path / "logs" / "events.jsonl"
    padding = json.dumps({"event": "cli_output", "text": "x" * 2000}) + "\n"
    events_path.write_text(padding * 40 + json.dumps({"event": "daemon_timeout", "ts": "2026-01-02T03:06:00Z"}) + "\n", encoding="utf-8")

    listing = mgr._handle_list(contains="large events tail")
    assert listing["total_matches"] == 1
    em = listing["emanations"][0]
    assert em["status"] == "timeout"
    assert em["migration"]["reason"] == "daemon_json_missing"

    rebuilt = json.loads((run_path / "daemon.json").read_text(encoding="utf-8"))
    assert rebuilt["state"] == "timeout"
    assert rebuilt["finished_at"] == "2026-01-02T03:06:00Z"


def test_handle_list_rebuilds_stale_daemon_json_version(tmp_path):
    """list upgrades stale daemon.json records and preserves backend extras."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-stale", task="stale version task")
    rd.mark_done("stale version result")
    state = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    state["data_version"] = -1
    state["backend_options"] = {"search": True}
    state["future_backend_field"] = {"preserve": True}
    rd.daemon_json_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    listing = mgr._handle_list(contains="stale version", status_filter="done")
    assert listing["total_matches"] == 1
    em = listing["emanations"][0]
    assert em["run_id"] == rd.run_id
    assert em["status"] == "done"

    rebuilt = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    assert rebuilt["data_version"] == DaemonRunDir.DATA_VERSION
    assert rebuilt["migration"]["reason"] == "daemon_json_data_version_mismatch"
    assert rebuilt["backend_options"] == {"search": True}
    assert rebuilt["future_backend_field"] == {"preserve": True}


def test_handle_list_rejects_non_positive_last(tmp_path):
    """list reuses last as a positive limit, not a zero/negative slice."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    result = mgr._handle_list(limit=0)
    assert result["status"] == "error"
    assert "last must be" in result["message"]


def test_handle_ask_sends_followup(tmp_path):
    """ask buffers a follow-up for a running emanation."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    mgr._emanations["em-1"] = {
        "future": MagicMock(done=MagicMock(return_value=False)),
        "task": "x",
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": None,
    }
    result = mgr._handle_ask("em-1", "also check tests/")
    assert result["status"] == "sent"
    assert mgr._emanations["em-1"]["followup_buffer"] == "also check tests/"


def test_handle_ask_collapses_multiple(tmp_path):
    """Multiple asks collapse into one buffer."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    mgr._emanations["em-1"] = {
        "future": MagicMock(done=MagicMock(return_value=False)),
        "task": "x",
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": None,
    }
    mgr._handle_ask("em-1", "first")
    mgr._handle_ask("em-1", "second")
    assert mgr._emanations["em-1"]["followup_buffer"] == "first\n\nsecond"


def test_handle_reclaim_cancels_all(tmp_path):
    """reclaim sets cancel events and clears registry."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    pool = MagicMock()
    mgr._pools = [(pool, cancel)]
    mgr._emanations = {
        "em-1": {"future": MagicMock(done=MagicMock(return_value=False)), "cancel_event": cancel, "run_dir": None},
    }
    result = mgr._handle_reclaim()
    assert result["status"] == "reclaimed"
    assert result["cancelled"] == 1
    assert cancel.is_set()
    assert len(mgr._emanations) == 0
    pool.shutdown.assert_called_once()


def test_run_emanation_respects_cancel_mid_loop(tmp_path, monkeypatch):
    """Emanation exits on cancel event between tool-call rounds."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _reuse_parent_service(monkeypatch, agent)
    mgr = agent.get_capability("daemon")

    tc = ToolCall(name="read", args={}, id="tc-1")
    resp = MagicMock()
    resp.text = ""
    resp.tool_calls = [tc]
    resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                           thinking_tokens=0, cached_tokens=0)

    mock_session = MagicMock()
    agent.service.create_session = MagicMock(return_value=mock_session)
    agent.service.make_tool_result = MagicMock(return_value="mock_result")
    agent._tool_handlers["read"] = MagicMock(return_value={})

    cancel = threading.Event()
    call_count = [0]
    def send_and_cancel(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] >= 2:
            cancel.set()
        return resp
    mock_session.send = send_and_cancel

    em_id = "em-test"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch,
                                "do stuff", cancel)
    assert result == "[cancelled]"


def test_end_to_end_emanate_list_ask_reclaim(tmp_path, monkeypatch):
    """Detached lifecycle: emanate → durable list/check → terminal reclaim."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _enable_detached_fake_llm(agent, monkeypatch)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "summarize architecture", "tools": ["file"]},
    ]})
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    _assert_compact_daemon_id(em_id)
    run_dir = mgr._emanations[em_id]["run_dir"]
    state = _poll_daemon_terminal(run_dir)
    assert state["owner"] == "supervisor"
    assert "Task done" in run_dir.result_path.read_text(encoding="utf-8")

    list_result = mgr._handle_list()
    statuses = {e["id"]: e["status"] for e in list_result["emanations"]}
    assert statuses.get(em_id) == "done"

    check = mgr.handle({"action": "check", "id": em_id})
    assert check["state"] == "done"

    from tests._notification_store_helpers import snapshot_notifications

    assert agent.inbox.empty()
    # Terminal state is committed before the detached supervisor publishes the
    # durable notification receipt.  Assert eventual publication rather than
    # reintroducing a parent-future/synchronous-callback assumption.
    notification_deadline = time.monotonic() + 5.0
    events = []
    while time.monotonic() < notification_deadline:
        system = snapshot_notifications(agent._working_dir).get("system", {})
        events = system.get("data", {}).get("events", [])
        if any(e.get("source") == "daemon" and e.get("ref_id") == em_id for e in events):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"detached terminal notification not published for {em_id}")
    assert any(e["source"] == "daemon" and e["ref_id"] == em_id for e in events)
    assert any("Task done" in e["body"] for e in events)
    assert any(f"daemon(action=\"check\", id=\"{em_id}\")" in e["body"] for e in events)
    assert all("[daemon:em-" not in e["body"] for e in events)

    reclaim_result = mgr._handle_reclaim()
    assert reclaim_result["status"] == "reclaimed"



def test_on_emanation_done_publishes_system_notification_not_request(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-9")

    future = MagicMock()
    future.result.return_value = "final report long enough to notify"
    mgr._emanations["em-9"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-9", "test task", future)

    assert agent.inbox.empty()
    notifications = snapshot_notifications(agent._working_dir)
    events = notifications["system"]["data"]["events"]
    assert len(events) == 1
    event = events[0]
    assert event["source"] == "daemon"
    assert event["ref_id"] == "em-9"
    assert "Daemon em-9 done" in event["body"]
    assert "daemon(action=\"check\", id=\"em-9\")" in event["body"]
    assert "final report" in event["body"]
    assert "[daemon:em-" not in event["body"]


def test_on_emanation_done_failure_always_notifies(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-fail")

    future = MagicMock()
    future.result.side_effect = RuntimeError("boom")
    mgr._emanations["em-fail"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-fail", "test task", future)

    assert agent.inbox.empty()
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    assert len(events) == 1
    assert events[0]["source"] == "daemon"
    assert events[0]["ref_id"] == "em-fail"
    assert "failed" in events[0]["body"]
    assert "boom" in events[0]["body"]
    assert "[daemon:em-" not in events[0]["body"]


def test_on_emanation_done_short_success_notifies(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-short")

    future = MagicMock()
    future.result.return_value = "ok"
    mgr._emanations["em-short"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-short", "test task", future)

    assert agent.inbox.empty()
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    assert len(events) == 1
    assert events[0]["source"] == "daemon"
    assert events[0]["ref_id"] == "em-short"
    assert "Daemon em-short done." in events[0]["body"]
    assert "Preview:\nok" in events[0]["body"]


def test_on_emanation_done_cancelled_notifies_despite_short_text(tmp_path):
    """A cancelled run returns the short ``[cancelled]`` sentinel but its
    run_dir state is authoritative. The parent must always learn the daemon
    terminated, with the correct terminal label."""
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-cancel")
    rd.mark_cancelled()

    future = MagicMock()
    future.result.return_value = "[cancelled]"
    mgr._emanations["em-cancel"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-cancel", "test task", future)

    assert agent.inbox.empty()
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    assert len(events) == 1
    assert events[0]["source"] == "daemon"
    assert events[0]["ref_id"] == "em-cancel"
    assert "cancelled" in events[0]["body"]


def test_on_emanation_done_timeout_notifies_despite_short_text(tmp_path):
    """A timed-out run also returns the short sentinel; its terminal state is
    ``timeout`` and must be reported with that label."""
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-timeout")
    rd.mark_timeout()

    future = MagicMock()
    future.result.return_value = "[cancelled]"
    mgr._emanations["em-timeout"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-timeout", "test task", future)

    assert agent.inbox.empty()
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    assert len(events) == 1
    assert events[0]["ref_id"] == "em-timeout"
    assert "timeout" in events[0]["body"]


def test_on_emanation_done_notifies_terminal_only_once(tmp_path):
    """A daemon run's terminal notification is delivered exactly once even if
    the done-callback fires more than once for the same run (e.g. a racing
    reclaim or a duplicated callback). Dedup is keyed on the run's ref_id."""
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-dup")
    rd.mark_failed(RuntimeError("boom"))

    future = MagicMock()
    future.result.side_effect = RuntimeError("boom")
    mgr._emanations["em-dup"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-dup", "test task", future)
    mgr._on_emanation_done("em-dup", "test task", future)

    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    daemon_events = [e for e in events if e["ref_id"] == "em-dup"]
    assert len(daemon_events) == 1


def test_terminal_notification_enqueue_failure_leaves_retryable_state(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-retry")
    rd.mark_done("retryable terminal result")

    future = MagicMock()
    future.result.return_value = "retryable terminal result"
    mgr._emanations["em-retry"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    original_enqueue = agent._enqueue_system_notification

    def fail_enqueue(**kwargs):
        raise OSError("notification write failed")

    agent._enqueue_system_notification = fail_enqueue
    mgr._on_emanation_done("em-retry", "test task", future)

    state = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    assert state["terminal_notified"] is False
    assert state["terminal_notification_claim"] is None
    assert "system" not in snapshot_notifications(agent._working_dir)

    agent._enqueue_system_notification = original_enqueue
    mgr._on_emanation_done("em-retry", "test task", future)

    state = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    assert state["terminal_notified"] is True
    assert state["terminal_notification_claim"] is None
    assert state["terminal_notification_receipt"]["idempotency_key"] == (
        f"daemon-terminal:{rd.run_id}"
    )
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    daemon_events = [e for e in events if e["ref_id"] == "em-retry"]
    assert len(daemon_events) == 1


def test_daemon_startup_retries_unpublished_terminal_notification(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    daemon_json = _write_daemon_json(
        tmp_path,
        "em-restart",
        state="done",
        finished_at="2026-07-10T10:00:00Z",
        result_preview="finished while parent was down",
        terminal_notified=False,
        terminal_notification_claim={
            "status": "pending",
            "idempotency_key": "daemon-terminal:em-restart",
        },
    )

    agent = _make_agent(tmp_path, ["daemon"])

    state = json.loads(daemon_json.read_text(encoding="utf-8"))
    assert state["terminal_notified"] is True
    assert state["terminal_notification_claim"] is None
    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    daemon_events = [e for e in events if e["ref_id"] == "em-restart"]
    assert len(daemon_events) == 1
    assert daemon_events[0]["idempotency_key"] == "daemon-terminal:em-restart"
    assert "finished while parent was down" in daemon_events[0]["body"]


def test_concurrent_terminal_callbacks_publish_once(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-race")
    rd.mark_done("race result")

    future = MagicMock()
    future.result.return_value = "race result"
    mgr._emanations["em-race"] = {
        "future": future,
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    start = threading.Barrier(3)

    def callback():
        start.wait(timeout=5)
        mgr._on_emanation_done("em-race", "test task", future)

    threads = [threading.Thread(target=callback) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    daemon_events = [e for e in events if e["ref_id"] == "em-race"]
    assert len(daemon_events) == 1
    state = json.loads(rd.daemon_json_path.read_text(encoding="utf-8"))
    assert state["terminal_notified"] is True


def test_crash_after_publish_before_receipt_retry_is_idempotent(tmp_path):
    from lingtai.kernel.notifications import submit
    from tests._notification_store_helpers import snapshot_notifications

    daemon_json = _write_daemon_json(
        tmp_path,
        "em-crash",
        state="failed",
        finished_at="2026-07-10T10:00:00Z",
        error={"type": "RuntimeError", "message": "boom"},
        terminal_notified=False,
    )
    workdir = tmp_path / "daemon-agent"
    submit(
        store_agent_for(workdir),
        "system",
        header="1 system notification",
        icon="🔔",
        priority="normal",
        data={
            "events": [{
                "event_id": "evt_existing",
                "source": "daemon",
                "ref_id": "em-crash",
                "idempotency_key": "daemon-terminal:em-crash",
                "body": "already published before receipt write",
                "at": "2026-07-10T10:00:00Z",
            }],
        },
    )

    agent = _make_agent(tmp_path, ["daemon"])

    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    daemon_events = [e for e in events if e.get("idempotency_key") == "daemon-terminal:em-crash"]
    assert len(daemon_events) == 1
    assert daemon_events[0]["event_id"] == "evt_existing"
    state = json.loads(daemon_json.read_text(encoding="utf-8"))
    assert state["terminal_notified"] is True
    assert state["terminal_notification_receipt"]["idempotency_key"] == (
        "daemon-terminal:em-crash"
    )


def test_legacy_terminal_notified_true_is_not_republished(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    _write_daemon_json(
        tmp_path,
        "em-legacy",
        state="done",
        finished_at="2026-07-10T10:00:00Z",
        result_preview="legacy result",
        terminal_notified=True,
    )

    agent = _make_agent(tmp_path, ["daemon"])

    assert "system" not in snapshot_notifications(agent._working_dir)


def test_legacy_missing_terminal_notified_key_is_not_republished_or_mutated(tmp_path):
    from tests._notification_store_helpers import snapshot_notifications

    daemon_json = _write_daemon_json(
        tmp_path,
        "em-legacy-missing",
        state="done",
        finished_at="2026-07-10T10:00:00Z",
        result_preview="legacy missing-key result",
    )
    before = json.loads(daemon_json.read_text(encoding="utf-8"))
    assert "terminal_notified" not in before

    agent = _make_agent(tmp_path, ["daemon"])

    after = json.loads(daemon_json.read_text(encoding="utf-8"))
    assert "system" not in snapshot_notifications(agent._working_dir)
    assert "terminal_notified" not in after
    assert "terminal_notification_receipt" not in after


def test_terminal_reconciliation_preview_reads_are_bounded(tmp_path, monkeypatch):
    from pathlib import Path

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    result_file = tmp_path / "large-result.txt"
    result_file.write_text("x" * 5000, encoding="utf-8")
    fallback_dir = tmp_path / "fallback-run"
    fallback_dir.mkdir()
    (fallback_dir / "result.txt").write_text("y" * 5000, encoding="utf-8")

    def forbidden_read_text(self, *args, **kwargs):
        raise AssertionError(f"unbounded Path.read_text used for {self}")

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    assert mgr._terminal_notification_text_from_state(
        {"result_path": str(result_file)}, tmp_path,
    ) == "x" * 2000
    assert mgr._terminal_notification_text_from_state(
        {}, fallback_dir,
    ) == "y" * 2000


def test_daemon_schema_has_no_terminal_notification_toggle():
    from lingtai.tools.daemon import get_schema

    def walk_properties(schema):
        if isinstance(schema, dict):
            for name, value in schema.get("properties", {}).items():
                yield name, value
                yield from walk_properties(value)
            if "items" in schema:
                yield from walk_properties(schema["items"])

    toggles = [
        name for name, value in walk_properties(get_schema("en"))
        if value.get("type") == "boolean"
        and ("notify" in name.lower() or "notification" in name.lower())
    ]
    assert toggles == []


def test_on_emanation_done_notification_includes_task_summary(tmp_path):
    """The terminal notification carries a bounded task summary so the parent
    can recognize which dispatched daemon ended without opening the run dir."""
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(
        agent, em_id="em-task",
        task="Audit the payment retry logic for double-charge bugs",
    )
    rd.mark_done("a sufficiently long final result body here")

    future = MagicMock()
    future.result.return_value = "a sufficiently long final result body here"
    mgr._emanations["em-task"] = {
        "future": future,
        "task": "Audit the payment retry logic for double-charge bugs",
        "start_time": time.time(),
        "run_dir": rd,
    }

    mgr._on_emanation_done("em-task", "test task", future)

    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    body = next(e["body"] for e in events if e["ref_id"] == "em-task")
    assert "payment retry logic" in body


def test_terminal_notification_not_blocked_by_prior_followup(tmp_path):
    """A follow-up (ask) notification shares the daemon's ref_id and fires while
    the run is still alive. The terminal notification must still be delivered
    afterward — the once-only guard is scoped to the terminal event, not to any
    event carrying the same ref_id."""
    from tests._notification_store_helpers import snapshot_notifications

    agent = _make_agent(tmp_path, ["daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    rd = _make_run_dir(agent, em_id="em-ask")
    mgr._emanations["em-ask"] = {
        "future": MagicMock(),
        "task": "test task",
        "start_time": time.time(),
        "run_dir": rd,
    }

    # A follow-up reply lands first (run still running), same ref_id.
    mgr._publish_followup_if_live(
        "em-ask", status="follow-up completed",
        text="here is the follow-up answer, long enough", run_dir=rd,
    )

    # Then the run reaches a terminal state and the done-callback fires.
    rd.mark_done("final long-enough report from the daemon run")
    future = MagicMock()
    future.result.return_value = "final long-enough report from the daemon run"
    mgr._on_emanation_done("em-ask", "test task", future)

    events = snapshot_notifications(agent._working_dir)["system"]["data"]["events"]
    bodies = [e["body"] for e in events if e["ref_id"] == "em-ask"]
    assert any("Daemon em-ask done" in b for b in bodies), bodies


def test_sequential_emanate_increments_ids(tmp_path):
    """Multiple emanate calls produce distinct compact IDs."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    r1 = mgr.handle({"action": "emanate", "tasks": [{"task": "a", "tools": ["file"]}]})
    time.sleep(0.5)
    r2 = mgr.handle({"action": "emanate", "tasks": [{"task": "b", "tools": ["file"]}]})

    id1 = r1["ids"][0]
    id2 = r2["ids"][0]
    _assert_compact_daemon_id(id1)
    _assert_compact_daemon_id(id2)
    assert id1 != id2


def test_emanate_creates_folder_on_disk(tmp_path):
    """_handle_emanate creates daemons/<run_id>/ before the future starts."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                 thinking_tokens=0, cached_tokens=0)
    release_send = threading.Event()

    def _blocking_send(*args, **kwargs):
        release_send.wait(timeout=5.0)
        return mock_resp

    mock_session.send = MagicMock(side_effect=_blocking_send)
    agent.service.create_session = MagicMock(return_value=mock_session)

    try:
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "find todos", "tools": ["file"]},
        ]})
        assert result["status"] == "dispatched"
        em_id = result["ids"][0]
        _assert_compact_daemon_id(em_id)

        daemons_dir = agent._working_dir / "daemons"
        assert daemons_dir.is_dir()
        children = list(daemons_dir.iterdir())
        assert len(children) == 1
        folder = children[0]
        # New daemon ids are compact and identical to the folder/run id.
        assert folder.name == em_id
        # daemon.json exists with state=running and identity fields
        data = json.loads((folder / "daemon.json").read_text())
        assert data["handle"] == em_id
        assert data["run_id"] == em_id
        assert data["task"] == "find todos"
        assert data["tools"] == ["file"]
        assert data["state"] == "running"
    finally:
        release_send.set()


def test_reclaim_preserves_compact_id_uniqueness(tmp_path):
    """After reclaim, new compact ids do not reuse an existing daemon folder."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                 thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    r1 = mgr.handle({"action": "emanate", "tasks": [{"task": "a", "tools": ["file"]}]})
    id1 = r1["ids"][0]
    _assert_compact_daemon_id(id1)
    time.sleep(0.5)
    mgr.handle({"action": "reclaim"})
    r2 = mgr.handle({"action": "emanate", "tasks": [{"task": "b", "tools": ["file"]}]})
    id2 = r2["ids"][0]
    _assert_compact_daemon_id(id2)
    assert id2 != id1


def test_reclaim_preserves_folders(tmp_path):
    """reclaim stops processes but leaves daemon folders on disk."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                 thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    mgr.handle({"action": "emanate", "tasks": [{"task": "a", "tools": ["file"]}]})
    time.sleep(0.5)
    daemons_dir = agent._working_dir / "daemons"
    folders_before = list(daemons_dir.iterdir())
    assert len(folders_before) == 1

    mgr.handle({"action": "reclaim"})
    folders_after = list(daemons_dir.iterdir())
    assert folders_after == folders_before  # same folder still there


def test_handle_list_includes_run_id_and_path(tmp_path):
    """list output exposes run_id and path so inspectors know where to read."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "running"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                 thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)
    agent.service.create_session = MagicMock(return_value=mock_session)

    mgr.handle({"action": "emanate", "tasks": [{"task": "x", "tools": ["file"]}]})
    time.sleep(0.5)
    listing = mgr._handle_list()
    assert len(listing["emanations"]) >= 1
    em = listing["emanations"][0]
    assert "run_id" in em
    assert "path" in em
    _assert_compact_daemon_id(em["run_id"])
    assert em["path"].endswith(em["run_id"])


def test_e2e_emanate_writes_full_fs_artifact(tmp_path, monkeypatch):
    """Detached lifecycle: tool dispatch → completion → forensic folder."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _enable_detached_fake_llm(agent, monkeypatch, scenario="artifact")
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "find TODOs", "tools": ["file"]},
    ]})
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    run_dir = mgr._emanations[em_id]["run_dir"]
    state = _poll_daemon_terminal(run_dir)
    folder = run_dir.path

    # daemon.json shows terminal state with full info
    data = state
    assert data["state"] == "done"
    assert data["finished_at"] is not None
    assert data["task"] == "find TODOs"
    assert data["tool_call_count"] == 2
    assert data["result_preview"] == "Task done. Found 3 TODOs."
    assert data["tokens"]["input"] == 180
    assert data["tokens"]["output"] == 35

    # chat_history.jsonl has user+assistant entries across both rounds
    chat_lines = (folder / "history" / "chat_history.jsonl").read_text().splitlines()
    assert len(chat_lines) >= 4  # task + assistant1 + tool_results + assistant2
    chat_entries = [json.loads(line) for line in chat_lines]
    assert any(
        e["role"] == "user"
        and e["kind"] == "kickoff"
        and e["text"] == "Begin the assigned daemon task."
        for e in chat_entries
    )
    assert any(e["role"] == "assistant" and "Found 3 TODOs" in e["text"] for e in chat_entries)

    # events.jsonl has daemon_start, tool_call, tool_result, daemon_done
    events = [json.loads(line) for line in (folder / "logs" / "events.jsonl").read_text().splitlines()]
    event_types = [e["event"] for e in events]
    assert "daemon_start" in event_types
    assert "tool_call" in event_types
    assert "tool_result" in event_types
    assert "daemon_done" in event_types

    # Daemon's own token ledger has 2 entries
    daemon_ledger = (folder / "logs" / "token_ledger.jsonl").read_text().splitlines()
    assert len(daemon_ledger) == 2

    # Parent's ledger has the same 2 entries, tagged
    parent_ledger_path = agent._working_dir / "logs" / "token_ledger.jsonl"
    parent_lines = parent_ledger_path.read_text().splitlines()
    daemon_tagged = [json.loads(line) for line in parent_lines
                     if json.loads(line).get("source") == "daemon"]
    assert len(daemon_tagged) == 2
    assert all(e["em_id"] == em_id for e in daemon_tagged)
    assert all(e["codex_pool_source_index"] == 1 for e in daemon_tagged)
    assert all(e["codex_pool_size"] == 2 for e in daemon_tagged)
    assert all(e["codex_pool_weight"] == 1 for e in daemon_tagged)
    assert all(e["codex_pool_model_scope"] == "gpt-5.6" for e in daemon_tagged)
    assert all("codex_pool_source_ref" not in e for e in daemon_tagged)
    assert all("unsafe" not in e for e in daemon_tagged)

    # Reclaim does not touch folder
    mgr.handle({"action": "reclaim"})
    assert folder.is_dir()
    # daemon.json still readable, still state=done (reclaim doesn't rewrite completed daemons)
    data_after = json.loads((folder / "daemon.json").read_text())
    assert data_after["state"] == "done"


def test_run_emanation_timeout_calls_mark_timeout(tmp_path):
    """When timeout_event is set alongside cancel_event, the run loop calls
    mark_timeout (state=timeout) instead of mark_cancelled (state=cancelled)."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id="em-test")

    cancel = threading.Event()
    timeout_event = threading.Event()
    # Watchdog-style: set both, with timeout_event marking the cause
    timeout_event.set()
    cancel.set()

    mgr._emanations["em-test"] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation("em-test", run_dir, schemas, dispatch,
                                 "task", cancel, timeout_event)
    assert result == "[cancelled]"
    data = json.loads(run_dir.daemon_json_path.read_text())
    assert data["state"] == "timeout"
    last_event = json.loads(run_dir.events_path.read_text().splitlines()[-1])
    assert last_event["event"] == "daemon_timeout"


def test_run_emanation_manual_reclaim_calls_mark_cancelled(tmp_path):
    """When cancel_event is set WITHOUT timeout_event, the run loop calls
    mark_cancelled (the manual-reclaim semantic)."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id="em-test")

    cancel = threading.Event()
    timeout_event = threading.Event()
    # Reclaim-style: only cancel_event set
    cancel.set()

    mgr._emanations["em-test"] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    result = mgr._run_emanation("em-test", run_dir, schemas, dispatch,
                                 "task", cancel, timeout_event)
    assert result == "[cancelled]"
    data = json.loads(run_dir.daemon_json_path.read_text())
    assert data["state"] == "cancelled"
    last_event = json.loads(run_dir.events_path.read_text().splitlines()[-1])
    assert last_event["event"] == "daemon_cancelled"


def test_watchdog_sets_both_events(tmp_path):
    """Watchdog must set timeout_event before cancel_event so the run loop
    can observe the cause when it next checks."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    timeout_event = threading.Event()
    # Use a tiny timeout so the watchdog fires almost immediately
    mgr._watchdog(cancel, timeout_event, timeout=0.01)
    assert timeout_event.is_set()
    assert cancel.is_set()


def test_watchdog_returns_when_already_cancelled(tmp_path):
    """Watchdog must NOT set timeout_event when cancel_event was set first
    (manual reclaim path — timeout_event must remain unset)."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    timeout_event = threading.Event()
    cancel.set()  # simulate manual reclaim before watchdog deadline
    # Long timeout so we'd notice if it fired
    mgr._watchdog(cancel, timeout_event, timeout=60.0)
    assert cancel.is_set()
    assert not timeout_event.is_set()


# ---------------------------------------------------------------------------
# Per-emanation preset tests
# ---------------------------------------------------------------------------

def _write_preset_file(presets_dir, name, provider="deepseek", model="deepseek-v3",
                        api_key_env="DEEPSEEK_API_KEY", base_url=None):
    """Write a minimal preset JSON file to the presets directory."""
    import json
    preset = {
        "name": name,
        "description": {"summary": f"{name} preset"},
        "manifest": {
            "llm": {
                "provider": provider,
                "model": model,
                "api_key": None,
                "api_key_env": api_key_env,
                **({"base_url": base_url} if base_url else {}),
            },
            "capabilities": {"file": {}},
        },
    }
    (presets_dir / f"{name}.json").write_text(json.dumps(preset))
    return f"{name}.json"


def _make_agent_with_presets(tmp_path, presets_dir):
    """Create an agent whose init.json references a preset library."""
    from lingtai.agent import Agent
    svc = MagicMock()
    svc.provider = "mock"
    svc.model = "mock-model"
    svc.create_session = MagicMock()
    svc.make_tool_result = MagicMock()
    agent = Agent(
        svc,
        working_dir=tmp_path / "daemon-agent",
        capabilities=["file", "daemon"],
        config=AgentConfig(),
    )
    # Patch _read_init to return a manifest with a preset.path pointing to our dir
    agent._read_init = lambda: {
        "manifest": {
            "preset": {
                "active": "mock",
                "default": "mock",
                "path": str(presets_dir),
            },
            "llm": {"provider": "mock", "model": "mock-model"},
        }
    }
    # The daemon authorization gate reads the raw allowlist via the
    # side-effect-free `_read_preset_from_init` (never `_read_init`); wire
    # every preset file in the library as allowed so these fixtures'
    # per-task preset paths stay authorized.
    allowed_paths = [
        str(p) for p in sorted(presets_dir.glob("*.json"))
        if p.name != "_kernel_meta.json"
    ]
    agent._read_preset_from_init = lambda: {
        "active": "mock",
        "default": "mock",
        "allowed": allowed_paths,
    }
    return agent


# ---------------------------------------------------------------------------
# Preset authorization gate (LingTai backend only) — must run before
# load/probe/capability/run-dir/executor/dispatch. CLI backends are
# explicitly out of scope: see
# test_emanate_cli_backend_unauthorized_preset_string_unaffected below.
# ---------------------------------------------------------------------------

def test_emanate_lingtai_valid_but_unallowed_preset_rejected_before_preflight(
        tmp_path, monkeypatch):
    """A syntactically valid preset that is absent from `allowed` must be
    rejected before `load_preset`, connectivity probe, capability
    instantiation, run-dir construction, or executor submission — not just
    before dispatch."""
    from unittest.mock import patch
    from concurrent.futures import ThreadPoolExecutor
    import lingtai.kernel.preset_connectivity as preset_connectivity
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "unlisted")
    _write_preset_file(presets_dir, "listed")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    # Restrict the allowlist to only "listed" — "unlisted" is syntactically
    # valid and loadable but not authorized.
    listed_path = str(presets_dir / "listed.json")
    agent._read_preset_from_init = lambda: {
        "active": "mock", "default": "mock", "allowed": [listed_path],
    }
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    unlisted_path = str(presets_dir / "unlisted.json")

    with patch.object(agent, "load_preset") as mock_load, \
         patch.object(preset_connectivity, "check_connectivity") as mock_conn, \
         patch.object(mgr, "_instantiate_preset_capabilities") as mock_caps, \
         patch.object(DaemonRunDir, "__init__", side_effect=AssertionError(
             "DaemonRunDir must not be constructed for an unauthorized preset")), \
         patch.object(ThreadPoolExecutor, "submit") as mock_submit, \
         patch.object(mgr, "_run_emanation") as mock_run:
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "task A", "tools": ["file"], "preset": unlisted_path},
        ]})

    assert result["status"] == "error"
    assert "unlisted" in result["message"] or unlisted_path in result["message"]
    mock_load.assert_not_called()
    mock_conn.assert_not_called()
    mock_caps.assert_not_called()
    mock_submit.assert_not_called()
    mock_run.assert_not_called()
    daemons_dir = agent._working_dir / "daemons"
    assert not daemons_dir.exists() or not list(daemons_dir.iterdir())


def test_emanate_lingtai_authorized_equivalent_path_reaches_preflight(
        tmp_path, monkeypatch):
    """An authorized preset requested via the non-canonical form (tilde vs.
    absolute) must still pass the gate and reach the existing preflight —
    proving the gate normalizes rather than merely string-compares."""
    from unittest.mock import patch
    import lingtai.kernel.preset_connectivity as preset_connectivity

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "deepseek", api_key_env="DEEPSEEK_API_KEY_EQ")
    monkeypatch.setenv("DEEPSEEK_API_KEY_EQ", "sk-test")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    abs_path = presets_dir / "deepseek.json"
    agent._read_preset_from_init = lambda: {
        "active": "mock", "default": "mock", "allowed": [str(abs_path)],
    }
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    # Connectivity is mocked so the LingTai preflight completes.
    with patch.object(preset_connectivity, "_probe_host", return_value=10):
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "task A", "tools": ["file"], "preset": str(abs_path)},
        ]})

    # The gate passed — batch either dispatched or failed later in the
    # pipeline (e.g. session mocking), never with the allowlist message.
    assert "is not in this agent's allowed list" not in result.get("message", "")


def test_emanate_lingtai_omitted_preset_never_reads_allowlist(tmp_path, monkeypatch):
    """Omitted `tasks[].preset` is the documented parent-derived/no-preset
    path and must stay unchanged: the new gate must not even call
    `_read_preset_from_init` when no task requests an explicit preset."""
    from unittest.mock import patch
    agent = _make_agent(tmp_path, ["file", "daemon"])
    _reuse_parent_service(monkeypatch, agent)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_resp = MagicMock()
    mock_resp.text = "done"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                thinking_tokens=0, cached_tokens=0)
    agent.service.create_session.return_value.send = MagicMock(return_value=mock_resp)

    with patch.object(agent, "_read_preset_from_init") as mock_read:
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "task A", "tools": ["file"]},
        ]})

    mock_read.assert_not_called()
    assert result["status"] == "dispatched"


def test_emanate_cli_backend_unauthorized_preset_string_unaffected(
        tmp_path, monkeypatch):
    """CLI backends are explicitly out of scope for the LingTai allowlist
    gate: an explicit preset that would be unauthorized on the LingTai path
    must NOT be rejected by the new gate on a CLI backend — the gate must
    never run before `_handle_emanate_cli`, so CLI behavior here is
    byte-for-byte unchanged from before this patch (any per-backend CLI
    preset handling downstream of `_handle_emanate_cli`, if any, is
    unaffected)."""
    from unittest.mock import patch

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "unlisted")
    _write_preset_file(presets_dir, "listed")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    listed_path = str(presets_dir / "listed.json")
    agent._read_preset_from_init = lambda: {
        "active": "mock", "default": "mock", "allowed": [listed_path],
    }
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    unlisted_path = str(presets_dir / "unlisted.json")

    # The gate itself must never fire on the CLI path — assert the raw
    # allowlist reader is never even called.
    with patch.object(agent, "_read_preset_from_init",
                       wraps=agent._read_preset_from_init) as spy_read, \
         patch.object(mgr, "_handle_emanate_cli",
                       return_value={"status": "dispatched", "count": 1}) as mock_cli:
        result = mgr.handle({
            "action": "emanate", "backend": "codex",
            "tasks": [{"task": "task A", "tools": ["file"], "preset": unlisted_path}],
        })

    spy_read.assert_not_called()
    mock_cli.assert_called_once()
    assert result["status"] == "dispatched"


def test_emanate_with_preset_validates_preset_exists(tmp_path, monkeypatch):
    """If a per-task preset is specified but doesn't exist in the library,
    refuse THE WHOLE BATCH (no partial emanations)."""
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "deepseek")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    ghost_path = str(presets_dir / "ghost.json")
    # 'ghost' doesn't exist in the library
    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "task A", "tools": ["file"], "preset": ghost_path},
        {"task": "task B", "tools": ["file"]},  # valid task, but should be refused too
    ]})
    assert result["status"] == "error"
    assert "ghost" in result["message"]
    # No daemons spawned — whole batch refused
    daemons_dir = agent._working_dir / "daemons"
    assert not daemons_dir.exists() or not list(daemons_dir.iterdir())


def test_emanate_with_preset_unreachable_refuses(tmp_path, monkeypatch):
    """If the requested preset has connectivity 'unreachable', refuse the emanation."""
    from unittest.mock import patch
    import lingtai.kernel.preset_connectivity as preset_connectivity

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "deepseek", api_key_env="DEEPSEEK_API_KEY")

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    preset_path = str(presets_dir / "deepseek.json")
    with patch.object(preset_connectivity, "_probe_host",
                      side_effect=OSError("connection refused")):
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "task A", "tools": ["file"], "preset": preset_path},
        ]})
    assert result["status"] == "error"
    assert "unreachable" in result["message"]
    assert "deepseek" in result["message"]


def test_emanate_with_preset_no_credentials_refuses(tmp_path, monkeypatch):
    """If the requested preset has 'no_credentials', refuse the emanation."""
    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "deepseek", api_key_env="DEEPSEEK_API_KEY_MISSING_XYZ")

    # Ensure the env var is NOT set
    monkeypatch.delenv("DEEPSEEK_API_KEY_MISSING_XYZ", raising=False)

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    preset_path = str(presets_dir / "deepseek.json")
    result = mgr.handle({"action": "emanate", "tasks": [
        {"task": "task A", "tools": ["file"], "preset": preset_path},
    ]})
    assert result["status"] == "error"
    assert "no_credentials" in result["message"]
    assert "deepseek" in result["message"]


def test_emanate_with_preset_passes_through(tmp_path, monkeypatch):
    """When preset is valid and reachable, emanation is scheduled and
    daemon.json records the preset name + provider + model."""
    from unittest.mock import patch
    import lingtai.kernel.preset_connectivity as preset_connectivity

    presets_dir = tmp_path / "presets"
    presets_dir.mkdir()
    _write_preset_file(presets_dir, "deepseek", provider="deepseek",
                       model="deepseek-v3", api_key_env="DEEPSEEK_API_KEY_TEST")

    monkeypatch.setenv("DEEPSEEK_API_KEY_TEST", "sk-test-key")

    agent = _make_agent_with_presets(tmp_path, presets_dir)
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")

    mock_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "task done — finished successfully"
    mock_resp.tool_calls = []
    mock_resp.usage = MagicMock(input_tokens=0, output_tokens=0,
                                thinking_tokens=0, cached_tokens=0)
    mock_session.send = MagicMock(return_value=mock_resp)

    # The preset's LLMService will call create_session — mock at the class level
    preset_svc = MagicMock()
    preset_svc.create_session = MagicMock(return_value=mock_session)
    preset_svc.make_tool_result = MagicMock(return_value="mock_result")
    preset_svc._base_url = "https://mock.deepseek.com"

    preset_path = str(presets_dir / "deepseek.json")
    with patch.object(preset_connectivity, "_probe_host", return_value=42),\
         patch("lingtai.llm.service.LLMService", return_value=preset_svc):
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "find todos", "tools": ["file"], "preset": preset_path},
        ]})

    assert result["status"] == "dispatched"
    assert result["count"] == 1

    # Wait for completion
    time.sleep(1.5)

    # Check daemon.json records preset metadata
    daemons_dir = agent._working_dir / "daemons"
    folders = list(daemons_dir.iterdir())
    assert len(folders) == 1
    data = json.loads((folders[0] / "daemon.json").read_text())
    assert data.get("preset_name") == preset_path
    assert data.get("preset_provider") == "deepseek"
    assert data.get("preset_model") == "deepseek-v3"


def test_emanate_without_preset_inherits_parent(tmp_path, monkeypatch):
    """Omitted preset inherits parent effective identity without allowlist reads."""
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "anthropic"
    agent.service.model = "claude-opus-4-8"
    agent.service._base_url = "https://api.anthropic.com"
    agent.service._context_window = 200000
    agent.service._key_resolver = lambda provider: "token"
    agent.service._provider_defaults = {"anthropic": {"max_rpm": 5}}
    agent.inbox = queue.Queue()
    mgr = agent.get_capability("daemon")
    from unittest.mock import patch
    from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
    records = []

    def fake_owner(self, request, *, capsule=None):
        manifest = json.loads(Path(request.manifest_path).read_text(encoding="utf-8"))
        run_dir = DaemonRunDir.attach(Path(manifest["run_dir"]))
        run_dir.update_state(owner="supervisor", supervisor_pid=os.getpid())
        run_dir.mark_done("[fake detached done]")
        records.append((manifest, run_dir))

    with patch.object(PosixDaemonSupervisorAdapter, "spawn_detached", fake_owner), \
         patch.object(agent, "_read_preset_from_init") as read_allowlist:
        result = mgr.handle({"action": "emanate", "tasks": [
            {"task": "task A", "tools": ["file"]},
        ]})

    assert result["status"] == "dispatched"
    state = _poll_daemon_terminal(records[0][1])
    manifest = records[0][0]
    read_allowlist.assert_not_called()
    agent.service.create_session.assert_not_called()
    assert manifest["llm"]["provider"] == "anthropic"
    assert manifest["llm"]["model"] == "claude-opus-4-8"
    assert manifest["llm"]["base_url"] == "https://api.anthropic.com"
    assert manifest.get("preset_name") is None
    assert "## Available host tools" in records[0][1].prompt_path.read_text(encoding="utf-8")
    assert state["preset_name"] is None

    # daemon.json has no preset_name (None)
    daemons_dir = agent._working_dir / "daemons"
    folders = list(daemons_dir.iterdir())
    assert len(folders) == 1
    data = json.loads((folders[0] / "daemon.json").read_text())
    assert data.get("preset_name") is None


def test_claude_code_env_strips_auth_overrides(monkeypatch):
    """Spawned claude-code processes must not inherit auth overrides.

    ANTHROPIC_* force the CLI off the user's Claude Code subscription onto
    API billing (GH #107); a stale CLAUDE_CODE_OAUTH_TOKEN can override a
    refreshed credentials.json and look like a false weekly limit (GH #189).
    """
    from lingtai.tools.daemon import _claude_code_env, _CLAUDE_CODE_STRIP_ENV

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leaked")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "oauth-leaked")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-claude-code-oauth")
    # A detached execution child may have restored selected parent credentials;
    # that transport fact must not override the runner's established policy.
    monkeypatch.setenv("LINGTAI_DAEMON_CREDENTIALS_RESTORED", "1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # sentinel non-stripped var
    monkeypatch.setenv("HOME", "/tmp/home")

    env = _claude_code_env()

    for key in _CLAUDE_CODE_STRIP_ENV:
        assert key not in env, f"{key} should be stripped from claude-code env"
    # Non-auth vars must pass through unchanged so claude can still find HOME,
    # PATH, CLAUDE_CONFIG_DIR, etc.
    assert env.get("PATH") == "/usr/bin:/bin"
    assert env.get("HOME") == "/tmp/home"


def test_claude_code_env_noop_when_unset(monkeypatch):
    """When no Claude auth override vars are set, sanitized env equals os.environ."""
    import os
    from lingtai.tools.daemon import _claude_code_env, _CLAUDE_CODE_STRIP_ENV

    for key in _CLAUDE_CODE_STRIP_ENV:
        monkeypatch.delenv(key, raising=False)

    env = _claude_code_env()
    assert env == os.environ


# ---------------------------------------------------------------------------
# CLI-backend ask: non-blocking dispatch + concurrent-ask guard (GH issue:
# daemon(ask) hanging the parent agent's tool turn). The handlers must
# return promptly even when the resumed `claude --resume` / `codex exec
# resume` process is slow/hangs, and a second ask while one is in flight
# must be refused with a clear busy error.
# ---------------------------------------------------------------------------


class _FakeStream:
    """A line-iterable stream that the test can append to live."""

    def __init__(self):
        import threading as _t
        self._lock = _t.Lock()
        self._lines: list[str] = []
        self._closed = False
        self._cond = _t.Condition(self._lock)

    def feed(self, line: str) -> None:
        with self._cond:
            self._lines.append(line)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def __iter__(self):
        return self

    def __next__(self):
        with self._cond:
            while not self._lines and not self._closed:
                self._cond.wait()
            if self._lines:
                return self._lines.pop(0)
            raise StopIteration


class _FakeProc:
    """Subprocess.Popen stand-in with controllable stdout/stderr."""

    def __init__(self):
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.returncode: int | None = None
        self.pid = 0  # _kill_process_group uses pid as pgid, but we override it
        self._wait_evt = threading.Event()

    def finish(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout.close()
        self.stderr.close()
        self._wait_evt.set()

    def wait(self, timeout=None):
        if not self._wait_evt.wait(timeout=timeout):
            import subprocess as _sp
            raise _sp.TimeoutExpired(cmd=["fake"], timeout=timeout)
        return self.returncode

    def poll(self):
        return self.returncode


def _install_fake_popen(monkeypatch, proc: _FakeProc):
    """Replace subprocess.Popen inside the daemon module with one that
    returns `proc` on next call, and neutralize _kill_process_group so it
    doesn't try to signal pid 0.
    """
    from lingtai.tools import daemon as daemon_mod

    def fake_popen(cmd, **kwargs):
        return proc

    monkeypatch.setattr(daemon_mod.subprocess, "Popen", fake_popen)
    # Don't actually os.killpg(0) — just mark the proc finished as if killed.
    monkeypatch.setattr(daemon_mod, "_kill_process_group",
                        lambda p: p.finish(returncode=-15))
    # Codex now uses the daemon-local POSIX Port, whose adapter owns the
    # concrete kill operation rather than the legacy daemon helper.
    monkeypatch.setattr(
        "lingtai.tools.daemon.posix_process.PosixDaemonProcessPort._terminate",
        lambda _adapter, p, reason, pgid=None, **_kwargs: p.finish(returncode=-15),
    )


def _cli_entry(mgr, agent, em_id: str, backend: str, session_id: str) -> dict:
    """Register an em-N entry as if it had been spawned by _handle_emanate_cli,
    with a real run_dir whose <backend>_session_id is already populated.
    """
    run_dir = _make_run_dir(agent, em_id=em_id)
    # Simulate the streaming handler having captured the session id.
    if backend == "claude-code":
        run_dir._state["claude_session_id"] = session_id
    elif backend == "codex":
        run_dir._state["codex_session_id"] = session_id
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    entry = {
        "future": MagicMock(done=MagicMock(return_value=False)),
        "task": "primary task",
        "start_time": time.time(),
        "cancel_event": threading.Event(),
        "timeout_event": threading.Event(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
        "backend": backend,
        "ask_in_flight": False,
        "ask_future": None,
    }
    mgr._emanations[em_id] = entry
    return entry


def test_ask_claude_code_returns_immediately_when_subprocess_hangs(tmp_path, monkeypatch):
    """`daemon(ask)` against a claude-code emanation must not block the
    parent's tool turn even when the resumed subprocess is slow."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    _cli_entry(mgr, agent, "em-1", "claude-code", "claude-sess-abc")

    t0 = time.monotonic()
    result = mgr._handle_ask("em-1", "follow-up please")
    elapsed = time.monotonic() - t0

    # The handler must return synchronously and quickly — the subprocess
    # is still hanging (no stdout fed, no finish() called). Generous bound
    # so this passes on a loaded CI box but still fails the regression
    # (which blocked for up to self._timeout seconds, default 3600).
    assert elapsed < 1.0, f"ask blocked for {elapsed:.2f}s"
    assert result["status"] == "sent"
    assert result.get("async") is True
    assert result["id"] == "em-1"

    # The ask is tracked as in-flight until the worker observes EOF.
    assert mgr._emanations["em-1"]["ask_in_flight"] is True
    ask_future = mgr._emanations["em-1"]["ask_future"]
    assert ask_future is not None and not ask_future.done()

    # Drive the fake subprocess to completion and let the worker drain.
    proc.stdout.feed(
        '{"type":"result","result":"all done","is_error":false}\n'
    )
    proc.finish(returncode=0)
    ask_future.result(timeout=5)

    # Worker cleared the in-flight flag and persisted progress to the run_dir.
    assert mgr._emanations["em-1"]["ask_in_flight"] is False
    run_dir = mgr._emanations["em-1"]["run_dir"]
    assert run_dir._state.get("last_output") is not None
    # The dispatched marker + the assistant/result text should both have
    # landed as cli_output events.
    events_text = run_dir.events_path.read_text()
    assert "ask dispatched" in events_text


def test_ask_claude_code_second_ask_is_busy(tmp_path, monkeypatch):
    """While an ask is in flight, a second concurrent ask returns busy."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    _cli_entry(mgr, agent, "em-1", "claude-code", "claude-sess-abc")

    first = mgr._handle_ask("em-1", "first follow-up")
    assert first["status"] == "sent"

    second = mgr._handle_ask("em-1", "second follow-up")
    assert second["status"] == "busy"
    assert "still" in second["message"].lower()
    assert second["id"] == "em-1"

    # Let the first one finish so the test teardown is clean.
    proc.finish(returncode=0)
    mgr._emanations["em-1"]["ask_future"].result(timeout=5)

    # After it clears, another ask should succeed.
    proc2 = _FakeProc()
    _install_fake_popen(monkeypatch, proc2)
    third = mgr._handle_ask("em-1", "third follow-up")
    assert third["status"] == "sent"
    proc2.finish(returncode=0)
    mgr._emanations["em-1"]["ask_future"].result(timeout=5)


def test_ask_codex_returns_immediately_when_subprocess_hangs(tmp_path, monkeypatch):
    """Codex ask must also be non-blocking."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    _cli_entry(mgr, agent, "em-1", "codex", "codex-thread-xyz")

    t0 = time.monotonic()
    result = mgr._handle_ask("em-1", "what next?")
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f"codex ask blocked for {elapsed:.2f}s"
    assert result["status"] == "sent"
    assert result.get("async") is True

    assert mgr._emanations["em-1"]["ask_in_flight"] is True
    ask_future = mgr._emanations["em-1"]["ask_future"]

    # Drive to completion with a synthetic codex JSONL stream.
    proc.stdout.feed(
        '{"type":"item.completed","item":{"type":"agent_message","text":"reply text"}}\n'
    )
    proc.stdout.feed('{"type":"turn.completed"}\n')
    proc.finish(returncode=0)
    ask_future.result(timeout=5)

    assert mgr._emanations["em-1"]["ask_in_flight"] is False


def test_ask_codex_second_ask_is_busy(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    _cli_entry(mgr, agent, "em-1", "codex", "codex-thread-xyz")

    first = mgr._handle_ask("em-1", "first follow-up")
    assert first["status"] == "sent"

    second = mgr._handle_ask("em-1", "second follow-up")
    assert second["status"] == "busy"

    proc.finish(returncode=0)
    mgr._emanations["em-1"]["ask_future"].result(timeout=5)


def test_ask_claude_code_missing_session_id_still_synchronous(tmp_path):
    """If the session id hasn't been captured yet, the pre-flight check
    must still respond immediately (no subprocess spawn, no busy flag)."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    # Build an entry whose run_dir has NO claude_session_id.
    run_dir = _make_run_dir(agent, em_id="em-1")
    mgr._emanations["em-1"] = {
        "future": MagicMock(done=MagicMock(return_value=False)),
        "task": "x",
        "start_time": time.time(),
        "cancel_event": threading.Event(),
        "timeout_event": threading.Event(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
        "backend": "claude-code",
        "ask_in_flight": False,
        "ask_future": None,
    }
    result = mgr._handle_ask("em-1", "anything")
    assert result["status"] == "error"
    assert "session" in result["message"].lower()
    # Must not have flipped the in-flight flag on a pre-flight failure.
    assert mgr._emanations["em-1"]["ask_in_flight"] is False


def test_ask_lingtai_backend_unchanged(tmp_path):
    """The builtin lingtai backend ask still buffers into followup_buffer
    and is unaffected by the CLI-ask refactor."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    mgr._emanations["em-1"] = {
        "future": MagicMock(done=MagicMock(return_value=False)),
        "task": "x",
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": None,
        # No 'backend' key → routes to the in-process followup buffer path.
    }
    result = mgr._handle_ask("em-1", "buffered follow-up")
    assert result["status"] == "sent"
    assert mgr._emanations["em-1"]["followup_buffer"] == "buffered follow-up"


def test_ask_claude_code_reclaim_suppresses_followup_notification(tmp_path, monkeypatch):
    """If the emanation is reclaimed while a CLI ask is mid-flight, the
    worker must NOT publish a follow-up notification when it eventually
    exits — the parent has already torn the entry down."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    entry = _cli_entry(mgr, agent, "em-1", "claude-code", "claude-sess-abc")
    # Register a fake pool entry so _handle_reclaim's pool.shutdown loop
    # doesn't hit an empty list (the cli entry helper doesn't add one).
    mgr._pools = [(MagicMock(), threading.Event())]
    mgr._cli_procs.append(proc)  # so reclaim's kill loop targets it too

    # Count published notifications + log events.
    published: list[tuple] = []
    monkeypatch.setattr(
        mgr, "_publish_daemon_notification",
        lambda em_id, *, status, text, run_dir=None: published.append((em_id, status)),
    )
    logged: list[tuple] = []
    real_log = mgr._log
    def _log_capture(event, **fields):
        logged.append((event, fields))
        return real_log(event, **fields)
    monkeypatch.setattr(mgr, "_log", _log_capture)

    # Dispatch the ask — returns immediately, worker is blocked on stdout.
    result = mgr._handle_ask("em-1", "anything")
    assert result["status"] == "sent"
    ask_future = entry["ask_future"]
    assert not ask_future.done()

    # Reclaim races the in-flight ask. _handle_reclaim kills _cli_procs
    # (which marks the fake proc finished via the patched _kill_process_group).
    mgr._handle_reclaim()
    assert "em-1" not in mgr._emanations

    # Worker finishes its drain after the kill — it would otherwise have
    # published a "follow-up failed" notification on the non-zero returncode.
    ask_future.result(timeout=5)

    assert published == [], (
        f"reclaimed ask must not publish follow-up notifications, got {published}"
    )
    post_reclaim_logs = [e for e, _ in logged if e == "daemon_ask_post_reclaim"]
    assert post_reclaim_logs, (
        "expected a daemon_ask_post_reclaim log event when worker tried to "
        f"publish after reclaim; got {[e for e, _ in logged]}"
    )


def _run_silent_subprocess_ask_test(tmp_path, monkeypatch, backend: str):
    """Shared body for the claude-code + codex silent-subprocess tests.

    Models the regression: the resumed CLI is spawned but never writes a
    single byte to stdout and never exits. Before this fix, the worker's
    `for raw_line in proc.stdout` blocked the worker thread forever; the
    `if time.monotonic() > deadline` check inside the loop never ran.
    The fix routes stdout through a daemon reader thread + `queue.get`
    with a deadline, so the worker observes the timeout regardless.
    """
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    # Make the test fast — without this the default 3600s timeout would
    # itself be the bug we'd be reproducing.
    mgr._timeout = 0.5

    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    session_id = ("claude-sess-silent" if backend == "claude-code"
                  else "codex-thread-silent")
    entry = _cli_entry(mgr, agent, "em-1", backend, session_id)

    published: list[tuple] = []
    monkeypatch.setattr(
        mgr, "_publish_daemon_notification",
        lambda em_id, *, status, text, run_dir=None:
            published.append((em_id, status, text)),
    )
    logged: list[tuple] = []
    real_log = mgr._log
    def _log_capture(event, **fields):
        logged.append((event, fields))
        return real_log(event, **fields)
    monkeypatch.setattr(mgr, "_log", _log_capture)

    t0 = time.monotonic()
    result = mgr._handle_ask("em-1", "anything")
    dispatch_elapsed = time.monotonic() - t0

    # Dispatcher itself must still return promptly even with a silent
    # subprocess (this part already worked before the fix).
    assert dispatch_elapsed < 1.0
    assert result["status"] == "sent"
    assert result["async"] is True

    ask_future = entry["ask_future"]
    # The worker should observe the deadline (mgr._timeout=0.5s) and
    # finish — without the fix this future.result(timeout=3) would itself
    # time out because the worker is blocked on proc.stdout iteration.
    worker_result = ask_future.result(timeout=3.0)

    total_elapsed = time.monotonic() - t0
    assert total_elapsed < 2.5, (
        f"worker took {total_elapsed:.2f}s — likely still blocked on stdout. "
        "Did _iter_stdout_with_deadline get bypassed?"
    )

    # Worker must report timeout error and clean up everything.
    assert worker_result["status"] == "error"
    assert "timed out" in worker_result["message"]
    assert entry["ask_in_flight"] is False, (
        "ask_in_flight must clear so a subsequent ask isn't permanently busy"
    )
    assert proc not in mgr._cli_procs, (
        "proc must be removed from _cli_procs so reclaim/list see clean state"
    )

    # The fake _kill_process_group (installed by _install_fake_popen)
    # marks the proc finished with returncode=-15 (simulating SIGTERM).
    # The real implementation actually SIGTERMs the process group.
    assert proc.returncode is not None, "subprocess must have been killed"

    # Publish a "follow-up failed" notification so the parent agent sees
    # the timeout instead of nothing.
    failures = [p for p in published if p[1] == "follow-up failed"]
    assert failures, f"expected a follow-up failed notification, got {published}"
    assert "timed out" in failures[0][2]

    # No post-reclaim log — reclaim didn't happen, the entry is still live.
    post_reclaim_logs = [e for e, _ in logged if e == "daemon_ask_post_reclaim"]
    assert not post_reclaim_logs


def test_ask_claude_code_silent_subprocess_enforces_timeout(tmp_path, monkeypatch):
    """REGRESSION: a silent `claude --resume` subprocess (no stdout, never
    exits) must NOT hang the ask worker indefinitely. Worker must observe
    self._timeout, kill the proc group, clear ask_in_flight, and publish
    a follow-up failed notification."""
    _run_silent_subprocess_ask_test(tmp_path, monkeypatch, "claude-code")


def test_ask_codex_silent_subprocess_enforces_timeout(tmp_path, monkeypatch):
    """REGRESSION: same as the claude-code case but for
    `codex exec resume`. Symmetric worker, same fix path."""
    _run_silent_subprocess_ask_test(tmp_path, monkeypatch, "codex")


def test_ask_stream_workers_reuse_shared_stderr_drainer(tmp_path, monkeypatch):
    """The subprocess ask workers share the runtime stderr drainer.

    This pins the narrow dedupe: helper thread names stay backend-specific,
    the worker still joins the drain handle, and failure messages still use
    the captured stderr tail.
    """
    from lingtai.tools import daemon as daemon_mod

    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    published: list[tuple] = []
    monkeypatch.setattr(
        mgr, "_publish_daemon_notification",
        lambda em_id, *, status, text, run_dir=None:
            published.append((em_id, status, text)),
    )

    drains = []
    calls = []

    class FakeDrain:
        def __init__(self, lines):
            self.lines = lines
            self.joined = False
            self.join_timeout = None

        def join(self, timeout=2.0):
            self.joined = True
            self.join_timeout = timeout

    def fake_spawn_stderr_drainer(proc, run_dir, *, thread_name):
        drain = FakeDrain([f"{thread_name}: stderr tail"])
        drains.append(drain)
        calls.append((proc, run_dir, thread_name))
        return drain

    monkeypatch.setattr(
        daemon_mod, "_spawn_stderr_drainer", fake_spawn_stderr_drainer,
    )
    # Codex's process Port owns its stderr mechanism; keep this shared
    # behavior test's synthetic drain observable at the new patch point too.
    monkeypatch.setattr(
        mgr._process_port, "drain_stderr",
        lambda handle, *, on_line=None, thread_name="daemon-stderr":
            fake_spawn_stderr_drainer(handle, None, thread_name=thread_name),
    )

    cases = [
        (
            "em-claude",
            "claude-code",
            "daemon-claude-ask-stderr-em-claude",
            lambda entry, proc, run_dir:
                mgr._run_ask_claude_code_stream(
                    "em-claude", entry, proc, run_dir,
                ),
        ),
        (
            "em-codex",
            "codex",
            "daemon-codex-ask-stderr-em-codex",
            lambda entry, proc, run_dir:
                mgr._run_ask_codex_stream("em-codex", entry, proc, run_dir),
        ),
        (
            "em-opencode",
            "opencode",
            "daemon-opencode-ask-stderr-em-opencode",
            lambda entry, proc, run_dir:
                mgr._run_ask_opencode_stream(
                    "em-opencode", entry, proc, run_dir, "opencode",
                ),
        ),
        (
            "em-cursor",
            "cursor",
            "daemon-cursor-ask-stderr-em-cursor",
            lambda entry, proc, run_dir:
                mgr._run_ask_cursor_stream("em-cursor", entry, proc, run_dir),
        ),
    ]

    for em_id, backend, expected_thread_name, run_worker in cases:
        proc = _FakeProc()
        proc.finish(returncode=1)
        run_dir = _make_run_dir(agent, em_id=em_id)
        entry = {
            "future": MagicMock(done=MagicMock(return_value=False)),
            "task": "x",
            "start_time": time.time(),
            "cancel_event": threading.Event(),
            "timeout_event": threading.Event(),
            "followup_buffer": "",
            "followup_lock": threading.Lock(),
            "run_dir": run_dir,
            "backend": backend,
            "ask_in_flight": True,
            "ask_future": None,
        }
        mgr._emanations[em_id] = entry

        worker_handle = proc
        if backend in {"claude-code", "codex", "opencode", "cursor"}:
            # All headless ask workers receive opaque Port handles.
            monkeypatch.setattr(
                "lingtai.tools.daemon.posix_process.subprocess.Popen",
                lambda *args, **kwargs: proc,
            )
            worker_handle = mgr._process_port.spawn(
                DaemonProcessCommand((backend,), agent._working_dir),
            )
        result = run_worker(entry, worker_handle, run_dir)

        assert result["status"] == "error"
        assert expected_thread_name in result["message"]
        assert entry["ask_in_flight"] is False

    assert [thread_name for _, _, thread_name in calls] == [
        case[2] for case in cases
    ]
    assert all(drain.joined for drain in drains)
    assert all(drain.join_timeout == 2.0 for drain in drains)
    assert len(published) == len(cases)


def test_ask_worker_exception_is_logged(tmp_path, monkeypatch):
    """An unexpected exception in the ask worker must be logged via
    daemon_ask_worker_error and recorded into the run_dir as a cli_output
    line so daemon(check) shows what happened."""
    agent = _make_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    proc = _FakeProc()
    _install_fake_popen(monkeypatch, proc)
    entry = _cli_entry(mgr, agent, "em-1", "claude-code", "claude-sess-abc")

    # Replace the worker with one that raises immediately. _on_ask_done
    # runs as the future's done-callback so the exception is surfaced
    # rather than swallowed.
    def boom(em_id, entry, handle, run_dir):
        raise RuntimeError("simulated worker crash")
    monkeypatch.setattr(mgr, "_run_ask_claude_code_stream", boom)

    logged: list[tuple] = []
    real_log = mgr._log
    def _log_capture(event, **fields):
        logged.append((event, fields))
        return real_log(event, **fields)
    monkeypatch.setattr(mgr, "_log", _log_capture)

    mgr._handle_ask("em-1", "anything")
    ask_future = entry["ask_future"]

    # Wait for the worker future AND its done-callback to run.
    try:
        ask_future.result(timeout=5)
    except RuntimeError:
        pass  # expected
    # add_done_callback runs synchronously after .result returns/raises,
    # but be tolerant of scheduling.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if any(e == "daemon_ask_worker_error" for e, _ in logged):
            break
        time.sleep(0.05)

    err_logs = [f for e, f in logged if e == "daemon_ask_worker_error"]
    assert err_logs, f"expected daemon_ask_worker_error, got {[e for e, _ in logged]}"
    assert err_logs[0]["em_id"] == "em-1"
    assert err_logs[0]["exception"] == "RuntimeError"
    assert "simulated worker crash" in err_logs[0]["message"]

    # Run_dir should have a stderr cli_output line marking the worker error.
    events_text = entry["run_dir"].events_path.read_text()
    assert "[ask worker error]" in events_text
    assert "RuntimeError" in events_text

    # ask_in_flight cleared even though worker raised before its finally
    # would have run.
    assert entry["ask_in_flight"] is False


# ---------------------------------------------------------------------------
# Preset-driven daemon provider defaults — ``codex_auth_path`` propagation.
# A preset/manifest that points an agent at its own Codex OAuth token file must
# carry that path into daemon-scoped LLM services so preset-driven daemon work
# uses the same auth file (true multiple Codex accounts).
# ---------------------------------------------------------------------------


def test_daemon_llm_defaults_carries_codex_auth_path():
    """The preset manifest.llm allowlist includes ``codex_auth_path``."""
    from lingtai.tools.daemon import DaemonManager

    defaults = DaemonManager._llm_defaults_from_manifest(
        {
            "provider": "codex",
            "model": "gpt-5.5",
            "codex_auth_path": "/secrets/alice/codex-auth.json",
            # An unrelated key must still be dropped (allowlist, not pass-all).
            "api_key": "should-not-survive",
        }
    )
    assert defaults["codex_auth_path"] == "/secrets/alice/codex-auth.json"
    assert "api_key" not in defaults


def test_daemon_provider_defaults_preserves_codex_auth_path(tmp_path):
    """Daemon-scoped Codex defaults keep the agent's ``codex_auth_path``.

    The daemon overrides the cache-affinity anchor with the per-run daemon path
    (its own cache slot), but the chosen token file must survive so daemon
    traffic authenticates against the same Codex account.
    """
    from types import SimpleNamespace

    from lingtai.tools.daemon import DaemonManager

    run_dir = SimpleNamespace(path=tmp_path / "run")
    mgr = DaemonManager.__new__(DaemonManager)
    out = DaemonManager._daemon_provider_defaults(
        mgr,
        "codex",
        {
            "codex_auth_path": "/secrets/alice/codex-auth.json",
            "codex_session_anchor": "/agents/alice/init.json",
        },
        run_dir,
    )
    assert out["codex"]["codex_auth_path"] == "/secrets/alice/codex-auth.json"
    # The per-run daemon anchor replaces the parent agent's anchor.
    assert out["codex"]["codex_session_anchor"] == str(
        (run_dir.path / "daemon.json").resolve()
    )


def _assert_codex_pool_daemon_defaults(provider, tmp_path):
    """codex-pool daemon defaults keep the pool path, re-anchor to daemon.json.

    ``codex-pool`` reuses the Codex adapter and also seeds its sticky auth-pool
    choice off the anchor, so a daemon run must keep the non-secret
    ``codex_auth_pool_path`` while getting its own per-run anchor (its own cache
    slot and an independent pool selection from the parent).
    """
    from types import SimpleNamespace

    from lingtai.tools.daemon import DaemonManager

    run_dir = SimpleNamespace(path=tmp_path / "run")
    mgr = DaemonManager.__new__(DaemonManager)
    out = DaemonManager._daemon_provider_defaults(
        mgr,
        provider,
        {
            "codex_auth_pool_path": "/home/alice/.lingtai-tui/codex-auth-pool.json",
            "codex_session_anchor": "/agents/alice/init.json",
        },
        run_dir,
    )
    assert out[provider]["codex_auth_pool_path"] == (
        "/home/alice/.lingtai-tui/codex-auth-pool.json"
    )
    # The per-run daemon anchor replaces the parent agent's anchor.
    assert out[provider]["codex_session_anchor"] == str(
        (run_dir.path / "daemon.json").resolve()
    )


def test_daemon_provider_defaults_codex_pool_dash(tmp_path):
    _assert_codex_pool_daemon_defaults("codex-pool", tmp_path)


def test_daemon_provider_defaults_codex_pool_underscore(tmp_path):
    _assert_codex_pool_daemon_defaults("codex_pool", tmp_path)


def _capturing_fake_service(captured, text="daemon done"):
    """Build a FakeService class that records its init kwargs into *captured*."""
    class FakeService:
        def __init__(self, **kwargs):
            captured["init"] = kwargs
            self.model = kwargs["model"]
            self.provider = kwargs["provider"]

        def create_session(self, **kwargs):
            mock_session = MagicMock()
            mock_response = MagicMock()
            mock_response.text = text
            mock_response.tool_calls = []
            mock_response.usage = MagicMock(
                input_tokens=0, output_tokens=0,
                thinking_tokens=0, cached_tokens=0,
            )
            mock_session.send = MagicMock(return_value=mock_response)
            return mock_session

    return FakeService


def test_run_emanation_no_preset_uses_parent_api_key_without_resolver(
    tmp_path, monkeypatch
):
    """No-preset daemon uses the implicit parent preset's direct api_key.

    Jason's effective-preset design: a daemon with no explicit preset runs from
    the parent's existing LLM configuration as an implicit preset. The parent's
    already-resolved ``api_key`` is used directly; the daemon execution layer
    must NOT call the parent's ``_key_resolver`` to derive the primary key — not
    even when the parent's direct api_key is ``None`` (the prior bespoke
    fallback resolver path that this change removes). Make the resolver raise so
    any primary-key resolution attempt fails the test.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "custom"
    agent.service.model = "glm-5.1"
    agent.service._base_url = "https://proxy.example/v1"
    agent.service._context_window = 200000

    def exploding_resolver(provider):
        raise AssertionError(
            "daemon must not call parent _key_resolver for the primary key"
        )

    agent.service._key_resolver = exploding_resolver
    # No direct api_key on the parent: the old bespoke no-preset branch would
    # fall back to calling the resolver here. The effective-preset path must not.
    agent.service._api_key = None
    agent.service.api_key = None
    agent.service._provider_defaults = {"custom": {"api_compat": "openai"}}

    captured = {}
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", _capturing_fake_service(captured))

    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    em_id = "em-implicit"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    # The resolver raising would surface as an exception here if consulted for
    # the primary key.
    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch, "x", cancel)

    assert result == "daemon done"
    agent.service.create_session.assert_not_called()
    # Primary key is the parent's direct effective key (None here) — no resolver
    # fallback. The old branch would have raised by calling the resolver.
    assert captured["init"]["api_key"] is None
    # The resolver is still carried for on-demand adapters of *other* providers,
    # but was never invoked (it would have raised).
    assert captured["init"]["key_resolver"] is exploding_resolver


def test_run_emanation_no_preset_preserves_parent_provider_defaults(
    tmp_path, monkeypatch
):
    """Implicit preset path carries the parent provider_defaults bucket verbatim.

    Includes a field outside the manifest allowlist (``codex_base_urls``) to
    prove the implicit path forwards the parent bucket directly rather than
    re-deriving it through ``_llm_defaults_from_manifest``.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "custom"
    agent.service.model = "glm-5.1"
    agent.service._base_url = "https://proxy.example/v1"
    agent.service._context_window = 200000
    agent.service._key_resolver = lambda provider: "token"
    agent.service._api_key = "sk-effective"
    agent.service.api_key = "sk-effective"
    agent.service._provider_defaults = {
        "custom": {
            "api_compat": "openai",
            "max_rpm": 9,
            "default_headers": {"x-test": "1"},
            # Outside the _llm_defaults_from_manifest allowlist on purpose.
            "codex_base_urls": ["https://a.example", "https://b.example"],
        }
    }

    captured = {}
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", _capturing_fake_service(captured))

    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    em_id = "em-defaults"
    schemas, dispatch = mgr._build_tool_surface(["file"])
    run_dir = _make_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    result = mgr._run_emanation(em_id, run_dir, schemas, dispatch, "x", cancel)

    assert result == "daemon done"
    # The whole parent bucket survives — including codex_base_urls, which the
    # manifest allowlist would have dropped (no Codex anchor for non-codex).
    assert captured["init"]["provider_defaults"] == {
        "custom": {
            "api_compat": "openai",
            "max_rpm": 9,
            "default_headers": {"x-test": "1"},
            "codex_base_urls": ["https://a.example", "https://b.example"],
        }
    }


def test_implicit_parent_preset_llm_does_not_resolve_primary_key(tmp_path):
    """``_implicit_parent_preset_llm`` reads the parent's effective key directly.

    It must not invoke the parent ``_key_resolver`` to synthesize the implicit
    preset's primary key — the parent already resolved it at boot.
    """
    agent = _make_agent(tmp_path, ["file", "daemon"])
    agent.service.provider = "anthropic"
    agent.service.model = "claude-opus-4-8"
    agent.service._base_url = "https://api.anthropic.com"
    agent.service._context_window = 200000
    agent.service.api_key = "sk-effective"

    def exploding_resolver(provider):
        raise AssertionError("must not resolve the primary key here")

    agent.service._key_resolver = exploding_resolver
    agent.service._provider_defaults = {"anthropic": {"max_rpm": 5}}

    mgr = agent.get_capability("daemon")
    preset = mgr._implicit_parent_preset_llm()

    assert preset["provider"] == "anthropic"
    assert preset["model"] == "claude-opus-4-8"
    assert preset["api_key"] == "sk-effective"
    assert preset["base_url"] == "https://api.anthropic.com"
    assert preset["context_window"] == 200000
    assert preset["key_resolver"] is exploding_resolver
    assert preset["_provider_defaults"] == {"max_rpm": 5}


class _SummaryRunDir:
    def __init__(self):
        self.calls = []

    def append_tokens(self, **kwargs):
        self.calls.append(kwargs)


class _SummarySession:
    def __init__(self, response):
        self.response = response
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return self.response


class _SummaryService:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.session = _SummarySession(response)

    def create_session(self, **kwargs):
        self.calls.append(kwargs)
        return self.session


def test_daemon_summary_closure_uses_effective_session_and_accounts_usage(tmp_path):
    usage = UsageMetadata(input_tokens=17, output_tokens=5, thinking_tokens=2, cached_tokens=3)
    response = LLMResponse(text="LOCAL SUMMARY", tool_calls=[], usage=usage)
    service = _SummaryService(response)
    run_dir = _SummaryRunDir()
    fn = daemon_tool._build_daemon_apriori_summarizer_fn(
        service, run_dir, provider="mock", model="daemon-model", endpoint="endpoint",
    )

    assert fn("SUMMARY SYSTEM", "SUMMARY USER", "read", "tc-summary") == "LOCAL SUMMARY"
    assert service.calls == [{
        "system_prompt": "SUMMARY SYSTEM",
        "tools": None,
        "model": "daemon-model",
        "tracked": False,
        "provider": "mock",
    }]
    assert service.session.messages == ["SUMMARY USER"]
    assert run_dir.calls == [{
        "input": 17, "output": 5, "thinking": 2, "cached": 3,
        "model": "daemon-model", "endpoint": "endpoint",
        "usage_extra": {},
    }]


def test_daemon_tool_executor_wires_summary_gateway_and_preserves_raw_log(tmp_path, monkeypatch):
    agent = _make_agent(tmp_path, ["file", "daemon"])
    mgr = agent.get_capability("daemon")
    raw = {"content": "DAEMON-RAW-MARKER"}
    agent._tool_handlers["read"] = MagicMock(return_value=raw)
    first = LLMResponse(
        text="",
        tool_calls=[ToolCall(name="read", args={"file_path": "/tmp/x", "summary": True, "reasoning": "retain marker"}, id="tc-daemon-summary")],
        usage=None,
    )
    final = LLMResponse(text="daemon finished", tool_calls=[], usage=None)
    summary = LLMResponse(
        text="DAEMON-SUMMARY-MARKER", tool_calls=[],
        usage=UsageMetadata(input_tokens=11, output_tokens=7, thinking_tokens=1, cached_tokens=2),
    )
    service = _CanonicalFakeService([[first, final], [summary]])
    import lingtai.llm.service as service_mod
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    run_dir = _make_run_dir(agent, em_id="em-summary")
    mgr._emanations["em-summary"] = {
        "followup_buffer": "", "followup_lock": threading.Lock(), "run_dir": run_dir,
    }
    # Match the detached supervisor's run-local log sink.
    monkeypatch.setattr(agent, "_log", run_dir.append_event)

    result = mgr._run_emanation(
        "em-summary", run_dir, *mgr._build_tool_surface(["file"]),
        "summarize the read result", threading.Event(),
    )

    assert result == "daemon finished"
    assert len(service.sessions) == 2
    summary_call = service.sessions[1]
    assert summary_call.sent_messages
    assert "DAEMON-RAW-MARKER" in summary_call.sent_messages[0]
    worker_tool_batch = service.sessions[0].request_snapshots[1]
    worker_payload = worker_tool_batch[-1]["content"][0]["content"]
    worker_visible = str(worker_payload)
    assert "DAEMON-SUMMARY-MARKER" in worker_visible
    assert "DAEMON-RAW-MARKER" not in worker_visible

    locator = worker_payload["raw_locator"]
    assert locator["log"] == run_dir.events_path.relative_to(agent._working_dir).as_posix()
    assert locator["event_type"] == "daemon_tool_result"
    located_events = [
        json.loads(line)
        for line in (agent._working_dir / locator["log"]).read_text(encoding="utf-8").splitlines()
    ]
    assert any(
        event.get("event") == locator["event_type"]
        and event.get("tool_call_id") == locator["tool_call_id"]
        and "DAEMON-RAW-MARKER" in str(event.get("result"))
        for event in located_events
    )
    daemon_rows = [json.loads(line) for line in run_dir.token_ledger_path.read_text().splitlines()]
    assert any(row["input"] == 11 and row["output"] == 7 and row["source"] == "daemon" for row in daemon_rows)
    parent_rows = [json.loads(line) for line in (agent._working_dir / "logs" / "token_ledger.jsonl").read_text().splitlines()]
    assert any(row["input"] == 11 and row["output"] == 7 and row["source"] == "daemon" for row in parent_rows)
