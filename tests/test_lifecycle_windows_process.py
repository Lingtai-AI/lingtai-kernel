"""Native-Windows process/lifecycle safety for the refresh watcher.

Proves that:
  * the watcher subprocess is spawned with platform-aware detach kwargs
    (``start_new_session`` on POSIX, never on native Windows);
  * the generated watcher script routes its relaunch spawn, PID liveness, and
    duplicate termination through the neutral ``lingtai_kernel.process_control``
    helpers rather than raw ``os.kill``/``start_new_session``;
  * the script's import fallback (used if the kernel import is unavailable in
    the child interpreter) still compiles and remains platform-aware.

The subprocess.Popen call is patched throughout so no real relaunch happens.
"""
from __future__ import annotations

import ast
from unittest.mock import patch

from lingtai_kernel import process_control as pc


def make_mock_service():
    from unittest.mock import MagicMock
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "p"
    svc.model = "m"
    return svc


def _make_agent(tmp_path):
    from lingtai_kernel.base_agent import BaseAgent
    wd = tmp_path / "agent"
    wd.mkdir(exist_ok=True)
    agent = BaseAgent(service=make_mock_service(), agent_name="w", working_dir=wd)
    agent._build_launch_cmd = lambda: ["python", "-c", "pass"]
    return agent


def _capture_spawn(agent):
    """Return (script_text, popen_kwargs) from the watcher spawn."""
    with patch("lingtai_kernel.base_agent.lifecycle.subprocess.Popen") as mock:
        agent._perform_refresh()
    assert mock.called
    args, kwargs = mock.call_args
    return args[0][2], kwargs


# ---------------------------------------------------------------------------
# Watcher spawn kwargs are platform-aware
# ---------------------------------------------------------------------------

def test_watcher_spawn_posix_uses_start_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    agent = _make_agent(tmp_path)
    _script, kwargs = _capture_spawn(agent)
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs


def test_watcher_spawn_windows_omits_start_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.setattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
                        raising=False)
    agent = _make_agent(tmp_path)
    _script, kwargs = _capture_spawn(agent)
    # Python rejects start_new_session on native Windows -> must be absent.
    assert "start_new_session" not in kwargs
    assert kwargs.get("creationflags") == 0x200


def test_watcher_spawn_windows_no_flag_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.delattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
    agent = _make_agent(tmp_path)
    _script, kwargs = _capture_spawn(agent)
    assert "start_new_session" not in kwargs
    assert "creationflags" not in kwargs


# ---------------------------------------------------------------------------
# Generated watcher script content + fallback safety
# ---------------------------------------------------------------------------

def test_watcher_script_routes_through_process_control(tmp_path):
    agent = _make_agent(tmp_path)
    script, _kwargs = _capture_spawn(agent)
    # Relaunch spawn no longer hardcodes start_new_session.
    assert "start_new_session=True" not in script
    assert "**_detached_process_kwargs()" in script
    # Liveness/termination route through the platform-aware helpers, not raw
    # os.kill signalling of the duplicate PID.
    assert "os.kill(pid, signal.SIGTERM)" not in script
    assert "os.kill(pid, signal.SIGKILL)" not in script
    assert "_terminate_pid(pid" in script
    assert "_pid_is_alive(pid)" in script
    assert "from lingtai_kernel.process_control import" in script


def test_watcher_script_compiles(tmp_path):
    agent = _make_agent(tmp_path)
    script, _kwargs = _capture_spawn(agent)
    compile(script, "<watcher>", "exec")


def test_watcher_script_import_fallback_is_platform_aware(tmp_path):
    """If the kernel import fails in the child, fallback helpers must still
    avoid POSIX-only kwargs/signals on native Windows while preserving POSIX.
    """
    agent = _make_agent(tmp_path)
    script, _kwargs = _capture_spawn(agent)
    tree = ast.parse(script)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }
    # The except-branch fallbacks must define all three helper names and carry
    # Windows-safe fallback paths, not just raw POSIX os.kill/start_new_session.
    assert {"_detached_process_kwargs", "_pid_is_alive", "_terminate_pid"} <= defined
    assert "CREATE_NEW_PROCESS_GROUP" in script
    assert "taskkill" in script
    assert "tasklist" in script
