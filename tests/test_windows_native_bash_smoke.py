"""Native-Windows smoke tests for the bash capability.

Unlike ``tests/test_bash_windows.py`` — which *simulates* a native-Windows host
by monkeypatching ``_supports_killpg`` and fakes ``subprocess`` so no real shell
runs — this file spawns **real** ``powershell.exe`` through ``BashManager`` on an
actual ``win32`` host. It is the only place the Windows shell strategy
(``_spawn_kwargs`` → ``powershell.exe -NoProfile -NonInteractive -Command``) is
exercised end to end against a live PowerShell, plus the async job lifecycle and
the ``working_dir`` sandbox on real Windows path separators (#815's
``Path.relative_to`` containment fix).

We have no local Windows runner, so these run in GitHub Actions on
``windows-latest`` (`.github/workflows/windows-smoke.yml`). The whole module is
skipped on POSIX so macOS/Linux CI stays green — the simulated guards in
``tests/test_bash_windows.py`` cover the same code paths there.

Sleeps are short and every wait is bounded so a hung shell fails fast rather
than stalling CI.
"""
from __future__ import annotations

import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="native-Windows smoke: real powershell.exe only runs on win32 "
    "(simulated coverage lives in tests/test_bash_windows.py)",
)

from lingtai.core.bash import BashManager, BashPolicy


def _make_manager(tmp_path) -> BashManager:
    return BashManager(policy=BashPolicy.yolo(), working_dir=str(tmp_path))


def _poll_until_done(mgr: BashManager, job_id: str, *, timeout: float = 15.0) -> dict:
    """Poll a job until it reports ``done`` or the bound elapses.

    Returns the terminal poll result. Fails the test (rather than hanging) if the
    job never finishes within ``timeout`` — a real Windows shell that never exits
    is a smoke failure, not something to wait on forever.
    """
    deadline = time.monotonic() + timeout
    poll = {"status": "running"}
    while time.monotonic() < deadline:
        poll = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
        if poll["status"] != "running":
            return poll
        time.sleep(0.2)
    pytest.fail(f"async job {job_id} did not finish within {timeout}s: {poll}")


# ---------------------------------------------------------------------------
# Sync — a real PowerShell command runs and its stdout is captured.
# ---------------------------------------------------------------------------

def test_sync_powershell_captures_stdout(tmp_path):
    result = _make_manager(tmp_path).handle({"command": "Write-Output smoke-ok"})

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["ok"] is True
    assert "smoke-ok" in result["stdout"]


# ---------------------------------------------------------------------------
# Async — a real background job completes and poll returns its output.
# ---------------------------------------------------------------------------

def test_async_powershell_completes_and_poll_returns_output(tmp_path):
    mgr = _make_manager(tmp_path)
    started = mgr.handle({"command": "Write-Output async-smoke", "async": True})

    assert started["status"] == "ok"
    job_id = started["job_id"]
    assert job_id.startswith("job-")

    poll = _poll_until_done(mgr, job_id)
    assert poll["status"] == "done"
    assert poll["exit_code"] == 0
    assert "async-smoke" in poll["stdout"]


# ---------------------------------------------------------------------------
# Cancel — cancelling a long sleep returns an honest cancelled status.
# ---------------------------------------------------------------------------

def test_cancel_long_sleep_returns_cancelled(tmp_path):
    mgr = _make_manager(tmp_path)
    started = mgr.handle({"command": "Start-Sleep -Seconds 30", "async": True})

    assert started["status"] == "ok"
    job_id = started["job_id"]

    # It should still be running when we cancel it — no fixed race, just a quick
    # confirmation that the sleep is live before we tear the process tree down.
    running = mgr.handle({"action": "poll", "command": "", "job_id": job_id})
    assert running["status"] == "running"

    cancelled = mgr.handle({"action": "cancel", "command": "", "job_id": job_id})
    assert cancelled["status"] == "cancelled"
    assert cancelled["job_id"] == job_id


# ---------------------------------------------------------------------------
# working_dir — a nested path under the agent dir is accepted and used, on real
# Windows path separators. This is the live exercise of #815's Path.relative_to
# containment fix (a slash-prefix check would misjudge "\" vs "/").
# ---------------------------------------------------------------------------

def test_nested_working_dir_is_accepted_on_windows_paths(tmp_path):
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)

    result = _make_manager(tmp_path).handle(
        {"command": "Write-Output (Get-Location).Path", "working_dir": str(nested)}
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    # PowerShell echoes the resolved cwd; it must be the nested dir we asked for,
    # confirming the sandbox check let the containment through rather than
    # rejecting it on separator mismatch.
    assert "deeper" in result["stdout"]
