"""Native-Windows safety tests for the bash capability.

We have no real Windows runner, so every test simulates a native-Windows host
by monkeypatching ``bash._supports_killpg`` to ``False`` (mirrors the daemon's
``tests/test_daemon_runtime_helpers.py`` approach) and captures the resulting
``subprocess`` calls with small fakes — no real shells are spawned.

The POSIX/macOS/Linux behavior these guards preserve is exercised in
``tests/test_bash_async.py`` / ``tests/test_layers_bash.py`` on the real host.
"""
import subprocess

import pytest

from lingtai.core import bash
from lingtai.core.bash import BashManager, BashPolicy
from lingtai.core.daemon import runtime


def _win(monkeypatch):
    """Simulate a native-Windows host (no os.killpg / process groups).

    ``bash._spawn_kwargs`` and the daemon ``runtime`` helpers it reuses each
    call their *own* module's ``_supports_killpg``; on a real Windows host both
    return ``False`` together. Patch both so the simulation is consistent.
    """
    monkeypatch.setattr(bash, "_supports_killpg", lambda: False)
    monkeypatch.setattr(runtime, "_supports_killpg", lambda: False)


def _make_manager(tmp_path) -> BashManager:
    return BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))


# ---------------------------------------------------------------------------
# _spawn_kwargs — explicit PowerShell command line on Windows, shell=True POSIX
# ---------------------------------------------------------------------------

def test_spawn_kwargs_posix_uses_shell_true(monkeypatch):
    monkeypatch.setattr(bash, "_supports_killpg", lambda: True)
    spawn = bash._spawn_kwargs("echo hi | wc -l")
    assert spawn == {"args": "echo hi | wc -l", "shell": True}


def test_spawn_kwargs_windows_uses_explicit_powershell(monkeypatch):
    _win(monkeypatch)
    spawn = bash._spawn_kwargs("Get-ChildItem")
    assert spawn["shell"] is False
    assert spawn["args"] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-ChildItem",
    ]


# ---------------------------------------------------------------------------
# Sync run — criterion 2: explicit PowerShell path, not implicit cmd.exe
# ---------------------------------------------------------------------------

def test_sync_run_windows_invokes_powershell_not_cmd(monkeypatch, tmp_path):
    _win(monkeypatch)
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(bash.subprocess, "run", fake_run)

    result = _make_manager(tmp_path).handle({"command": "Write-Output ok"})

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    # Explicit PowerShell argv, shell=False — no implicit cmd.exe via shell=True.
    assert captured["kwargs"]["shell"] is False
    assert captured["args"][0] == "powershell.exe"
    assert captured["args"][-1] == "Write-Output ok"
    # cwd/timeout/capture semantics preserved.
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["cwd"] == str(tmp_path)


def test_sync_run_windows_timeout_reports_error(monkeypatch, tmp_path):
    _win(monkeypatch)

    def fake_run(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])

    monkeypatch.setattr(bash.subprocess, "run", fake_run)

    result = _make_manager(tmp_path).handle({"command": "Start-Sleep 99", "timeout": 1})
    assert result["status"] == "error"
    assert "timed out" in result["message"]


# ---------------------------------------------------------------------------
# Async spawn — criterion 3: CREATE_NEW_PROCESS_GROUP, no start_new_session,
# and the explicit PowerShell command line.
# ---------------------------------------------------------------------------

class _FakePopen:
    pid = 4321

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None


def test_async_spawn_windows_uses_creationflags_and_powershell(monkeypatch, tmp_path):
    _win(monkeypatch)
    monkeypatch.setattr(
        bash.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePopen(args, **kwargs)

    monkeypatch.setattr(bash.subprocess, "Popen", fake_popen)

    result = _make_manager(tmp_path).handle({"command": "Get-Date", "async": True})

    assert result["status"] == "ok"
    kwargs = captured["kwargs"]
    # Windows process-group isolation via creationflags, NOT start_new_session.
    assert kwargs.get("creationflags") == 0x200
    assert "start_new_session" not in kwargs
    assert kwargs["shell"] is False
    assert captured["args"][0] == "powershell.exe"
    assert captured["args"][-1] == "Get-Date"


# ---------------------------------------------------------------------------
# Cancel — criterion 4: taskkill tree teardown; no POSIX-only APIs.
# ---------------------------------------------------------------------------

def test_cancel_windows_with_handle_uses_taskkill(monkeypatch, tmp_path):
    _win(monkeypatch)
    monkeypatch.setattr(
        bash.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    monkeypatch.setattr(bash.subprocess, "Popen", lambda args, **kw: _FakePopen(args, **kw))

    taskkill_calls = []

    def fake_run(cmd, **kwargs):
        taskkill_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    # kill_process_group lives in daemon.runtime; patch subprocess.run there.
    monkeypatch.setattr(bash.kill_process_group.__globals__["subprocess"], "run", fake_run)
    # Guard: os.killpg must NOT be called on the simulated Windows host.
    monkeypatch.delattr(bash.os, "killpg", raising=False)

    mgr = _make_manager(tmp_path)
    started = mgr.handle({"command": "Start-Sleep 99", "async": True})
    cancel = mgr.handle({"action": "cancel", "command": "", "job_id": started["job_id"]})

    assert cancel["status"] == "cancelled"
    assert taskkill_calls, "expected taskkill to be invoked for tree teardown"
    assert taskkill_calls[0][0] == "taskkill"
    assert "/T" in taskkill_calls[0] and "/F" in taskkill_calls[0]


def test_kill_foreign_pid_windows_uses_taskkill_not_killpg(monkeypatch):
    _win(monkeypatch)
    calls = []
    monkeypatch.setattr(
        bash.subprocess, "run",
        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )

    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("os.killpg must not be called on Windows")

    monkeypatch.setattr(bash.os, "killpg", _boom, raising=False)

    BashManager._kill_foreign_pid(9999)

    assert calls == [["taskkill", "/PID", "9999", "/T", "/F"]]


# ---------------------------------------------------------------------------
# Poll — criterion 5: Windows liveness via tasklist, never os.kill(pid, 0).
# ---------------------------------------------------------------------------

def test_poll_foreign_pid_windows_alive_via_tasklist(monkeypatch):
    _win(monkeypatch)

    def fake_run(cmd, **kwargs):
        # tasklist prints a row containing the pid when the process exists.
        return subprocess.CompletedProcess(cmd, 0, stdout="powershell.exe  9999 Console", stderr="")

    monkeypatch.setattr(bash.subprocess, "run", fake_run)

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("os.kill must not be called on Windows")

    monkeypatch.setattr(bash.os, "kill", _boom, raising=False)

    assert BashManager._poll_foreign_pid(9999) is None  # still alive


def test_poll_foreign_pid_windows_dead_via_tasklist(monkeypatch):
    _win(monkeypatch)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="INFO: No tasks are running.", stderr="")

    monkeypatch.setattr(bash.subprocess, "run", fake_run)
    assert BashManager._poll_foreign_pid(9999) == -1  # gone


def test_poll_foreign_pid_windows_tasklist_missing_reports_running(monkeypatch):
    """No tasklist ⇒ we cannot prove death ⇒ report running, never fake done."""
    _win(monkeypatch)

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("tasklist")

    monkeypatch.setattr(bash.subprocess, "run", fake_run)
    assert BashManager._poll_foreign_pid(9999) is None


def test_poll_windows_foreign_instance_reports_done(monkeypatch, tmp_path):
    """A second manager instance (PID-file only) polling a finished Windows job."""
    _win(monkeypatch)
    monkeypatch.setattr(
        bash.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False
    )
    monkeypatch.setattr(bash.subprocess, "Popen", lambda args, **kw: _FakePopen(args, **kw))

    mgr = _make_manager(tmp_path)
    started = mgr.handle({"command": "Get-Date", "async": True})
    job_id = started["job_id"]

    # Simulate a *different* manager instance: drop the in-process handle so
    # poll takes the PID-file fallback path.
    mgr._open_handles.clear()

    # Pre-seed the log files the fallback poll reads.
    jobs = tmp_path / "system" / "jobs" / job_id
    (jobs / "stdout.log").write_text("done", encoding="utf-8")
    (jobs / "stderr.log").write_text("", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="INFO: No tasks are running.", stderr="")

    monkeypatch.setattr(bash.subprocess, "run", fake_run)

    poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
    assert poll["status"] == "done"
    assert poll["exit_code"] == -1  # sentinel: dead, real code unrecoverable
