"""Capability-local byte-stream Port for interactive terminal children.

This boundary is intentionally separate from :mod:`daemon.process_port`:
headless CLI children have line-oriented stdout/stderr, while an interactive
TUI needs one lossless bidirectional byte stream and a terminal size.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol


@dataclass(frozen=True)
class InteractiveTerminalCommand:
    """Immutable direct launch request for one interactive terminal child."""

    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...] | None = None
    columns: int = 120
    rows: int = 40

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("interactive terminal argv must be a non-empty string tuple")
        object.__setattr__(self, "argv", argv)
        if not isinstance(self.cwd, Path):
            object.__setattr__(self, "cwd", Path(self.cwd))
        if self.environment is not None:
            try:
                environment = tuple(self.environment)
            except TypeError as exc:
                raise ValueError("interactive terminal environment must be key/value pairs") from exc
            normalized: list[tuple[str, str]] = []
            for entry in environment:
                if (
                    not isinstance(entry, (tuple, list))
                    or len(entry) != 2
                    or not isinstance(entry[0], str)
                    or not isinstance(entry[1], str)
                ):
                    raise ValueError(
                        "interactive terminal environment must contain string key/value pairs"
                    )
                normalized.append((entry[0], entry[1]))
            object.__setattr__(self, "environment", tuple(normalized))
        for name, value in (("columns", self.columns), ("rows", self.rows)):
            if type(value) is not int or value < 1 or value > 65535:
                raise ValueError(f"interactive terminal {name} must be a positive integer")


@dataclass(frozen=True)
class InteractiveTerminalExit:
    """Child return code and the first local termination cause, if any."""

    returncode: int | None
    reason: str | None = None


class InteractiveTerminalHandle:
    """Opaque live handle owned by an ``InteractiveTerminalPort``."""

    __slots__ = ("_token",)

    def __init__(self, token: object) -> None:
        self._token = token


class InteractiveTerminalPort(Protocol):
    """Lossless byte-stream and process-ownership boundary for interactive TUI children."""

    def spawn(
        self,
        command: InteractiveTerminalCommand,
        *,
        group_id: str | None = None,
    ) -> InteractiveTerminalHandle: ...

    def read(
        self,
        handle: InteractiveTerminalHandle,
        *,
        deadline: float | None = None,
    ) -> Iterable[bytes]: ...

    def write(self, handle: InteractiveTerminalHandle, data: bytes) -> None: ...

    def wait(
        self,
        handle: InteractiveTerminalHandle,
        *,
        timeout: float | None = None,
    ) -> InteractiveTerminalExit: ...

    def terminate(
        self,
        handle: InteractiveTerminalHandle,
        *,
        reason: str | None = None,
    ) -> InteractiveTerminalExit: ...

    def terminate_group(self, group_id: str, *, reason: str | None = None) -> int: ...

    def terminate_all(self, *, reason: str | None = None) -> int: ...

    def release(self, handle: InteractiveTerminalHandle) -> bool: ...


__all__ = [
    "InteractiveTerminalCommand",
    "InteractiveTerminalExit",
    "InteractiveTerminalHandle",
    "InteractiveTerminalPort",
]
