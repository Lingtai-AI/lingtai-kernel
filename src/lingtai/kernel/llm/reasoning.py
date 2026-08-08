"""Provider-neutral reasoning construction contracts.

This module owns only the immutable construction shapes shared by the kernel and
provider adapters. Provider model tables, defaults, and wire field paths belong
to provider-local contracts.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ReasoningRouteKey:
    """The provider and wire pair used to select a reasoning contract."""

    provider: str
    wire: str


@dataclass(frozen=True)
class ReasoningRouteContext:
    """Exact route facts supplied to a selected provider contract."""

    key: ReasoningRouteKey
    endpoint: str | None
    model: str


class ReasoningWirePatch(Protocol):
    """Opaque provider-owned operation over a request mapping."""

    def apply(self, request: MutableMapping[str, Any]) -> None:
        """Apply this immutable patch to one newly constructed request."""


@dataclass(frozen=True)
class ReasoningConstructionResult:
    """Immutable, mechanically exact reasoning construction capture."""

    requested: str | None
    normalized: str | None
    actual: str | None
    source: str
    capability_source: str
    wire_patch: ReasoningWirePatch


class ReasoningContract(Protocol):
    """Provider-owned reasoning policy selected at a registered route."""

    def construct(
        self,
        route: ReasoningRouteContext,
        requested: object,
    ) -> ReasoningConstructionResult | None:
        """Construct an exact result, or return ``None`` for legacy fallthrough."""
