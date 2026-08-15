"""Technology-neutral execution-policy values and Port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

API_VERSION = 1


class ExecutionPolicyError(RuntimeError):
    """Base error for execution-policy failures."""


class ExecutionPolicyCompatibilityError(ExecutionPolicyError):
    """Raised when an execution-policy boundary uses an unsupported API."""


class ExecutionPolicyUnavailableError(ExecutionPolicyError):
    """Raised when policy requires a route but none is eligible and healthy."""


def require_api_version(value: object, *, source: str) -> None:
    """Fail fast unless *value* names this Core API exactly."""

    if isinstance(value, bool) or value != API_VERSION:
        raise ExecutionPolicyCompatibilityError(
            f"{source} API version {value!r} is incompatible; expected {API_VERSION}"
        )


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Immutable context supplied for one execution decision."""

    workload: str
    preset: str | None
    backend: str | None
    parent_address: str
    parent_is_admin: bool
    allowed_preset_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    """Immutable route decision returned to the execution caller."""

    preset: str | None
    backend: str | None
    route_id: str | None = None

    @classmethod
    def pass_through(cls, request: ExecutionRequest) -> ExecutionDecision:
        return cls(preset=request.preset, backend=request.backend)


@dataclass(frozen=True, slots=True)
class RouteHealth:
    """Availability of one technology-neutral route at snapshot time."""

    route_id: str
    available: bool


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Immutable route availability observed at one file-boundary read."""

    routes: tuple[RouteHealth, ...]

    def is_available(self, route_id: str) -> bool:
        return any(
            route.route_id == route_id and route.available for route in self.routes
        )


@runtime_checkable
class ExecutionPolicyPort(Protocol):
    """Core-owned inbound decision boundary used by execution callers."""

    api_version: int

    def decide(self, request: ExecutionRequest) -> ExecutionDecision:
        """Return the route decision for *request* or fail loudly."""


class PassThroughExecutionPolicy:
    """Default policy that preserves the caller's execution selection."""

    api_version = API_VERSION

    def decide(self, request: ExecutionRequest) -> ExecutionDecision:
        return ExecutionDecision.pass_through(request)


__all__ = [
    "API_VERSION",
    "ExecutionDecision",
    "ExecutionPolicyCompatibilityError",
    "ExecutionPolicyError",
    "ExecutionPolicyPort",
    "ExecutionPolicyUnavailableError",
    "ExecutionRequest",
    "HealthSnapshot",
    "PassThroughExecutionPolicy",
    "RouteHealth",
    "require_api_version",
]
