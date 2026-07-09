"""Native-Windows detached-spawn safety for the CPR (resuscitate) path.

``Agent._cpr_agent`` relaunches a suspended agent as a detached process. On
POSIX that uses ``start_new_session=True``; native Windows rejects that kwarg,
so it must use ``creationflags=CREATE_NEW_PROCESS_GROUP`` (or degrade to a plain
spawn). We drive ``_cpr_agent`` as an unbound method against a minimal stub so
no full Agent/LLM setup is needed, and patch Popen so nothing really launches.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lingtai_kernel import process_control as pc
from lingtai.agent import Agent


def _make_target(base: Path) -> Path:
    target = base / "sleeper"
    target.mkdir()
    (target / ".agent.json").write_text(json.dumps({"admin": "root"}), encoding="utf-8")
    (target / "init.json").write_text(json.dumps({}), encoding="utf-8")
    return target


def _run_cpr(base: Path):
    """Call Agent._cpr_agent as an unbound method on a minimal stub, returning
    the captured Popen kwargs. is_alive is patched True so CPR reports success
    immediately without polling a real heartbeat.
    """
    target = _make_target(base)
    caller_wd = base / "caller"
    caller_wd.mkdir()
    stub = SimpleNamespace(_working_dir=caller_wd, _log=lambda *a, **k: None)

    captured = {}

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["kwargs"] = kwargs
        return _FakeProc()

    with patch("lingtai.venv_resolve.resolve_venv", return_value=None), \
         patch("lingtai.venv_resolve.venv_python", return_value="python"), \
         patch("lingtai_kernel.handshake.is_alive", return_value=True), \
         patch("subprocess.Popen", side_effect=fake_popen):
        result = Agent._cpr_agent(stub, "sleeper")
    assert result is True
    return captured["kwargs"]


def test_cpr_posix_uses_start_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    kwargs = _run_cpr(tmp_path)
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs


def test_cpr_windows_uses_creationflags(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.setattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
                        raising=False)
    kwargs = _run_cpr(tmp_path)
    assert "start_new_session" not in kwargs
    assert kwargs.get("creationflags") == 0x200


def test_cpr_windows_no_flag_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.delattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
    kwargs = _run_cpr(tmp_path)
    assert "start_new_session" not in kwargs
    assert "creationflags" not in kwargs
