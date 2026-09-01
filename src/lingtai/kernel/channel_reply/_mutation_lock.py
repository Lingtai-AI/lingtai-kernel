"""Cross-process mutation lock Port for channel_reply state."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol


EntryKind = Literal["regular", "directory", "symlink", "other"]
WriteMode = Literal["atomic-replace", "atomic-create-hard-link"]
MoveDisposition = Literal["destination-must-be-absent", "replace-destination-entry"]
RemovalState = Literal["absent", "removed", "progress", "retryable", "rejected"]


@dataclass(frozen=True, slots=True)
class ChannelReplyObjectIdentity:
    """Adapter-defined stable object identity."""

    scheme: str
    volume: int | str | bytes
    object_id: int | str | bytes


ChannelReplyRootIdentity = ChannelReplyObjectIdentity


class ChannelReplyExpectedRootMismatch(OSError):
    """The named state root is not the object pinned by this consumer."""


@dataclass(frozen=True, slots=True)
class ChannelReplyEntryInfo:
    """No-follow metadata for one root-relative entry."""

    name: str
    kind: EntryKind
    identity: ChannelReplyObjectIdentity
    size: int
    link_count: int
    private_owner: bool
    private_access: bool
    same_root_filesystem: bool

    @property
    def private_regular_single_link(self) -> bool:
        return (
            self.kind == "regular"
            and self.link_count == 1
            and self.private_owner
            and self.private_access
            and self.same_root_filesystem
        )


class ChannelReplyDirectoryToken(Protocol):
    """Opaque, session-owned directory capability."""


@dataclass(frozen=True, slots=True)
class DirectoryScanBudget:
    inspections: int = 512
    candidates: int = 64


@dataclass(frozen=True, slots=True)
class DirectoryScanCursor:
    """Opaque, identity-bound continuation for one directory inventory."""

    scheme: str
    volume: int | str | bytes
    object_id: int | str | bytes
    position: int
    pending: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DirectoryScanBatch:
    entries: tuple[ChannelReplyEntryInfo, ...]
    inspections: int
    complete: bool
    next_cursor: DirectoryScanCursor | None = None


@dataclass(frozen=True, slots=True)
class OwnedRemovalBudget:
    inspections: int = 512
    removals: int = 128
    max_depth: int = 64
    candidates: int = 64


@dataclass(frozen=True, slots=True)
class OwnedRemovalResult:
    state: RemovalState
    inspections: int = 0
    removals: int = 0
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AtomicWriteResult:
    state: Literal["created", "replaced", "exists"]
    entry: ChannelReplyEntryInfo | None


@dataclass(frozen=True, slots=True)
class MoveResult:
    state: Literal[
        "moved",
        "source-absent",
        "source-changed",
        "destination-exists",
        "rejected",
    ]
    entry: ChannelReplyEntryInfo | None


class ChannelReplyMutationSession(Protocol):
    """Root-bound channel_reply mutation session v1."""

    protocol_marker: Literal["channel-reply-mutation-session/v1"]
    root_identity: ChannelReplyRootIdentity
    root: ChannelReplyDirectoryToken

    def verify(self) -> None: ...

    def open_directory(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        create_private: bool = False,
    ) -> ChannelReplyDirectoryToken: ...

    def inspect(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
    ) -> ChannelReplyEntryInfo | None: ...

    def scan(
        self,
        directory: ChannelReplyDirectoryToken,
        *,
        budget: DirectoryScanBudget,
        cursor: DirectoryScanCursor | None = None,
    ) -> DirectoryScanBatch: ...

    def read_bytes(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        max_bytes: int,
        expected: ChannelReplyObjectIdentity | None = None,
    ) -> bytes: ...

    def atomic_write_bytes(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        data: bytes,
        *,
        mode: WriteMode,
    ) -> AtomicWriteResult: ...

    def move_entry(
        self,
        source_parent: ChannelReplyDirectoryToken,
        source_name: str,
        destination_parent: ChannelReplyDirectoryToken,
        destination_name: str,
        *,
        expected_source: ChannelReplyObjectIdentity,
        disposition: MoveDisposition,
    ) -> MoveResult: ...

    def remove_owned_entry(
        self,
        parent: ChannelReplyDirectoryToken,
        name: str,
        *,
        budget: OwnedRemovalBudget,
        expected: ChannelReplyObjectIdentity | None = None,
    ) -> OwnedRemovalResult: ...

    def fsync_directory(self, directory: ChannelReplyDirectoryToken) -> None: ...


class ChannelReplyMutationLockPort(Protocol):
    """Serialize channel_reply store mutations across composed processes.

    Production adapters validate that ``state_dir`` already exists as a private
    non-symlink/non-reparse directory and must not create, chmod, truncate, or
    repair the root or a rejected lock leaf while acquiring the lock.
    """

    def exclusive(
        self,
        state_dir: Path,
        *,
        expected_root: ChannelReplyRootIdentity | None = None,
    ) -> AbstractContextManager[ChannelReplyMutationSession]: ...
