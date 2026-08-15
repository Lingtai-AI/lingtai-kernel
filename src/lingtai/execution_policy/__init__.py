"""Composition helpers for execution-policy adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from lingtai.execution_policy.configured import (
    ConfiguredExecutionPolicy,
    ExecutionPolicyConfigurationError,
    configured_policy_factory,
)
from lingtai.execution_policy.registry import (
    ExecutionPolicyFactory,
    get_execution_policy_factory,
    register_execution_policy_factory,
)
from lingtai.kernel.execution_policy import (
    API_VERSION,
    ExecutionDecision,
    ExecutionPolicyCompatibilityError,
    ExecutionPolicyPort,
    ExecutionPolicyUnavailableError,
    ExecutionRequest,
    HealthSnapshot,
    PassThroughExecutionPolicy,
    RouteHealth,
    require_api_version,
)

register_execution_policy_factory("configured", configured_policy_factory)


def load_execution_policy(
    declaration: Mapping[str, object] | None, working_dir: Path
) -> ExecutionPolicyPort:
    """Build the declared adapter, or the pass-through policy when absent."""

    if declaration is None:
        return PassThroughExecutionPolicy()
    if not isinstance(declaration, Mapping):
        raise ExecutionPolicyConfigurationError(
            "execution-policy declaration must be an object"
        )
    if not declaration:
        return PassThroughExecutionPolicy()

    require_api_version(
        declaration.get("api_version"), source="execution-policy declaration"
    )
    adapter = declaration.get("adapter")
    if adapter is None or adapter == "pass-through":
        allowed = {"api_version"} if adapter is None else {"api_version", "adapter"}
        if set(declaration) != allowed:
            raise ExecutionPolicyConfigurationError(
                f"pass-through execution-policy declaration has unknown fields: "
                f"{sorted(set(declaration) - allowed)!r}"
            )
        return PassThroughExecutionPolicy()
    if not isinstance(adapter, str) or not adapter:
        raise ExecutionPolicyConfigurationError(
            "execution-policy adapter must be a non-empty string"
        )

    factory = get_execution_policy_factory(adapter)
    policy = factory(declaration, Path(working_dir))
    require_api_version(
        getattr(policy, "api_version", None),
        source=f"execution-policy adapter {adapter!r}",
    )
    return policy


__all__ = [
    "API_VERSION",
    "ConfiguredExecutionPolicy",
    "ExecutionDecision",
    "ExecutionPolicyCompatibilityError",
    "ExecutionPolicyConfigurationError",
    "ExecutionPolicyFactory",
    "ExecutionPolicyPort",
    "ExecutionPolicyUnavailableError",
    "ExecutionRequest",
    "HealthSnapshot",
    "PassThroughExecutionPolicy",
    "RouteHealth",
    "get_execution_policy_factory",
    "load_execution_policy",
    "register_execution_policy_factory",
]
