"""Windows avatar-launcher adapter: cross-platform wiring pins + native tier.

The wiring/exact-argument tests run everywhere: the selector routing and the
exact ``subprocess.Popen`` kwargs are pinned on POSIX CI by monkeypatching
``os.name`` and ``subprocess.Popen``; importing the Windows adapter (and its
``_win32`` helper) is safe on every platform. The native tier
(``windows_mechanism``) proves a real detached launch, poll, forceful
termination, and idempotent release only on Windows.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.adapters.windows import _win32
from lingtai.adapters.windows.avatar_launcher import WindowsAvatarLauncherAdapter
from lingtai.tools.avatar._launcher import AvatarLaunchReceipt, AvatarLaunchRequest

windows_mechanism = pytest.mark.skipif(
    os.name != "nt", reason="native Windows detached spawn requires Windows"
)


def test_selector_returns_windows_adapter_when_os_name_is_nt():
    """os.name == 'nt' selects the Windows adapter via a safe lazy import."""
    from lingtai.adapters import avatar_launcher

    with patch.object(avatar_launcher.os, "name", "nt"), \
         patch.object(avatar_launcher.sys, "platform", "win32"):
        adapter = avatar_launcher.select_avatar_launcher()
    assert isinstance(adapter, WindowsAvatarLauncherAdapter)


def test_windows_launch_uses_detached_flags_and_disconnects_streams(tmp_path):
    """Exact Popen kwargs: detached creation flags, DEVNULL stdin/stdout, owned
    binary stderr closed in the parent, close_fds, and NO start_new_session."""
    process = MagicMock(pid=909, poll=MagicMock(return_value=None))
    stderr = tmp_path / "logs" / "spawn.stderr"
    request = AvatarLaunchRequest(("python", "-m", "lingtai", "run", "/avatar"), stderr)
    with patch(
        "lingtai.adapters.windows.avatar_launcher.subprocess.Popen",
        return_value=process,
    ) as popen:
        receipt = WindowsAvatarLauncherAdapter().launch(request)

    assert receipt == AvatarLaunchReceipt(909, process)
    assert popen.call_args.args == (["python", "-m", "lingtai", "run", "/avatar"],)
    kwargs = popen.call_args.kwargs
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["creationflags"] == _win32.DETACHED_CREATIONFLAGS
    assert kwargs["close_fds"] is True
    # Owned binary-write stderr handle, closed in the parent after launch.
    assert kwargs["stderr"].mode == "wb"
    assert kwargs["stderr"].closed is True
    # No POSIX session knob and no cwd/env override leaked through.
    assert "start_new_session" not in kwargs
    assert "cwd" not in kwargs
    assert "env" not in kwargs
    # The parent-side stderr directory was created for the child to write into.
    assert stderr.parent.is_dir()


def test_windows_terminate_and_force_terminate_both_forceful():
    """Owner decision U7: both map to the handle's forceful kill/terminate;
    the adapter never pretends a graceful tier exists."""
    adapter = WindowsAvatarLauncherAdapter()
    handle = MagicMock()
    adapter.terminate(handle)
    adapter.force_terminate(handle)
    handle.terminate.assert_called_once_with()
    handle.kill.assert_called_once_with()


def test_windows_poll_delegates_exact_return_code():
    adapter = WindowsAvatarLauncherAdapter()
    assert adapter.poll(SimpleNamespace(poll=lambda: None)) is None
    assert adapter.poll(SimpleNamespace(poll=lambda: 42)) == 42


def test_windows_release_never_raises_and_never_terminates():
    """release() is a best-effort observation: it polls once, swallows
    OSError/ValueError, and never terminates a live handle."""
    adapter = WindowsAvatarLauncherAdapter()

    live = MagicMock(poll=MagicMock(return_value=None))
    adapter.release(live)
    live.poll.assert_called_once()
    live.terminate.assert_not_called()
    live.kill.assert_not_called()

    for exc in (OSError("gone"), ValueError("closed")):
        raising = MagicMock(poll=MagicMock(side_effect=exc))
        adapter.release(raising)  # must not raise
        raising.terminate.assert_not_called()
        raising.kill.assert_not_called()


@windows_mechanism
def test_windows_native_launch_poll_force_terminate_and_release(tmp_path):
    """Real detached child: alive → poll None; force_terminate → poll not-None;
    release is idempotent and non-raising."""
    stderr = tmp_path / "logs" / "spawn.stderr"
    request = AvatarLaunchRequest(
        (sys.executable, "-c", "import time; time.sleep(60)"), stderr
    )
    adapter = WindowsAvatarLauncherAdapter()
    receipt = adapter.launch(request)
    try:
        assert receipt.pid > 0
        assert adapter.poll(receipt.handle) is None  # still alive
        adapter.force_terminate(receipt.handle)
        receipt.handle.wait(timeout=10)
        assert adapter.poll(receipt.handle) is not None  # exited
    finally:
        adapter.force_terminate(receipt.handle)
        adapter.release(receipt.handle)
        adapter.release(receipt.handle)  # idempotent, never raises
    assert Path(stderr).is_file()
