"""Shell-language values used by the Bash capability."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


def extract_posix_commands(command: str) -> tuple[str, ...]:
    """Extract command names using the existing POSIX Bash policy rules."""
    flat = re.sub(r"\$\([^)]*\)", lambda m: "; " + m.group()[2:-1] + " ;", command)
    flat = re.sub(r"`[^`]*`", lambda m: "; " + m.group()[1:-1] + " ;", flat)
    parts = re.split(r"\|{1,2}|&&|;|\n", flat)
    commands: list[str] = []
    for part in parts:
        tokens = part.strip().split()
        while tokens and re.fullmatch(r"[A-Za-z_]\w*=\S*", tokens[0]):
            tokens = tokens[1:]
        if tokens:
            commands.append(tokens[0])
    return tuple(commands)


@dataclass(frozen=True)
class ShellInvocation:
    """Serializable shell execution form; no cwd, timeout, or result policy."""

    script: str
    executable: str | None = None
    argv: tuple[str, ...] | None = None
    encoding: str | None = None
    errors: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.script, str) or not self.script.strip():
            raise ValueError("script must be a non-empty string")
        for name in ("executable", "encoding", "errors"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be a non-empty string when present")
        if self.argv is None:
            return
        if not isinstance(self.argv, (tuple, list)):
            raise ValueError("argv must be a tuple or list of strings")
        if self.executable is None:
            raise ValueError("argv form requires a non-empty executable")
        if not all(isinstance(item, str) for item in self.argv):
            raise ValueError("argv elements must be strings")
        object.__setattr__(self, "argv", tuple(self.argv))

    def to_dict(self) -> dict[str, Any]:
        return {
            "script": self.script,
            "executable": self.executable,
            "argv": list(self.argv) if self.argv is not None else None,
            "encoding": self.encoding,
            "errors": self.errors,
        }

    @classmethod
    def from_dict(cls, value: object) -> "ShellInvocation | None":
        keys = {"script", "executable", "argv", "encoding", "errors"}
        if not isinstance(value, dict) or set(value) != keys:
            return None
        argv = value.get("argv")
        if argv is not None and (
            not isinstance(argv, (list, tuple)) or not all(isinstance(item, str) for item in argv)
        ):
            return None
        executable = value.get("executable")
        encoding = value.get("encoding")
        errors = value.get("errors")
        if any(item is not None and not isinstance(item, str) for item in (executable, encoding, errors)):
            return None
        try:
            return cls(
                script=value["script"], executable=executable, argv=argv,
                encoding=encoding, errors=errors,
            )
        except (TypeError, ValueError):
            return None

    def process_args(self) -> tuple[object, dict[str, object]]:
        """Return only dialect process arguments; callers add lifecycle policy."""
        if self.argv is not None:
            return [self.executable, *self.argv, self.script], {"shell": False}
        kwargs: dict[str, object] = {"shell": True}
        if self.executable is not None:
            kwargs["executable"] = self.executable
        return self.script, kwargs


class ShellDialect:
    """Bash-local port for policy extraction and invocation construction."""

    def extract_commands(self, script: str) -> tuple[str, ...]:
        raise NotImplementedError

    def make_invocation(self, script: str) -> ShellInvocation:
        raise NotImplementedError

    def state_key(self) -> str:
        raise NotImplementedError
