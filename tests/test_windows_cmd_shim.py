"""Unit tests for the trusted cmd.exe shim handling (PR-3).

Covers npm/npx resolution to a direct node invocation, .cmd/.bat shim
identification, cmd.exe escaping, unsafe metachar rejection, the simple
command tokenizer, and the PowerShellDialect wiring.
"""
import os
import shutil
import stat

import pytest

from lingtai.adapters.windows.powershell import PowerShellDialect
from lingtai.adapters.windows import windows_cmd_shim as shim
from lingtai.adapters.windows.windows_cmd_shim import (
    build_cmd_exe_invocation,
    escape_for_windows_cmd_exe,
    is_cmd_bat_shim,
    reject_unsafe_metachars,
    resolve_cmd_bat_shim,
    resolve_npm_argv,
    split_simple_command,
    try_cmd_shim_plan,
)

_FAKE_CMD_EXE = r"C:\Windows\System32\cmd.exe"


def _make_executable(path) -> None:
    path.write_bytes(b"@echo off\r\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def npm_prefix(tmp_path):
    """A fake npm prefix: node + npm.cmd/npx.cmd shims + npm-cli.js layout."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "node").write_bytes(b"#!/bin/sh\nexit 0\n")
    (bin_dir / "node").chmod(0o755)
    # Windows resolves bare ``node`` through PATHEXT to ``node.exe``; provide
    # both so the fixtures behave on POSIX CI and on Windows hosts.
    (bin_dir / "node.exe").write_bytes(b"#!/bin/sh\nexit 0\n")
    (bin_dir / "node.exe").chmod(0o755)
    _make_executable(bin_dir / "npm.cmd")
    _make_executable(bin_dir / "npx.cmd")
    npm_bin = bin_dir / "node_modules" / "npm" / "bin"
    npm_bin.mkdir(parents=True)
    (npm_bin / "npm-cli.js").write_text("// npm-cli\n", encoding="utf-8")
    (npm_bin / "npx-cli.js").write_text("// npx-cli\n", encoding="utf-8")
    return bin_dir


# --- shim detection ----------------------------------------------------------


def test_is_cmd_bat_shim():
    assert is_cmd_bat_shim("npm.cmd")
    assert is_cmd_bat_shim("yarn.bat")
    assert is_cmd_bat_shim("NPM.CMD")
    assert is_cmd_bat_shim(r"C:\tools\foo.cmd")
    assert not is_cmd_bat_shim("npm")
    assert not is_cmd_bat_shim("npm.exe")
    assert not is_cmd_bat_shim("foo.txt")
    assert not is_cmd_bat_shim("a.cmd.exe")


# --- escaping ----------------------------------------------------------------


def test_escape_for_windows_cmd_exe():
    assert escape_for_windows_cmd_exe("npm.cmd") == "npm.cmd"
    assert escape_for_windows_cmd_exe("run") == "run"
    assert escape_for_windows_cmd_exe("a^b") == "a^^b"
    assert escape_for_windows_cmd_exe('a"b') == '"a""b"'
    for bad in ("a&b", "a|b", "a<b", "a>b", "a%b", "a\rb", "a\nb"):
        with pytest.raises(ValueError, match="unsafe metacharacter"):
            escape_for_windows_cmd_exe(bad)


def test_reject_unsafe_metachars():
    reject_unsafe_metachars(["npm.cmd", "run", "build"])  # clean args pass
    for bad in ("%FOO%", "a`b", "a^b", "$var", "$(cmd)", "${x}", "$env:PATH", "$5"):
        with pytest.raises(ValueError, match="unsafe metacharacter"):
            reject_unsafe_metachars(["foo.cmd", bad])


# --- invocation construction ------------------------------------------------


def test_build_cmd_exe_invocation():
    argv = build_cmd_exe_invocation(
        ["npm.cmd", "run", "build"], cmd_exe=_FAKE_CMD_EXE
    )
    assert argv == [_FAKE_CMD_EXE, "/d", "/s", "/c", "npm.cmd run build"]
    # args with spaces are quoted by list2cmdline inside the /c payload
    argv = build_cmd_exe_invocation(["foo.cmd", "a b"], cmd_exe="cmd.exe")
    assert argv[-1] == 'foo.cmd "a b"'
    # caret escaping survives into the payload
    argv = build_cmd_exe_invocation(["foo.cmd", "a^b"], cmd_exe="cmd.exe")
    assert argv[-1] == "foo.cmd a^^b"
    with pytest.raises(ValueError, match="empty command"):
        build_cmd_exe_invocation([])


# --- simple command tokenizer -----------------------------------------------


def test_split_simple_command():
    assert split_simple_command("npm.cmd run build") == ["npm.cmd", "run", "build"]
    assert split_simple_command("  npm.cmd   run  build  ") == ["npm.cmd", "run", "build"]
    assert split_simple_command('foo.cmd run "a b"') == ["foo.cmd", "run", "a b"]
    assert split_simple_command("foo.cmd run 'a b'") == ["foo.cmd", "run", "a b"]
    # doubled quotes inside a quoted region decode to one literal quote
    assert split_simple_command("foo.cmd 'it''s'") == ["foo.cmd", "it's"]
    # ...while a bare ``it''s`` is token concatenation (``it`` + ``''`` + ``s``)
    assert split_simple_command("foo.cmd it''s") == ["foo.cmd", "its"]
    assert split_simple_command("") is None
    assert split_simple_command("   ") is None
    assert split_simple_command("foo.cmd a; b") is None
    assert split_simple_command("foo.cmd a | b") is None
    assert split_simple_command("foo.cmd a && b") is None
    assert split_simple_command("foo.cmd $(x)") is None
    assert split_simple_command("foo.cmd $env:X") is None
    assert split_simple_command("foo.cmd a`tb") is None
    assert split_simple_command("foo.cmd 'unbalanced") is None
    # embedded double quotes cannot round-trip through cmd.exe/list2cmdline
    assert split_simple_command('foo.cmd run "a""b"') is None


# --- npm/npx resolution ------------------------------------------------------


def test_resolve_npm_argv(npm_prefix):
    path = str(npm_prefix)
    node = shutil.which("node", path=path)
    cli = str(npm_prefix / "node_modules" / "npm" / "bin" / "npm-cli.js")
    npx_cli = str(npm_prefix / "node_modules" / "npm" / "bin" / "npx-cli.js")
    assert resolve_npm_argv(["npm", "run", "build"], path=path) == [
        node, cli, "run", "build",
    ]
    assert resolve_npm_argv(["npm.cmd", "run", "build"], path=path) == [
        node, cli, "run", "build",
    ]
    upper = resolve_npm_argv(["NPM.CMD", "run", "build"], path=path)
    assert upper is not None
    assert upper[0] == node and upper[2:] == ["run", "build"]
    assert os.path.samefile(upper[1], cli)
    assert resolve_npm_argv(["npx", "tsc"], path=path) == [node, npx_cli, "tsc"]
    assert resolve_npm_argv(["npx.cmd", "tsc"], path=path) == [node, npx_cli, "tsc"]
    assert resolve_npm_argv(["yarn", "build"], path=path) is None
    assert resolve_npm_argv(["npm", "run"], path=str(npm_prefix / "missing")) is None
    assert resolve_npm_argv([], path=path) is None


# --- PATH resolution ---------------------------------------------------------


def test_resolve_cmd_bat_shim(npm_prefix):
    path = str(npm_prefix)
    assert resolve_cmd_bat_shim("npm.cmd", path=path) == str(npm_prefix / "npm.cmd")
    assert resolve_cmd_bat_shim("node", path=path) is None
    assert resolve_cmd_bat_shim("missing.cmd", path=path) is None


def test_resolve_cmd_bat_shim_pathext_bare_name(monkeypatch, tmp_path):
    # On Windows a bare name can resolve through PATHEXT to a .cmd shim;
    # stub _which so that branch is exercised on POSIX too.
    fake = str(tmp_path / "npm.cmd")
    monkeypatch.setattr(shim, "_which", lambda name, path: fake if name == "npm" else None)
    assert resolve_cmd_bat_shim("npm") == fake


# --- public plan entry -------------------------------------------------------


def test_try_cmd_shim_plan(npm_prefix, tmp_path, monkeypatch):
    path = str(npm_prefix)
    node = shutil.which("node", path=path)
    cli = str(npm_prefix / "node_modules" / "npm" / "bin" / "npm-cli.js")

    # npm/npx -> direct node invocation (no cmd.exe involved)
    kind, argv = try_cmd_shim_plan("npm run build", path=path)
    assert (kind, argv) == ("node", [node, cli, "run", "build"])
    kind, argv = try_cmd_shim_plan("npm.cmd --version", path=path)
    assert (kind, argv) == ("node", [node, cli, "--version"])

    # non-shim and complex scripts -> None (pwsh fallback)
    assert try_cmd_shim_plan("echo hi", path=path) is None
    assert try_cmd_shim_plan("npm.cmd run build; echo hi", path=path) is None

    # other .cmd/.bat shims -> trusted cmd.exe /d /s /c wrapper
    _make_executable(tmp_path / "tool.cmd")
    monkeypatch.setenv("COMSPEC", _FAKE_CMD_EXE)
    # neutralise the SystemRoot preference on hosts that set it (e.g. Windows)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "no-such-root"))
    kind, argv = try_cmd_shim_plan("tool.cmd run --flag", path=str(tmp_path))
    assert kind == "cmd"
    assert argv[0] == _FAKE_CMD_EXE
    assert argv[1:4] == ["/d", "/s", "/c"]
    assert argv[-1] == "tool.cmd run --flag"

    # unsafe metachars on a shim command are rejected, not reinterpreted
    with pytest.raises(ValueError, match="unsafe metacharacter"):
        try_cmd_shim_plan("tool.cmd run %FOO%", path=str(tmp_path))
    with pytest.raises(ValueError, match="unsafe metacharacter"):
        try_cmd_shim_plan("tool.cmd run a^b", path=str(tmp_path))
    # ``$var`` needs PowerShell expansion, so it keeps pwsh semantics instead
    # of being handed to cmd.exe verbatim (no rejection, no shim)
    assert try_cmd_shim_plan("tool.cmd run $env:VAR", path=str(tmp_path)) is None
    # ...but a complex script that needs PowerShell semantics is not an error
    assert try_cmd_shim_plan("tool.cmd run %FOO%; echo done", path=str(tmp_path)) is None


# --- PowerShellDialect wiring ------------------------------------------------


def test_powershell_dialect_uses_trusted_cmd_shim(npm_prefix, tmp_path, monkeypatch):
    _make_executable(tmp_path / "tool.cmd")
    monkeypatch.setenv(
        "PATH",
        str(tmp_path) + os.pathsep + str(npm_prefix) + os.pathsep
        + os.environ.get("PATH", ""),
    )
    monkeypatch.setenv("COMSPEC", _FAKE_CMD_EXE)
    # neutralise the SystemRoot preference on hosts that set it (e.g. Windows)
    monkeypatch.setenv("SystemRoot", str(tmp_path / "no-such-root"))
    dialect = PowerShellDialect(executable="pwsh")

    # .cmd first token -> trusted cmd.exe invocation, pwsh never runs
    invocation = dialect.make_invocation("tool.cmd run build")
    args, kwargs = invocation.process_args()
    assert kwargs == {"shell": False}
    assert args == [_FAKE_CMD_EXE, "/d", "/s", "/c", "tool.cmd run build"]

    # npm -> direct node invocation inside the normal pwsh envelope, emitted
    # as PS source via the call operator + single-quoted literals
    invocation = dialect.make_invocation("npm run build")
    args, kwargs = invocation.process_args()
    assert args[:5] == ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
    node = shutil.which("node", path=str(npm_prefix))
    cli = str(npm_prefix / "node_modules" / "npm" / "bin" / "npm-cli.js")
    assert f"& '{node}' '{cli}' 'run' 'build'" in invocation.stdin_script
    assert '"' not in invocation.stdin_script  # PS source: never list2cmdline C-runtime quoting
    assert "$global:__lingtai_success" in invocation.stdin_script

    # unsafe metachars on a shim command -> clean ValueError
    with pytest.raises(ValueError, match="unsafe metacharacter"):
        dialect.make_invocation("tool.cmd run %FOO%")

    # non-shim commands keep the plain pwsh wrapper
    invocation = dialect.make_invocation("Write-Output hi")
    args, kwargs = invocation.process_args()
    assert args[:5] == ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Write-Output hi" in invocation.stdin_script
    assert kwargs == {"shell": False}


# --- PS quoting of the npm/npx rewrite (PR #1189 blocker fixes) -------------


def _fake_nodejs_prefix(tmp_path):
    r"""A node prefix whose path contains spaces, like ``C:\Program Files\nodejs``."""
    prefix = tmp_path / "Program Files" / "nodejs"
    prefix.mkdir(parents=True)
    for name in ("node", "node.exe"):
        script = prefix / name
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
    _make_executable(prefix / "npm.cmd")
    _make_executable(prefix / "npx.cmd")
    npm_bin = prefix / "node_modules" / "npm" / "bin"
    npm_bin.mkdir(parents=True)
    (npm_bin / "npm-cli.js").write_text("// npm-cli\n", encoding="utf-8")
    (npm_bin / "npx-cli.js").write_text("// npx-cli\n", encoding="utf-8")
    return prefix


def test_npm_script_with_spaces_in_path(tmp_path, monkeypatch):
    r"""Default Windows layout (path with spaces) yields valid PS source.

    Regression for the PR #1189 blocker: ``subprocess.list2cmdline`` quoting
    (``"C:\Program Files\nodejs\node.exe" ...``) is rejected by PowerShell
    as a quoted string in command position; the rewrite must use the call
    operator with PS single-quoted elements instead.
    """
    prefix = _fake_nodejs_prefix(tmp_path)
    monkeypatch.setenv(
        "PATH", str(prefix) + os.pathsep + os.environ.get("PATH", ""),
    )
    dialect = PowerShellDialect(executable="pwsh")
    invocation = dialect.make_invocation("npm run build")
    args, kwargs = invocation.process_args()
    node = shutil.which("node", path=str(prefix))
    cli = str(prefix / "node_modules" / "npm" / "bin" / "npm-cli.js")
    assert " " in node  # the spacey path is genuinely exercised
    assert f"& '{node}' '{cli}' 'run' 'build'" in invocation.stdin_script
    assert '"' not in invocation.stdin_script  # no list2cmdline C-runtime quoting
    assert invocation.stdin_script.count("'") % 2 == 0  # balanced quotes -> no parse error


def test_npm_script_with_single_quote_arg(npm_prefix, monkeypatch):
    """An argument containing an apostrophe stays balanced in the PS source.

    Regression for the PR #1189 must-fix: ``npm view "it's"`` must become
    ``... view 'it''s'`` (PS ``''`` doubling), not leak an unbalanced quote.
    """
    monkeypatch.setenv(
        "PATH", str(npm_prefix) + os.pathsep + os.environ.get("PATH", ""),
    )
    dialect = PowerShellDialect(executable="pwsh")
    invocation = dialect.make_invocation('npm view "it\'s"')
    args, kwargs = invocation.process_args()
    node = shutil.which("node", path=str(npm_prefix))
    cli = str(npm_prefix / "node_modules" / "npm" / "bin" / "npm-cli.js")
    assert f"& '{node}' '{cli}' 'view' 'it''s'" in invocation.stdin_script
    assert invocation.stdin_script.count("'") % 2 == 0  # balanced quotes -> no parse error
