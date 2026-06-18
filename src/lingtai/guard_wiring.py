"""Advisory-first wrapper wiring of the SDK guard bridge (stage 18, C3).

Stage 17 built a pure, import-light SDK adapter
(:mod:`lingtai_sdk.guard_bridge`) that turns one or more
:class:`~lingtai_sdk.capabilities.BundleManifest` objects into a kernel
:class:`~lingtai_kernel.tool_call_guard.ToolCallGuard` — but wired *nothing* into
a live agent. This module is the thin wrapper-layer seam that finally installs
such a guard onto the Stage-16 ``BaseAgent._tool_call_guard`` slot, so the turn
loop's ``ToolExecutor`` consults declared bundle posture before a tool is
dispatched.

Behaviour contract (deliberately advisory-first / fail-open)
------------------------------------------------------------
* **Default live mode is advisory.** :data:`DEFAULT_LIVE_GUARD_MODE` is
  :attr:`~lingtai_sdk.guard_bridge.GuardPolicyMode.ADVISORY`: a manifest-declared
  ``destructive`` tool is surfaced as a *warning*, never denied, in default live
  wiring. Blocking is reachable only by an explicit ``mode=`` opt-in and is never
  the wrapper default.
* **Default/existing agents stay pass-through.** The default capability→manifest
  registry (:func:`default_manifest_registry`) is empty, so nothing is wired
  unless a capability genuinely declares a bundle manifest. A freshly built
  agent therefore keeps the unchanged ``default_allow`` pass-through.
* **Unknown / unmanifested tools fail open.** MCP tools, ``add_tool`` tools, and
  any capability tool without a manifest are unknown to the bridge and pass
  through cleanly — this slice can only ever *add advisories* for explicitly
  declared surfaces, never block an undeclared tool.
* **No lifecycle/system tool is blocked.** Because the live mode is advisory,
  even a destructive core tool (e.g. ``system``) would only warn, never deny.
* **Fail open on any error.** If manifest collection or guard construction
  raises, the seam is left at its existing safe value rather than failing closed.

Import direction
----------------
The wrapper (``src/lingtai/...``) may import the SDK bridge/types; the kernel
must stay SDK-free. This module therefore imports
:mod:`lingtai_sdk.guard_bridge` (a wrapper→SDK edge, which is allowed), and the
kernel never imports it back.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from lingtai_sdk.capabilities import BundleManifest
from lingtai_sdk.guard_bridge import (
    GuardPolicyMode,
    tool_call_guard_from_manifests,
)

#: A capability→manifest provider maps an enabled capability name to a
#: zero-arg callable returning that capability's declared :class:`BundleManifest`.
ManifestProvider = Callable[[], BundleManifest]
ManifestRegistry = dict[str, ManifestProvider]

#: The wrapper's default live policy mode. Advisory-first: declared destructive
#: tools warn, they are never blocked by default live wiring.
DEFAULT_LIVE_GUARD_MODE: GuardPolicyMode = GuardPolicyMode.ADVISORY


def default_manifest_registry() -> ManifestRegistry:
    """The default capability→manifest registry — empty.

    No shipping capability declares an SDK bundle manifest yet, so the default
    registry is empty and live wiring is behaviour-neutral: existing/default
    agents keep the unchanged ``default_allow`` pass-through. A capability gains
    advisory posture only by registering a provider here (or by passing a
    ``registry`` into :func:`wire_agent_guard`).
    """
    return {}


def collect_agent_bundle_manifests(
    agent: Any,
    *,
    registry: ManifestRegistry | None = None,
) -> list[BundleManifest]:
    """Collect declared bundle manifests for an agent's enabled capabilities.

    Walks the agent's ``_capabilities`` (a list of ``(name, kwargs)`` pairs set
    by wrapper ``Agent`` construction) and, for each capability that has a
    provider in ``registry``, calls the provider to obtain its
    :class:`BundleManifest`. Capabilities with no provider contribute nothing —
    their tools remain unknown to the bridge and fail open.

    Fail-open: a provider that raises is skipped (logged via the agent's
    ``_log`` if available) rather than aborting collection, so one broken
    manifest can never deny an otherwise-clean agent its construction.
    """
    if registry is None:
        registry = default_manifest_registry()
    if not registry:
        return []

    capabilities = getattr(agent, "_capabilities", None) or []
    manifests: list[BundleManifest] = []
    seen: set[str] = set()
    for entry in capabilities:
        name = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if not isinstance(name, str) or name in seen:
            continue
        seen.add(name)
        provider = registry.get(name)
        if provider is None:
            continue
        try:
            manifest = provider()
        except Exception as exc:  # fail open — never block construction
            _safe_log(agent, "guard_wiring_manifest_skipped", capability=name,
                      reason=str(exc))
            continue
        if isinstance(manifest, BundleManifest):
            manifests.append(manifest)
    return manifests


def install_bundle_guard(
    agent: Any,
    *,
    manifests: Iterable[BundleManifest],
    mode: GuardPolicyMode = DEFAULT_LIVE_GUARD_MODE,
) -> None:
    """Build a guard from ``manifests`` and install it on the Stage-16 seam.

    Replaces ``agent._tool_call_guard`` with the chain
    :func:`~lingtai_sdk.guard_bridge.tool_call_guard_from_manifests` returns for
    the supplied manifests and ``mode`` (advisory by default). With no manifests
    this is the unchanged ``default_allow`` pass-through, so calling it is always
    safe. The turn loop already threads ``_tool_call_guard`` into every
    ``ToolExecutor`` it builds (Stage 16), so the installed guard becomes live
    without any executor/turn change.
    """
    guard = tool_call_guard_from_manifests(list(manifests), mode=mode)
    agent._tool_call_guard = guard


def wire_agent_guard(
    agent: Any,
    *,
    registry: ManifestRegistry | None = None,
    mode: GuardPolicyMode = DEFAULT_LIVE_GUARD_MODE,
) -> None:
    """Live entry point: collect an agent's declared manifests and install them.

    Called once near the end of wrapper ``Agent`` construction (and reconstruct).
    Advisory-first and fail-open:

    * collects manifests for enabled capabilities from ``registry`` (default:
      the empty :func:`default_manifest_registry`, i.e. behaviour-neutral);
    * installs an advisory guard (default ``mode``) onto the Stage-16 seam;
    * on **any** error leaves the seam untouched (safe pass-through) rather than
      failing closed.

    A default (empty-registry) call leaves the seam a pure pass-through, so
    existing/default agents are unaffected.
    """
    try:
        manifests = collect_agent_bundle_manifests(agent, registry=registry)
        if not manifests:
            # Nothing declared — leave the existing (default pass-through) seam
            # untouched. Avoids needlessly replacing a guard a host may have set.
            return
        install_bundle_guard(agent, manifests=manifests, mode=mode)
    except Exception as exc:  # fail open — never break construction
        _safe_log(agent, "guard_wiring_failed", reason=str(exc))


def _safe_log(agent: Any, event: str, **fields: Any) -> None:
    """Best-effort structured log via the agent's ``_log``; never raises."""
    log = getattr(agent, "_log", None)
    if callable(log):
        try:
            log(event, **fields)
        except Exception:
            pass


__all__ = [
    "ManifestProvider",
    "ManifestRegistry",
    "DEFAULT_LIVE_GUARD_MODE",
    "default_manifest_registry",
    "collect_agent_bundle_manifests",
    "install_bundle_guard",
    "wire_agent_guard",
]
