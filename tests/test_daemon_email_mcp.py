"""Tests for the daemon-email MCP server and its daemon-subsystem wiring.

``email`` is no longer a daemon *intrinsic* exception (``_daemon_intrinsic_
surface``): it is provided as an MCP tool identical to every other daemon
backend (``mcp_servers/daemon_email/``), auto-mounted by ``DaemonManager``
only when a task explicitly requests ``tools: ["email"]`` — the same
opt-in security posture the removed intrinsic exception enforced.

Covers:
* the MCP server's LTP v2 envelope (schema shape, dispatch, unknown-tool
  classification) over an in-memory ``mcp.Client``;
* daemon-A-to-daemon-B delivery through the unmodified filesystem mail
  adapter, using two ``DaemonEmailAgentShim``s bound to two distinct
  "parent" working directories;
* the daemon-subsystem registration/gating helpers
  (``_daemon_email_mcp_registration``, ``_with_daemon_email_mcp``) and the
  ``_build_tool_surface`` pre-connection tolerance for ``email``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from mcp import Client
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS

from lingtai.mcp_servers.daemon_email.agent_shim import DaemonEmailAgentShim
from lingtai.mcp_servers.daemon_email.server import build_agent, build_server
from lingtai.tools import email as email_tool
from lingtai.tools.daemon import DaemonManager
from lingtai.tools.daemon.run_dir import DaemonRunDir

from tests._daemon_helpers import make_daemon_agent as _make_agent
from tests._daemon_helpers import make_daemon_run_dir as _make_run_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _setup_agent_dir(path: Path) -> None:
    """Minimal live-agent directory: a manifest plus a fresh heartbeat.

    Mirrors ``tests/test_three_agent_email.py``'s helper — this is exactly
    the handshake ``PosixFilesystemMailAdapter.send`` requires of a
    recipient, and exactly what a real running parent agent maintains.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / ".agent.json").write_text(json.dumps({
        "agent_id": path.name,
        "agent_name": path.name,
    }))
    (path / ".agent.heartbeat").write_text(str(time.time()))


def _wait_for_inbox(working_dir: Path, count: int = 1, timeout: float = 5.0) -> None:
    """Poll until *working_dir*'s inbox has at least *count* messages.

    ``send`` delivers via a background thread (``EmailManager._send`` spawns
    ``_mailman``), so the tool call returning ``status: sent`` does not mean
    the recipient's inbox is populated yet.
    """
    inbox = working_dir / "mailbox" / "inbox"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if inbox.is_dir() and sum(1 for d in inbox.iterdir() if d.is_dir()) >= count:
            return
        time.sleep(0.02)
    raise AssertionError(f"inbox at {working_dir} did not reach {count} message(s)")


# ---------------------------------------------------------------------------
# MCP server: LTP v2 envelope, schema, dispatch
# ---------------------------------------------------------------------------


async def test_server_negotiates_protocol_and_lists_ltp_v2_email_tool(tmp_path):
    _setup_agent_dir(tmp_path)
    agent = DaemonEmailAgentShim(tmp_path)
    email_tool.boot(agent)

    async with Client(build_server(agent)) as client:
        assert client.protocol_version == "2026-07-28"
        result = await client.list_tools()
        assert result.next_cursor is None
        assert [t.name for t in result.tools] == ["email"]

        schema = result.tools[0].input_schema
        assert schema["required"] == ["action", "input", "reasoning"]
        assert "check" in schema["properties"]["action"]["enum"]
        assert "send" in schema["properties"]["action"]["enum"]


async def test_server_dispatches_check_via_ltp_v2_envelope(tmp_path):
    _setup_agent_dir(tmp_path)
    agent = DaemonEmailAgentShim(tmp_path)
    email_tool.boot(agent)

    async with Client(build_server(agent)) as client:
        result = await client.call_tool(
            "email",
            {"action": "check", "input": {}, "reasoning": "test check"},
        )
        payload = json.loads(result.content[0].text)
        assert payload["status"] == "ok"
        assert result.is_error is False


async def test_server_unknown_tool_is_invalid_params(tmp_path):
    _setup_agent_dir(tmp_path)
    agent = DaemonEmailAgentShim(tmp_path)
    email_tool.boot(agent)

    async with Client(build_server(agent)) as client:
        with pytest.raises(MCPError) as raised:
            await client.call_tool("not_a_real_tool", {})
        assert raised.value.code == INVALID_PARAMS


def test_handle_accepts_kernel_normalized_reasoning_key(tmp_path):
    """A daemon's real tool call arrives via ``_reasoning``, not ``reasoning``.

    ``kernel.tool_executor._prepare_args`` pops ``reasoning`` off every
    in-process-dispatched daemon tool call (intrinsics *and* MCP-proxied
    ones — email included, since ``_dispatch_daemon_tool`` is the one
    generic dispatcher for the whole daemon tool surface) and re-adds it as
    ``_reasoning`` before the handler runs. ``ToolFamily``'s envelope
    validator already tolerates this (``_ROOT_FIELDS``); this pins that the
    same holds all the way through email's dispatch.
    """
    _setup_agent_dir(tmp_path)
    agent = DaemonEmailAgentShim(tmp_path)
    email_tool.boot(agent)

    result = email_tool.handle(agent, {
        "action": "check",
        "input": {},
        "_reasoning": "kernel-normalized call shape",
    })
    assert result["status"] == "ok"


def test_build_agent_reads_lingtai_agent_dir_env(tmp_path, monkeypatch):
    _setup_agent_dir(tmp_path)
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    agent = build_agent()
    assert agent._working_dir == tmp_path
    assert agent._email_manager is not None


# ---------------------------------------------------------------------------
# Daemon A -> daemon B round trip (and daemon -> parent)
# ---------------------------------------------------------------------------


def test_daemon_a_to_daemon_b_email_round_trip(tmp_path):
    """Two daemons, each speaking as its own parent's mailbox, can email.

    This is the real cross-agent mechanism the intrinsic used too — an
    unmodified ``PosixFilesystemMailAdapter`` handshake against
    ``.agent.json``/``.agent.heartbeat`` followed by an atomic filesystem
    write into the recipient's ``mailbox/inbox/``. The daemon-email MCP
    server changes only how a daemon *reaches* this mechanism (MCP call
    instead of an in-process intrinsic dispatch), not the mechanism itself.
    """
    parent_a = tmp_path / "parent-a"
    parent_b = tmp_path / "parent-b"
    _setup_agent_dir(parent_a)
    _setup_agent_dir(parent_b)

    shim_a = DaemonEmailAgentShim(parent_a)
    email_tool.boot(shim_a)
    shim_b = DaemonEmailAgentShim(parent_b)
    email_tool.boot(shim_b)

    send_result = email_tool.handle(shim_a, {
        "action": "send",
        "input": {
            "address": str(parent_b),
            "mode": "abs",
            "subject": "hello from daemon A",
            "message": "daemon A -> daemon B",
        },
        "reasoning": "round trip test",
    })
    assert send_result.get("status") != "error", send_result
    _wait_for_inbox(parent_b)

    check_result = email_tool.handle(shim_b, {
        "action": "check",
        "input": {},
        "reasoning": "read inbox",
    })
    assert check_result["status"] == "ok"
    bodies = json.dumps(check_result)
    assert "daemon A -> daemon B" in bodies or "hello from daemon A" in bodies


def test_daemon_email_reaches_parents_own_mailbox(tmp_path):
    """A daemon and its own parent share one mailbox (same working dir)."""
    parent = tmp_path / "parent"
    other = tmp_path / "other"
    _setup_agent_dir(parent)
    _setup_agent_dir(other)

    daemon_shim = DaemonEmailAgentShim(parent)
    email_tool.boot(daemon_shim)
    other_shim = DaemonEmailAgentShim(other)
    email_tool.boot(other_shim)

    email_tool.handle(other_shim, {
        "action": "send",
        "input": {
            "address": str(parent),
            "mode": "abs",
            "subject": "for the parent",
            "message": "reaches the daemon too, same mailbox",
        },
        "reasoning": "daemon-parent shared mailbox",
    })
    _wait_for_inbox(parent)

    # The daemon shim reads exactly what a live parent Agent's own
    # EmailManager would read: same working_dir/mailbox/inbox.
    result = email_tool.handle(daemon_shim, {
        "action": "check",
        "input": {},
        "reasoning": "daemon reads parent's inbox",
    })
    assert result["status"] == "ok"
    assert "reaches the daemon too" in json.dumps(result)


# ---------------------------------------------------------------------------
# Daemon subsystem: registration + gating
# ---------------------------------------------------------------------------


def test_daemon_email_mcp_registration_env(tmp_path):
    agent = _make_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, tools=["email"])

    reg = DaemonManager._daemon_email_mcp_registration(run_dir, agent._working_dir)
    assert reg["name"] == "email"
    assert reg["args"] == ["-m", "lingtai.mcp_servers.daemon_email"]
    assert reg["env"]["LINGTAI_AGENT_DIR"] == str(agent._working_dir)


def test_with_daemon_email_mcp_reserves_name(tmp_path):
    agent = _make_agent(tmp_path)
    run_dir = _make_run_dir(agent, tools=["email"])

    regs = DaemonManager._with_daemon_email_mcp([], run_dir, agent._working_dir)
    assert [r["name"] for r in regs] == ["email"]

    with pytest.raises(ValueError, match="reserved"):
        DaemonManager._with_daemon_email_mcp(
            [{"name": "email", "transport": "stdio", "command": "x"}],
            run_dir, agent._working_dir,
        )


def test_email_mcp_mounted_only_when_requested(tmp_path, monkeypatch):
    """End-to-end: tools=[] never persists the email registration; tools=['email'] does."""
    from tests._daemon_helpers import install_fake_detached_owner

    agent = _make_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "tasks": [{"task": "result-only task", "tools": []}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    state = json.loads(
        (agent._working_dir / "daemons" / em_id / "daemon.json").read_text()
    )
    mcp_names = {r.get("name") for r in state["call_parameters"].get("mcp", [])}
    assert "email" not in mcp_names
    assert "daemon_common" in mcp_names


def test_email_mcp_mounted_when_tools_requests_it(tmp_path, monkeypatch):
    from tests._daemon_helpers import install_fake_detached_owner

    agent = _make_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "tasks": [{"task": "email-capable task", "tools": ["email"]}],
    })
    assert result["status"] == "dispatched"
    em_id = result["ids"][0]
    state = json.loads(
        (agent._working_dir / "daemons" / em_id / "daemon.json").read_text()
    )
    mcp_names = {r.get("name") for r in state["call_parameters"].get("mcp", [])}
    assert "email" in mcp_names
