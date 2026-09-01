"""Batch-2 B1: ASCII-only cmdline + UTF-8 stdin bootstrap for PowerShell.

Instead of passing the user command through ``pwsh -NoProfile -NonInteractive
-Command <cmd>`` (code-page mangling of the ``-Command`` argument, the
32,768-character Windows process command-line limit, UTF-8 in/out problems),
the dialect keeps the command line fixed and ASCII-only and delivers the real
(exit-wrapper-wrapped) command over UTF-8 stdin.

These tests run on Linux: invocation-shape tests always run, and the
end-to-end tests use a fake ``pwsh`` that implements the bootstrap contract
(read stdin to EOF, run the payload, propagate a native exit code).  The
Windows-native intent is covered by the real-pwsh tests in
``test_shell_windows_native.py`` (skipped here) and the PR body.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.adapters.windows.powershell import PowerShellDialect, _ASCII_BOOTSTRAP
from lingtai.tools.bash import ShellManager, ShellPolicy
from lingtai.tools.bash._shell_dialect import ShellInvocation
from tests._notification_store_helpers import notification_store_for


# A fake pwsh that implements only the bootstrap contract: read stdin to EOF,
# extract the user payload the exit wrapper embeds between ``& {`` and the
# final-success capture line, execute it as Python, and propagate a
# SystemExit code as the process exit code (PowerShell native-exit fidelity).
_FAKE_PWSH = r'''#!/usr/bin/env python3
"""Fake pwsh for Linux stdin-bootstrap plumbing tests."""
import sys


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    data = sys.stdin.read()
    # Marker proves the whole wrapped script arrived over stdin (UTF-8 text).
    sys.stdout.write(f"__fake_pwsh_stdin_chars__:{len(data)}\n")
    sys.stdout.flush()
    start = data.find("& {\n")
    end = data.find("\n    $global:__lingtai_success")
    if start < 0 or end < 0:
        sys.stderr.write("fake pwsh: wrapper markers not found\n")
        sys.exit(125)
    user = data[start + 4 : end]
    try:
        exec(user)
    except SystemExit as exc:
        code = exc.code
        sys.exit(0 if code is None else (code if isinstance(code, int) else 1))
    sys.exit(0)


if __name__ == "__main__":
    main()
'''


@pytest.fixture
def fake_pwsh(tmp_path: Path) -> Path:
    path = tmp_path / "fake-pwsh"
    path.write_text(_FAKE_PWSH, encoding="utf-8")
    path.chmod(0o755)
    return path


def _manager(root: Path, fake: Path) -> ShellManager:
    agent = SimpleNamespace(_notification_store=notification_store_for(root))
    return ShellManager(
        policy=ShellPolicy.yolo(),
        working_dir=str(root),
        agent=agent,
        dialect=PowerShellDialect(executable=str(fake)),
        max_output=200_000,
    )


def _poll_done(manager: ShellManager, job_id: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {"status": "running"}
    while time.monotonic() < deadline:
        latest = manager.handle({"action": "poll", "job_id": job_id})
        if latest.get("status") == "done":
            return latest
        assert latest.get("status") == "running", latest
        time.sleep(0.03)
    pytest.fail(f"async job {job_id} did not finish: {latest}")


# ---------------------------------------------------------------------------
# Invocation shape: the command line must stay fixed, ASCII-only, and free of
# user source; the wrapped command travels as the stdin payload.
# ---------------------------------------------------------------------------


def test_powershell_command_line_is_fixed_ascii_bootstrap():
    dialect = PowerShellDialect(executable="pwsh")
    command = "Write-Output 'héllo 世界'"
    invocation = dialect.make_invocation(command)
    args, kwargs = invocation.process_args()

    assert args == [
        "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        _ASCII_BOOTSTRAP,
    ]
    assert _ASCII_BOOTSTRAP.isascii()
    assert all(arg.isascii() for arg in args)
    assert command not in args
    assert kwargs == {"shell": False}

    # The bootstrap forces UTF-8 console encodings, reads stdin to EOF, and
    # appends an exit-status check before executing the payload.
    assert "InputEncoding" in _ASCII_BOOTSTRAP
    assert "OutputEncoding" in _ASCII_BOOTSTRAP
    assert "UTF8Encoding" in _ASCII_BOOTSTRAP
    assert "In.ReadToEnd" in _ASCII_BOOTSTRAP
    assert "ScriptBlock" in _ASCII_BOOTSTRAP
    assert "exit 1" in _ASCII_BOOTSTRAP

    # The existing exit wrapper is preserved byte-for-byte as the stdin payload.
    assert invocation.stdin_script == invocation.script
    assert command in invocation.stdin_script
    assert "$global:__lingtai_success = $?" in invocation.stdin_script
    assert "exit $global:__lingtai_native_exit" in invocation.stdin_script


def test_long_command_never_reaches_the_command_line():
    dialect = PowerShellDialect(executable="pwsh")
    command = "Write-Output '" + ("y" * 40_000) + "'"
    assert len(command) > 32_768  # beyond the Windows cmdline limit

    invocation = dialect.make_invocation(command)
    args, _ = invocation.process_args()

    cmdline = " ".join(args)
    assert len(cmdline) < 4_096  # fixed bootstrap; nothing user-controlled
    assert "yyyy" not in cmdline
    assert len(invocation.stdin_script) > 32_768
    assert command in invocation.stdin_script


def test_utf8_command_is_preserved_verbatim_in_stdin_payload():
    command = "Write-Output 'héllo 世界 🚀'"
    invocation = PowerShellDialect(executable="pwsh").make_invocation(command)
    assert command in invocation.stdin_script
    # The stdin payload must be encodable as UTF-8 for the pipe write.
    encoded = invocation.stdin_script.encode("utf-8")
    assert "héllo 世界 🚀".encode("utf-8") in encoded


def test_stdin_script_serializes_as_optional_sixth_key():
    invocation = PowerShellDialect(executable="pwsh").make_invocation("Write-Output hi")
    data = invocation.to_dict()
    assert data["stdin_script"] == invocation.script
    assert ShellInvocation.from_dict(data) == invocation

    # Pre-B1 five-key records remain readable: stdin_script defaults to None.
    legacy = {
        key: value
        for key, value in data.items()
        if key not in {"stdin_script", "command_line"}
    }
    assert set(legacy) == {"script", "executable", "argv", "encoding", "errors"}
    assert ShellInvocation.from_dict(legacy).stdin_script is None


def test_process_args_with_and_without_stdin_script():
    stdin_form = ShellInvocation(
        script="wrapped", stdin_script="wrapped",
        executable="pwsh", argv=("-Command", "bootstrap"),
    )
    assert stdin_form.process_args() == (
        ["pwsh", "-Command", "bootstrap"], {"shell": False}
    )
    classic = ShellInvocation(script="wrapped", executable="pwsh", argv=("-Command",))
    assert classic.process_args() == (["pwsh", "-Command", "wrapped"], {"shell": False})


def test_invocation_rejects_empty_stdin_script():
    with pytest.raises(ValueError):
        ShellInvocation(script="x", stdin_script="")
    with pytest.raises(ValueError):
        ShellInvocation(script="x", stdin_script="   ")


def test_stdin_script_without_argv_fails_loudly():
    # A stdin payload needs a fixed argv command line (the bootstrap) to
    # receive it; silently dropping it would execute nothing.
    invocation = ShellInvocation(script="wrapped", stdin_script="wrapped")
    with pytest.raises(ValueError, match="requires the argv form"):
        invocation.process_args()


# ---------------------------------------------------------------------------
# End-to-end plumbing through the real ShellManager sync runner.  The fake
# pwsh executes the payload as Python, so the user commands below are Python
# snippets; what is under test is the transport (UTF-8 stdin delivery,
# >32KB payloads, exit-code propagation), not PowerShell syntax.
# ---------------------------------------------------------------------------


def test_sync_long_command_over_32kb_arrives_via_stdin(tmp_path, fake_pwsh):
    command = "print('" + ("y" * 40_000) + "')"
    assert len(command) > 32_768
    manager = _manager(tmp_path, fake_pwsh)

    result = manager.handle({"command": command, "timeout": 30})

    assert result["status"] == "ok", result
    assert result["exit_code"] == 0
    marker = re.search(r"__fake_pwsh_stdin_chars__:(\d+)", result["stdout"])
    assert marker is not None, result
    assert int(marker.group(1)) > 32_768
    assert "y" * 100 in result["stdout"]


def test_sync_utf8_round_trip_via_stdin(tmp_path, fake_pwsh):
    manager = _manager(tmp_path, fake_pwsh)

    result = manager.handle({"command": "print('héllo 世界 🚀')", "timeout": 30})

    assert result["status"] == "ok", result
    assert result["exit_code"] == 0
    assert "héllo 世界 🚀" in result["stdout"]


def test_sync_exit_code_reflects_native_failure(tmp_path, fake_pwsh):
    manager = _manager(tmp_path, fake_pwsh)

    result = manager.handle({"command": "import sys; sys.exit(7)", "timeout": 30})

    assert result["status"] == "ok", result
    assert result["exit_code"] == 7
    assert result["ok"] is False
    assert result["command_status"] == "failed"


def test_async_long_command_via_stdin_through_supervisor(tmp_path, fake_pwsh):
    manager = _manager(tmp_path, fake_pwsh)
    command = "print('" + ("z" * 40_000) + "')"

    started = manager.handle({"command": command, "async": True, "reminder": 30})
    assert started["status"] == "ok", started

    terminal = _poll_done(manager, started["job_id"])
    assert terminal["exit_status_known"] is True
    assert terminal["exit_code"] == 0
    assert "z" * 100 in terminal["stdout"]
