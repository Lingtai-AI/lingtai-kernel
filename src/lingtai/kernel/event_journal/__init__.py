"""Core-owned outbound Port for durable structured event journaling."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class JournalPosition:
    """Authoritative source location returned after a durable append."""

    source_file: str
    source_offset: int


class EventJournalPort(ABC):
    """Append-only structured event journal boundary owned by Core."""

    @abstractmethod
    def append(self, event: dict[str, Any]) -> JournalPosition | None:
        """Durably append ``event`` and return its authoritative position."""

    @abstractmethod
    def close(self) -> None:
        """Flush and release resources; repeated calls must be safe."""


__all__ = ["EventJournalPort", "JournalPosition"]
