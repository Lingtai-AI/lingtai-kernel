"""macOS shell adapter tests: login-shell detection, PATH guarantee, process-group kill.

Covers the PR batch decisions for macOS (no cgroups/Job Objects, no
``/usr/bin/timeout``, GUI-launched apps inherit launchd's minimal PATH):

- Darwin POSIX invocations become ``[shell, "-lc", script]`` argv through
  the user's login shell (``/bin/zsh`` or ``/bin/bash``), never ``shell=True``.
- The child env prepends ``/opt/homebrew/bin`` and ``/usr/local/bin`` and
  strips credential-shaped variables; Linux keeps the historical behavior.
- POSIX spawns own their process group (``start_new_session=True``) and
  cancellation/timeout kills the whole group gracefully-then-KILL.
"""
from __future__ import annotations

import os
import signal
import threading
import time

import pytest

from lingtai.adapters.posix.bash_process import PosixBashAsyncProcessAdapter
from lingtai.tools.bash import BashManager, BashPolicy
from lingtai.tools.bash import _shell_dialect as dialect_mod
from lingtai.tools.bash._shell_dialect import (
    ShellInvocation,
    ShellKind,
    _darwin_default_shell,
    make_invocation_for_kind,
    posix_login_env,
)


class _SyncOnlyAgent:
    """Strict stand-in for manager tests that exercise only synchronous runs."""


# ---------------------------------------------------------------------------
# macOS shell-kind detection (login-shell argv form)
# ---------------------------------------------------------------------------


def test_macos_posix_invocation_uses_login_shell_argv(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(dialect_mod, "_darwin_default_shell", lambda env=None: "/bin/zsh")

    invocation = make_invocation_for_kind(ShellKind.POSIX, "echo hi")
    assert invocation.executable == "/bin/zsh"
    assert invocation.argv == ("-lc",)
    assert invocation.encoding == "utf-8"
    assert invocation.errors == "replace"
    # The Homebrew PATH guarantee is attached as the child env.
    assert invocation.env is not None
    assert invocation.env["PATH"].startswith("/opt/homebrew/bin:")

    args, kwargs = invocation.process_args()
    assert args == ["/bin/zsh", "-lc", "echo hi"]
    assert kwargs["shell"] is False
    assert kwargs["env"] == invocation.env
    # The env survives durable async-state serialization.
    assert ShellInvocation.from_dict(invocation.to_dict()) == invocation


def test_macos_posix_invocation_respects_explicit_executable(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    invocation = make_invocation_for_kind(
        ShellKind.POSIX, "ls -la", executable="/bin/bash",
    )
    args, kwargs = invocation.process_args()
    assert args == ["/bin/bash", "-lc", "ls -la"]
    assert kwargs == {"shell": False, "env": invocation.env}


def test_macos_posix_dialect_builds_login_shell_invocation(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(dialect_mod, "_darwin_default_shell", lambda env=None: "/bin/zsh")
    from lingtai.adapters.posix.bash import PosixBashDialect

    invocation = PosixBashDialect().make_invocation("pwd")
    assert invocation.process_args()[0] == ["/bin/zsh", "-lc", "pwd"]


def test_non_darwin_posix_keeps_historical_shell_true_form(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Linux")
    invocation = make_invocation_for_kind(ShellKind.POSIX, "echo hi")
    assert invocation.env is None
    assert invocation.process_args() == ("echo hi", {"shell": True})


def test_macos_classifier_stays_posix_kind(monkeypatch):
    # macOS resolution (zsh/bash + ``-lc``) lives in the POSIX dialect, not in
    # a new ShellKind: the durable kind vocabulary is unchanged and the async
    # state key ``posix`` keeps loading on every platform.
    from lingtai.adapters import shell as shell_adapter

    monkeypatch.setattr(shell_adapter.platform, "system", lambda: "Darwin")
    assert shell_adapter.resolve_shell_kind(os_name="posix") is ShellKind.POSIX
    assert shell_adapter.select_shell_dialect().state_key() == "posix"


# ---------------------------------------------------------------------------
# Login-shell resolution (_darwin_default_shell)
# ---------------------------------------------------------------------------


def test_darwin_default_shell_prefers_shell_env():
    assert _darwin_default_shell({"SHELL": "/bin/zsh"}) == "/bin/zsh"
    assert _darwin_default_shell({"SHELL": "/bin/bash"}) == "/bin/bash"
    # Homebrew zsh paths are accepted too; fish is not.
    assert _darwin_default_shell({"SHELL": "/opt/homebrew/bin/zsh"}) == "/opt/homebrew/bin/zsh"
    assert _darwin_default_shell({"SHELL": "/bin/fish"}) != "/bin/fish"


def test_darwin_default_shell_uses_dscl_when_shell_not_usable(monkeypatch):
    captured = []

    def fake_run(argv, **kwargs):
        captured.append(argv)
        return type("R", (), {"stdout": "UserShell: /bin/zsh", "returncode": 0})()

    monkeypatch.setattr(dialect_mod.subprocess, "run", fake_run)
    assert _darwin_default_shell({"SHELL": "/bin/fish", "USER": "alice"}) == "/bin/zsh"
    assert captured == [["dscl", ".", "-read", "/Users/alice", "UserShell"]]


def test_darwin_default_shell_defaults_to_bin_zsh_when_dscl_fails(monkeypatch):
    def failing_run(argv, **kwargs):
        raise OSError("dscl not available")

    monkeypatch.setattr(dialect_mod.subprocess, "run", failing_run)
    monkeypatch.setattr(dialect_mod.os.path, "exists", lambda path: path == "/bin/zsh")
    assert _darwin_default_shell({"SHELL": "/bin/fish", "USER": "alice"}) == "/bin/zsh"


def test_darwin_default_shell_falls_back_to_bin_bash(monkeypatch):
    def failing_run(argv, **kwargs):
        raise OSError("dscl not available")

    monkeypatch.setattr(dialect_mod.subprocess, "run", failing_run)
    monkeypatch.setattr(dialect_mod.os.path, "exists", lambda path: False)
    assert _darwin_default_shell({"SHELL": "/bin/fish"}) == "/bin/bash"


# ---------------------------------------------------------------------------
# Homebrew PATH guarantee + credential scrub (posix_login_env)
# ---------------------------------------------------------------------------


def test_posix_login_env_prepends_homebrew_paths(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    env = posix_login_env({"PATH": "/usr/bin:/bin", "HOME": "/Users/alice"})
    assert env is not None
    assert env["PATH"] == "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
    assert env["HOME"] == "/Users/alice"


def test_posix_login_env_does_not_duplicate_existing_brew_paths(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    env = posix_login_env({"PATH": "/opt/homebrew/bin:/usr/bin:/bin"})
    # Only the missing dir is prepended (canonical Homebrew priority order);
    # the already-present /opt/homebrew/bin entry is not duplicated.
    assert env["PATH"] == "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"


def test_posix_login_env_scrubs_credential_vars_but_keeps_tokens(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    env = posix_login_env(
        {
            "PATH": "/usr/bin:/bin",
            "OPENAI_API_KEY": "sk-secret",
            "ANTHROPIC_API_KEY": "sk-ant-secret",
            "AWS_SECRET_ACCESS_KEY": "aws-secret",
            "DB_PASSWORD": "hunter2",
            "GITHUB_TOKEN": "gh-token",
            "NPM_TOKEN": "npm-token",
            "HOME": "/Users/alice",
        }
    )
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "DB_PASSWORD" not in env
    # General-purpose tokens and ordinary vars survive so gh/npm keep working.
    assert env["GITHUB_TOKEN"] == "gh-token"
    assert env["NPM_TOKEN"] == "npm-token"
    assert env["HOME"] == "/Users/alice"


def test_posix_login_env_is_none_on_non_darwin(monkeypatch):
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Linux")
    assert posix_login_env({"PATH": "/usr/bin"}) is None


# ---------------------------------------------------------------------------
# ShellInvocation env field (serialization + spawn kwargs)
# ---------------------------------------------------------------------------


def test_invocation_env_round_trips_and_feeds_process_kwargs():
    env = {"PATH": "/opt/homebrew/bin:/usr/bin", "LANG": "en_US.UTF-8"}
    invocation = ShellInvocation(
        script="echo hi", executable="/bin/zsh", argv=("-lc",), env=env,
    )
    loaded = ShellInvocation.from_dict(invocation.to_dict())
    assert loaded == invocation
    args, kwargs = invocation.process_args()
    assert args == ["/bin/zsh", "-lc", "echo hi"]
    assert kwargs["env"] == env


def test_invocation_env_rejects_non_string_values():
    with pytest.raises(ValueError, match="env must be a dict"):
        ShellInvocation(script="echo hi", env={"PATH": 7})
    serialized = {
        "script": "echo hi", "executable": None, "argv": None,
        "encoding": None, "errors": None, "env": {"PATH": 7},
    }
    assert ShellInvocation.from_dict(serialized) is None


def test_legacy_state_without_env_still_loads():
    legacy = {
        "script": "echo hi", "executable": None, "argv": None,
        "encoding": None, "errors": None,
    }
    loaded = ShellInvocation.from_dict(legacy)
    assert loaded is not None
    assert loaded.env is None
    assert loaded.process_args() == ("echo hi", {"shell": True})


# ---------------------------------------------------------------------------
# Process-group spawn + graceful-then-KILL (POSIX async adapter)
# ---------------------------------------------------------------------------


def test_posix_spawn_uses_own_session_and_group(tmp_path):
    port = PosixBashAsyncProcessAdapter()
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    invocation = ShellInvocation(script="sleep 5", executable="/bin/sh", argv=("-c",))
    ref, owned = port.spawn(invocation, str(tmp_path), stdout_path, stderr_path)
    try:
        # start_new_session=True: the child is its own session leader and its
        # PGID equals its PID -- the group handle cancellation signals.
        assert os.getsid(ref.public_id) == ref.public_id
        assert os.getpgid(ref.public_id) == ref.public_id
    finally:
        try:
            os.killpg(ref.public_id, signal.SIGKILL)
        except ProcessLookupError:
            pass
        owned.close_logs()


def test_posix_async_cancel_kills_whole_process_group(tmp_path):
    from lingtai.adapters.posix import bash_process as posix_process

    port = PosixBashAsyncProcessAdapter()
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    invocation = ShellInvocation(
        script="sleep 30 & echo started; wait",
        executable="/bin/sh",
        argv=("-c",),
    )
    ref, owned = port.spawn(invocation, str(tmp_path), stdout_path, stderr_path)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if posix_process._alive(ref.public_id):
            break
        time.sleep(0.02)
    assert posix_process._alive(ref.public_id), "command did not start"

    requested = {"value": False}
    threading.Timer(0.3, lambda: requested.__setitem__("value", True)).start()
    completion = port.wait(owned, lambda: requested["value"])
    assert completion.cancellation_outcome == "group_cancelled"

    # Graceful-then-KILL: no member of the former process group may survive.
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.killpg(ref.public_id, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("process group still alive after cancellation")


def test_sync_timeout_kills_whole_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    mgr = BashManager(
        agent=_SyncOnlyAgent(), policy=BashPolicy.yolo(), working_dir=str(tmp_path),
    )
    result = mgr.handle(
        {
            "command": f"sleep 30 & echo $! > {pid_file}; wait",
            "timeout": 0.4,
        }
    )
    assert result["status"] == "error"
    assert "timed out" in result["message"]
    child_pid = int(pid_file.read_text().strip())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("background grandchild survived the sync timeout")
