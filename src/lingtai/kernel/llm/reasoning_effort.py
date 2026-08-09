"""Neutral process-local reasoning-effort capability, snapshot, and controller.

Core-owned, technology-ignorant vocabulary for "the agent may change its own
reasoning effort at runtime". Nothing here knows Codex, OpenAI, a model name, an
endpoint, or a wire field: an adapter that actually owns such a route publishes a
:class:`ReasoningEffortCapability` describing it (exact ordered values, the
provider catalog default it observed, the construction baseline the integration
deliberately applies, and an opaque route label + fingerprint), and the kernel
stores, validates, and revisions the process-local override against it.

Ownership: the controller belongs to the agent / ``SessionManager``, never to a
cached adapter, a module global, or a replaceable provider session object. It
therefore survives an in-process session rebuild and intentionally does NOT
survive process refresh / restart / molt / suspend — durability is a separate,
later concern.

Fail-closed by default: a bare controller is unavailable and get-only, an
unknown value never mutates state, and a capability whose fingerprint changed
(route drift) drops the override rather than silently re-applying it to a
different route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReasoningEffortCapability:
    """What an adapter promises about one real, currently active route.

    ``route`` and ``fingerprint`` are opaque adapter-owned identity tokens. Core
    stores and compares them; it never parses or branches on their content.

    ``provider_default`` (what the provider's own catalog says it would do) and
    ``baseline`` are two DIFFERENT facts, deliberately kept apart. ``baseline``
    is specifically the value the adapter session ACTUALLY constructed with —
    not a fixed policy constant — so a controller bound to this capability
    reproduces the unchanged wire and can never move a request by itself. An
    adapter that also has a fixed omitted/default policy reports that on its own
    descriptor, not here.
    """

    available: bool = False
    route: str | None = None
    values: tuple[str, ...] = ()
    provider_default: str | None = None
    baseline: str | None = None
    settable: bool = False
    fingerprint: str | None = None
    evidence_revision: int = 0
    reason: str | None = "unavailable"

    def supports(self, value: Any) -> bool:
        return isinstance(value, str) and value in self.values


UNAVAILABLE_CAPABILITY = ReasoningEffortCapability()


@dataclass(frozen=True)
class ReasoningEffortSnapshot:
    """One immutable decision captured at the start of a single dispatch.

    A dispatch captures this exactly once and reuses it for every retry and
    fallback of that same dispatch, so a concurrent set/clear can only affect the
    NEXT not-yet-captured dispatch.
    """

    effective: str
    source: str  # "baseline" | "override"
    revision: int
    fingerprint: str | None
    baseline: str | None
    requested: str | None = None


@dataclass(frozen=True)
class ReasoningEffortResult:
    """Outcome of a self-facing ``set``/``clear`` call."""

    ok: bool
    reason: str
    status: dict[str, Any] = field(default_factory=dict)


class ReasoningEffortController:
    """Process-local, revisioned reasoning-effort state for one agent.

    State is intentionally tiny and neutral: the bound capability (which carries
    the descriptor fingerprint and the integration baseline), an optional
    override, and a monotonic revision.
    """

    def __init__(self) -> None:
        self._capability = UNAVAILABLE_CAPABILITY
        self._override: str | None = None
        self._revision = 0

    # -- capability binding -------------------------------------------------

    @property
    def capability(self) -> ReasoningEffortCapability:
        return self._capability

    def bind_capability(
        self, capability: ReasoningEffortCapability | None
    ) -> bool:
        """Bind (or rebind) the active-route capability.

        Returns True when an existing override survived. Rebinding the SAME
        route (identical fingerprint) keeps the override — that is what makes an
        in-process session rebuild transparent. A different or missing
        fingerprint is route drift: the override is dropped and the revision
        advances, so no request can silently inherit an override that was chosen
        for another route.
        """
        new_capability = capability or UNAVAILABLE_CAPABILITY
        previous = self._capability
        self._capability = new_capability
        if self._override is None:
            return False
        same_route = (
            new_capability.available
            and new_capability.fingerprint is not None
            and new_capability.fingerprint == previous.fingerprint
            and new_capability.supports(self._override)
        )
        if same_route:
            return True
        self._override = None
        self._revision += 1
        return False

    # -- self-facing get / set / clear --------------------------------------

    def status(self) -> dict[str, Any]:
        """Truthful query status. Safe to log: no secrets, bodies, or prompts."""
        capability = self._capability
        return {
            "available": bool(capability.available),
            "settable": bool(capability.settable),
            "route": capability.route,
            "values": list(capability.values),
            "provider_default": capability.provider_default,
            "baseline": capability.baseline,
            "override": self._override,
            "effective": self._effective(),
            "fingerprint": capability.fingerprint,
            "evidence_revision": capability.evidence_revision,
            "revision": self._revision,
            "reason": capability.reason,
        }

    def set(self, value: Any) -> ReasoningEffortResult:
        """Request an override. Invalid requests never mutate state."""
        capability = self._capability
        if not capability.available:
            return ReasoningEffortResult(False, "unavailable", self.status())
        if not capability.settable:
            return ReasoningEffortResult(False, "not_settable", self.status())
        if not capability.supports(value):
            return ReasoningEffortResult(False, "unsupported_value", self.status())
        if self._override != value:
            self._override = value
            self._revision += 1
        return ReasoningEffortResult(True, "ok", self.status())

    def clear(self) -> ReasoningEffortResult:
        """Remove the override and restore the integration baseline.

        This is NOT a provider value: it restores whatever baseline the active
        descriptor owns.
        """
        if not self._capability.available:
            return ReasoningEffortResult(False, "unavailable", self.status())
        if self._override is None:
            return ReasoningEffortResult(True, "no_override", self.status())
        self._override = None
        self._revision += 1
        return ReasoningEffortResult(True, "ok", self.status())

    # -- dispatch seam ------------------------------------------------------

    def snapshot(self) -> ReasoningEffortSnapshot | None:
        """The immutable value a dispatch should apply, or ``None`` when the
        route is unavailable (fail closed — the adapter then leaves its wire
        untouched)."""
        capability = self._capability
        effective = self._effective()
        if not capability.available or effective is None:
            return None
        return ReasoningEffortSnapshot(
            effective=effective,
            source="override" if self._override is not None else "baseline",
            revision=self._revision,
            fingerprint=capability.fingerprint,
            baseline=capability.baseline,
            requested=self._override,
        )

    # -- internals ----------------------------------------------------------

    def _effective(self) -> str | None:
        if not self._capability.available:
            return None
        if self._override is not None:
            return self._override
        return self._capability.baseline
