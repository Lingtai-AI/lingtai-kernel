"""Daemon-local process mechanism port.

Backend policy receives only immutable commands, opaque handles, and exit
receipts.  Platform details live in the adapter selected by daemon setup.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol


@dataclass(frozen=True)
class DaemonProcessCommand:
    """A direct argv launch request; shell syntax is deliberately impossible."""

    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self) -> None:
        argv = tuple(self.argv)
        object.__setattr__(self, "argv", argv)
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise ValueError("daemon process argv must be a non-empty string tuple")
        if not isinstance(self.cwd, Path):
            object.__setattr__(self, "cwd", Path(self.cwd))
        if self.environment is not None:
            try:
                environment = tuple(self.environment)
            except TypeError as exc:
                raise ValueError("daemon process environment must be key/value pairs") from exc
            normalized: list[tuple[str, str]] = []
            for entry in environment:
                if (not isinstance(entry, (tuple, list)) or len(entry) != 2
                        or not isinstance(entry[0], str) or not isinstance(entry[1], str)):
                    raise ValueError("daemon process environment must contain string key/value pairs")
                normalized.append((entry[0], entry[1]))
            object.__setattr__(self, "environment", tuple(normalized))


class DaemonProcessTerminationScope(str, Enum):
    """Who owns the process group used by termination/reclaim.

    ``PRIVATE_PROCESS_GROUP`` is created by an ordinary manager Port and may
    be signalled by that Port. ``INHERITED_SUPERVISOR_GROUP`` belongs to the
    detached execution supervisor: a Port may signal only its exact child,
    while the supervisor alone may reclaim the inherited group.
    """

    PRIVATE_PROCESS_GROUP = "private_process_group"
    INHERITED_SUPERVISOR_GROUP = "inherited_supervisor_group"


@dataclass(frozen=True)
class DaemonProcessObservation:
    """Portable immutable identity observed immediately after child spawn."""

    pid: int
    pgid: int | None
    start_identity: str | None
    termination_scope: DaemonProcessTerminationScope = (
        DaemonProcessTerminationScope.PRIVATE_PROCESS_GROUP
    )


@dataclass(frozen=True)
class DaemonProcessExit:
    """Raw child return code plus an optional LingTai termination cause."""

    returncode: int | None
    reason: str | None = None


class DaemonProcessHandle:
    """Opaque live handle; adapter internals are intentionally not public."""

    __slots__ = ("_token",)

    def __init__(self, token: object) -> None:
        self._token = token


class DaemonStderr(Protocol):
    lines: list[str]

    def join(self, timeout: float = 2.0) -> None: ...


class DaemonProcessPort(Protocol):
    def spawn(self, command: DaemonProcessCommand, *, group_id: str | None = None) -> DaemonProcessHandle: ...
    def iter_stdout(self, handle: DaemonProcessHandle, *, deadline: float | None = None) -> Iterable[str]: ...
    def drain_stderr(self, handle: DaemonProcessHandle, *, on_line=None, thread_name: str = "daemon-stderr") -> DaemonStderr: ...
    def wait(self, handle: DaemonProcessHandle, *, timeout: float | None = None) -> DaemonProcessExit: ...
    def terminate(self, handle: DaemonProcessHandle, *, reason: str | None = None) -> DaemonProcessExit: ...
    def terminate_group(self, group_id: str, *, reason: str | None = None) -> int: ...
    def terminate_all(self, *, reason: str | None = None) -> int: ...
    def release(self, handle: DaemonProcessHandle) -> bool: ...
