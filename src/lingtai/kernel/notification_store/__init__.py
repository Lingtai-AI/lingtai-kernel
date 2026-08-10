"""Core-owned Notification Store Port for durable channel mirrors.

See the sibling CONTRACT.md for normative semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable, Final, NamedTuple

# A predicate a caller provides to limit snapshot / fingerprint to only
# accepted channel names.  The store enumerates directory entries and
# calls the predicate with each channel (stem) name; Core supplies the
# live allow-predicate so the adapter never imports channel policy.
AllowPredicate = Callable[[str], bool]

# A pure Core mutation function.  Receives the current payload (dict, or
# {} when absent / malformed) and returns
# ``(new_payload, new_changed, policy_value)`` where:
#
# * ``new_payload`` may be the same dict, a mutated copy, ``None``
#   (meaning "clear this channel"), or ``{}`` (meaning "publish empty but
#   keep the file").
# * ``new_changed`` is a bool that may equal False when the mutator
#   decided no change is needed.
# * ``policy_value`` is an opaque, immutable policy value carried through
#   to ``CompareUpdateResult.value``.  Callers that have no policy to
#   report pass ``None``.
PureCoreMutator = Callable[[dict], tuple[dict | None, bool, object]]

# Ack policy has the same pure shape, specialized to the persisted ref set.
# The adapter passes a copy and commits the returned set under one lock.
PureAckMutator = Callable[[set[str]], tuple[set[str], bool, object]]

# Hook-manifest policy has the same pure shape, specialized to the persisted
# manifest list. The adapter passes a copy and commits the returned list under
# one lock (empty list clears the registry file).
PureHookManifestMutator = Callable[
    [list[dict]], tuple[list[dict], bool, object]
]

# Store-owned non-channel filenames under .notification/. These are registry
# / acknowledgement files, never channels; Core validation rejects them as
# hook channels so a registered hook can never publish over or clear them.
# Adapters MUST keep this set in sync with their own skip lists.
STORE_RESERVED_NON_CHANNEL_STEMS: frozenset[str] = frozenset(
    {"hooks", "large_result_acks"}
)

# Expected version: UNCONDITIONAL, None for expected absence, or a
# fingerprint tuple for one delivered version.


class _UnconditionalSentinel(Enum):
    """Singleton sentinel for unconditional version checks.

    Compared by identity (``is``), not by value.  Not serializable;
    exists only as a typed module-level constant so that
    ``ExpectedVersion`` excludes arbitrary strings.
    """

    UNCONDITIONAL = "UNCONDITIONAL"


# Module-level singleton — callers import ``UNCONDITIONAL`` from this module.
UNCONDITIONAL: Final = _UnconditionalSentinel.UNCONDITIONAL

# Type alias: expected version can be a fingerprint tuple, None (expected
# absence), or the ``UNCONDITIONAL`` sentinel (Enum member).
ExpectedVersion = tuple | None | _UnconditionalSentinel


class UpdateAckRefsResult(NamedTuple):
    """Typed evidence from an atomic acknowledgement-set update."""

    changed: bool
    value: object


class UpdateHookManifestsResult(NamedTuple):
    """Typed evidence from an atomic hook-manifest-list update."""

    changed: bool
    value: object


class CompareUpdateResult(NamedTuple):
    """Applied/conflict outcome plus policy and version evidence."""

    applied: bool
    conflict: bool
    changed: bool
    cleared: bool
    value: object
    current_version: list | None
    previous_version: list | None


# Convenience constructors for the most common result shapes.

def _applied_result(
    *,
    changed: bool,
    cleared: bool,
    value: object,
    current_version: list | None,
    previous_version: list | None,
) -> CompareUpdateResult:
    return CompareUpdateResult(
        applied=True,
        conflict=False,
        changed=changed,
        cleared=cleared,
        value=value,
        current_version=current_version,
        previous_version=previous_version,
    )


def _conflict_result(
    *,
    expected_version: ExpectedVersion,
    current_version: list | None,
) -> CompareUpdateResult:
    return CompareUpdateResult(
        applied=False,
        conflict=True,
        changed=False,
        cleared=False,
        value=None,
        current_version=current_version,
        previous_version=current_version,
    )


class NotificationStorePort(ABC):
    """Eight-family persistence boundary owned by notification Core."""

    @abstractmethod
    def snapshot(self, allow_channel: AllowPredicate) -> dict[str, object]:
        """Return parsed current channel payloads for accepted channels.

        ``allow_channel`` is a live predicate supplied by Core.  Missing
        store directory → ``{}``.  Malformed / unreadable files are
        silently skipped, preserving characterized reader behavior.
        """
        ...

    @abstractmethod
    def fingerprint(
        self, allow_channel: AllowPredicate
    ) -> tuple[tuple[str, int, str], ...]:
        """Return sorted ``(name, size, sha256)`` entries for accepted files.

        ``name`` is the filename (e.g. ``"email.json"``), not the stem.
        Missing store directory → ``()``.  Byte content matters; mtime
        does not. The tuple shape preserves the delivered-version protocol.
        """
        ...

    @abstractmethod
    def publish(self, channel: str, payload: dict) -> None:
        """Atomically replace one channel mirror (tmp + rename).

        Core validates syntax / allowlist before calling.  Non-ENOENT
        write errors propagate to the caller.  Serialization is owned by
        the store instance (not caller-held locks).
        """
        ...

    @abstractmethod
    def clear(self, channel: str) -> bool:
        """Remove one channel file.  Return True if it existed.

        Absence is idempotent (returns False).  Other I/O errors
        (permissions, …) propagate — the adapter does not suppress them.
        Core's best-effort ``clear`` wrapper may intentionally ignore
        those errors where current behaviour does.
        Serialization is owned by the store instance.
        """
        ...

    @abstractmethod
    def compare_update_channel(
        self,
        channel: str,
        expected_version: ExpectedVersion,
        pure_core_mutator: PureCoreMutator,
    ) -> CompareUpdateResult:
        """Guard and atomically apply one pure current-payload mutation.

        ``UNCONDITIONAL`` always runs; ``None`` expects absence; a fingerprint
        tuple expects that delivered version. A conflict never calls the
        mutator. Otherwise its payload/change/value triple is committed and
        returned as typed operational and policy evidence.
        """
        ...

    @abstractmethod
    def load_ack_refs(self) -> set[str]:
        """Return persisted legacy large-result acknowledgement refs.

        Absent / malformed file → empty set.
        """
        ...

    @abstractmethod
    def update_ack_refs(
        self, pure_core_set_mutator: PureAckMutator
    ) -> UpdateAckRefsResult:
        """Atomically read, mutate, and persist the acknowledgement set.

        The adapter owns serialization across the complete read/mutate/write
        transaction and carries the pure Core mutator's policy value back in
        typed changed/value evidence. Empty sets use the legacy best-effort
        clear behavior; read failures remain legacy best-effort empty reads.
        """
        ...

    @abstractmethod
    def load_hook_manifests(self) -> list[dict]:
        """Return the persisted hook-manifest list.

        Absent or malformed registry file → empty list (legacy best effort).
        The file is ``.notification/hooks.json`` — a single non-channel
        registry, invisible to snapshot/fingerprint.
        """
        ...

    @abstractmethod
    def update_hook_manifests(
        self, pure_core_manifest_mutator: PureHookManifestMutator
    ) -> UpdateHookManifestsResult:
        """Atomically read, mutate, and persist the hook-manifest list.

        Mirrors the ack-refs transaction: the adapter owns serialization
        across the complete read/mutate/write cycle under the same in-process
        and cross-process Store locks. An empty returned list clears the
        registry file (best-effort on unlink, like ack refs).
        """
        ...

    @abstractmethod
    def stat_hook_registry(self) -> tuple[int, int] | None:
        """Return a cheap staleness fingerprint of the hook registry file.

        Returns ``(st_mtime_ns, st_size)`` when the registry file exists,
        ``None`` when absent. Core uses this to re-seed its in-memory hook
        mirror when another process (sibling CLI, Telegram server, hook
        installer) wrote ``hooks.json`` out-of-band, without re-reading the
        file on every sync tick.
        """
        ...
