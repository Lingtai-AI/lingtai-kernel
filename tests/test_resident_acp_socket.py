"""The resident ACP transport never starts or stops the Agent it serves."""
from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
import time
from contextlib import suppress

import pytest

from lingtai.adapters.acp.resident_socket import ResidentAcpSocket, resident_acp_socket_path
from lingtai.kernel.turns import TurnOutcome, TurnResult


class _Agent:
    def __init__(self):
        self._shutdown = threading.Event()
        self.mcp_mounts = 0
        self.submissions = []

    def mount_session_mcp_stdio(self, _configs):
        self.mcp_mounts += 1
        raise AssertionError("resident local ACP must not mount session MCP")

    def submit_turn(self, content, *, correlation_id, **_kwargs):
        self.submissions.append(content)

        class _Handle:
            def __init__(self):
                self.correlation_id = correlation_id

            def result(self, timeout=None):
                return TurnResult(correlation_id, TurnOutcome.NORMAL, "resident reply")

            def cancel(self):
                return False

        return _Handle()


def _connect(path):
    client = socket.socket(socket.AF_UNIX)
    client.settimeout(2)
    client.connect(str(path))
    return client, client.makefile("r", encoding="utf-8"), client.makefile(
        "w", encoding="utf-8", newline="\n"
    )


def _request(reader, writer, request_id, method, params):
    writer.write(json.dumps({
        "jsonrpc": "2.0", "id": request_id, "method": method, "params": params,
    }) + "\n")
    writer.flush()
    return json.loads(reader.readline())


def _wait_for_idle(transport):
    deadline = time.monotonic() + 2
    while transport._client is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert transport._client is None


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_resident_socket_reconnects_without_stopping_agent(tmp_path):
    agent = _Agent()
    transport = ResidentAcpSocket(agent, tmp_path)
    transport.start()
    try:
        assert stat.S_IMODE(transport.path.stat().st_mode) == 0o600
        for _ in range(2):
            client, reader, writer = _connect(transport.path)
            try:
                assert _request(reader, writer, 1, "initialize", {
                    "protocolVersion": 1,
                })["result"]["protocolVersion"] == 1
                response = _request(reader, writer, 2, "session/new", {
                    "cwd": str(tmp_path), "mcpServers": [],
                })
                assert response["result"]["sessionId"].startswith("session_")
                writer.write(json.dumps({
                    "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
                    "params": {
                        "sessionId": response["result"]["sessionId"],
                        "prompt": [{"type": "text", "text": "hello"}],
                    },
                }) + "\n")
                writer.flush()
                first = json.loads(reader.readline())
                final = json.loads(reader.readline())
                assert first["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
                assert final["id"] == 3
                assert final["result"]["stopReason"] == "end_turn"
                assert agent.submissions[-1] == "hello"
                assert not agent._shutdown.is_set()
            finally:
                with suppress(OSError):
                    client.shutdown(socket.SHUT_RDWR)
                client.close()
                reader.close()
                writer.close()
            _wait_for_idle(transport)
    finally:
        transport.close()
    assert not transport.path.exists()
    assert not agent._shutdown.is_set()


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_resident_socket_rejects_session_mcp_and_does_not_replace_live_socket(tmp_path):
    agent = _Agent()
    first = ResidentAcpSocket(agent, tmp_path)
    first.start()
    second = ResidentAcpSocket(agent, tmp_path)
    try:
        client, reader, writer = _connect(first.path)
        try:
            _request(reader, writer, 1, "initialize", {"protocolVersion": 1})
            response = _request(reader, writer, 2, "session/new", {
                "cwd": str(tmp_path),
                "mcpServers": [{"name": "puffo", "command": "/bin/false", "args": [], "env": []}],
            })
            assert response["error"]["code"] == -32602
            assert agent.mcp_mounts == 0
            other = socket.socket(socket.AF_UNIX)
            other.settimeout(2)
            other.connect(str(first.path))
            try:
                assert other.recv(1) == b""
            finally:
                other.close()
            with pytest.raises(RuntimeError, match="already listening"):
                second.start()
        finally:
            with suppress(OSError):
                client.shutdown(socket.SHUT_RDWR)
            client.close()
            reader.close()
            writer.close()
    finally:
        first.close()


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_resident_socket_refuses_non_socket_collision(tmp_path):
    path = resident_acp_socket_path(tmp_path)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text("user data", encoding="utf-8")
    try:
        with pytest.raises(RuntimeError, match="refusing to replace"):
            ResidentAcpSocket(_Agent(), tmp_path).start()
        assert path.read_text(encoding="utf-8") == "user data"
    finally:
        path.unlink()


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_resident_socket_recovers_only_a_refused_stale_socket(tmp_path):
    path = resident_acp_socket_path(tmp_path)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()
    transport = ResidentAcpSocket(_Agent(), tmp_path)
    try:
        transport.start()
        assert path.exists()
    finally:
        transport.close()
    assert not path.exists()


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_resident_socket_rejects_wrong_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(ResidentAcpSocket, "_check_peer", staticmethod(lambda _client: False))
    transport = ResidentAcpSocket(_Agent(), tmp_path)
    transport.start()
    try:
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(2)
        client.connect(str(transport.path))
        try:
            assert client.recv(1) == b""
        finally:
            client.close()
    finally:
        transport.close()


@pytest.mark.skipif(os.name != "posix", reason="owner-only POSIX socket")
def test_socket_path_cli_is_read_only(tmp_path, monkeypatch, capsys):
    from lingtai.cli import main

    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "acp-socket-path", str(tmp_path)])
    main()
    assert capsys.readouterr().out.strip() == str(resident_acp_socket_path(tmp_path))
    assert not resident_acp_socket_path(tmp_path).exists()


def test_run_flag_survives_refresh_environment(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from lingtai import cli

    run = Mock()
    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setenv("LINGTAI_ACP_SOCKET", "0")
    monkeypatch.setattr(sys, "argv", [
        "lingtai-agent", "run", str(tmp_path), "--acp-socket",
    ])
    cli.main()
    run.assert_called_once_with(tmp_path.resolve(), acp_socket=True)
    assert os.environ["LINGTAI_ACP_SOCKET"] == "1"


def test_run_owns_socket_inside_agent_lifetime(tmp_path, monkeypatch):
    from lingtai import cli
    from lingtai.adapters.acp import resident_socket
    from lingtai import venv_resolve

    events = []

    class _Flag:
        def set(self):
            pass

    class _Shutdown:
        def wait(self):
            events.append("wait")

    class _FakeAgent:
        _asleep = _Flag()
        _shutdown = _Shutdown()

        def start(self):
            events.append("agent-start")

        def stop(self, timeout=10.0):
            events.append("agent-stop")

    class _Socket:
        def __init__(self, agent, path):
            assert isinstance(agent, _FakeAgent)
            assert path == tmp_path

        def start(self):
            events.append("socket-start")

        def close(self):
            events.append("socket-close")

    monkeypatch.setenv("LINGTAI_ACP_SOCKET", "0")
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda _path: None)
    monkeypatch.setattr(cli, "_clean_signal_files", lambda _path: None)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _path, _agent: None)
    monkeypatch.setattr(cli, "load_init", lambda _path: {})
    monkeypatch.setattr(cli, "build_agent", lambda _data, _path, **_kw: _FakeAgent())
    monkeypatch.setattr(venv_resolve, "resolve_venv", lambda _data: tmp_path)
    monkeypatch.setattr(resident_socket, "ResidentAcpSocket", _Socket)

    cli.run(tmp_path, acp_socket=True)
    assert events == ["agent-start", "socket-start", "wait", "socket-close", "agent-stop"]
