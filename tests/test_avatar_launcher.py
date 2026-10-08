"""Focused Contract tests for the avatar-local launcher boundary."""
import os
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.tools.avatar import AvatarManager
from lingtai.tools.avatar._launcher import (
    AvatarLaunchReceipt,
    AvatarLaunchRequest,
    DerivedAvatarState,
    derived_avatar_state_path,
    legacy_derived_avatar_state_path,
    probe_derived_avatar_state,
)
from lingtai.adapters.posix.avatar_launcher import PosixAvatarLauncherAdapter


def test_posix_launch_contract_and_release(tmp_path):
    process = MagicMock(pid=417, poll=MagicMock(return_value=None))
    stderr = tmp_path / "logs" / "spawn.stderr"
    request = AvatarLaunchRequest(("python", "-m", "lingtai", "run", "/avatar"), stderr)
    with patch("lingtai.adapters.posix.avatar_launcher.subprocess.Popen", return_value=process) as popen:
        receipt = PosixAvatarLauncherAdapter().launch(request)
    assert receipt == AvatarLaunchReceipt(417, process)
    kwargs = popen.call_args.kwargs
    assert popen.call_args.args == (["python", "-m", "lingtai", "run", "/avatar"],)
    assert kwargs["stdin"] is __import__("subprocess").DEVNULL
    assert kwargs["stdout"] is __import__("subprocess").DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["stderr"].closed is True
    assert "cwd" not in kwargs
    assert "env" not in kwargs
    adapter = PosixAvatarLauncherAdapter()
    adapter.release(process)
    process.poll.assert_called_once()
    adapter.terminate(process)
    adapter.force_terminate(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


def test_posix_launch_propagates_explicit_derived_child_requirement(tmp_path):
    """A derived-avatar marker tightens child boot without carrying authority."""
    process = MagicMock(pid=418, poll=MagicMock(return_value=None))
    stderr = tmp_path / "logs" / "spawn.stderr"
    request = AvatarLaunchRequest(
        ("python", "-m", "lingtai", "run", "/avatar"),
        stderr,
        environment={"LINGTAI_DERIVED_AVATAR_EXECUTION": "1"},
    )
    with patch("lingtai.adapters.posix.avatar_launcher.subprocess.Popen", return_value=process) as popen:
        PosixAvatarLauncherAdapter().launch(request)
    assert popen.call_args.kwargs["env"]["LINGTAI_DERIVED_AVATAR_EXECUTION"] == "1"


@pytest.mark.parametrize("derived", [False, True])
def test_avatar_launch_does_not_inherit_parent_resident_acp_opt_in(
    tmp_path, monkeypatch, derived,
):
    """An enabled parent must not expose an ACP socket on its Avatar child."""
    parent = tmp_path / "parent"
    child = tmp_path / "child"
    monkeypatch.setenv("LINGTAI_ACP_SOCKET_AGENT_DIR", str(parent.resolve()))
    environment = {"LINGTAI_DERIVED_AVATAR_EXECUTION": "1"} if derived else None
    request = AvatarLaunchRequest(
        ("python", "-m", "lingtai", "run", str(child)),
        tmp_path / "logs" / "spawn.stderr",
        environment=environment,
    )
    process = MagicMock(pid=419)
    with patch(
        "lingtai.adapters.posix.avatar_launcher.subprocess.Popen", return_value=process
    ) as popen:
        PosixAvatarLauncherAdapter().launch(request)
    child_environment = popen.call_args.kwargs["env"]
    assert "LINGTAI_ACP_SOCKET_AGENT_DIR" not in child_environment
    assert child_environment.get("LINGTAI_DERIVED_AVATAR_EXECUTION") == (
        "1" if derived else None
    )


def test_derived_avatar_state_probe_keeps_io_failure_distinct_from_absence(
    tmp_path, monkeypatch,
):
    """Only a missing marker relaxes the durable child restriction."""
    marker = derived_avatar_state_path(tmp_path)
    assert probe_derived_avatar_state(tmp_path) is DerivedAvatarState.ABSENT
    marker.write_text("present", encoding="utf-8")
    assert probe_derived_avatar_state(tmp_path) is DerivedAvatarState.PRESENT

    real_lstat = Path.lstat

    def fail_for_marker(path):
        if path == marker:
            raise PermissionError("simulated marker read failure")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_for_marker)
    assert probe_derived_avatar_state(tmp_path) is DerivedAvatarState.UNKNOWN


def test_legacy_derived_marker_keeps_cli_boot_restrictive(tmp_path):
    """An upgrade preserves the restriction on the available child boot path."""
    from lingtai import cli

    legacy_marker = legacy_derived_avatar_state_path(tmp_path)
    legacy_marker.parent.mkdir(parents=True)
    legacy_marker.write_text("present", encoding="utf-8")

    assert probe_derived_avatar_state(tmp_path) is DerivedAvatarState.PRESENT
    assert cli._derived_avatar_requires_admission(tmp_path) is True


def test_unreadable_legacy_derived_marker_keeps_cli_boot_restrictive(
    tmp_path, monkeypatch,
):
    """A failed compatibility read cannot relax child boot restrictions."""
    from lingtai import cli

    legacy_marker = legacy_derived_avatar_state_path(tmp_path)
    legacy_marker.parent.mkdir(parents=True)
    legacy_marker.write_text("present", encoding="utf-8")
    real_lstat = Path.lstat

    def fail_for_legacy_marker(path):
        if path == legacy_marker:
            raise PermissionError("simulated legacy marker read failure")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_for_legacy_marker)

    assert probe_derived_avatar_state(tmp_path) is DerivedAvatarState.UNKNOWN
    assert cli._derived_avatar_requires_admission(tmp_path) is True


def test_manager_marks_driver_derived_avatar_child_as_requiring_authority(tmp_path):
    """A Driver-derived avatar launch carries only a restrictive boot hint."""
    launcher = MagicMock()
    launcher.launch.return_value = AvatarLaunchReceipt(419, object())
    manager = AvatarManager(SimpleNamespace(), launcher=launcher)
    with patch("lingtai.venv_resolve.resolve_venv", return_value=tmp_path), patch(
        "lingtai.venv_resolve.venv_python", return_value=tmp_path / "python"
    ):
        manager._launch(tmp_path, derived_child=True)
    request = launcher.launch.call_args.args[0]
    assert request.environment == {"LINGTAI_DERIVED_AVATAR_EXECUTION": "1"}


def test_manager_keeps_generic_avatar_launch_free_of_driver_hints(tmp_path):
    launcher = MagicMock()
    launcher.launch.return_value = AvatarLaunchReceipt(419, object())
    manager = AvatarManager(SimpleNamespace(), launcher=launcher)
    with patch("lingtai.venv_resolve.resolve_venv", return_value=tmp_path), patch(
        "lingtai.venv_resolve.venv_python", return_value=tmp_path / "python"
    ):
        manager._launch(tmp_path)
    request = launcher.launch.call_args.args[0]
    assert request.environment is None
    assert request.authority_lease is None


def test_posix_launch_passes_only_the_one_shot_driver_child_endpoint(tmp_path):
    """The child receives a lease endpoint, never a root authority descriptor."""
    from lingtai.adapters.acp.driver_authority import DriverChildEndpointLease

    process = MagicMock(pid=419, poll=MagicMock(return_value=None))
    client, driver = socket.socketpair()
    request = AvatarLaunchRequest(
        ("python", "-m", "lingtai", "run", "/avatar"),
        tmp_path / "logs" / "spawn.stderr",
        authority_lease=DriverChildEndpointLease(client),
    )
    try:
        with patch(
            "lingtai.adapters.posix.avatar_launcher.subprocess.Popen",
            return_value=process,
        ) as popen:
            PosixAvatarLauncherAdapter().launch(request)
        kwargs = popen.call_args.kwargs
        child_fd = kwargs["pass_fds"]
        assert len(child_fd) == 1
        assert kwargs["env"]["LINGTAI_DRIVER_AUTHORITY_FD"] == str(child_fd[0])
        assert kwargs["close_fds"] is True
        with pytest.raises(OSError):
            os.fstat(child_fd[0])
    finally:
        driver.close()


def test_posix_launch_closes_driver_endpoint_when_popen_fails(tmp_path):
    """A consumed endpoint is still released if process creation aborts."""
    from lingtai.adapters.acp.driver_authority import DriverChildEndpointLease

    client, driver = socket.socketpair()
    driver.settimeout(1)
    request = AvatarLaunchRequest(
        ("python", "-m", "lingtai", "run", "/avatar"),
        tmp_path / "logs" / "spawn.stderr",
        authority_lease=DriverChildEndpointLease(client),
    )
    try:
        with patch(
            "lingtai.adapters.posix.avatar_launcher.subprocess.Popen",
            side_effect=OSError("launch failed"),
        ):
            with pytest.raises(OSError, match="launch failed"):
                PosixAvatarLauncherAdapter().launch(request)
        assert driver.recv(1) == b""
    finally:
        driver.close()


def test_manager_boot_policy_uses_opaque_port_and_preserves_precedence(tmp_path):
    launcher = MagicMock()
    manager = AvatarManager(SimpleNamespace(), launcher=launcher)
    receipt = AvatarLaunchReceipt(123, object())
    launcher.poll.return_value = 37
    stderr = tmp_path / "spawn.stderr"
    stderr.write_bytes(b"x" * 3000)
    status, error = manager._wait_for_boot(tmp_path, receipt, stderr)
    assert status == "failed"
    assert error.startswith("process exited with code 37: ...[truncated]")
    assert launcher.poll.call_args.args == (receipt.handle,)

    launcher.reset_mock()
    (tmp_path / ".agent.heartbeat").write_text("now")
    launcher.poll.return_value = 99
    assert manager._wait_for_boot(tmp_path, receipt, stderr) == ("ok", None)
    launcher.poll.assert_not_called()  # heartbeat remains first observation


def test_manager_slow_observation_does_not_terminate_child(tmp_path):
    launcher = MagicMock()
    manager = AvatarManager(SimpleNamespace(), launcher=launcher)
    launcher.poll.return_value = None
    with patch("lingtai.tools.avatar.time.monotonic", side_effect=[0.0, 0.1, 5.0]), \
         patch("lingtai.tools.avatar.time.sleep") as sleep:
        assert manager._wait_for_boot(
            tmp_path, AvatarLaunchReceipt(1, "opaque"), tmp_path / "missing"
        ) == ("slow", None)
    launcher.poll.assert_called_once_with("opaque")
    sleep.assert_called_once_with(manager._BOOT_POLL_INTERVAL)
    launcher.terminate.assert_not_called()
    launcher.force_terminate.assert_not_called()


def test_selector_selects_posix_and_fails_loud_for_unsupported():
    from lingtai.adapters import avatar_launcher

    with patch.object(avatar_launcher.os, "name", "posix"), \
         patch.object(avatar_launcher.sys, "platform", "linux"):
        assert isinstance(avatar_launcher.select_avatar_launcher(), PosixAvatarLauncherAdapter)

    # ``nt`` is now a supported platform (Windows adapter) — its positive
    # selector assertion lives in tests/test_avatar_launcher_windows.py. Only a
    # genuinely unrecognized ``os.name`` still fails loudly here.
    for name, platform in (("other", "other"),):
        with patch.object(avatar_launcher.os, "name", name), \
             patch.object(avatar_launcher.sys, "platform", platform):
            try:
                avatar_launcher.select_avatar_launcher()
            except NotImplementedError as exc:
                assert "No production avatar launcher" in str(exc)
            else:
                raise AssertionError("unsupported platform must fail loudly")
