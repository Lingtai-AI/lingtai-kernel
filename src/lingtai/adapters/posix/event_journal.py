"""POSIX JSONL event journal backed by the existing SQLite sidecar index."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from lingtai.kernel.event_journal import EventJournalPort, JournalPosition
from lingtai.kernel.services.logging import (
    CompositeLoggingService,
    JSONLLoggingService,
    SQLiteEventIndex,
)


class PosixJsonlEventJournalAdapter(EventJournalPort):
    """Write authoritative JSONL events plus a best-effort SQLite index."""

    def __init__(
        self,
        working_dir: str | Path,
        *,
        ensure_ascii: bool = False,
    ) -> None:
        log_dir = Path(working_dir) / "logs"
        primary = JSONLLoggingService(
            log_dir / "events.jsonl",
            ensure_ascii=ensure_ascii,
        )
        self._journal = CompositeLoggingService(
            primary,
            sqlite_index=SQLiteEventIndex(
                log_dir / "log.sqlite",
                ensure=False,
                keep_open=False,
            ),
        )

    def append(self, event: dict[str, Any]) -> JournalPosition | None:
        metadata = self._journal.log(event)
        if metadata is None:
            return None
        return JournalPosition(
            source_file=str(metadata["source_file"]),
            source_offset=int(metadata["source_offset"]),
        )

    def close(self) -> None:
        self._journal.close()
