"""Focused PR1 contract checks for canonical shell and Windows composition."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import lingtai.adapters.shell as shell_adapter
from lingtai.adapters.windows.powershell import (
    PowerShellDialect,
    _ASCII_BOOTSTRAP,
    _commands,
)
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
    assert dialect.extract_commands("Write-Output hi") == ("Write-Output",)
    # PR-5: metachar pipelines fail closed under policy enforcement instead of
    # extracting their constituent commands (the scanner flags | ; and $( ).
    assert dialect.extract_commands(
        "Write-Output hi | ForEach-Object { $_ }; Write-Output $(Get-Date)"
    ) == ("__powershell_unsupported__",)
    invocation = dialect.make_invocation("Write-Output hi")
    args, kwargs = invocation.process_args()
    assert args[:5] == ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
    # The command line is fixed and ASCII-only: the last argument is the
    # stdin bootstrap, and the user script is NOT on the command line.
    assert args[-1] == _ASCII_BOOTSTRAP
    assert args[-1].isascii()
    assert "Write-Output hi" not in args
    # The exit-code wrapper (existing escape/quote handling) travels unchanged
    # as the UTF-8 stdin payload instead of the code-page-mangled cmdline.
    assert invocation.stdin_script == invocation.script
    assert "Write-Output hi" in invocation.stdin_script
    assert "$global:__lingtai_success = $?" in invocation.stdin_script
    assert "exit $global:__lingtai_native_exit" in invocation.stdin_script
    assert kwargs == {"shell": False}
    assert invocation.encoding == "utf-8"


def test_powershell_parentheses_are_recursive_and_malformed_syntax_fails_closed():
    dialect = PowerShellDialect(executable="pwsh")
    # PR-5: unquoted parentheses are metachars, so parenthesized commands fail
    # closed under policy enforcement; recursion remains available for quoted
    # and paren-free scripts.
    assert dialect.extract_commands(r"(Remove-Item -LiteralPath .\victim)") == (
        "__powershell_unsupported__",
    )
    assert dialect.extract_commands(
        r"Write-Output (Remove-Item -LiteralPath .\victim)"
    ) == ("__powershell_unsupported__",)
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
    # PR-5: unquoted parentheses are flagged metachars, so the command fails
    # closed before invocation (human confirmation) rather than being reviewed
    # as an extractable Remove-Item call.
    assert denied["status"] == "error"
    assert "does not support this syntax" in denied["message"]
    assert "refusing to run" in denied["message"]
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


def test_cmd_normalized_command_names_are_denied_by_policy(tmp_path):
    from lingtai.adapters.windows.cmd import CmdDialect

    dialect = CmdDialect()
    dialect.make_invocation = MagicMock(side_effect=AssertionError("cmd must not run"))
    manager = ShellManager(
        policy=ShellPolicy(deny=["del"]),
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=dialect,
    )
    # cmd.exe runs ``del`` for all of these; the extractor must normalize
    # them so the deny-list cannot be bypassed.
    for command in (
        "d^el victim.txt",
        '"del" victim.txt',
        "del,victim.txt",
        "del;victim.txt",
        "if 1==1 (del victim.txt)",
    ):
        denied = manager.handle({"command": command})
        assert denied["status"] == "error"
        assert "not allowed" in denied["message"]
        assert "del" in denied["message"]
    dialect.make_invocation.assert_not_called()


def test_cmd_percent_expansion_fails_closed_before_invocation(tmp_path):
    from lingtai.adapters.windows.cmd import CmdDialect

    dialect = CmdDialect()
    dialect.make_invocation = MagicMock(side_effect=AssertionError("cmd must not run"))
    manager = ShellManager(
        policy=ShellPolicy(deny=["del"]),
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=dialect,
    )
    # ``%comspec%`` expands before cmd parses, so the command identity is
    # unknowable; a configured policy must refuse rather than guess.
    denied = manager.handle({"command": "%comspec% /c del victim.txt"})
    assert denied["status"] == "error"
    assert "does not support this syntax" in denied["message"]
    assert "refusing to run" in denied["message"]
    dialect.make_invocation.assert_not_called()


def test_cmd_percent_expansion_is_allowed_without_policy(tmp_path):
    from lingtai.adapters.windows.cmd import CmdDialect
    from lingtai.tools.bash._shell_dialect import ShellInvocation

    dialect = CmdDialect()
    dialect.make_invocation = MagicMock(return_value=ShellInvocation(script="echo x"))
    manager = ShellManager(
        policy=ShellPolicy.yolo(),
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=dialect,
    )
    # yolo mode has no policy to protect; the unsupported marker is inert and
    # the command proceeds to invocation instead of being refused.
    assert dialect.extract_commands("echo %PATH%") == ("__cmd_unsupported__",)
    manager.handle({"command": "echo %PATH%"})
    dialect.make_invocation.assert_called_once_with("echo %PATH%")


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


def test_unsupported_os_name_fails_loud_not_with_name_error(monkeypatch):
    import lingtai.adapters.shell_process as process_selector
    import lingtai.adapters.shell_state_lock as lock_selector

    monkeypatch.setattr(process_selector.os, "name", "java")
    with pytest.raises(NotImplementedError, match="unsupported on 'java'"):
        process_selector.select_shell_async_process()

    monkeypatch.setattr(lock_selector.os, "name", "java")
    with pytest.raises(NotImplementedError, match="unsupported on 'java'"):
        lock_selector.select_shell_state_lock()


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


# These two assert on ``_commands`` rather than ``PowerShellDialect.extract_commands``
# because PR-5's quote-aware scanner now fails the whole script closed on ``|``
# before extraction ever runs, which would mask the brace-depth behaviour under
# test.  ``_commands`` is the recursive extractor these cases are about.
def test_powershell_script_block_semicolons_do_not_split_statements():
    """Semicolons inside a { ... } script block must not split statements."""
    commands = _commands(
        "Get-Process | Where-Object { $_.Name -eq 'pwsh'; $_.CPU -gt 10 }"
    )
    assert "Get-Process" in commands
    assert "Where-Object" in commands
    # The variable references inside the script block are not commands.
    assert "$_" not in commands


def test_powershell_unbalanced_brace_fails_closed():
    """An unterminated script block is malformed and must fail closed."""
    commands = _commands("Get-Process | Where-Object { $_.Name -eq 'pwsh'")
    assert commands == ("__powershell_unsupported__",)


def test_powershell_dynamic_unsupported_message_is_actionable(tmp_path):
    """The unsupported-syntax error keeps the original wording and adds options."""
    policy = ShellPolicy(deny=["Remove-Item"])
    manager = ShellManager(
        policy=policy,
        working_dir=str(tmp_path),
        agent=SimpleNamespace(),
        dialect=PowerShellDialect(executable="pwsh"),
    )
    denied = manager.handle({"command": "& $command victim"})
    assert denied["status"] == "error"
    assert "does not support this syntax" in denied["message"]
    assert "refusing to run" in denied["message"]
    assert "yolo=true" in denied["message"]
