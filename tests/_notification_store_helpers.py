"""Notification Store test doubles and explicit test composition helpers.

The fake implements the final eight-family Port plus its composed mutation-lock
seam exactly. POSIX helpers are intentionally test-only: they keep old
filesystem-oriented assertions useful without restoring removed Path-based
production APIs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.notification_store import (
    AllowPredicate,
    CompareUpdateResult,
    ExpectedVersion,
    NotificationStorePort,
    PureAckMutator,
    PureCoreMutator,
    PureHookManifestMutator,
    UNCONDITIONAL,
    UpdateAckRefsResult,
    UpdateHookManifestsResult,
)
from lingtai.kernel.notifications import is_channel_allowed


def _fake_raw(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _fake_version(channel: str, payload: dict) -> list:
    raw = _fake_raw(payload)
    return [f"{channel}.json", len(raw), hashlib.sha256(raw).hexdigest()]


class FakeNotificationStore(NotificationStorePort):
    """Thread-safe in-memory implementation of the final Port contract."""

    def __init__(self):
        self._channels: dict[str, dict] = {}
        self._ack_refs: set[str] = set()
        self._ack_present = False
        self._hook_manifests: list[dict] = []
        self._hook_present = False
        self._lock = threading.RLock()
        self.channel_mutations: list[str] = []
        self.ack_mutations = 0
        self.hook_mutations = 0

    @property
    def mutation_lock(self):
        return self

    @contextmanager
    def exclusive(self, _notification_dir: Path, _scope: str):
        """Real in-process exclusion for the fake Store's composed lock Port."""
        with self._lock:
            yield

    def snapshot(self, allow_channel: AllowPredicate) -> dict[str, object]:
        with self._lock:
            return {
                channel: copy.deepcopy(payload)
                for channel, payload in self._channels.items()
                if allow_channel(channel)
            }

    def fingerprint(
        self, allow_channel: AllowPredicate
    ) -> tuple[tuple[str, int, str], ...]:
        with self._lock:
            return tuple(
                sorted(
                    tuple(_fake_version(channel, payload))
                    for channel, payload in self._channels.items()
                    if allow_channel(channel)
                )
            )

    def publish(self, channel: str, payload: dict) -> None:
        with self._lock:
            self._channels[channel] = copy.deepcopy(payload)

    def clear(self, channel: str) -> bool:
        with self._lock:
            if channel not in self._channels:
                return False
            self._channels.pop(channel)
            return True

    def compare_update_channel(
        self,
        channel: str,
        expected_version: ExpectedVersion,
        pure_core_mutator: PureCoreMutator,
        *,
        owner: str | None = None,
    ) -> CompareUpdateResult:
        if owner is not None and channel != "daemon":
            raise ValueError("notification mutation owner is only valid for daemon")
        with self._lock:
            present = channel in self._channels
            current_payload = copy.deepcopy(self._channels.get(channel, {}))
            current_version = (
                _fake_version(channel, self._channels[channel]) if present else None
            )
            if expected_version is not UNCONDITIONAL:
                expected = list(expected_version) if expected_version is not None else None
                if expected != current_version:
                    return CompareUpdateResult(
                        applied=False,
                        conflict=True,
                        changed=False,
                        cleared=False,
                        value=None,
                        current_version=copy.deepcopy(current_version),
                        previous_version=copy.deepcopy(current_version),
                    )

            new_payload, requested_change, value = pure_core_mutator(current_payload)
            self.channel_mutations.append(channel)
            if not requested_change:
                return CompareUpdateResult(
                    applied=True,
                    conflict=False,
                    changed=False,
                    cleared=False,
                    value=value,
                    current_version=copy.deepcopy(current_version),
                    previous_version=copy.deepcopy(current_version),
                )

            previous_version = copy.deepcopy(current_version)
            if new_payload is None:
                did_clear = channel in self._channels
                self._channels.pop(channel, None)
                return CompareUpdateResult(
                    applied=True,
                    conflict=False,
                    changed=did_clear,
                    cleared=did_clear,
                    value=value,
                    current_version=None,
                    previous_version=previous_version,
                )

            self._channels[channel] = copy.deepcopy(new_payload)
            return CompareUpdateResult(
                applied=True,
                conflict=False,
                changed=True,
                cleared=False,
                value=value,
                current_version=_fake_version(channel, new_payload),
                previous_version=previous_version,
            )

    def load_ack_refs(self) -> set[str]:
        with self._lock:
            return set(self._ack_refs)

    def update_ack_refs(
        self, pure_core_set_mutator: PureAckMutator
    ) -> UpdateAckRefsResult:
        with self._lock:
            refs, requested_change, value = pure_core_set_mutator(set(self._ack_refs))
            self.ack_mutations += 1
            if not requested_change:
                return UpdateAckRefsResult(changed=False, value=value)
            if not refs:
                changed = self._ack_present
                self._ack_refs = set()
                self._ack_present = False
                return UpdateAckRefsResult(changed=changed, value=value)
            self._ack_refs = set(refs)
            self._ack_present = True
            return UpdateAckRefsResult(changed=True, value=value)

    def load_hook_manifests(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._hook_manifests)

    def stat_hook_registry(self) -> tuple[int, int] | None:
        with self._lock:
            if not self._hook_present:
                return None
            return (self.hook_mutations, len(self._hook_manifests))

    def update_hook_manifests(
        self, pure_core_manifest_mutator: PureHookManifestMutator
    ) -> UpdateHookManifestsResult:
        with self._lock:
            manifests, requested_change, value = pure_core_manifest_mutator(
                copy.deepcopy(self._hook_manifests)
            )
            self.hook_mutations += 1
            if not requested_change:
                return UpdateHookManifestsResult(changed=False, value=value)
            if not manifests:
                changed = self._hook_present
                self._hook_manifests = []
                self._hook_present = False
                return UpdateHookManifestsResult(changed=changed, value=value)
            self._hook_manifests = copy.deepcopy(manifests)
            self._hook_present = True
            return UpdateHookManifestsResult(changed=True, value=value)


def notification_store_for(target: object) -> NotificationStorePort:
    """Return an explicitly composed Store for a test agent or workdir."""
    store = getattr(target, "_notification_store", None)
    if isinstance(store, NotificationStorePort):
        return store
    return PosixNotificationStoreAdapter(Path(target))


def store_agent_for(target: object) -> SimpleNamespace:
    """Build the smallest explicit producer agent for strict Core APIs."""
    return SimpleNamespace(_notification_store=notification_store_for(target))


def snapshot_notifications(target: object) -> dict[str, object]:
    return notification_store_for(target).snapshot(is_channel_allowed)


def fingerprint_notifications(target: object) -> tuple[tuple[str, int, str], ...]:
    return notification_store_for(target).fingerprint(is_channel_allowed)


def publish_test_payload(target: object, channel: str, payload: dict) -> None:
    """Publish a raw test payload through the Port (not a production shim)."""
    notification_store_for(target).publish(channel, payload)


def clear_test_payload(target: object, channel: str) -> bool:
    """Clear a raw test payload through the Port (not a production shim)."""
    return notification_store_for(target).clear(channel)


def load_ack_refs_for_test(target: object) -> set[str]:
    return notification_store_for(target).load_ack_refs()


def replace_ack_refs_for_test(target: object, refs: set[str]) -> None:
    """Seed exact ack state through atomic family seven for a test."""
    notification_store_for(target).update_ack_refs(
        lambda current: (set(refs), set(refs) != current, None)
    )


def load_hook_manifests_for_test(target: object) -> list[dict]:
    """Read the hook-manifest registry through family eight for a test."""
    return notification_store_for(target).load_hook_manifests()


def replace_hook_manifests_for_test(target: object, manifests: list[dict]) -> None:
    """Seed exact hook-manifest state through atomic family eight for a test."""
    notification_store_for(target).update_hook_manifests(
        lambda current: (list(manifests), list(manifests) != current, None)
    )
