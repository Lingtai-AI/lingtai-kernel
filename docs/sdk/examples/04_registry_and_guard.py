"""The declared-bundle registry and the guard/advisory bridge (read-only).

Two import-pure, network-free surfaces:

* :func:`lingtai_sdk.default_registry` — a validated, name- and tool-indexed
  view over the *full declared* SDK bundle set. It answers "which bundle owns
  this tool, and how dangerous did that bundle declare itself" via
  :meth:`BundleRegistry.dispatch_target`. It declares no handler and calls no
  tool — it is the read-only lookup a router or guard installer consults
  *before* dispatch.

* :mod:`lingtai_sdk.guard` — turns those declared
  :class:`~lingtai_sdk.bundles.contracts.SecurityDanger` postures into a kernel
  ``GuardCheck``. The policy is deliberately narrow and fail-open: ``safe`` →
  clean pass-through, ``caution`` → allow-but-warn advisory, ``destructive`` →
  deny (BLOCKING) or warn (ADVISORY), unknown tool → allow (fail open).

Both are pure metadata operations — nothing is wired into a live agent here.

Run it directly::

    python docs/sdk/examples/04_registry_and_guard.py
"""
from __future__ import annotations

from lingtai_sdk import all_bundle_manifests, default_registry
from lingtai_sdk.guard import (
    GuardPolicyMode,
    guard_check_from_manifests,
    tool_danger_index,
)
from lingtai.kernel.tool_call_guard import ToolProposal


def main() -> None:
    registry = default_registry()
    print("declared bundles:", registry.names())

    # Resolve a tool to its owning bundle + declared danger posture.
    for tool in ("read", "bash", "soul"):
        target = registry.dispatch_target(tool)
        print(f"  {tool!r} -> bundle {target.bundle_name!r}, danger {target.danger.value}")

    # The danger index is the flat {tool: danger} map the guard keys on.
    index = tool_danger_index(all_bundle_manifests())
    print("danger postures:", {k: v.value for k, v in index.items()})

    # Build a blocking guard check and ask it about a few tools. The check maps a
    # ToolProposal to a GuardDecision (or None for a clean pass-through).
    check = guard_check_from_manifests(
        all_bundle_manifests(), mode=GuardPolicyMode.BLOCKING
    )
    for tool in ("read", "edit", "bash", "totally_unknown_tool"):
        decision = check(ToolProposal(tool_name=tool, tool_args={}))
        if decision is None:
            verdict = "allow (clean pass-through)"
        else:
            verdict = f"{decision.action} (severity={decision.severity})"
        print(f"  guard({tool!r}) -> {verdict}")


if __name__ == "__main__":
    main()
