"""Windows detached supervisor: handle-wire shape, entrypoint mirrors, gates.

Cross-platform tier: the real Windows adapter code runs with only the
mechanism pieces faked (``msvcrt`` identity handles, ``STARTUPINFO``,
``set_handle_inheritable``, ``Popen``) and the platform gate bypassed via its
own seam — every path/env/payload computation is real, and a dup-reader
thread inside the fake ``Popen`` plays the inheriting child so the one-shot
pipe transport is exercised end to end. Native tier: a real detached
supervisor reaching a real capsule read and a terminal state on Windows.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from lingtai.adapters.windows import _win32
from lingtai.adapters.windows import daemon_supervisor as windows_supervisor
from lingtai.adapters.windows.daemon_supervisor import (
    ENTRYPOINT_MODULE,
    EXECUTION_CHILD_MODULE,
    RESUME_OWNER_MODULE,
    WindowsDaemonSupervisorAdapter,
    adopt_capsule_handle_to_fd,
)
from lingtai.kernel.daemon_supervisor import DaemonSupervisorRequest, decode_request
from lingtai.kernel.daemon_supervisor.manifest import (
    build_manifest,
    manifest_path_for,
    write_manifest,
)
from lingtai.tools.daemon.run_dir import DaemonRunDir

windows_mechanism = pytest.mark.skipif(
    os.name != "nt", reason="handle inheritance mechanism requires native Windows"
)

FAKE_LLM_ENV = "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM"
CAPSULE_SECRET = "CAPSULE_ONLY_SECRET_VALUE"


def _make_run_dir(tmp_path: Path, *, task="say hi", timeout_s=30.0) -> DaemonRunDir:
    parent = tmp_path / "agent"
    parent.mkdir(parents=True, exist_ok=True)
    return DaemonRunDir(
        parent_working_dir=parent,
        handle="em-win",
        run_id="em-win",
        task=task,
        tools=[],
        model="fake-model",
        max_turns=5,
        timeout_s=timeout_s,
        parent_addr=parent.name,
        parent_pid=os.getpid(),
        system_prompt=f"You are a test daemon.\n\nYour task:\n{task}",
        call_parameters={"task": task, "tools": []},
    )


def _write_lingtai_manifest(run_dir: DaemonRunDir, *, task="say hi", timeout_s=30.0):
    manifest = build_manifest(
        run_id=run_dir.run_id, backend="lingtai",
        parent_working_dir=str(run_dir.path.parent.parent),
        run_dir=str(run_dir.path), task=task, tools=[],
        max_turns=5, timeout_s=timeout_s, group_id=None,
        llm={"provider": "lingtai-supervisor-test-fake", "model": "fake-model",
             "api_key": None, "base_url": None, "context_window": None,
             "provider_defaults": None},
    )
    write_manifest(run_dir.path, manifest)
    return DaemonSupervisorRequest(
        run_id=run_dir.run_id,
        manifest_path=str(manifest_path_for(run_dir.path)),
        python_executable=sys.executable,
    )


class _FakeStartupInfo:
    def __init__(self, *, lpAttributeList=None, **kwargs):
        self.lpAttributeList = lpAttributeList
        self.kwargs = kwargs


class _CapsuleWire:
    """Install every mechanism fake and capture the spawned child's view."""

    def __init__(self, monkeypatch):
        self.popen_calls: list[tuple[list, dict]] = []
        self.inheritable: list[int] = []
        self.child_bytes: list[bytes] = []
        self._readers: list[threading.Thread] = []
        monkeypatch.setattr(windows_supervisor, "_require_windows", lambda: None)
        monkeypatch.setitem(
            sys.modules, "msvcrt",
            types.SimpleNamespace(get_osfhandle=lambda fd: fd),
        )
        monkeypatch.setattr(
            os, "set_handle_inheritable",
            lambda handle, flag: self.inheritable.append(handle) if flag else None,
            raising=False,
        )
        monkeypatch.setattr(subprocess, "STARTUPINFO", _FakeStartupInfo, raising=False)

        wire = self

        def fake_popen(argv, **kwargs):
            wire.popen_calls.append((list(argv), kwargs))
            env = kwargs.get("env") or {}
            raw_handle = env.get("LINGTAI_DAEMON_CAPSULE_HANDLE")
            if raw_handle is not None:
                # Simulate handle inheritance: the "child" holds its own dup of
                # the read end, surviving the parent's post-spawn close, and
                # reads until every write end is closed (the one-shot EOF).
                inherited = os.dup(int(raw_handle))

                def read_all():
                    chunks = []
                    while True:
                        chunk = os.read(inherited, 65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    os.close(inherited)
                    wire.child_bytes.append(b"".join(chunks))

                reader = threading.Thread(target=read_all, daemon=True)
                reader.start()
                wire._readers.append(reader)
            return types.SimpleNamespace(pid=54321)

        monkeypatch.setattr(windows_supervisor.subprocess, "Popen", fake_popen)

    def join(self, timeout=5.0):
        for reader in self._readers:
            reader.join(timeout=timeout)


def test_spawn_detached_guards_against_non_windows_use(tmp_path):
    if os.name == "nt":
        pytest.skip("guard is only observable off Windows")
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    with pytest.raises(OSError, match="requires Windows"):
        WindowsDaemonSupervisorAdapter().spawn_detached(request)


def test_windows_spawn_detached_exact_handle_wire_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_API_KEY", "must-not-cross")
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    wire = _CapsuleWire(monkeypatch)
    capsule = {"task": "runtime task", "llm": {"api_key": CAPSULE_SECRET}}

    WindowsDaemonSupervisorAdapter().spawn_detached(request, capsule=capsule)
    wire.join()

    argv, kwargs = wire.popen_calls[0]
    assert argv[:3] == [sys.executable, "-m", ENTRYPOINT_MODULE]
    assert decode_request(argv[3]) == request
    assert kwargs["close_fds"] is True
    assert kwargs["creationflags"] == _win32.DETACHED_CREATIONFLAGS
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["cwd"] == str(run_dir.path.parent.parent)
    env = kwargs["env"]
    handle = int(env["LINGTAI_DAEMON_CAPSULE_HANDLE"])
    assert wire.inheritable == [handle]
    assert kwargs["startupinfo"].lpAttributeList == {"handle_list": [handle]}
    # Secrets: env values carry only the handle NUMBER; capsule bytes cross
    # only the inherited pipe; secret-shaped parent env never crosses at all.
    assert "MY_API_KEY" not in env
    assert CAPSULE_SECRET not in json.dumps(env)
    assert CAPSULE_SECRET not in " ".join(argv)
    assert wire.child_bytes == [
        json.dumps(capsule, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ]
    assert (run_dir.path / "supervisor.stdout.log").exists()
    assert (run_dir.path / "supervisor.stderr.log").exists()
    # One-shot: nothing capsule-shaped may reach disk in the run dir.
    for path in run_dir.path.rglob("*"):
        if path.is_file():
            assert CAPSULE_SECRET not in path.read_text(encoding="utf-8", errors="replace")


def test_windows_spawn_without_capsule_has_no_handle_wire(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    wire = _CapsuleWire(monkeypatch)
    WindowsDaemonSupervisorAdapter().spawn_detached(request)
    argv, kwargs = wire.popen_calls[0]
    assert "LINGTAI_DAEMON_CAPSULE_HANDLE" not in kwargs["env"]
    assert kwargs["startupinfo"] is None
    assert wire.child_bytes == []


def test_windows_oversize_capsule_fails_before_spawn(tmp_path, monkeypatch):
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    wire = _CapsuleWire(monkeypatch)
    with pytest.raises(ValueError, match="capsule exceeds"):
        WindowsDaemonSupervisorAdapter().spawn_detached(
            request, capsule={"blob": "x" * (4 * 1024 * 1024 + 1)},
        )
    assert wire.popen_calls == []


def test_windows_execution_child_and_resume_owner_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_TOKEN", "must-not-cross")
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    wire = _CapsuleWire(monkeypatch)
    adapter = WindowsDaemonSupervisorAdapter()

    adapter.spawn_execution_child(
        python_executable=sys.executable,
        manifest_path=request.manifest_path,
        run_id=run_dir.run_id, run_dir=run_dir.path,
        capsule={"credential_env": {"X": CAPSULE_SECRET}},
    )
    adapter.spawn_resume_owner(
        python_executable=sys.executable,
        manifest_path=request.manifest_path,
        run_id=run_dir.run_id, run_dir=run_dir.path,
        generation="g1-abc",
        capsule={"message": "hi", "claim_nonce": "n"},
    )
    wire.join()

    child_argv, child_kwargs = wire.popen_calls[0]
    assert child_argv[:3] == [sys.executable, "-m", EXECUTION_CHILD_MODULE]
    assert child_argv[3:] == [request.manifest_path, run_dir.run_id, "emanation"]
    assert "SOME_TOKEN" not in child_kwargs["env"]
    assert child_kwargs["creationflags"] == _win32.DETACHED_CREATIONFLAGS
    assert (run_dir.path / "execution.stdout.log").exists()

    resume_argv, _resume_kwargs = wire.popen_calls[1]
    assert resume_argv[:3] == [sys.executable, "-m", RESUME_OWNER_MODULE]
    assert resume_argv[3:] == [request.manifest_path, run_dir.run_id, "g1-abc"]
    assert (run_dir.path / "resume-owner.stdout.log").exists()

    payloads = [json.loads(chunk.decode("utf-8")) for chunk in wire.child_bytes]
    assert payloads[0] == {"credential_env": {"X": CAPSULE_SECRET}}
    assert payloads[1] == {"message": "hi", "claim_nonce": "n"}


def test_adopt_capsule_handle_bridges_to_the_shared_posix_reader(monkeypatch):
    """handle→fd conversion feeds the unchanged POSIX read loop."""
    from lingtai.adapters.posix import daemon_supervisor_entrypoint as posix_entrypoint

    payload = {"llm": {"api_key": "capsule-value"}}
    read_fd, write_fd = os.pipe()
    os.write(write_fd, json.dumps(payload).encode("utf-8"))
    os.close(write_fd)
    monkeypatch.setitem(
        sys.modules, "msvcrt",
        types.SimpleNamespace(open_osfhandle=lambda handle, flags: handle),
    )
    monkeypatch.setenv("LINGTAI_DAEMON_CAPSULE_HANDLE", str(read_fd))
    adopt_capsule_handle_to_fd(strict=True)
    assert "LINGTAI_DAEMON_CAPSULE_HANDLE" not in os.environ
    assert os.environ["LINGTAI_DAEMON_CAPSULE_FD"] == str(read_fd)
    assert posix_entrypoint._read_capsule() == payload
    assert "LINGTAI_DAEMON_CAPSULE_FD" not in os.environ  # consumed one-shot


def test_adopt_capsule_handle_strictness(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "msvcrt",
        types.SimpleNamespace(open_osfhandle=lambda handle, flags: handle),
    )
    monkeypatch.setenv("LINGTAI_DAEMON_CAPSULE_HANDLE", "not-a-handle")
    with pytest.raises(ValueError):
        adopt_capsule_handle_to_fd(strict=True)
    monkeypatch.setenv("LINGTAI_DAEMON_CAPSULE_HANDLE", "not-a-handle")
    adopt_capsule_handle_to_fd(strict=False)  # lenient: "no capsule"
    assert "LINGTAI_DAEMON_CAPSULE_HANDLE" not in os.environ
    assert "LINGTAI_DAEMON_CAPSULE_FD" not in os.environ
    # Absent env var is a silent no-op in both modes.
    adopt_capsule_handle_to_fd(strict=True)
    adopt_capsule_handle_to_fd(strict=False)


def test_windows_entrypoints_fail_loudly_off_windows():
    if os.name == "nt":
        pytest.skip("guards are only observable off Windows")
    from lingtai.adapters.windows import daemon_supervisor_entrypoint as sup_ep
    from lingtai.adapters.windows import daemon_execution_child_entrypoint as child_ep
    from lingtai.adapters.windows import daemon_resume_owner_entrypoint as resume_ep

    with pytest.raises(OSError, match="requires Windows"):
        sup_ep.main(["{}"])
    with pytest.raises(OSError, match="requires Windows"):
        child_ep.main(["m", "r", "emanation"])
    with pytest.raises(OSError, match="requires Windows"):
        resume_ep.main(["m", "r", "g1"])
    # Usage errors stay first and identical to the POSIX mirrors.
    with pytest.raises(SystemExit, match="usage"):
        child_ep.main(["only-one"])
    with pytest.raises(SystemExit, match="usage"):
        resume_ep.main(["only-one"])


def test_windows_supervisor_entrypoint_delegates_to_shared_main(tmp_path, monkeypatch):
    """Handle adoption happens, then the POSIX decode/dispatch runs unchanged."""
    from lingtai.adapters.posix import daemon_supervisor_entrypoint as posix_entrypoint
    from lingtai.adapters.windows import daemon_supervisor_entrypoint as windows_entrypoint
    from lingtai.kernel.daemon_supervisor import encode_request

    request = DaemonSupervisorRequest(
        run_id="em-x", manifest_path=str(tmp_path / "m.json"),
        python_executable=sys.executable,
    )
    capsule = {"task": "runtime"}
    read_fd, write_fd = os.pipe()
    os.write(write_fd, json.dumps(capsule).encode("utf-8"))
    os.close(write_fd)
    monkeypatch.setitem(
        sys.modules, "msvcrt",
        types.SimpleNamespace(open_osfhandle=lambda handle, flags: handle),
    )
    monkeypatch.setenv("LINGTAI_DAEMON_CAPSULE_HANDLE", str(read_fd))
    recorded: list[tuple] = []
    monkeypatch.setattr(
        posix_entrypoint, "run_supervisor",
        lambda req, *, capsule=None: recorded.append((req, capsule)),
    )
    monkeypatch.setattr(os, "name", "nt")
    assert windows_entrypoint.main([encode_request(request)]) == 0
    assert recorded == [(request, capsule)]


def test_supervisor_adapter_selector_is_platform_exact(monkeypatch):
    from lingtai.tools.daemon.supervisor_runtime import select_daemon_supervisor_adapter

    if os.name == "posix":
        from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
        assert isinstance(select_daemon_supervisor_adapter(), PosixDaemonSupervisorAdapter)
    monkeypatch.setattr(os, "name", "nt")
    assert isinstance(select_daemon_supervisor_adapter(), WindowsDaemonSupervisorAdapter)
    monkeypatch.setattr(os, "name", "java")
    with pytest.raises(NotImplementedError, match="unsupported"):
        select_daemon_supervisor_adapter()


def test_manager_composition_gate_opens_for_windows(tmp_path, monkeypatch):
    """On nt the manager gets the Windows process port and NO terminal port."""
    from types import SimpleNamespace
    from lingtai.tools.daemon import DaemonManager
    from lingtai.tools.daemon.windows_process import WindowsDaemonProcessPort
    from lingtai.tools.daemon.process_port import DaemonProcessTerminationScope

    agent = SimpleNamespace(
        service=SimpleNamespace(model="m"), _working_dir=tmp_path / "agent",
    )
    monkeypatch.setattr(os, "name", "nt")
    manager = DaemonManager(agent)
    assert isinstance(manager._process_port, WindowsDaemonProcessPort)
    assert (
        manager._process_port._termination_scope
        is DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
    )
    assert manager._interactive_terminal_port is None
    monkeypatch.setattr(os, "name", "java")
    with pytest.raises(NotImplementedError, match="unsupported"):
        DaemonManager(agent)


def test_detached_default_port_gate_opens_for_windows(monkeypatch):
    from lingtai.tools.daemon.execution_host import _default_detached_process_port
    from lingtai.tools.daemon.windows_process import WindowsDaemonProcessPort
    from lingtai.tools.daemon.process_port import DaemonProcessTerminationScope

    if os.name == "posix":
        from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
        port = _default_detached_process_port()
        assert isinstance(port, PosixDaemonProcessPort)
        assert port._start_new_session is False
    monkeypatch.setattr(os, "name", "nt")
    port = _default_detached_process_port()
    assert isinstance(port, WindowsDaemonProcessPort)
    assert (
        port._termination_scope
        is DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP
    )
    monkeypatch.setattr(os, "name", "java")
    with pytest.raises(RuntimeError, match="unsupported"):
        _default_detached_process_port()


def test_windows_identity_delegation_never_signals(monkeypatch):
    """posix process_identity delegates to the Windows observer before its
    os.kill(pid, 0) probe — which on Windows would TERMINATE the target."""
    from lingtai.adapters.posix import process_identity as posix_identity

    kills: list[tuple] = []
    monkeypatch.setattr(os, "kill", lambda *args: kills.append(args))
    monkeypatch.setattr(
        _win32, "process_creation_identity", lambda pid: f"windows:{pid}",
    )
    monkeypatch.setattr(posix_identity.sys, "platform", "win32")
    monkeypatch.setattr(os, "name", "nt")
    assert posix_identity.process_identity(4242) == "windows:4242"
    assert posix_identity.process_identity_matches(4242, "windows:4242") is True
    assert posix_identity.process_identity_matches(4242, "windows:9") is False
    assert kills == []


def _poll_until(predicate, *, timeout=20.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


def _disk_state(run_dir: DaemonRunDir) -> dict:
    try:
        return DaemonRunDir.read_state_from_disk(run_dir.path)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


@windows_mechanism
def test_windows_adapter_spawns_real_detached_supervisor_reaching_capsule_read(tmp_path, monkeypatch):
    """Real detached spawn on Windows: the supervisor records its PID, the
    execution child consumes the one-shot capsule, and the run reaches ``done``
    only because the capsule-delivered api_key crossed both handle hops (the
    fake provider requires the runtime key before it emits completion)."""
    monkeypatch.setenv(FAKE_LLM_ENV, "1")
    monkeypatch.setenv("LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM_FINISH", "1")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(dict.fromkeys([
            str(Path(__file__).parent),
            str(Path(__file__).parent.parent / "src"),
            *[p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p],
        ])),
    )
    run_dir = _make_run_dir(tmp_path, task="windows capsule acceptance")
    request = _write_lingtai_manifest(run_dir, task="windows capsule acceptance")
    WindowsDaemonSupervisorAdapter().spawn_detached(
        request,
        capsule={"llm": {"api_key": CAPSULE_SECRET}},
    )
    pid = _poll_until(lambda: _disk_state(run_dir).get("supervisor_pid"))
    assert pid != os.getpid()
    _poll_until(lambda: _disk_state(run_dir).get("state") == "done", timeout=60.0)
    state = _disk_state(run_dir)
    assert state.get("execution_pgid") is None
    identity = state.get("execution_start_identity")
    assert isinstance(identity, str) and identity.startswith("windows:")
    # Secret boundary: the capsule value must never reach any durable artifact.
    for path in run_dir.path.rglob("*"):
        if path.is_file():
            assert CAPSULE_SECRET not in path.read_text(encoding="utf-8", errors="replace")


@windows_mechanism
def test_windows_supervisor_without_fake_env_commits_failed_not_hang(tmp_path):
    """No fake-LLM hook: real provider construction fails loudly and the
    detached Windows supervisor still commits terminal truth."""
    run_dir = _make_run_dir(tmp_path)
    request = _write_lingtai_manifest(run_dir)
    WindowsDaemonSupervisorAdapter().spawn_detached(request)
    _poll_until(lambda: _disk_state(run_dir).get("supervisor_pid"))
    terminal = _poll_until(
        lambda: (_disk_state(run_dir).get("state") or None)
        if _disk_state(run_dir).get("state") in ("failed", "done", "cancelled", "timeout")
        else None,
        timeout=30.0,
    )
    assert terminal == "failed"
