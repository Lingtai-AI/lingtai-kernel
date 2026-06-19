"""Migration map from canonical import paths to the SDK public surface.

The machine-readable contract behind the compatibility strategy: each entry
says "the name reachable from *here* is the SAME object the SDK exports from
*there*." It powers the migration table in the docs and a round-trip test that
asserts every active path resolves to the *same object* the SDK exports —
re-export, never a parallel fork.

Every ``legacy`` path listed here MUST still resolve. The kernel was relocated
from the top-level ``lingtai_kernel`` package to ``lingtai.kernel`` as a hard
cut (no compatibility shim): the old ``lingtai_kernel.*`` import paths no longer
exist and are therefore NOT carried here. The canonical kernel import root is
``lingtai.kernel``; the curated public surface is ``lingtai_sdk``. This map
proves the two agree by object identity.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Deprecation:
    legacy: str
    current: str
    symbol: str
    since: str
    removed_in: str | None = None
    note: str = ""

    @property
    def is_active_alias(self) -> bool:
        """True while the legacy path is still importable (not yet removed)."""
        return self.removed_in is None


_SDK_INTRODUCED = "0.12.3"

DEPRECATIONS: tuple[Deprecation, ...] = (
    Deprecation(
        legacy="lingtai.kernel.BaseAgent",
        current="lingtai_sdk.BaseAgent",
        symbol="BaseAgent",
        since=_SDK_INTRODUCED,
        note="Kernel coordinator. Still exported by lingtai.kernel and lingtai.",
    ),
    Deprecation(
        legacy="lingtai.Agent",
        current="lingtai_sdk.Agent",
        symbol="Agent",
        since=_SDK_INTRODUCED,
        note="Batteries-included agent. Lives in the wrapper; SDK re-exports lazily.",
    ),
    Deprecation(
        legacy="lingtai.kernel.config.AgentConfig",
        current="lingtai_sdk.types.AgentConfig",
        symbol="AgentConfig",
        since=_SDK_INTRODUCED,
    ),
    Deprecation(
        legacy="lingtai.kernel.state.AgentState",
        current="lingtai_sdk.types.AgentState",
        symbol="AgentState",
        since=_SDK_INTRODUCED,
    ),
    Deprecation(
        legacy="lingtai.kernel.message.Message",
        current="lingtai_sdk.types.Message",
        symbol="Message",
        since=_SDK_INTRODUCED,
    ),
    Deprecation(
        legacy="lingtai.kernel.types.UnknownToolError",
        current="lingtai_sdk.errors.UnknownToolError",
        symbol="UnknownToolError",
        since=_SDK_INTRODUCED,
    ),
)


def active_aliases() -> tuple[Deprecation, ...]:
    """Legacy paths that still import successfully (the common case today)."""
    return tuple(d for d in DEPRECATIONS if d.is_active_alias)


def migration_for(legacy_path: str) -> Deprecation | None:
    """Look up the recommended move for a legacy import path, if any."""
    for d in DEPRECATIONS:
        if d.legacy == legacy_path:
            return d
    return None


__all__ = ["Deprecation", "DEPRECATIONS", "active_aliases", "migration_for"]
