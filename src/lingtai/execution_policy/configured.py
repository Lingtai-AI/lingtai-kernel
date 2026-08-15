"""First-party JSON-file execution-policy adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from lingtai.kernel.execution_policy import (
    API_VERSION,
    ExecutionDecision,
    ExecutionPolicyError,
    ExecutionPolicyUnavailableError,
    ExecutionRequest,
    HealthSnapshot,
    RouteHealth,
    require_api_version,
)
from lingtai.kernel.presets import _preset_ref_in


class ExecutionPolicyConfigurationError(ExecutionPolicyError):
    """Raised when external execution-policy JSON is invalid."""


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    route_id: str
    preset: str


@dataclass(frozen=True, slots=True)
class WorkloadRoute:
    workload: str
    candidates: tuple[RouteCandidate, ...]


@dataclass(frozen=True, slots=True)
class ConfiguredExecutionPolicy:
    """Select the first allowed, available candidate for an exact workload."""

    routes: tuple[WorkloadRoute, ...]
    health_path: Path
    working_dir: Path
    api_version: int = API_VERSION

    def decide(self, request: ExecutionRequest) -> ExecutionDecision:
        if request.preset is not None:
            return ExecutionDecision.pass_through(request)

        route = next(
            (item for item in self.routes if item.workload == request.workload),
            None,
        )
        if route is None:
            raise ExecutionPolicyUnavailableError(
                f"no execution route configured for workload {request.workload!r}"
            )

        health = load_health_snapshot(self.health_path)
        for candidate in route.candidates:
            if _preset_ref_in(
                candidate.preset,
                list(request.allowed_preset_refs),
                working_dir=self.working_dir,
            ) and health.is_available(candidate.route_id):
                return ExecutionDecision(
                    preset=candidate.preset,
                    backend=request.backend,
                    route_id=candidate.route_id,
                )

        raise ExecutionPolicyUnavailableError(
            f"no allowed healthy execution route for workload {request.workload!r}"
        )


def _read_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError) as exc:
        raise ExecutionPolicyConfigurationError(
            f"cannot read valid {label} JSON from {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ExecutionPolicyConfigurationError(
            f"{label} JSON at {path} must contain an object"
        )
    return value


def _require_exact_keys(
    value: Mapping[str, object], *, expected: set[str], label: str
) -> None:
    actual = set(value)
    if actual != expected:
        raise ExecutionPolicyConfigurationError(
            f"{label} must contain exactly {sorted(expected)!r}; got {sorted(actual)!r}"
        )


def _require_non_empty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExecutionPolicyConfigurationError(f"{label} must be a non-empty string")
    return value


def load_policy_config(path: Path) -> tuple[WorkloadRoute, ...]:
    """Read and validate the configured policy at its file boundary."""

    data = _read_json_object(path, label="execution-policy config")
    _require_exact_keys(
        data,
        expected={"api_version", "workloads"},
        label="execution-policy config",
    )
    require_api_version(data["api_version"], source="execution-policy config")

    workloads = data["workloads"]
    if not isinstance(workloads, dict):
        raise ExecutionPolicyConfigurationError(
            "execution-policy config workloads must be an object"
        )

    routes: list[WorkloadRoute] = []
    for raw_workload, raw_candidates in workloads.items():
        workload = _require_non_empty_string(
            raw_workload, label="execution-policy workload"
        )
        if not isinstance(raw_candidates, list) or not raw_candidates:
            raise ExecutionPolicyConfigurationError(
                f"execution-policy workload {workload!r} must have candidates"
            )

        candidates: list[RouteCandidate] = []
        for index, raw_candidate in enumerate(raw_candidates):
            label = f"execution-policy workload {workload!r} candidate {index}"
            if not isinstance(raw_candidate, dict):
                raise ExecutionPolicyConfigurationError(f"{label} must be an object")
            _require_exact_keys(
                raw_candidate,
                expected={"route_id", "preset"},
                label=label,
            )
            candidates.append(
                RouteCandidate(
                    route_id=_require_non_empty_string(
                        raw_candidate["route_id"], label=f"{label} route_id"
                    ),
                    preset=_require_non_empty_string(
                        raw_candidate["preset"], label=f"{label} preset"
                    ),
                )
            )
        routes.append(WorkloadRoute(workload=workload, candidates=tuple(candidates)))
    return tuple(routes)


def load_health_snapshot(path: Path) -> HealthSnapshot:
    """Read and validate current route availability at its file boundary."""

    data = _read_json_object(path, label="execution-policy health")
    _require_exact_keys(
        data,
        expected={"api_version", "routes"},
        label="execution-policy health",
    )
    require_api_version(data["api_version"], source="execution-policy health")

    raw_routes = data["routes"]
    if not isinstance(raw_routes, dict):
        raise ExecutionPolicyConfigurationError(
            "execution-policy health routes must be an object"
        )

    routes: list[RouteHealth] = []
    for raw_route_id, raw_health in raw_routes.items():
        route_id = _require_non_empty_string(
            raw_route_id, label="execution-policy health route_id"
        )
        label = f"execution-policy health route {route_id!r}"
        if not isinstance(raw_health, dict):
            raise ExecutionPolicyConfigurationError(f"{label} must be an object")
        _require_exact_keys(raw_health, expected={"available"}, label=label)
        available = raw_health["available"]
        if not isinstance(available, bool):
            raise ExecutionPolicyConfigurationError(
                f"{label} available must be a boolean"
            )
        routes.append(RouteHealth(route_id=route_id, available=available))
    return HealthSnapshot(routes=tuple(routes))


def _resolve_declared_path(working_dir: Path, value: object, *, field: str) -> Path:
    relative = Path(_require_non_empty_string(value, label=f"execution-policy {field}"))
    if relative.is_absolute() or ".." in relative.parts:
        raise ExecutionPolicyConfigurationError(
            f"execution-policy {field} must be a safe path relative to working_dir"
        )
    return (working_dir / relative).resolve()


def configured_policy_factory(
    declaration: Mapping[str, object], working_dir: Path
) -> ConfiguredExecutionPolicy:
    """Construct the configured adapter from its validated declaration."""

    _require_exact_keys(
        declaration,
        expected={"api_version", "adapter", "config", "health"},
        label="execution-policy declaration",
    )
    require_api_version(
        declaration["api_version"], source="execution-policy declaration"
    )
    if declaration["adapter"] != "configured":
        raise ExecutionPolicyConfigurationError(
            "configured execution-policy factory requires adapter 'configured'"
        )
    config_path = _resolve_declared_path(
        working_dir, declaration["config"], field="config"
    )
    health_path = _resolve_declared_path(
        working_dir, declaration["health"], field="health"
    )
    routes = load_policy_config(config_path)
    load_health_snapshot(health_path)
    return ConfiguredExecutionPolicy(
        routes=routes,
        health_path=health_path,
        working_dir=working_dir,
    )


__all__ = [
    "ConfiguredExecutionPolicy",
    "ExecutionPolicyConfigurationError",
    "RouteCandidate",
    "WorkloadRoute",
    "configured_policy_factory",
    "load_health_snapshot",
    "load_policy_config",
]
