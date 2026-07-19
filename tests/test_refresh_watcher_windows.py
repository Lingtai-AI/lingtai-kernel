"""Windows refresh-watcher adapters: wiring, exact spawn shapes, and native tier.

Cross-platform tests here run the real Windows adapter code with only the
concrete mechanism boundaries (``subprocess``, ``_win32``) substituted, so the
transport and composition shapes are pinned on POSIX CI too. Mechanism truth
(real detached spawn, real CIM observation, real termination) is the
``windows_mechanism``-marked native tier.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from lingtai.adapters.windows.refresh_watcher import (
    ENTRYPOINT_MODULE,
    WindowsRefreshWatcherAdapter,
)
from lingtai.adapters.windows.refresh_watcher_process import (
    WindowsRefreshWatcherProcessAdapter,
)
from lingtai.kernel.refresh_watcher import (
    RefreshWatcherProcessHandle,
    RefreshWatcherRequest,
    encode_request,
)

windows_mechanism = pytest.mark.skipif(
    os.name != "nt", reason="native Windows process mechanism"
)


def _sample_request(working_dir: str = "/wd") -> RefreshWatcherRequest:
    return RefreshWatcherRequest(
        taken_path=f"{working_dir}/.refresh.taken",
        lock_path=f"{working_dir}/.agent.lock",
        events_path=f"{working_dir}/logs/events.jsonl",
        stderr_log=f"{working_dir}/logs/refresh_relaunch.log",
        working_dir=working_dir,
        cmd=("lingtai-agent", "run", working_dir),
        agent_name="alice",
        address="wd",
        identity_fields_json='{"kernel_version": "v1"}',
    )


def test_windows_refresh_watcher_adapter_spawns_exact_detached_process():
    """Same transport contract as the POSIX adapter, Windows detachment flags.

    Current interpreter, the owned *Windows* entrypoint module via ``-m``, the
    compact encoded-request payload, shared env policy, DEVNULL stdio, and the
    shared detached creation flags instead of ``start_new_session``.
    """
    from lingtai.adapters.posix.refresh_watcher import build_watcher_env
    from lingtai.adapters.windows._win32 import DETACHED_CREATIONFLAGS

    request = _sample_request()
    expected_payload = encode_request(request)

    with patch("lingtai.adapters.windows.refresh_watcher.subprocess.Popen") as popen, \
            patch.dict("os.environ", {"PATH": "/test/bin"}, clear=True):
        WindowsRefreshWatcherAdapter().spawn_detached(request)
        expected_env = build_watcher_env(request)

    popen.assert_called_once_with(
        [sys.executable, "-m", ENTRYPOINT_MODULE, expected_payload],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED_CREATIONFLAGS,
        close_fds=True,
        env=expected_env,
    )
    assert expected_env["PATH"] == "/test/bin"
    assert expected_env["LINGTAI_REFRESH_ENV_OVERWRITE"] == "1"
    assert ENTRYPOINT_MODULE == "lingtai.adapters.windows.refresh_watcher_entrypoint"


def test_windows_adapter_env_overwrite_false_removes_stale_marker():
    import dataclasses

    request = dataclasses.replace(_sample_request(), env_overwrite=False)
    with patch("lingtai.adapters.windows.refresh_watcher.subprocess.Popen") as popen, \
            patch.dict(
                "os.environ",
                {"PATH": "/test/bin", "LINGTAI_REFRESH_ENV_OVERWRITE": "1"},
                clear=True,
            ):
        WindowsRefreshWatcherAdapter().spawn_detached(request)
    env = popen.call_args.kwargs["env"]
    assert "LINGTAI_REFRESH_ENV_OVERWRITE" not in env


def test_windows_entrypoint_composes_windows_process_mechanism(tmp_path):
    """The Windows entrypoint injects the Windows adapter bound to the request
    working directory — the only composition site for the generated policy's
    process mechanism on Windows."""
    import lingtai.adapters.windows.refresh_watcher_entrypoint as entrypoint

    request = _sample_request(working_dir=str(tmp_path))
    payload = encode_request(request)
    captured: dict = {}

    def fake_render(decoded):
        captured["request"] = decoded
        return "CAPTURED_MECHANISM = PROCESS_MECHANISM"

    with patch.object(entrypoint, "render_watcher_script", fake_render):
        # exec() writes CAPTURED_MECHANISM into the globals dict it was given;
        # grab it by also handing the namespace a recorder through builtins.
        namespace_probe: dict = {}

        real_exec = exec

        def record_exec(code, namespace):
            real_exec(code, namespace)
            namespace_probe.update(namespace)

        with patch("builtins.exec", record_exec):
            assert entrypoint.main([payload]) == 0

    assert captured["request"] == request
    mechanism = namespace_probe["CAPTURED_MECHANISM"]
    assert isinstance(mechanism, WindowsRefreshWatcherProcessAdapter)
    assert mechanism._working_dir == tmp_path


def test_windows_entrypoint_rejects_wrong_argv():
    import lingtai.adapters.windows.refresh_watcher_entrypoint as entrypoint

    with pytest.raises(SystemExit, match="usage"):
        entrypoint.main([])
    with pytest.raises(ValueError):
        entrypoint.main(["{not json"])


def test_windows_graceful_stop_writes_suspend_file(tmp_path):
    """Graceful stop is the workdir `.suspend` cooperative channel: Windows has
    no deliverable SIGTERM for detached headless processes, and the agent's
    heartbeat loop consumes `.suspend` within a second."""
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    adapter.graceful_stop(RefreshWatcherProcessHandle(pid=424242))
    assert (tmp_path / ".suspend").is_file()


def test_windows_is_alive_and_force_stop_use_win32_surface(tmp_path, monkeypatch):
    import lingtai.adapters.windows.refresh_watcher_process as module

    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        module._win32, "process_alive", lambda pid: calls.append(("alive", pid)) or True
    )
    monkeypatch.setattr(
        module._win32, "terminate_pid", lambda pid: calls.append(("terminate", pid)) or True
    )
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    assert adapter.is_alive(RefreshWatcherProcessHandle(pid=101)) is True
    adapter.force_stop(RefreshWatcherProcessHandle(pid=102))
    assert calls == [("alive", 101), ("terminate", 102)]


def test_windows_observe_queries_cim_command_line(tmp_path, monkeypatch):
    import lingtai.adapters.windows.refresh_watcher_process as module

    recorded: dict = {}

    def fake_check_output(argv, **kwargs):
        recorded["argv"] = argv
        return "C:\\Python312\\python.exe -m lingtai run C:\\agents\\wd\n"

    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    observation = adapter.observe(4242)
    assert observation is not None
    assert observation.pid == 4242
    assert observation.command_line.endswith("run C:\\agents\\wd")
    assert recorded["argv"][0] == "pwsh"
    assert "ProcessId = 4242" in recorded["argv"][-1]
    assert "Win32_Process" in recorded["argv"][-1]


def test_windows_observe_maps_failures_and_empty_to_none(tmp_path, monkeypatch):
    import lingtai.adapters.windows.refresh_watcher_process as module

    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    assert adapter.observe(1) is None

    monkeypatch.setattr(module.shutil, "which", lambda name: "pwsh")
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(1, a[0])),
    )
    assert adapter.observe(2) is None

    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **k: "  \n")
    assert adapter.observe(3) is None


def test_windows_start_agent_spawns_detached_with_stderr_log(tmp_path, monkeypatch):
    from lingtai.adapters.windows._win32 import DETACHED_CREATIONFLAGS
    import lingtai.adapters.windows.refresh_watcher_process as module

    recorded: dict = {}

    class FakeProcess:
        pid = 777

    def fake_popen(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    stderr_log = tmp_path / "relaunch.log"
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    handle = adapter.start_agent(("python", "-m", "lingtai", "run", str(tmp_path)), str(stderr_log))
    assert handle.pid == 777
    assert recorded["argv"] == ["python", "-m", "lingtai", "run", str(tmp_path)]
    kwargs = recorded["kwargs"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["creationflags"] == DETACHED_CREATIONFLAGS
    assert kwargs["close_fds"] is True
    assert kwargs["stderr"].mode == "a"
    kwargs["stderr"].close()
    assert "start_new_session" not in kwargs


# ---------------------------------------------------------------------------
# Native Windows mechanism tier
# ---------------------------------------------------------------------------


@windows_mechanism
def test_native_start_agent_is_alive_then_force_stop(tmp_path):
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    stderr_log = tmp_path / "relaunch.log"
    handle = adapter.start_agent(
        (sys.executable, "-c", "import time; time.sleep(60)"), str(stderr_log)
    )
    try:
        assert adapter.is_alive(handle) is True
        adapter.force_stop(handle)
        deadline = time.monotonic() + 10
        while adapter.is_alive(handle) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert adapter.is_alive(handle) is False
    finally:
        adapter.force_stop(handle)


@windows_mechanism
def test_native_observe_reports_own_command_line(tmp_path):
    adapter = WindowsRefreshWatcherProcessAdapter(tmp_path)
    observation = adapter.observe(os.getpid())
    assert observation is not None
    assert "python" in observation.command_line.lower()
