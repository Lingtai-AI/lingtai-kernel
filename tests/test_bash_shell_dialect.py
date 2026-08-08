"""Contract coverage for the Bash-local shell-language boundary."""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from lingtai.adapters.bash import select_bash_shell_dialect
from lingtai.tools.bash import BashManager, BashPolicy
from lingtai.tools.bash import _BACKGROUND_GUIDANCE, _timeout_error
from lingtai.tools.bash._shell_dialect import ShellDialect, ShellInvocation
from tests._notification_store_helpers import notification_store_for


class MarkerDialect(ShellDialect):
    def extract_commands(self, script):
        return ("blocked",) if script == "marker" else ("echo",)

    def make_invocation(self, script):
        return ShellInvocation(script="printf dialect-marker")

    def state_key(self):
        return "test-marker"


def manager(tmp_path, dialect=None, policy=None):
    return BashManager(
        policy=policy or BashPolicy.yolo(),
        working_dir=str(tmp_path),
        agent=SimpleNamespace(_notification_store=notification_store_for(tmp_path)),
        dialect=dialect,
    )


def test_posix_policy_extraction_and_invocation_are_compatible():
    dialect = select_bash_shell_dialect()
    assert dialect.state_key() == "posix"
    assert dialect.extract_commands("FOO=1 echo x | tr x y; $(printf z)") == (
        "echo", "tr", "printf",
    )
    invocation = dialect.make_invocation("echo hello")
    assert invocation.to_dict() == {
        "script": "echo hello", "executable": None, "argv": None,
        "command_line": None, "encoding": None, "errors": None,
    }
    assert ShellInvocation.from_dict(invocation.to_dict()) == invocation


def test_argv_invocation_round_trip_and_process_args_are_unambiguous():
    invocation = ShellInvocation(
        script="Write-Output 'hello'",
        executable="pwsh",
        argv=("-NoProfile", "-Command"),
        encoding="utf-8",
        errors="strict",
    )

    assert ShellInvocation.from_dict(invocation.to_dict()) == invocation
    assert invocation.process_args() == (
        ["pwsh", "-NoProfile", "-Command", "Write-Output 'hello'"],
        {"shell": False},
    )


@pytest.mark.parametrize(
    "field, value",
    [("script", ""), ("script", "   "), ("executable", ""),
     ("encoding", ""), ("errors", "")],
)
def test_invocation_rejects_empty_fields(field, value):
    fields = {"script": "echo hello", "executable": None, "argv": None,
              "encoding": None, "errors": None}
    fields[field] = value
    with pytest.raises(ValueError):
        ShellInvocation(**fields)


def test_argv_requires_executable_and_accepts_list_in_memory():
    with pytest.raises(ValueError):
        ShellInvocation(script="Write-Output hello", argv=("-Command",))
    invocation = ShellInvocation(
        script="Write-Output hello", executable="pwsh", argv=["-Command"]
    )
    assert invocation.argv == ("-Command",)


def test_unknown_or_missing_serialized_keys_are_rejected():
    serialized = ShellInvocation(script="echo hello").to_dict()
    serialized["future"] = True
    assert ShellInvocation.from_dict(serialized) is None
    del serialized["future"]
    del serialized["errors"]
    assert ShellInvocation.from_dict(serialized) is None


@pytest.mark.parametrize(
    "update",
    [{"script": ""}, {"script": "   "}, {"executable": ""},
     {"argv": ["-Command"]}, {"encoding": ""}, {"errors": ""},
     {"argv": ["-Command", 7], "executable": "pwsh"}],
)
def test_invalid_serialized_invocation_returns_none(update):
    serialized = ShellInvocation(script="echo hello").to_dict()
    serialized.update(update)
    assert ShellInvocation.from_dict(serialized) is None


def test_script_process_args_omits_unset_executable():
    assert ShellInvocation(script="echo hello").process_args() == (
        "echo hello", {"shell": True}
    )


def test_selected_dialect_drives_policy_and_sync_execution(tmp_path):
    dialect = MarkerDialect()
    policy = BashPolicy(deny=["blocked"])
    mgr = manager(tmp_path, dialect=dialect, policy=policy)
    assert mgr.handle({"command": "marker"})["status"] == "error"
    result = mgr.handle({"command": "echo"})
    assert result["stdout"] == "dialect-marker"


def test_async_state_persists_dialect_invocation_and_raw_command(tmp_path):
    mgr = manager(tmp_path, dialect=MarkerDialect())
    started = mgr.handle({"command": "echo original", "async": True, "reminder": 30})
    assert started["status"] == "ok"
    state_path = tmp_path / "system" / "jobs" / started["job_id"] / "state.json"
    state = json.loads(state_path.read_text())
    assert state["command"] == "echo original"
    assert state["shell_dialect"] == "test-marker"
    assert ShellInvocation.from_dict(state["invocation"]).script == "printf dialect-marker"
    deadline = time.time() + 3
    while time.time() < deadline:
        if json.loads(state_path.read_text()).get("status") == "completed":
            break
        time.sleep(0.02)
    result = mgr.handle({"action": "poll", "job_id": started["job_id"]})
    assert result["status"] == "done"
    assert result["stdout"] == "dialect-marker"


def test_selector_fails_loudly_on_unsupported_platform(monkeypatch):
    import lingtai.adapters.bash as composition

    monkeypatch.setattr(composition.os, "name", "java")
    with pytest.raises(NotImplementedError, match="unsupported"):
        composition.select_bash_shell_dialect()


# ---------------------------------------------------------------------------
# PR-7: no-output timeout backgrounding guidance (OpenClaw exec-runner.ts
# overall/no-output timeout copy + Hermes foreground hint, adapted to our
# ``async`` parameter name)
# ---------------------------------------------------------------------------


def test_timeout_error_no_output_appends_background_guidance():
    result = _timeout_error("sleep 5", 5.0, no_output=True)
    assert result == {
        "status": "error",
        "message": (
            "Command timed out after 5.0s. Long-running or no-output work should "
            "be launched with async=true (or as a daemon) rather than as a "
            "foreground command; do not rely on shell backgrounding with a "
            "trailing &."
        ),
    }


def test_timeout_error_with_output_keeps_historical_message():
    assert _timeout_error("echo x", 5.0) == {
        "status": "error",
        "message": "Command timed out after 5.0s",
    }
    assert _timeout_error("echo x", 5.0, no_output=False) == {
        "status": "error",
        "message": "Command timed out after 5.0s",
    }


def test_sync_timeout_with_no_output_returns_background_guidance(tmp_path):
    mgr = manager(tmp_path)
    result = mgr.handle({"command": "sleep 5", "timeout": 1.0})
    assert result["status"] == "error"
    assert result["message"].startswith("Command timed out after 1.0s")
    assert _BACKGROUND_GUIDANCE in result["message"]


def test_sync_timeout_with_captured_output_omits_guidance(tmp_path):
    mgr = manager(tmp_path)
    command = (
        f"{sys.executable} -u -c \"print('started'); import time; time.sleep(5)\""
    )
    result = mgr.handle({"command": command, "timeout": 1.0})
    assert result["status"] == "error"
    assert result["message"].startswith("Command timed out after 1.0s")
    assert "trailing &" not in result["message"]
