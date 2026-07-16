"""POSIX production adapter for the Bash-local shell dialect."""
from __future__ import annotations

from lingtai.tools.bash._shell_dialect import (
    ShellDialect,
    ShellInvocation,
    extract_posix_commands,
)


class PosixBashDialect(ShellDialect):
    def extract_commands(self, script: str) -> tuple[str, ...]:
        return extract_posix_commands(script)

    def make_invocation(self, script: str) -> ShellInvocation:
        # None/None preserve subprocess's historical POSIX text decoding.
        return ShellInvocation(script=script)

    def state_key(self) -> str:
        return "posix"
