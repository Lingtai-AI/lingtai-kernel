"""Native-Windows detached-spawn safety for the avatar launch path.

``AvatarManager._launch`` spawns ``lingtai-agent run <dir>`` fully detached.
On POSIX that means ``start_new_session=True``; on native Windows Python rejects
that kwarg, so the launch must use ``creationflags=CREATE_NEW_PROCESS_GROUP``
(or degrade to a plain spawn when the flag is unavailable). These tests patch
Popen and venv resolution so no real child is spawned.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from lingtai_kernel import process_control as pc
from lingtai.core.avatar import AvatarManager


def _capture_launch_kwargs(tmp_path):
    wd = tmp_path / "avatar"
    wd.mkdir()
    captured = {}

    class _FakeProc:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    with patch("lingtai.venv_resolve.resolve_venv", return_value=None), \
         patch("lingtai.venv_resolve.venv_python", return_value="python"), \
         patch("subprocess.Popen", side_effect=fake_popen):
        proc, stderr_path = AvatarManager._launch(wd)
    assert isinstance(stderr_path, Path)
    return captured["kwargs"]


def test_avatar_launch_posix_uses_start_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: True)
    kwargs = _capture_launch_kwargs(tmp_path)
    assert kwargs.get("start_new_session") is True
    assert "creationflags" not in kwargs


def test_avatar_launch_windows_uses_creationflags(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.setattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200,
                        raising=False)
    kwargs = _capture_launch_kwargs(tmp_path)
    assert "start_new_session" not in kwargs
    assert kwargs.get("creationflags") == 0x200


def test_avatar_launch_windows_no_flag_degrades(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "supports_posix_signals", lambda: False)
    monkeypatch.delattr(pc.subprocess, "CREATE_NEW_PROCESS_GROUP", raising=False)
    kwargs = _capture_launch_kwargs(tmp_path)
    assert "start_new_session" not in kwargs
    assert "creationflags" not in kwargs
