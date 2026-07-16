"""Native Windows/PowerShell evidence for the canonical shell PR1.

These tests intentionally skip outside Windows.  The pull-request workflow runs
them on ``windows-latest`` with PowerShell 7 available as ``pwsh``; mock tests
alone are not evidence for Job Object, msvcrt lock, or PowerShell exit behavior.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters.windows.powershell_state_lock import WindowsShellStateLockAdapter
from lingtai.tools.bash import ShellManager, ShellPolicy


_PWSH = shutil.which("pwsh")
pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="native Windows contract"),
    pytest.mark.skipif(_PWSH is None, reason="PowerShell 7 (pwsh) is required"),
]


class _NotificationSink:
    """Small no-I/O notification Port sufficient for completion publication."""

    def publish(self, channel: str, payload: dict) -> None:
        return None

    def compare_update_channel(self, channel, expected_version, mutator):
        _payload, _changed, value = mutator({})
        return SimpleNamespace(value=value)


def _manager(root: Path) -> ShellManager:
    agent = SimpleNamespace(_notification_store=_NotificationSink())
    return ShellManager(
        policy=ShellPolicy.yolo(),
        working_dir=str(root),
        agent=agent,
    )


def _poll_terminal(manager: ShellManager, job_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {"status": "not-polled"}
    while time.monotonic() < deadline:
        latest = manager.handle({"action": "poll", "job_id": job_id})
        if latest.get("status") == "done":
            return latest
        assert latest.get("status") == "running", latest
        time.sleep(0.05)
    pytest.fail(f"async job {job_id} did not finish: {latest}")


def _wait_for_file(path: Path, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {path}")


def _ps_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def test_native_powershell_sync_captures_streams_and_exact_native_exit(tmp_path):
    manager = _manager(tmp_path)
    result = manager.handle({
        "command": (
            "Write-Output 'native-stdout'; "
            "[Console]::Error.WriteLine('native-stderr'); "
            "& $env:ComSpec /d /c exit 7"
        ),
        "timeout": 10,
    })

    assert result["status"] == "ok"
    assert result["exit_code"] == 7
    assert result["ok"] is False
    assert result["command_status"] == "failed"
    assert "native-stdout" in result["stdout"]
    assert "native-stderr" in result["stderr"]


def test_native_powershell_failure_and_sync_timeout_are_explicit(tmp_path):
    manager = _manager(tmp_path)

    failed = manager.handle({"command": "Write-Error 'powershell-failure'", "timeout": 10})
    assert failed["status"] == "ok"
    assert failed["exit_code"] == 1
    assert failed["ok"] is False
    assert "powershell-failure" in failed["stderr"]

    sticky_native_then_powershell = manager.handle({
        "command": (
            "& $env:ComSpec /d /c exit 7; "
            "Write-Error 'final-powershell-failure'"
        ),
        "timeout": 10,
    })
    assert sticky_native_then_powershell["status"] == "ok"
    assert sticky_native_then_powershell["exit_code"] == 1

    native_then_success = manager.handle({
        "command": "& $env:ComSpec /d /c exit 7; Write-Output 'final-success'",
        "timeout": 10,
    })
    assert native_then_success["status"] == "ok"
    assert native_then_success["exit_code"] == 0

    timed_out = manager.handle({"command": "Start-Sleep -Seconds 10", "timeout": 0.5})
    assert timed_out["status"] == "error"
    assert "timed out" in timed_out["message"].lower()


def test_native_powershell_async_poll_preserves_streams_and_exit_code(tmp_path):
    manager = _manager(tmp_path)
    started = manager.handle({
        "command": (
            "Write-Output 'async-stdout'; "
            "[Console]::Error.WriteLine('async-stderr'); "
            "& $env:ComSpec /d /c exit 7"
        ),
        "async": True,
        "reminder": 30,
    })

    assert started["status"] == "ok", started
    terminal = _poll_terminal(manager, started["job_id"])
    assert terminal["exit_status_known"] is True
    assert terminal["exit_code"] == 7
    assert terminal["ok"] is False
    assert terminal["command_status"] == "failed"
    assert "async-stdout" in terminal["stdout"]
    assert "async-stderr" in terminal["stderr"]


def test_native_windows_state_lock_serializes_processes(tmp_path):
    lock_root = tmp_path / "state-lock"
    lock_root.mkdir()
    held = lock_root / "held"
    repo_root = Path(__file__).resolve().parents[1]
    child = r"""
import sys
import time
from pathlib import Path
from lingtai.adapters.windows.powershell_state_lock import WindowsShellStateLockAdapter

root = Path(sys.argv[1])
with WindowsShellStateLockAdapter().exclusive(root):
    (root / "held").write_text("held", encoding="utf-8")
    time.sleep(0.8)
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root / "src") + os.pathsep + env.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-c", child, str(lock_root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    _wait_for_file(held)

    began = time.monotonic()
    with WindowsShellStateLockAdapter().exclusive(lock_root):
        elapsed = time.monotonic() - began

    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, {"stdout": stdout, "stderr": stderr}
    assert elapsed >= 0.3


def test_native_job_object_cancel_after_root_exit_terminates_descendant_tree(tmp_path):
    manager = _manager(tmp_path)
    ready = tmp_path / "root-exited.ready"
    survived = tmp_path / "root-exited-survived.txt"

    child_script = (
        "Start-Sleep -Seconds 5; "
        f"Set-Content -LiteralPath {_ps_literal(survived)} -Value 'survived'"
    )
    encoded_child = base64.b64encode(child_script.encode("utf-16le")).decode("ascii")
    parent_script = (
        "$null = Start-Process -FilePath (Get-Command pwsh).Source "
        "-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive',"
        f"'-EncodedCommand','{encoded_child}'); "
        f"Set-Content -LiteralPath {_ps_literal(ready)} -Value 'root spawned'; "
        "exit 0"
    )

    started = manager.handle({
        "command": parent_script,
        "async": True,
        "reminder": 30,
    })
    assert started["status"] == "ok", started
    _wait_for_file(ready)
    cancelled = manager.handle({"action": "cancel", "job_id": started["job_id"]})
    assert cancelled == {"status": "cancelled", "job_id": started["job_id"]}

    state_path = tmp_path / "system" / "jobs" / started["job_id"] / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["exit_status_known"] is True
    assert state["cancellation_outcome"] == "group_cancelled"
    time.sleep(1.0)
    assert not survived.exists(), "a descendant escaped after the root pwsh exited"


def test_native_job_object_cancel_terminates_descendant_tree(tmp_path):
    manager = _manager(tmp_path)
    ready = tmp_path / "descendant.ready"
    survived = tmp_path / "descendant-survived.txt"

    child_script = (
        "Start-Sleep -Milliseconds 1200; "
        f"Set-Content -LiteralPath {_ps_literal(survived)} -Value 'survived'"
    )
    encoded_child = base64.b64encode(child_script.encode("utf-16le")).decode("ascii")
    parent_script = (
        "$child = Start-Process -FilePath (Get-Command pwsh).Source "
        "-ArgumentList @('-NoLogo','-NoProfile','-NonInteractive',"
        f"'-EncodedCommand','{encoded_child}') -PassThru; "
        f"Set-Content -LiteralPath {_ps_literal(ready)} -Value $child.Id; "
        "Wait-Process -Id $child.Id"
    )

    started = manager.handle({
        "command": parent_script,
        "async": True,
        "reminder": 30,
    })
    assert started["status"] == "ok", started
    _wait_for_file(ready)

    cancelled = manager.handle({"action": "cancel", "job_id": started["job_id"]})
    assert cancelled == {"status": "cancelled", "job_id": started["job_id"]}

    state_path = tmp_path / "system" / "jobs" / started["job_id"] / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["exit_status_known"] is True
    assert state["cancellation_outcome"] == "group_cancelled"

    time.sleep(1.5)
    assert not survived.exists(), "a descendant escaped the owned Windows Job Object"
