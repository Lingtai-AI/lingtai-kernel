"""Core-owned Agent Presence Store Port, typed observations, and liveness policy.

This is the kernel's presence boundary: whether a working directory holds an
agent, whether that agent is a human, and whether it is currently alive by a
fresh heartbeat. Core owns the freshness threshold, the human-always-alive rule,
and the absent/malformed/valid distinctions; a construction-bound Port expresses
the four capability operations (observe manifest, observe heartbeat, publish own
heartbeat, withdraw own heartbeat) in technology-neutral terms.

No `Path`, file, JSON, symlink, POSIX name, `time`, threading, or temp-name
vocabulary appears in the Port signatures or the domain values below — those
concrete mechanisms live only in the outside adapter (see
``src/lingtai/adapters/posix/agent_presence.py``). See the sibling CONTRACT.md
for normative semantics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class ManifestKind(Enum):
    """Tri-state evidence for the agent manifest observation.

    ``ABSENT`` — no manifest present at the observed address.
    ``MALFORMED`` — a manifest is present but its content does not parse.
    ``VALID`` — a manifest is present and parses into a mapping.

    The absent/malformed split is load-bearing: ``is_agent`` treats *any*
    present manifest (malformed included) as an agent, so the observation must
    distinguish "present but unparseable" from "absent".
    """

    ABSENT = "absent"
    MALFORMED = "malformed"
    VALID = "valid"


class HeartbeatKind(Enum):
    """Tri-state evidence for the heartbeat observation.

    ``ABSENT`` — no heartbeat present at the observed address.
    ``MALFORMED`` — a heartbeat is present but its content is not a usable
    timestamp (unparseable or unreadable).
    ``PRESENT`` — a heartbeat is present and carries a parsed wall-clock value.
    """

    ABSENT = "absent"
    MALFORMED = "malformed"
    PRESENT = "present"


@dataclass(frozen=True)
class ManifestObservation:
    """Typed, technology-neutral evidence about an agent manifest.

    ``kind`` is the tri-state above. For ``VALID`` observations:

    * ``admin_absent_or_null`` preserves the current human-policy fact —
      ``True`` when the parsed manifest has no ``admin`` key or its value is
      null (JSON ``null`` → Python ``None``), ``False`` when ``admin`` is
      present and non-null.
    * ``data`` is the parsed mapping, exposed for identity consumers that read
      other manifest fields. It is empty for ``ABSENT``/``MALFORMED``.

    The value carries no path, file, JSON, or symlink vocabulary; the adapter
    maps concrete storage into these fields.
    """

    kind: ManifestKind
    admin_absent_or_null: bool = False
    data: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def absent(cls) -> "ManifestObservation":
        """No manifest at the observed address."""
        return cls(kind=ManifestKind.ABSENT)

    @classmethod
    def malformed(cls) -> "ManifestObservation":
        """A manifest is present on disk but does not parse."""
        return cls(kind=ManifestKind.MALFORMED)

    @classmethod
    def valid(cls, data: Mapping[str, object]) -> "ManifestObservation":
        """A manifest is present and parses into *data* (a mapping).

        ``admin_absent_or_null`` is derived from *data* here so every producer
        reports the human-policy fact identically: it is ``True`` exactly when
        ``data`` has no ``admin`` key or maps it to ``None``.
        """
        admin_absent_or_null = data.get("admin") is None
        return cls(
            kind=ManifestKind.VALID,
            admin_absent_or_null=admin_absent_or_null,
            data=data,
        )


@dataclass(frozen=True)
class HeartbeatObservation:
    """Typed, technology-neutral evidence about an agent heartbeat.

    ``kind`` is the tri-state above. ``wall_seconds`` carries the parsed
    wall-clock timestamp for ``PRESENT`` observations and is ``0.0`` otherwise;
    consult ``kind`` (or ``is_present``) before reading it. The stored value is
    the raw parsed float — future values, ``NaN``, and ``±inf`` are preserved
    verbatim so Core policy comparison behavior is unchanged.
    """

    kind: HeartbeatKind
    wall_seconds: float = 0.0

    @property
    def is_present(self) -> bool:
        """True when a usable heartbeat timestamp was observed."""
        return self.kind is HeartbeatKind.PRESENT

    @classmethod
    def absent(cls) -> "HeartbeatObservation":
        """No heartbeat at the observed address."""
        return cls(kind=HeartbeatKind.ABSENT)

    @classmethod
    def malformed(cls) -> "HeartbeatObservation":
        """A heartbeat is present but not a usable timestamp."""
        return cls(kind=HeartbeatKind.MALFORMED)

    @classmethod
    def present(cls, wall_seconds: float) -> "HeartbeatObservation":
        """A heartbeat is present carrying the parsed *wall_seconds* value."""
        return cls(kind=HeartbeatKind.PRESENT, wall_seconds=wall_seconds)


# Default non-human freshness window (seconds). Matches the historical
# ``handshake.is_alive`` handshake threshold; it is Core policy, never an
# adapter concern.
DEFAULT_LIVENESS_THRESHOLD_SECONDS: float = 2.0


class AgentPresenceStorePort(ABC):
    """Construction-bound presence boundary for one observed working directory.

    An instance is bound at construction to a single target address (the outer
    adapter is built per directory). The Port therefore takes no address
    argument: foreign-address observation constructs a target-bound adapter per
    address, and own-presence publish/withdraw acts on the bound directory.

    Exactly four capability operations, all technology-neutral:

    * ``observe_manifest`` / ``observe_heartbeat`` — read-only evidence for
      Core presence/liveness policy.
    * ``publish_heartbeat`` / ``withdraw_heartbeat`` — the agent's own
      liveness publication and withdrawal.

    There is no unlocked/no-op form, Path-or-Port overload, address argument, or
    direct-heartbeat dual route; a required constructor dependency injects it.
    """

    @abstractmethod
    def observe_manifest(self) -> ManifestObservation:
        """Return typed manifest evidence for the bound address.

        Distinguishes absent, malformed (present but unparseable), and valid.
        For valid manifests it preserves whether ``admin`` is missing-or-null.
        """
        ...

    @abstractmethod
    def observe_heartbeat(self) -> HeartbeatObservation:
        """Return typed heartbeat evidence for the bound address.

        Distinguishes absent, malformed (present but not a usable timestamp),
        and present, carrying the parsed wall-clock value when present. Future,
        ``NaN``, and ``±inf`` values are preserved without normalization.
        """
        ...

    @abstractmethod
    def publish_heartbeat(self, wall_seconds: float) -> None:
        """Publish the agent's own liveness at *wall_seconds* (wall clock).

        Best-effort: a publication failure is swallowed by the adapter, matching
        the historical heartbeat writer. The caller supplies the wall-clock
        value; the Port does not read a clock.
        """
        ...

    @abstractmethod
    def withdraw_heartbeat(self) -> None:
        """Withdraw the agent's own liveness publication.

        Best-effort and idempotent: withdrawing when nothing is published is a
        no-op, matching the historical unlink-on-stop mechanism.
        """
        ...


# ---------------------------------------------------------------------------
# Pure Core policy — importable by Core, never touches storage.
# ---------------------------------------------------------------------------


def is_agent(manifest_obs: ManifestObservation) -> bool:
    """Return whether the observed address holds an agent.

    True iff a manifest is present — *including a malformed one*. This
    preserves the historical ``handshake.is_agent`` manifest-presence
    semantics independently of parse validity: a malformed manifest still
    counts as an agent.
    """
    return manifest_obs.kind is not ManifestKind.ABSENT


def is_human(manifest_obs: ManifestObservation) -> bool:
    """Return whether the observed agent is a human.

    True iff the manifest is valid *and* its ``admin`` key is missing or null.
    Absent or malformed manifests are not human — matching the historical
    ``except (FileNotFoundError, json.JSONDecodeError): return False``.
    """
    return (
        manifest_obs.kind is ManifestKind.VALID
        and manifest_obs.admin_absent_or_null
    )


def is_alive(
    heartbeat_obs: HeartbeatObservation,
    manifest_obs: ManifestObservation,
    wall_now: float,
    threshold: float = DEFAULT_LIVENESS_THRESHOLD_SECONDS,
) -> bool:
    """Return whether the observed agent has a fresh heartbeat.

    Policy, preserved exactly from ``handshake.is_alive``:

    * A human agent (valid manifest, ``admin`` missing/null) is always alive —
      humans do not write heartbeats.
    * Otherwise the agent is alive iff a heartbeat is present and strictly
      fresher than *threshold*: ``wall_now - wall_seconds < threshold``.
    * Absent or malformed heartbeats are dead.

    The freshness comparison is the raw float subtraction: ``NaN`` and ``±inf``
    timestamps, and future timestamps, flow through unchanged (no
    normalization or rejection), so a heartbeat written slightly in the future
    still counts as alive exactly as before.
    """
    if is_human(manifest_obs):
        return True
    if not heartbeat_obs.is_present:
        return False
    return wall_now - heartbeat_obs.wall_seconds < threshold


def observe_alive(
    store: AgentPresenceStorePort,
    wall_now: float,
    threshold: float = DEFAULT_LIVENESS_THRESHOLD_SECONDS,
) -> bool:
    """Observe the bound address and apply liveness policy in legacy order.

    Manifest evidence is observed first. A valid human manifest returns ``True``
    without observing heartbeat at all, matching the historical short-circuit;
    non-human addresses then observe heartbeat and delegate to the pure
    :func:`is_alive` policy. The caller supplies wall time, so this use case owns
    no clock or storage mechanism.
    """
    manifest_obs = store.observe_manifest()
    if is_human(manifest_obs):
        return True
    return is_alive(
        store.observe_heartbeat(),
        manifest_obs,
        wall_now,
        threshold,
    )


__all__ = [
    "AgentPresenceStorePort",
    "ManifestObservation",
    "HeartbeatObservation",
    "ManifestKind",
    "HeartbeatKind",
    "DEFAULT_LIVENESS_THRESHOLD_SECONDS",
    "is_agent",
    "is_human",
    "is_alive",
    "observe_alive",
]
