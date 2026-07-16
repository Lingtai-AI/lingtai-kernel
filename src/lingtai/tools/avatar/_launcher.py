"""Avatar-local process launch Port."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AvatarLaunchRequest:
    """The complete launch input; cwd and environment are inherited."""

    argv: tuple[str, ...]
    stderr_path: Path


@dataclass(frozen=True)
class AvatarLaunchReceipt:
    """PID plus an opaque adapter-owned process handle."""

    pid: int
    handle: object


class AvatarLauncherPort(Protocol):
    def launch(self, request: AvatarLaunchRequest) -> AvatarLaunchReceipt: ...
    def poll(self, handle: object) -> int | None: ...
    def terminate(self, handle: object) -> None:
        """Request adapter-native termination; not a process-tree operation."""
    def force_terminate(self, handle: object) -> None:
        """Forcefully terminate one owned process; never a tree kill."""
    def release(self, handle: object) -> None:
        """Best-effort, non-raising release that never terminates a live process."""
