"""Shared test helpers for the Agent Presence Store Port.

Provides a deterministic in-memory ``FakeAgentPresenceStore`` that implements the
Core-owned ``lingtai.kernel.agent_presence.AgentPresenceStorePort`` without any
filesystem, plus a ``make_test_presence_store()`` factory the raw
``BaseAgent(...)`` construction tests use to inject a real (but directory-free)
presence store.

The fake models the same observable contract as the production POSIX adapter:
own-heartbeat publish/withdraw mutate an in-memory value (never a file), and
``observe_manifest`` / ``observe_heartbeat`` return typed observations. It is the
single authorized fake/factory helper for this Port — there is no second helper
name.
"""
from __future__ import annotations

from typing import Mapping

from lingtai.kernel.agent_presence import (
    AgentPresenceStorePort,
    HeartbeatObservation,
    ManifestObservation,
)


class FakeAgentPresenceStore(AgentPresenceStorePort):
    """In-memory presence store bound to one logical directory.

    No path, file, JSON parsing, or clock — pure Python — so it proves the Port
    is substitutable and that consumers depend only on the four operations.
    Own-heartbeat publication records the last published wall-clock value;
    withdrawal clears it. Manifest evidence is configurable so a construction
    test can present an agent/human/absent/malformed identity as needed.
    """

    def __init__(
        self,
        *,
        manifest: ManifestObservation | None = None,
        heartbeat: HeartbeatObservation | None = None,
    ) -> None:
        # Default: no manifest observed (bare construction tests do not depend on
        # a foreign manifest). Callers that exercise policy pass an explicit one.
        self._manifest = manifest if manifest is not None else ManifestObservation.absent()
        # Explicit initial heartbeat wins; otherwise start absent until the
        # agent publishes one. ``_published`` tracks own publications so
        # withdrawal is observable and idempotent.
        self._heartbeat = heartbeat if heartbeat is not None else HeartbeatObservation.absent()
        self.published_values: list[float] = []
        self.withdraw_calls: int = 0

    # --- observation ---

    def observe_manifest(self) -> ManifestObservation:
        return self._manifest

    def observe_heartbeat(self) -> HeartbeatObservation:
        return self._heartbeat

    # --- own presence ---

    def publish_heartbeat(self, wall_seconds: float) -> None:
        self.published_values.append(wall_seconds)
        self._heartbeat = HeartbeatObservation.present(wall_seconds)

    def withdraw_heartbeat(self) -> None:
        self.withdraw_calls += 1
        self._heartbeat = HeartbeatObservation.absent()

    # --- test configuration helpers ---

    def set_manifest(self, manifest: ManifestObservation) -> None:
        """Replace the manifest evidence this fake returns."""
        self._manifest = manifest

    def set_heartbeat(self, heartbeat: HeartbeatObservation) -> None:
        """Replace the heartbeat evidence this fake returns."""
        self._heartbeat = heartbeat


def make_test_presence_store(
    *,
    manifest: ManifestObservation | None = None,
    heartbeat: HeartbeatObservation | None = None,
) -> FakeAgentPresenceStore:
    """Return a fresh deterministic presence store for injecting into ``BaseAgent``.

    The common construction case needs no configuration — the fake accepts the
    heartbeat publish/withdraw the lifecycle performs without any filesystem.
    Tests exercising foreign-observation policy pass an explicit ``manifest`` /
    ``heartbeat`` observation.
    """
    return FakeAgentPresenceStore(manifest=manifest, heartbeat=heartbeat)


def valid_manifest(data: Mapping[str, object] | None = None) -> ManifestObservation:
    """Convenience: a VALID manifest observation carrying *data* (default empty)."""
    return ManifestObservation.valid(dict(data or {}))


class RecordingAgentPresenceStore(AgentPresenceStorePort):
    """A Port that records publish values and counts withdraw calls.

    Behaviorally pins the own-presence contract of consumers (the heartbeat tick
    publishes ``str(wall_seconds)``-equivalent floats; teardown withdraws exactly
    once) without searching source text. Observation returns absent by default.
    """

    def __init__(self) -> None:
        self.publishes: list[float] = []
        self.withdraws: int = 0

    def observe_manifest(self) -> ManifestObservation:
        return ManifestObservation.absent()

    def observe_heartbeat(self) -> HeartbeatObservation:
        return HeartbeatObservation.absent()

    def publish_heartbeat(self, wall_seconds: float) -> None:
        self.publishes.append(wall_seconds)

    def withdraw_heartbeat(self) -> None:
        self.withdraws += 1
