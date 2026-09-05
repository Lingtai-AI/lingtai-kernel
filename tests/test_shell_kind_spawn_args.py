"""Golden spawn-argument tests per ShellKind.

The spawn shape is the single ``ShellKind``-keyed authority
(``make_invocation_for_kind``); these tests pin the exact argv/encoding/errors
for every kind so a refactor can never silently change what ``subprocess``
runs or what the model-facing description claims.
"""
from __future__ import annotations

import pytest

from lingtai.tools.bash import _shell_dialect as dialect_mod
from lingtai.tools.bash._shell_dialect import ShellKind, make_invocation_for_kind


def test_posix_kind_preserves_historical_shell_true_form(monkeypatch):
    # The historical POSIX ``shell=True`` contract is the non-Darwin path;
    # Darwin intentionally spawns ``[shell, "-lc", script]`` argv through the
    # login shell (see test_macos_shell_adapter.py).  Pin the platform so the
    # golden record below is deterministic on every host (macOS CI included).
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Linux")
    invocation = make_invocation_for_kind(ShellKind.POSIX, "echo hi")
    assert invocation.to_dict() == {
        "script": "echo hi", "executable": None, "argv": None,
        "command_line": None, "encoding": None, "errors": None,
    }
    assert invocation.process_args() == ("echo hi", {"shell": True})


def test_powershell_kind_spawn_argv_golden():
    invocation = make_invocation_for_kind(
        ShellKind.POWERSHELL, "Get-ChildItem", executable="pwsh",
    )
    args, kwargs = invocation.process_args()
    assert args == [
        "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        "Get-ChildItem",
    ]
    assert kwargs == {"shell": False}
    assert invocation.encoding == "utf-8"
    assert invocation.errors == "replace"


def test_cmd_kind_spawn_raw_command_line_golden(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    invocation = make_invocation_for_kind(ShellKind.CMD, "dir")
    assert invocation.to_dict()["argv"] is None
    args, kwargs = invocation.process_args()
    # cmd.exe must receive a raw command-line string, never an argv list:
    # ``list2cmdline`` would escape embedded quotes as ``\\"`` which cmd
    # cannot parse.  The wrapper is ``/d /s /c " <script>"``.
    assert args == "C:\\Windows\\System32\\cmd.exe /d /s /c \" dir\""
    assert kwargs == {"shell": False, "executable": r"C:\Windows\System32\cmd.exe"}
    assert invocation.encoding == "utf-8"


def test_cmd_kind_raw_command_line_preserves_embedded_quotes(monkeypatch):
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    invocation = make_invocation_for_kind(ShellKind.CMD, 'echo "hello world"')
    args, _kwargs = invocation.process_args()
    assert args == "C:\\Windows\\System32\\cmd.exe /d /s /c \" echo \"hello world\"\""


def test_cmd_kind_defaults_to_cmd_exe_when_comspec_unset(monkeypatch):
    monkeypatch.delenv("COMSPEC", raising=False)
    invocation = make_invocation_for_kind(ShellKind.CMD, "dir")
    assert invocation.executable == "cmd.exe"
    args, kwargs = invocation.process_args()
    assert args == "cmd.exe /d /s /c \" dir\""
    assert kwargs == {"shell": False, "executable": "cmd.exe"}


def test_gitbash_kind_spawn_argv_golden():
    invocation = make_invocation_for_kind(
        ShellKind.GITBASH, "ls -la",
        executable=r"C:\Program Files\Git\bin\bash.exe",
    )
    args, kwargs = invocation.process_args()
    assert args == [r"C:\Program Files\Git\bin\bash.exe", "-lc", "ls -la"]
    assert kwargs == {"shell": False}


def test_wsl_kind_spawn_argv_golden():
    invocation = make_invocation_for_kind(
        ShellKind.WSL, "ls -la", executable=r"C:\Windows\System32\wsl.exe",
    )
    args, kwargs = invocation.process_args()
    assert args == [r"C:\Windows\System32\wsl.exe", "-e", "bash", "-lc", "ls -la"]
    assert kwargs == {"shell": False}


def test_argv_kind_requires_discovered_executable():
    with pytest.raises(ValueError, match="requires a discovered executable"):
        make_invocation_for_kind(ShellKind.GITBASH, "ls")


def test_powershell_dialect_wraps_script_and_spawns_through_kind():
    from lingtai.adapters.windows.powershell import PowerShellDialect

    dialect = PowerShellDialect(executable="pwsh")
    invocation = dialect.make_invocation("Write-Output hi")
    args, kwargs = invocation.process_args()
    assert args[:5] == [
        "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
    ]
    # The wrapped script travels over UTF-8 stdin (never the command line);
    # argv ends in the ASCII bootstrap that reads stdin and runs the script.
    assert "[Console]::In.ReadToEnd()" in args[-1]
    assert "$global:__lingtai_success" in invocation.stdin_script
    assert "Write-Output hi" in invocation.stdin_script
    assert kwargs == {"shell": False}
    assert dialect.state_key() == "powershell"
    assert dialect.kind() is ShellKind.POWERSHELL


def test_cmd_dialect_make_invocation_and_extraction():
    from lingtai.adapters.windows.cmd import CmdDialect

    dialect = CmdDialect()
    assert dialect.state_key() == "cmd"
    assert dialect.kind() is ShellKind.CMD
    assert dialect.extract_commands("dir & del x") == ("dir", "del")
    assert dialect.extract_commands("echo a | findstr b") == ("echo", "findstr")
    args, kwargs = dialect.make_invocation("dir").process_args()
    assert args == "cmd.exe /d /s /c \" dir\""
    assert kwargs == {"shell": False, "executable": "cmd.exe"}


def test_cmd_extractor_normalizes_delimiters_escapes_and_quotes():
    from lingtai.adapters.windows.cmd import extract_cmd_commands

    assert extract_cmd_commands("del,x.txt") == ("del",)
    assert extract_cmd_commands("del;x.txt") == ("del",)
    assert extract_cmd_commands("d^el x.txt") == ("del",)
    assert extract_cmd_commands('"del" x.txt') == ("del",)
    assert extract_cmd_commands("if 1==1 (del x)") == ("if", "del")
    assert extract_cmd_commands("(del x & echo done)") == ("del", "echo")


def test_cmd_extractor_fails_closed_on_percent_expansion():
    from lingtai.adapters.windows.cmd import extract_cmd_commands

    assert extract_cmd_commands("echo %PATH%") == ("__cmd_unsupported__",)
    assert extract_cmd_commands("%comspec% /c del x") == ("__cmd_unsupported__",)


def test_gitbash_discovery_rejects_wsl_launcher_on_path(monkeypatch):
    from lingtai.adapters.windows.gitbash import discover_git_bash

    monkeypatch.setattr(
        "shutil.which", lambda name: r"C:\Windows\System32\bash.exe"
    )
    # The WSL launcher on PATH must never satisfy Git Bash discovery: WSL is
    # opt-in only and would otherwise be silently auto-selected.
    assert discover_git_bash() is None


def test_gitbash_discovery_accepts_git_bash_from_path(monkeypatch):
    from lingtai.adapters.windows.gitbash import discover_git_bash

    monkeypatch.setattr(
        "shutil.which", lambda name: r"C:\Program Files\Git\bin\bash.exe"
    )
    assert discover_git_bash() == r"C:\Program Files\Git\bin\bash.exe"


def test_gitbash_discovery_rejects_syswow64_launcher_too(monkeypatch):
    from lingtai.adapters.windows.gitbash import discover_git_bash

    monkeypatch.setattr(
        "shutil.which", lambda name: "C:/Windows/SysWOW64/bash.exe"
    )
    assert discover_git_bash() is None


def test_shell_invocation_command_line_round_trip_and_legacy_dict():
    from lingtai.tools.bash._shell_dialect import ShellInvocation

    invocation = ShellInvocation(
        script="dir", executable=r"C:\Windows\System32\cmd.exe",
        command_line=r'C:\Windows\System32\cmd.exe /d /s /c " dir"',
    )
    assert ShellInvocation.from_dict(invocation.to_dict()) == invocation
    # A legacy 5-key record (no command_line) must still load so durable
    # async state written by an older kernel is not rejected.
    legacy = {
        "script": "dir", "executable": r"C:\Windows\System32\cmd.exe",
        "argv": None, "encoding": None, "errors": None,
    }
    loaded = ShellInvocation.from_dict(legacy)
    assert loaded is not None
    assert loaded.command_line is None
    # A legacy record without argv/command_line keeps the historical
    # ``shell=True`` spawn form with the executable hint.
    assert loaded.process_args() == (
        "dir",
        {"shell": True, "executable": r"C:\Windows\System32\cmd.exe"},
    )
    with pytest.raises(ValueError, match="mutually exclusive"):
        ShellInvocation(
            script="dir", executable="cmd.exe", argv=("/d",), command_line="x",
        )


def test_gitbash_dialect_make_invocation_and_extraction():
    from lingtai.adapters.windows.gitbash import GitBashDialect

    dialect = GitBashDialect(executable=r"C:\Program Files\Git\bin\bash.exe")
    assert dialect.state_key() == "gitbash"
    assert dialect.kind() is ShellKind.GITBASH
    assert dialect.extract_commands("FOO=1 ls | grep x; echo done") == (
        "ls", "grep", "echo",
    )
    args, kwargs = dialect.make_invocation("ls").process_args()
    assert args == [r"C:\Program Files\Git\bin\bash.exe", "-lc", "ls"]
    assert kwargs == {"shell": False}


def test_wsl_dialect_make_invocation_and_extraction():
    from lingtai.adapters.windows.wsl import WslDialect

    dialect = WslDialect(executable=r"C:\Windows\System32\wsl.exe")
    assert dialect.state_key() == "wsl"
    assert dialect.kind() is ShellKind.WSL
    args, kwargs = dialect.make_invocation("ls").process_args()
    assert args == [r"C:\Windows\System32\wsl.exe", "-e", "bash", "-lc", "ls"]
    assert kwargs == {"shell": False}


def test_description_exposes_shell_kind_and_sequencing():
    from lingtai.tools.bash._tool_family import get_description

    desc = get_description(dialect="powershell", shell_kind=ShellKind.POWERSHELL)
    assert "Active shell dialect: powershell" in desc
    assert "Active shell: PowerShell" in desc
    assert "not supported" in desc
    assert "'&&'" in desc

    desc_cmd = get_description(dialect="cmd", shell_kind=ShellKind.CMD)
    assert "Active shell dialect: cmd" in desc_cmd
    assert "Active shell: cmd.exe" in desc_cmd


def test_description_keeps_legacy_shape_without_kind(monkeypatch):
    from lingtai.tools.bash._tool_family import get_description

    # Pin the non-Darwin path so the static label is deterministic on every
    # host (macOS CI included) -- see test_posix_description_reflects_resolved_
    # darwin_login_shell below for the Darwin-truthful behavior.
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Linux")
    desc = get_description(dialect="posix")
    assert "Active shell dialect: posix" in desc
    assert "Active shell: Bash (POSIX)" in desc
    assert "Host OS" not in desc


def test_posix_display_name_is_static_on_non_darwin(monkeypatch):
    from lingtai.tools.bash._shell_dialect import posix_shell_display_name

    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Linux")
    assert posix_shell_display_name() == "Bash (POSIX)"


def test_posix_display_name_reflects_resolved_darwin_login_shell(monkeypatch):
    # Reproduces Lingtai-AI/lingtai#934: on a zsh-default macOS host, the
    # model-facing label must say Zsh, not a hardcoded "Bash (POSIX)" claim
    # that disagrees with the interpreter execution actually spawns.
    from lingtai.tools.bash._shell_dialect import posix_shell_display_name

    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    assert posix_shell_display_name({"SHELL": "/bin/zsh"}) == "Zsh (POSIX)"
    assert posix_shell_display_name({"SHELL": "/bin/bash"}) == "Bash (POSIX)"


def test_description_reflects_resolved_darwin_login_shell(monkeypatch):
    from lingtai.tools.bash._tool_family import get_description

    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")
    desc = get_description(dialect="posix", shell_kind=ShellKind.POSIX)
    assert "Active shell dialect: posix" in desc
    assert "Active shell: Zsh (POSIX)" in desc
    assert "Active shell: Bash (POSIX)" not in desc


def test_setup_posix_description_reflects_resolved_darwin_login_shell(monkeypatch, tmp_path):
    # End-to-end through the real composition path (bash.setup -> _bind ->
    # get_description), not just the description helper in isolation.
    from pathlib import Path
    from unittest.mock import MagicMock

    from lingtai.adapters import shell as shell_adapter
    from lingtai.tools.bash import setup

    monkeypatch.setattr(shell_adapter.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(shell_adapter.platform, "mac_ver", lambda: ("15.5", ("", "", ""), ""))
    monkeypatch.setattr(dialect_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("SHELL", "/bin/zsh")

    agent = MagicMock()
    agent.official_tool_plugins = {}
    agent.working_dir = Path(tmp_path)

    manager = setup(agent, yolo=True)
    assert manager.shell_kind is ShellKind.POSIX
    description = agent._mount_official_tool.call_args.args[0].plugin.description
    assert "Active shell dialect: posix" in description
    assert "Active shell: Zsh (POSIX)" in description
    assert "Host OS: macOS 15.5" in description


def test_setup_honors_shell_kind_override(tmp_path):
    from pathlib import Path
    from unittest.mock import MagicMock

    from lingtai.tools.bash import ShellManager, setup

    agent = MagicMock()
    agent.official_tool_plugins = {}
    agent.working_dir = Path(tmp_path)
    manager = setup(agent, yolo=True, shell_kind="cmd")
    assert isinstance(manager, ShellManager)
    assert manager.shell_kind is ShellKind.CMD
    description = agent._mount_official_tool.call_args.args[0].plugin.description
    assert "Active shell dialect: cmd" in description
    assert "Active shell: cmd.exe" in description
    assert "'&&'" in description


def test_manager_exposes_runtime_shell_kind_metadata(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from tests._notification_store_helpers import notification_store_for
    from lingtai.adapters.windows.cmd import CmdDialect
    from lingtai.tools.bash import ShellManager, ShellPolicy

    agent = SimpleNamespace(_notification_store=notification_store_for(tmp_path))
    manager = ShellManager(
        policy=ShellPolicy.yolo(), working_dir=str(tmp_path), agent=agent,
        dialect=CmdDialect(),
    )
    assert manager.shell_kind is ShellKind.CMD

    state = manager._initial_async_state(
        "job-00000000000000000000000000000001", "dir", str(tmp_path), 30,
    )
    assert state["shell_dialect"] == "cmd"
    assert state["shell_kind"] == "cmd"


def test_unknown_test_dialect_falls_back_to_posix_metadata(tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    from tests._notification_store_helpers import notification_store_for
    from lingtai.tools.bash import ShellManager, ShellPolicy
    from lingtai.tools.bash._shell_dialect import ShellDialect, ShellInvocation

    class MarkerDialect(ShellDialect):
        def extract_commands(self, script):
            return ("echo",)

        def make_invocation(self, script):
            return ShellInvocation(script="printf marker")

        def state_key(self):
            return "test-marker"

    agent = SimpleNamespace(_notification_store=notification_store_for(tmp_path))
    manager = ShellManager(
        policy=ShellPolicy.yolo(), working_dir=str(tmp_path), agent=agent,
        dialect=MarkerDialect(),
    )
    assert manager.shell_kind is ShellKind.POSIX
