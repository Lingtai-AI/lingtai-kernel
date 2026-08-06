"""PowerShell 7 dialect adapter for the shell language Port.

This adapter intentionally does not reuse the POSIX extractor.  It recognizes
PowerShell statement/pipeline boundaries and recursively inspects command
substitutions and script blocks.  Unsupported dynamic syntax is represented by
a sentinel command so a configured allowlist/denylist fails closed; trusted
(yolo) execution can still pass the original script to pwsh.

The extractor is conservative by design: it only emits statically knowable
command names, strips comments, honours here-strings and backtick line
continuations, skips literals/types/operators as data, and fails closed on
dynamic invocation (call operator on a variable, Invoke-Expression, etc.).
"""
from __future__ import annotations

import re
import shutil

from lingtai.tools.bash._shell_dialect import ShellDialect, ShellInvocation

_UNSUPPORTED = "__powershell_unsupported__"
_CONTROL_WORDS = {
    "begin", "break", "catch", "class", "continue", "data", "default", "do",
    "else", "elseif", "end", "enum", "exit", "filter", "finally", "for",
    "foreach", "function", "hidden", "if", "in", "param", "process",
    "return", "static", "switch", "throw", "trap", "try", "until", "using",
    "while",
}
# Dynamic evaluation primitives: invoking these with a non-literal argument
# executes arbitrary code, so the whole script must fail closed.
_EVAL_COMMANDS = {"invoke-expression", "iex"}
# Commands that accept a -ScriptBlock; a non-literal block is dynamic.
_SCRIPTBLOCK_COMMANDS = {"invoke-command", "start-job"}
_ASSIGNMENT_RE = re.compile(r"^(?:\$[A-Za-z_][\w:]*|[A-Za-z_][\w-]*)$")
_TOKEN_RE = re.compile(
    r"(?:'[^']*(?:''[^']*)*'|\"(?:`.|[^\"])*\"|&(?=\s|$)|\.(?=\s|$)|[^\s|;&(){}]+)"
)


def _find_here_string_end(script: str, start: int, quote: str) -> int | None:
    """Return the index just past a here-string's closing delimiter.

    ``start`` points at the ``@`` of an ``@'`` or ``@\"`` opener.  The closing
    delimiter (``'@`` / ``\"@``) must appear at the start of a line (allowing
    leading whitespace), per PowerShell syntax.  Returns ``None`` when the
    here-string is unterminated.
    """
    pos = start + 2
    needle = quote + "@"
    while pos < len(script):
        nl = script.find("\n", pos)
        if nl == -1:
            return None
        j = nl + 1
        while j < len(script) and script[j] in " \t":
            j += 1
        if script.startswith(needle, j):
            return j + 2
        pos = nl + 1
    return None


def _strip_comments(script: str) -> str:
    """Remove PowerShell ``#`` comments outside quotes/here-strings."""
    out: list[str] = []
    i = 0
    n = len(script)
    quote: str | None = None
    hs: str | None = None
    while i < n:
        ch = script[i]
        if hs:
            if ch == "\n":
                j = i + 1
                while j < n and script[j] in " \t":
                    j += 1
                if j + 1 < n and script[j] == hs and script[j + 1] == "@":
                    out.append(script[i:j + 2])
                    hs = None
                    i = j + 2
                    continue
            out.append(ch)
            i += 1
            continue
        if quote == "'":
            out.append(ch)
            if ch == "'":
                if i + 1 < n and script[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == '"':
            out.append(ch)
            if ch == "`" and i + 1 < n:
                out.append(script[i + 1])
                i += 2
                continue
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch == "#":
            while i < n and script[i] != "\n":
                i += 1
            continue
        if ch == "@" and i + 1 < n and script[i + 1] in ("'", '"'):
            hs = script[i + 1]
            out.append(ch)
            out.append(hs)
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _balanced_inner(script: str, start: int, opener: str, closer: str) -> tuple[str, int] | None:
    """Return a balanced region, respecting PowerShell quote/backtick rules."""
    depth = 1
    quote: str | None = None
    escaped = False
    i = start + 1
    while i < len(script):
        char = script[i]
        if quote == "'":
            if char == "'":
                if i + 1 < len(script) and script[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "`":
                escaped = True
            elif char == '"':
                quote = None
            i += 1
            continue
        if char == "@" and i + 1 < len(script) and script[i + 1] in ("'", '"'):
            end = _find_here_string_end(script, i, script[i + 1])
            if end is None:
                return None
            i = end
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return script[start + 1 : i], i + 1
        i += 1
    return None


def _split_statements(script: str) -> tuple[list[str], bool]:
    """Split top-level PowerShell statements and report malformed quoting."""
    pieces: list[str] = []
    begin = 0
    i = 0
    quote: str | None = None
    escaped = False
    paren_depth = 0
    brace_depth = 0  # Track brace depth for script blocks
    while i < len(script):
        char = script[i]
        if quote == "'":
            if char == "'":
                if i + 1 < len(script) and script[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "`":
                escaped = True
            elif char == '"':
                quote = None
            i += 1
            continue
        if char == "@" and i + 1 < len(script) and script[i + 1] in ("'", '"'):
            # Here-string: consume the whole body; quotes inside are literal.
            end = _find_here_string_end(script, i, script[i + 1])
            if end is None:
                return pieces, False
            i = end
            continue
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if char == "`" and i + 1 < len(script) and script[i + 1] == "\n":
            # Backtick line continuation: do not split at the newline.
            i += 2
            continue
        if char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0:
                return pieces, False
            paren_depth -= 1
        elif char == "{":  # Track brace depth
            brace_depth += 1
        elif char == "}":  # Track brace depth
            if brace_depth > 0:
                brace_depth -= 1
        if char in "|;\r\n" and paren_depth == 0 and brace_depth == 0:
            pieces.append(script[begin:i])
            if char == "|" and i + 1 < len(script) and script[i + 1] in "|&":
                i += 1
            elif char == "&" and i + 1 < len(script) and script[i + 1] == "&":
                i += 1
            begin = i + 1
        elif char == "&" and i + 1 < len(script) and script[i + 1] == "&" and paren_depth == 0:
            pieces.append(script[begin:i])
            i += 1
            begin = i + 1
        i += 1
    pieces.append(script[begin:])
    return pieces, quote is None and paren_depth == 0 and brace_depth == 0


def _is_quoted_at(script: str, index: int) -> bool:
    """Return whether ``index`` is inside a PowerShell quoted string or here-string."""
    quote: str | None = None
    escaped = False
    i = 0
    while i < index:
        char = script[i]
        if quote == "'":
            if char == "'":
                if i + 1 < index and script[i + 1] == "'":
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if quote == '"':
            if escaped:
                escaped = False
            elif char == "`":
                escaped = True
            elif char == '"':
                quote = None
            i += 1
            continue
        if char == "@" and i + 1 < len(script) and script[i + 1] in ("'", '"'):
            end = _find_here_string_end(script, i, script[i + 1])
            if end is None:
                return True  # unterminated here-string: treat the rest as quoted
            if index < end:
                return True
            i = end
            continue
        if char in {"'", '"'}:
            quote = char
        i += 1
    return quote is not None


def _is_data_token(token: str) -> bool:
    """Return True for tokens that are data, not a command name."""
    if not token:
        return True
    first = token[0]
    if first in "'\"$@`":
        return True
    if first.isdigit():
        return True
    if first in "-:?=[].#":
        return True
    return False


def _has_dynamic_scriptblock(tokens: tuple[str, ...], start: int) -> bool:
    """Return True when a -ScriptBlock argument after ``start`` is non-literal.

    A literal ``{ ... }`` block is consumed into ``nested`` by the char scan and
    does not appear in the token list, so ``-ScriptBlock`` as the last token is
    static; only a variable/expandable token after it is dynamic.
    """
    for idx in range(start, len(tokens)):
        if tokens[idx].casefold() == "-scriptblock":
            if idx + 1 >= len(tokens):
                return False
            return tokens[idx + 1].startswith(("$", "@", '"', "`"))
    return False




def _commands(script: str) -> tuple[str, ...]:
    script = _strip_comments(script)
    pieces, well_formed = _split_statements(script)
    if not well_formed:
        return (_UNSUPPORTED,)
    result: list[str] = []
    for piece in pieces:
        text = piece.strip()
        if not text:
            continue
        # Recursively inspect substitutions and script blocks before removing
        # them from the outer statement.  Dynamic invocation cannot be proved.
        remainder: list[str] = []
        nested: list[str] = []
        i = 0
        while i < len(text):
            if text[i] == "@" and i + 1 < len(text) and text[i + 1] in ("'", '"'):
                # Here-string: data, never commands.
                end = _find_here_string_end(text, i, text[i + 1])
                if end is None:
                    result.append(_UNSUPPORTED)
                    break
                i = end
                continue
            if text.startswith("$(", i) and not _is_quoted_at(text, i):
                region = _balanced_inner(text, i + 1, "(", ")")
                if region is None:
                    result.append(_UNSUPPORTED)
                    break
                nested.extend(_commands(region[0]))
                i = region[1]
                continue
            if (text[i] == "{" or (text[i] == "@" and i + 1 < len(text) and text[i + 1] == "{")) \
                    and not _is_quoted_at(text, i):
                opener_at = i if text[i] == "{" else i + 1
                region = _balanced_inner(text, opener_at, "{", "}")
                if region is None:
                    result.append(_UNSUPPORTED)
                    break
                nested.extend(_commands(region[0]))
                i = region[1]
                continue
            if text[i] == "(" and not _is_quoted_at(text, i):
                prev_j = i - 1
                while prev_j >= 0 and text[prev_j].isspace():
                    prev_j -= 1
                if prev_j >= 0 and text[prev_j] in "&.":
                    # & (Get-Command ...) / . (Get-Command ...): dynamic target.
                    result.append(_UNSUPPORTED)
                    break
                if i > 0 and text[i - 1] == "@":
                    # @(...) array literal: values are data unless they name
                    # a command (e.g. @(Get-Process)).
                    region = _balanced_inner(text, i, "(", ")")
                    if region is None:
                        result.append(_UNSUPPORTED)
                        break
                    if region[0].strip():
                        nested.extend(_commands(region[0]))
                    i = region[1]
                    continue
                region = _balanced_inner(text, i, "(", ")")
                if region is None:
                    result.append(_UNSUPPORTED)
                    break
                if not region[0].strip():
                    # Empty parens: method call ($x.ToString()) or attribute
                    # ([CmdletBinding()]) are data; a bare empty group after a
                    # command (Write-Output ()) stays unsupported.
                    prev = text[i - 1] if i > 0 else ""
                    if prev.isalnum() or prev in "_$].`":
                        i = region[1]
                        continue
                    result.append(_UNSUPPORTED)
                    break
                nested.extend(_commands(region[0]))
                i = region[1]
                continue
            if text[i] == ")" and not _is_quoted_at(text, i):
                result.append(_UNSUPPORTED)
                break
            remainder.append(text[i])
            i += 1
        else:
            outer = "".join(remainder).strip()
            tokens = _TOKEN_RE.findall(outer)
            if not tokens:
                result.extend(nested)
                continue
            emitted = False
            unsupported = False
            suppress_nested = False
            index = 0
            while index < len(tokens):
                tok = tokens[index]
                # Skip assignment LHS: any ``token =`` pair is data.
                if index + 1 < len(tokens) and tokens[index + 1] == "=":
                    index += 2
                    continue
                if tok in {"&", "."}:
                    if index + 1 >= len(tokens):
                        if tok == "&" and nested and not emitted:
                            # & { ... } static script-block target.
                            result.extend(nested)
                            emitted = True
                            break
                        unsupported = True
                        break
                    target = tokens[index + 1]
                    if target.startswith(("$", "@", '"', "`")):
                        unsupported = True
                        break
                    if target.startswith("'") and not target.endswith("'"):
                        unsupported = True
                        break
                    first = target[1:-1].replace("''", "'") if target.startswith("'") else target
                    if "`" in first:
                        unsupported = True
                        break
                    result.append(first)
                    emitted = True
                    break
                low = tok.casefold()
                if low in _CONTROL_WORDS:
                    if low in {"function", "class", "enum", "filter"}:
                        # Skip the declared name after these keywords.
                        if index + 1 < len(tokens):
                            index += 2
                        else:
                            index += 1
                        if low == "enum":
                            suppress_nested = True
                        continue
                    index += 1
                    continue
                if _is_data_token(tok):
                    index += 1
                    continue
                if "`" in tok:
                    unsupported = True
                    break
                if low in _EVAL_COMMANDS:
                    unsupported = True
                    break
                if low in _SCRIPTBLOCK_COMMANDS and _has_dynamic_scriptblock(tokens, index):
                    unsupported = True
                    break
                result.append(tok.strip("'\""))
                result.extend(nested)
                emitted = True
                break
            if unsupported:
                result.append(_UNSUPPORTED)
                result.extend(nested)
            elif not emitted and not suppress_nested:
                result.extend(nested)
    return tuple(result)


class PowerShellDialect(ShellDialect):
    """PowerShell 7 (``pwsh``) invocation and policy extraction."""

    def __init__(self, executable: str | None = None) -> None:
        self._executable = executable or shutil.which("pwsh")
        if not self._executable:
            raise FileNotFoundError(
                "PowerShell 7 executable 'pwsh' was not found; Windows shell requires pwsh and never falls back to Windows PowerShell 5.1"
            )

    def extract_commands(self, script: str) -> tuple[str, ...]:
        return _commands(script)

    def make_invocation(self, script: str) -> ShellInvocation:
        # ``pwsh -Command`` otherwise collapses an external program's native
        # exit status to PowerShell's generic 0/1 process status.  PowerShell
        # 7.3+ can expose non-zero native results as a typed ErrorRecord without
        # changing command flow.  Capture that final-operation type together
        # with ``$?`` and ``$LASTEXITCODE`` inside the user's script scope.
        # Crucially, the wrapper never resets or rewrites ``$LASTEXITCODE``
        # between user statements, so ordinary PowerShell status checks retain
        # their native semantics.
        wrapped = (
            "$global:__lingtai_success = $false\n"
            "$global:__lingtai_native_exit = 0\n"
            "$global:__lingtai_final_native_failure = $false\n"
            "$__lingtai_old_native_pref = $PSNativeCommandUseErrorActionPreference\n"
            "try {\n"
            "  $PSNativeCommandUseErrorActionPreference = $true\n"
            "  & {\n"
            f"{script}\n"
            # These assignments run in the same runtime scope as the user's
            # final pipeline, before the wrapper performs any later command.
            "    $global:__lingtai_success = $?\n"
            "    $global:__lingtai_native_exit = [int]$global:LASTEXITCODE\n"
            "    $global:__lingtai_final_native_failure = (\n"
            "      (-not $global:__lingtai_success) -and\n"
            "      ($Error.Count -gt 0) -and\n"
            "      ($Error[0].FullyQualifiedErrorId -eq 'ProgramExitedWithNonZeroCode')\n"
            "    )\n"
            "  }\n"
            "} catch {\n"
            "  $global:__lingtai_success = $false\n"
            "  $global:__lingtai_native_exit = [int]$global:LASTEXITCODE\n"
            "  $global:__lingtai_final_native_failure = (\n"
            "    $_.FullyQualifiedErrorId -eq 'ProgramExitedWithNonZeroCode'\n"
            "  )\n"
            "  if (-not $global:__lingtai_final_native_failure) {\n"
            "    [Console]::Error.WriteLine($_.ToString())\n"
            "  }\n"
            "} finally {\n"
            "  $PSNativeCommandUseErrorActionPreference = $__lingtai_old_native_pref\n"
            "}\n"
            "if ($global:__lingtai_success) { exit 0 }\n"
            "if ($global:__lingtai_final_native_failure -and "
            "$global:__lingtai_native_exit -ne 0) {\n"
            "  exit $global:__lingtai_native_exit\n"
            "}\n"
            "exit 1\n"
        )
        return ShellInvocation(
            script=wrapped,
            executable=self._executable,
            argv=("-NoLogo", "-NoProfile", "-NonInteractive", "-Command"),
            encoding="utf-8",
            errors="replace",
        )

    def state_key(self) -> str:
        return "powershell"


__all__ = ["PowerShellDialect"]
