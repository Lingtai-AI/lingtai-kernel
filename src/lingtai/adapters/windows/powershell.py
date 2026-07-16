"""PowerShell 7 dialect adapter for the shell language Port.

This adapter intentionally does not reuse the POSIX extractor.  It recognizes
PowerShell statement/pipeline boundaries and recursively inspects command
substitutions and script blocks.  Unsupported dynamic syntax is represented by
a sentinel command so a configured allowlist/denylist fails closed; trusted
(yolo) execution can still pass the original script to pwsh.
"""
from __future__ import annotations

import re
import shutil

from lingtai.tools.bash._shell_dialect import ShellDialect, ShellInvocation

_UNSUPPORTED = "__powershell_unsupported__"
_CONTROL_WORDS = {
    "begin", "break", "catch", "class", "continue", "data", "do", "else",
    "end", "finally", "for", "foreach", "function", "if", "param", "process",
    "return", "switch", "throw", "trap", "try", "until", "using", "while",
}
_ASSIGNMENT_RE = re.compile(r"^(?:\$[A-Za-z_][\w:]*|[A-Za-z_][\w-]*)$")
_TOKEN_RE = re.compile(r"(?:'[^']*(?:''[^']*)*'|\"(?:`.|[^\"])*\"|[^\s|;&(){}]+)")


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
        if char in {"'", '"'}:
            quote = char
            i += 1
            continue
        if char in "|;\r\n":
            pieces.append(script[begin:i])
            if char == "|" and i + 1 < len(script) and script[i + 1] in "|&":
                i += 1
            elif char == "&" and i + 1 < len(script) and script[i + 1] == "&":
                i += 1
            begin = i + 1
        elif char == "&" and i + 1 < len(script) and script[i + 1] == "&":
            pieces.append(script[begin:i])
            i += 1
            begin = i + 1
        i += 1
    pieces.append(script[begin:])
    return pieces, quote is None


def _commands(script: str) -> tuple[str, ...]:
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
            if text.startswith("$(", i):
                region = _balanced_inner(text, i + 1, "(", ")")
                if region is None:
                    result.append(_UNSUPPORTED)
                    break
                nested.extend(_commands(region[0]))
                i = region[1]
                continue
            if text[i] == "{" or (text[i] == "@" and i + 1 < len(text) and text[i + 1] == "{"):
                opener_at = i if text[i] == "{" else i + 1
                region = _balanced_inner(text, opener_at, "{", "}")
                if region is None:
                    result.append(_UNSUPPORTED)
                    break
                nested.extend(_commands(region[0]))
                i = region[1]
                continue
            remainder.append(text[i])
            i += 1
        else:
            outer = "".join(remainder).strip()
            tokens = _TOKEN_RE.findall(outer)
            if not tokens:
                result.extend(nested)
                continue
            first = tokens[0].strip("'\"")
            if first in {"&", "."}:
                if len(tokens) < 2 or tokens[1].startswith("$"):
                    result.append(_UNSUPPORTED)
                    result.extend(nested)
                    continue
                first = tokens[1].strip("'\"")
            # Skip assignments and PowerShell control syntax.  A bare control
            # statement without a block is unsupported rather than guessed.
            index = 0
            while index + 2 < len(tokens) and _ASSIGNMENT_RE.fullmatch(tokens[index]) and tokens[index + 1] == "=":
                index += 2
            if index >= len(tokens):
                result.extend(nested)
                continue
            first = tokens[index].strip("'\"")
            if first.casefold() in _CONTROL_WORDS:
                result.extend(nested)
                continue
            if first.startswith("$") or first.startswith("@"):
                # A variable/array expression in a script block is data, not a
                # command.  Dynamic invocation was already rejected at ``& $x``.
                result.extend(nested)
                continue
            result.append(first)
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
        # exit status to PowerShell's generic 0/1 process status.  Capture the
        # final pipeline truth immediately after the user script block: a
        # successful final pipeline is 0, a failed native final pipeline keeps
        # its exact ``$LASTEXITCODE``, and a failed PowerShell pipeline is 1.
        wrapped = (
            "$global:LASTEXITCODE = 0\n"
            "& {\n"
            f"{script}\n"
            "}\n"
            "$__lingtai_success = $?\n"
            "$__lingtai_native_exit = [int]$global:LASTEXITCODE\n"
            "if ($__lingtai_success) { exit 0 }\n"
            "if ($__lingtai_native_exit -ne 0) { exit $__lingtai_native_exit }\n"
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
