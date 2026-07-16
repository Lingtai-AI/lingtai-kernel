"""Focused PR1 contract checks for canonical shell and Windows composition."""
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.adapters.windows.powershell import PowerShellDialect
from lingtai.tools.bash import ShellManager, ShellPolicy, setup
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
    assert normalize_capabilities({"bash": {"yolo": True}, "shell": {"yolo": False}}) == {
        "shell": {"yolo": False}
    }
    assert normalize_capabilities({"shell": None, "bash": {"yolo": True}}) == {
        "shell": None
    }
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
    assert "dialect" not in agent.add_tool.call_args.kwargs["schema"]["properties"]


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
    assert "$__lingtai_success = $?" in args[-1]
    assert "exit $__lingtai_native_exit" in args[-1]
    assert kwargs == {"shell": False}
    assert invocation.encoding == "utf-8"


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
