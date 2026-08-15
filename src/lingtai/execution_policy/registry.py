"""Explicit execution-policy adapter factory registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from lingtai.kernel.execution_policy import ExecutionPolicyPort

ExecutionPolicyFactory = Callable[
    [Mapping[str, object], Path], ExecutionPolicyPort
]

_FACTORIES: dict[str, ExecutionPolicyFactory] = {}


def register_execution_policy_factory(
    name: str, factory: ExecutionPolicyFactory
) -> None:
    """Register *factory* under an exact adapter name."""

    if not name:
        raise ValueError("execution-policy adapter name must be non-empty")
    if name in _FACTORIES:
        raise ValueError(f"execution-policy adapter {name!r} is already registered")
    _FACTORIES[name] = factory


def get_execution_policy_factory(name: str) -> ExecutionPolicyFactory:
    """Return the explicitly registered factory for *name*."""

    try:
        return _FACTORIES[name]
    except KeyError as exc:
        raise KeyError(f"unknown execution-policy adapter {name!r}") from exc


__all__ = [
    "ExecutionPolicyFactory",
    "get_execution_policy_factory",
    "register_execution_policy_factory",
]
