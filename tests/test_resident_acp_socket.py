"""The resident ACP transport never starts or stops the Agent it serves."""
from __future__ import annotations

import json
import array
import os
import socket
import stat
import sys
import threading
import time
import struct
from contextlib import suppress
from types import SimpleNamespace

import pytest

from lingtai.adapters.acp.resident_socket import ResidentAcpSocket, resident_acp_socket_path
from lingtai.kernel.turns import TurnOutcome, TurnResult
from lingtai.kernel.provider_admission import (
    ConnectionScopedProviderAdmissionPort, ProviderAdmissionState,
    ProviderCallClass, RootProviderAdmission,
)


class _Agent:
    def __init__(self):
        self._shutdown = threading.Event()
        self.mcp_mounts = 0
        self.submissions = []
        self.submission_options = []
        self._provider_call_admission_port = ConnectionScopedProviderAdmissionPort()

    def mount_session_mcp_stdio(self, _configs):
        self.mcp_mounts += 1
        raise AssertionError("resident local ACP must not mount session MCP")

    def submit_turn(self, content, *, correlation_id, **_kwargs):
        self.submissions.append(content)
        self.submission_options.append(_kwargs)

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


@pytest.mark.skipif(os.name != "posix", reason="SCM_RIGHTS attach requires POSIX")
@pytest.mark.parametrize("grant", [True, False])
def test_attach_fd_is_connection_scoped_and_provider_decision_is_not_local(tmp_path, monkeypatch, grant):
    monkeypatch.setattr(
        "lingtai.adapters.acp.resident_socket.resolve_runtime",
        lambda runtime_id, *, registry_path: SimpleNamespace(agent_dir=tmp_path, workspace=tmp_path),
    )
    (tmp_path / "registry.json").touch()
    agent = _Agent()
    transport = ResidentAcpSocket(agent, tmp_path)
    transport.start()
    authority_end, driver_end = socket.socketpair()
    decisions = []

    def driver():
        for expected_op in ("hello", "authorize_provider_call"):
            header = driver_end.recv(4)
            size = struct.unpack("!I", header)[0]
            body = bytearray()
            while len(body) < size:
                body.extend(driver_end.recv(size - len(body)))
            request = json.loads(body)
            assert request["op"] == expected_op
            decisions.append(request)
            response = {"version": 1, "call_id": request["call_id"]}
            if expected_op == "hello":
                response.update(role="root", launch_id="attach-launch", capability=None)
            else:
                response.update(state="granted" if grant else "denied", reason_code="allowed" if grant else "endpoint_binding_mismatch")
            encoded = json.dumps(response).encode()
            driver_end.sendall(struct.pack("!I", len(encoded)) + encoded)
        driver_end.close()

    worker = threading.Thread(target=driver)
    worker.start()
    client, reader, writer = _connect(transport.path)
    try:
        attach = json.dumps({
            "type": "puffo.attach/1", "runtime_id": "runtime-1",
            "registry": str(tmp_path / "registry.json"), "launch_id": "attach-launch",
        }).encode() + b"\n"
        client.sendmsg([attach], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [authority_end.fileno()]))])
        authority_end.close()
        assert json.loads(reader.readline())["ok"] is True
        assert _request(reader, writer, 1, "initialize", {"protocolVersion": 1})["result"]["protocolVersion"] == 1
        session_id = _request(reader, writer, 2, "session/new", {
            "cwd": str(tmp_path), "mcpServers": [],
        })["result"]["sessionId"]
        writer.write(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": [{"type": "text", "text": "hello"}]},
        }) + "\n")
        writer.flush()
        assert json.loads(reader.readline())["method"] == "session/update"
        assert json.loads(reader.readline())["id"] == 3
        port = agent.submission_options[-1]["connection_provider_port"]
        decision = port.authorize_provider_call(
            RootProviderAdmission("attached-turn", "attach-v1", True), ProviderCallClass.ROOT,
        )
        assert decision.state is (
            ProviderAdmissionState.GRANTED if grant else ProviderAdmissionState.DENIED
        )
        assert len(decisions) == 2
        assert decisions[-1]["launch_id"] == "attach-launch"
    finally:
        client.close()
        reader.close()
        writer.close()
        worker.join(2)
        transport.close()


@pytest.mark.skipif(os.name != "posix", reason="SCM_RIGHTS attach requires POSIX")
def test_attach_without_authority_fd_is_rejected_before_acp(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "lingtai.adapters.acp.resident_socket.resolve_runtime",
        lambda runtime_id, *, registry_path: SimpleNamespace(agent_dir=tmp_path, workspace=tmp_path),
    )
    agent = _Agent()
    transport = ResidentAcpSocket(agent, tmp_path)
    transport.start()
    client, reader, writer = _connect(transport.path)
    try:
        writer.write(json.dumps({
            "type": "puffo.attach/1", "runtime_id": "runtime-1",
            "registry": str(tmp_path / "registry.json"), "launch_id": "attach-launch",
        }) + "\n")
        writer.flush()
        assert json.loads(reader.readline()) == {"ok": False, "reason": "attach_rejected"}
        assert reader.readline() == ""
        assert agent.submissions == []
    finally:
        client.close()
        reader.close()
        writer.close()
        transport.close()


@pytest.mark.skipif(os.name != "posix", reason="SCM_RIGHTS attach requires POSIX")
def test_attach_rejects_registry_identity_mismatch_and_closes_received_fd(tmp_path, monkeypatch):
    from lingtai.adapters.acp.resident_socket import _attach_authority

    registry = tmp_path / "registry.json"
    registry.touch()
    other = tmp_path / "other-agent"
    other.mkdir()
    monkeypatch.setattr(
        "lingtai.adapters.acp.resident_socket.resolve_runtime",
        lambda runtime_id, *, registry_path: SimpleNamespace(agent_dir=other, workspace=tmp_path),
    )
    received, driver = socket.socketpair()
    fd = received.detach()
    frame = json.dumps({
        "type": "puffo.attach/1", "runtime_id": "runtime-1",
        "registry": str(registry), "launch_id": "attach-launch",
    }).encode() + b"\n"
    try:
        with pytest.raises(ValueError, match="runtime_agent_mismatch"):
            _attach_authority(frame, [fd], tmp_path)
        driver.settimeout(2)
        assert driver.recv(1) == b""
    finally:
        driver.close()


@pytest.mark.skipif(os.name != "posix", reason="SCM_RIGHTS attach requires POSIX")
def test_attach_rejects_driver_hello_launch_id_mismatch(tmp_path, monkeypatch):
    from lingtai.adapters.acp.resident_socket import _attach_authority

    registry = tmp_path / "registry.json"
    registry.touch()
    monkeypatch.setattr(
        "lingtai.adapters.acp.resident_socket.resolve_runtime",
        lambda runtime_id, *, registry_path: SimpleNamespace(agent_dir=tmp_path, workspace=tmp_path),
    )
    received, driver = socket.socketpair()
    fd = received.detach()

    def hello():
        size = struct.unpack("!I", driver.recv(4))[0]
        request = json.loads(driver.recv(size))
        response = {
            "version": 1, "call_id": request["call_id"], "role": "root",
            "launch_id": "another-launch", "capability": None,
        }
        body = json.dumps(response).encode()
        driver.sendall(struct.pack("!I", len(body)) + body)

    worker = threading.Thread(target=hello)
    worker.start()
    frame = json.dumps({
        "type": "puffo.attach/1", "runtime_id": "runtime-1",
        "registry": str(registry), "launch_id": "attach-launch",
    }).encode() + b"\n"
    try:
        with pytest.raises(ValueError, match="authority_binding_mismatch"):
            _attach_authority(frame, [fd], tmp_path)
        worker.join(2)
        assert not worker.is_alive()
        driver.settimeout(2)
        assert driver.recv(1) == b""
    finally:
        driver.close()


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
    monkeypatch.setenv("LINGTAI_ACP_SOCKET_AGENT_DIR", "disabled")
    monkeypatch.setattr(sys, "argv", [
        "lingtai-agent", "run", str(tmp_path), "--acp-socket",
    ])
    cli.main()
    run.assert_called_once_with(tmp_path.resolve(), acp_socket=True)
    assert os.environ["LINGTAI_ACP_SOCKET_AGENT_DIR"] == str(tmp_path.resolve())


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

    monkeypatch.delenv("LINGTAI_ACP_SOCKET_AGENT_DIR", raising=False)
    monkeypatch.setattr(cli, "_check_duplicate_process", lambda _path: None)
    monkeypatch.setattr(cli, "_clean_signal_files", lambda _path: None)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _path, _agent: None)
    monkeypatch.setattr(cli, "load_init", lambda _path: {})
    def build(_data, _path, **options):
        assert isinstance(
            options["_provider_call_admission_port"], ConnectionScopedProviderAdmissionPort
        )
        return _FakeAgent()

    monkeypatch.setattr(cli, "build_agent", build)
    monkeypatch.setattr(venv_resolve, "resolve_venv", lambda _data: tmp_path)
    monkeypatch.setattr(resident_socket, "ResidentAcpSocket", _Socket)

    cli.run(tmp_path, acp_socket=True)
    assert events == ["agent-start", "socket-start", "wait", "socket-close", "agent-stop"]


@pytest.mark.parametrize("same_agent", [False, True])
def test_refresh_marker_only_enables_its_own_agent(tmp_path, monkeypatch, same_agent):
    """A parent opt-in survives its refresh, never an independent child run."""
    from lingtai import cli, venv_resolve
    from lingtai.adapters.acp import resident_socket

    parent = tmp_path / "parent"
    child = tmp_path / "child"
    target = parent if same_agent else child
    target.mkdir()
    monkeypatch.setenv("LINGTAI_ACP_SOCKET_AGENT_DIR", str(parent.resolve()))
    events = []

    class _Shutdown:
        def wait(self):
            events.append("wait")

    class _Agent:
        _shutdown = _Shutdown()
        _asleep = type("_Flag", (), {"set": lambda self: None})()

        def start(self):
            events.append("agent-start")

        def stop(self, timeout=10.0):
            events.append("agent-stop")

    class _Socket:
        def __init__(self, agent, path):
            assert path == target

        def start(self):
            events.append("socket-start")

        def close(self):
            events.append("socket-close")

    monkeypatch.setattr(cli, "_check_duplicate_process", lambda _path: None)
    monkeypatch.setattr(cli, "_clean_signal_files", lambda _path: None)
    monkeypatch.setattr(cli, "_install_signal_handlers", lambda _path, _agent: None)
    monkeypatch.setattr(cli, "load_init", lambda _path: {})
    monkeypatch.setattr(cli, "build_agent", lambda _data, _path, **_kw: _Agent())
    monkeypatch.setattr(venv_resolve, "resolve_venv", lambda _data: target)
    monkeypatch.setattr(resident_socket, "ResidentAcpSocket", _Socket)

    cli.run(target)
    if same_agent:
        assert events == ["agent-start", "socket-start", "wait", "socket-close", "agent-stop"]
    else:
        assert events == ["agent-start", "wait", "agent-stop"]
