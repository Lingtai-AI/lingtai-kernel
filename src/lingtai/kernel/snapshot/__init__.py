"""Core-owned Ports for workdir snapshots and source revision queries.

The sibling CONTRACT.md defines the normative behavior.  These interfaces carry
only capability-native values; process commands and Git result objects remain in
outside adapters.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class SnapshotPort(ABC):
    """Best-effort whole-workdir snapshot capability."""

    @abstractmethod
    def initialize(self) -> None:
        """Initialize snapshot storage and its required baseline files."""
        ...

    @abstractmethod
    def snapshot(self) -> str | None:
        """Capture all current workdir state, returning its revision if changed."""
        ...

    @abstractmethod
    def collect_garbage(self) -> None:
        """Run bounded, best-effort storage maintenance."""
        ...


class SourceRevisionPort(ABC):
    """Read-only source revision capability with caller-owned query policy."""

    @abstractmethod
    def current_revision(
        self, short_length: int | None, timeout_seconds: float
    ) -> str | None:
        """Return HEAD abbreviated natively or to ``short_length`` characters."""
        ...

    @abstractmethod
    def is_dirty(self, timeout_seconds: float) -> bool | None:
        """Return tracked-file dirty state, or ``None`` when it is unknown."""
        ...


__all__ = ["SnapshotPort", "SourceRevisionPort"]
