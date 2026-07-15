"""Small policy fakes for Snapshot and SourceRevision Ports."""
from __future__ import annotations

from dataclasses import dataclass, field

from lingtai.kernel.snapshot import SnapshotPort, SourceRevisionPort


@dataclass
class FakeSnapshotPort(SnapshotPort):
    snapshot_result: str | None = None
    initialize_calls: int = 0
    snapshot_calls: int = 0
    collect_garbage_calls: int = 0

    def initialize(self) -> None:
        self.initialize_calls += 1

    def snapshot(self) -> str | None:
        self.snapshot_calls += 1
        return self.snapshot_result

    def collect_garbage(self) -> None:
        self.collect_garbage_calls += 1


@dataclass
class FakeSourceRevisionPort(SourceRevisionPort):
    revision: str | None = None
    dirty: bool | None = None
    revision_calls: list[tuple[int | None, float]] = field(
        default_factory=list, compare=False, hash=False
    )
    dirty_calls: list[float] = field(default_factory=list, compare=False, hash=False)

    def current_revision(
        self, short_length: int | None, timeout_seconds: float
    ) -> str | None:
        self.revision_calls.append((short_length, timeout_seconds))
        return self.revision

    def is_dirty(self, timeout_seconds: float) -> bool | None:
        self.dirty_calls.append(timeout_seconds)
        return self.dirty


def make_test_snapshot_port() -> FakeSnapshotPort:
    return FakeSnapshotPort()


def make_test_source_revision_port() -> FakeSourceRevisionPort:
    return FakeSourceRevisionPort()
