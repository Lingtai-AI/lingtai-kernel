"""Focused PR1 contract checks for canonical shell and Windows composition."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lingtai.adapters.shell as shell_adapter
from lingtai.adapters.windows.powershell import PowerShellDialect
from lingtai.adapters.windows.powershell_process import _Owned, WindowsShellAsyncProcessAdapter
from lingtai.tools.bash import ShellManager, ShellPolicy, setup
from lingtai.tools.bash._async_process import ProcessRef
from lingtai.tools.bash._async_supervisor import _invocation_from_state
from lingtai.tools.registry import (
    BUILTIN_TOOLS,
    CORE_DEFAULTS,
    apply_core_defaults,
    normalize_capabilities,
)


def test_registry_has_one_canonical_public_shell_identity():
    assert BUILTIN_TOOLS["shell"] == "lingtai.tools.bash"
    assert "bash" not in BUILTIN_TOOLS
    assert "shell" in CORE_DEFAULTS
    assert "bash" not in CORE_DEFAULTS
    assert normalize_capabilities({"bash": {"yolo": False}}) == {
        "shell": {"yolo": False}
    }
    assert normalize_capabilities({"bash": {"yolo": True}, "shell": {"yolo": True}}) == {
        "shell": {"yolo": True}
    }
    with pytest.raises(ValueError, match="conflicting"):
        normalize_capabilities({"bash": {"yolo": True}, "shell": {"yolo": False}})
    with pytest.raises(ValueError, match="conflicting"):
        normalize_capabilities({"shell": None, "bash": {"yolo": True}})
    assert "shell" not in apply_core_defaults(None, disable=["bash"])
    assert "shell" not in apply_core_defaults({"bash": None})


def test_setup_registers_shell_and_advertises_selected_dialect(tmp_path):
    agent = MagicMock()
    agent._working_dir = Path(tmp_path)
    manager = setup(agent, yolo=True)
    assert isinstance(manager, ShellManager)
    assert agent.add_tool.call_args.args[0] == "shell"
    description = agent.add_tool.call_args.kwargs["description"]
    expected_dialect = "powershell" if os.name == "nt" else "posix"
    assert f"Active shell dialect: {expected_dialect}" in description
    assert f"Host OS: {shell_adapter.describe_host_os()}" in description
    properties = agent.add_tool.call_args.kwargs["schema"]["properties"]
    assert "dialect" not in properties
    assert "host_os" not in properties


def test_host_os_description_uses_human_readable_versions(monkeypatch):
    monkeypatch.setattr(shell_adapter.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shell_adapter.platform, "mac_ver", lambda: ("15.5", ("", "", ""), ""))
    assert shell_adapter.describe_host_os() == "macOS 15.5"

    monkeypatch.setattr(shell_adapter.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        shell_adapter.platform,
        "freedesktop_os_release",
        lambda: {"PRETTY_NAME": "Ubuntu 24.04 LTS"},
    )
    assert shell_adapter.describe_host_os() == "Ubuntu 24.04 LTS"

    monkeypatch.setattr(shell_adapter.platform, "system", lambda: "Windows")
    monkeypatch.setattr(shell_adapter.platform, "release", lambda: "11")
    monkeypatch.setattr(shell_adapter.platform, "version", lambda: "10.0.26100")
    assert shell_adapter.describe_host_os() == "Windows 11 (10.0.26100)"


def test_powershell_invocation_and_extractor_are_not_posix():
    dialect = PowerShellDialect(executable="pwsh")
    assert dialect.state_key() == "powershell"
    assert dialect.extract_commands(
        "Write-Output hi | ForEach-Object { $_ }; Write-Output $(Get-Date)"
    ) == ("Write-Output", "ForEach-Object", "Write-Output", "Get-Date")
    invocation = dialect.make_invocation("Write-Output hi")
    args, kwargs = invocation.process_args()
    assert args[:5] == ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Write-Output hi" in args[-1]
    assert "$global:__lingtai_success = $?" in args[-1]
    assert "exit $global:__lingtai_native_exit" in args[-1]
    assert kwargs == {"shell": False}
    assert invocation.encoding == "utf-8"


def test_powershell_parentheses_are_recursive_and_malformed_syntax_fails_closed():
    dialect = PowerShellDialect(executable="pwsh")
    assert dialect.extract_commands(r"(Remove-Item -LiteralPath .\victim)") == ("Remove-Item",)
    assert dialect.extract_commands(
        r"Write-Output (Remove-Item -LiteralPath .\victim)"
    ) == ("Write-Output", "Remove-Item")
    assert dialect.extract_commands(r"(Remove-Item -LiteralPath .\victim") == (
        "__powershell_unsupported__",
    )
    assert dialect.extract_commands(r"Write-Output ()") == ("__powershell_unsupported__",)


@pytest.mark.parametrize(
    "policy",
    [ShellPolicy(deny=["Remove-Item"]), ShellPolicy(allow=["Write-Output"])],
)
def test_powershell_parenthesized_policy_rejects_before_invocation(tmp_path, policy):
    dialect = PowerShellDialect(executable="pwsh")
    dialect.make_invocation = MagicMock(side_effect=AssertionError("pwsh must not run"))
    manager = ShellManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=dialect,
    )
    denied = manager.handle({"command": r"Write-Output (Remove-Item -LiteralPath .\victim)"})
    assert denied["status"] == "error"
    assert "not allowed" in denied["message"]
    assert "Remove-Item" in denied["message"]
    dialect.make_invocation.assert_not_called()


@pytest.mark.parametrize(
    "policy",
    [ShellPolicy(deny=["Remove-Item"]), ShellPolicy(allow=["Write-Output"])],
)
@pytest.mark.parametrize(
    "command",
    [
        r"Rem`ove-Item -LiteralPath .\victim",
        r"Write-Output (Rem`ove-Item -LiteralPath .\victim)",
        r"& Rem`ove-Item -LiteralPath .\victim",
    ],
)
def test_powershell_backtick_escaped_command_fails_closed_before_invocation(
    tmp_path, policy, command
):
    dialect = PowerShellDialect(executable="pwsh")
    assert "__powershell_unsupported__" in dialect.extract_commands(command)
    dialect.make_invocation = MagicMock(side_effect=AssertionError("pwsh must not run"))
    manager = ShellManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=dialect,
    )
    denied = manager.handle({"command": command})
    assert denied["status"] == "error"
    assert "does not support this syntax" in denied["message"]
    assert "refusing to run" in denied["message"]
    dialect.make_invocation.assert_not_called()


def test_powershell_policy_is_case_insensitive_and_dynamic_syntax_fails_closed(tmp_path):
    policy = ShellPolicy(deny=["Remove-Item"])
    manager = ShellManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=PowerShellDialect(executable="pwsh"),
    )
    denied = manager.handle({"command": "remove-item file.txt"})
    assert denied["status"] == "error"
    assert "not allowed" in denied["message"]
    dynamic = manager.handle({"command": "& $command"})
    assert dynamic["status"] == "error"
    expandable = manager.handle({"command": '& "$command" victim'})
    assert expandable["status"] == "error"
    static = manager.handle({"command": "& Remove-Item victim"})
    assert static["status"] == "error"
    quoted_static = manager.handle({"command": "& 'Remove-Item' victim"})
    assert quoted_static["status"] == "error"


def test_powershell_exit_wrapper_observes_final_native_type_without_rewriting_script():
    script = "& $env:ComSpec /d /c exit 7; if ($LASTEXITCODE -ne 7) { throw 'lost' }"
    wrapped = PowerShellDialect(executable="pwsh").make_invocation(script).script
    assert script in wrapped
    assert "$PSNativeCommandUseErrorActionPreference = $true" in wrapped
    assert "ProgramExitedWithNonZeroCode" in wrapped
    assert "$global:LASTEXITCODE = 0" not in wrapped


def test_windows_root_exit_child_live_cancel_race_keeps_job_owned(monkeypatch):
    import lingtai.adapters.windows.powershell_process as process_adapter

    class FakeProcess:
        def poll(self):
            return 0

        def wait(self):
            return 0

    class FakeKernel:
        def __init__(self):
            self.terminated = []

        def TerminateJobObject(self, handle, code):
            self.terminated.append((handle, code))
            return 1

        def CloseHandle(self, handle):
            return 1

    kernel = FakeKernel()
    waits = []
    monkeypatch.setattr(process_adapter, "_kernel32", lambda: kernel)

    def wait_job(_handle, timeout):
        waits.append(timeout)
        return timeout == 5.0

    monkeypatch.setattr(process_adapter, "_wait_job", wait_job)
    requests = iter([False, True])
    job_handle = object()
    owned = _Owned(FakeProcess(), ProcessRef(123, "test"), job_handle)
    completion = WindowsShellAsyncProcessAdapter().wait(owned, lambda: next(requests))
    assert completion.cancellation_outcome == "group_cancelled"
    assert kernel.terminated == [(job_handle, 1)]
    assert waits == [0.05, 5.0]


def test_windows_selector_modules_are_real_adapter_composition_points(monkeypatch):
    import lingtai.adapters.shell as dialect_selector
    import lingtai.adapters.shell_process as process_selector
    import lingtai.adapters.shell_state_lock as lock_selector
    from lingtai.adapters.windows.powershell_process import WindowsShellAsyncProcessAdapter
    from lingtai.adapters.windows.powershell_state_lock import WindowsShellStateLockAdapter

    monkeypatch.setattr(dialect_selector.os, "name", "nt")
    monkeypatch.setattr(process_selector.os, "name", "nt")
    monkeypatch.setattr(lock_selector.os, "name", "nt")
    monkeypatch.setattr("shutil.which", lambda name: "C:/Program Files/PowerShell/7/pwsh.exe")
    assert dialect_selector.select_shell_dialect().state_key() == "powershell"
    assert isinstance(process_selector.select_shell_async_process(), WindowsShellAsyncProcessAdapter)
    assert isinstance(lock_selector.select_shell_state_lock(), WindowsShellStateLockAdapter)


def test_windows_resume_uses_retained_process_handle_not_a_missing_thread(monkeypatch):
    import lingtai.adapters.windows.powershell_process as process_adapter

    calls = []
    fake_ntdll = SimpleNamespace(
        NtResumeProcess=lambda handle: calls.append(handle) or 0
    )
    monkeypatch.setattr(process_adapter, "_ntdll", lambda: fake_ntdll)
    process_adapter._resume_suspended_process(SimpleNamespace(_handle=1234))
    assert calls == [1234]


def test_non_posix_legacy_state_is_not_reinterpreted(monkeypatch):
    import lingtai.tools.bash._async_supervisor as supervisor

    monkeypatch.setattr(supervisor.os, "name", "nt")
    with pytest.raises(ValueError, match="refusing to reinterpret"):
        _invocation_from_state({"command": "echo old"}, "echo old")
