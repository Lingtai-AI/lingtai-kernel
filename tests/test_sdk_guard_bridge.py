"""Stage-17 proof: the pure SDK ``BundleManifest`` → ``GuardCheck`` bridge.

A bundle manifest already declares a per-bundle ``SecurityDanger`` posture
(``safe`` / ``caution`` / ``destructive``) over its named tools. This bridge
turns one or more manifests into kernel ``tool_call_guard`` primitives — a
``GuardCheck`` callable and a ready ``ToolCallGuard`` chain — so the existing
``ToolExecutor`` guard seam (stage 16) can consult bundle posture before a tool
is dispatched, *without* any executor / agent / wrapper change.

Policy is deliberately narrow and deterministic:

* ``safe``        → allow, clean pass-through (no advisory);
* ``caution``     → allow but warn (advisory metadata);
* ``destructive`` → ``BLOCKING`` denies, ``ADVISORY`` allows-with-warning;
* unknown tool / no manifest → **allow** (fail open), never fail closed.

The kernel must NOT import the SDK; this module imports kernel
``tool_call_guard`` types only (the dependency-light kernel, not the wrapper).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from lingtai_kernel.tool_call_guard import (
    ToolCallGuard,
    ToolProposal,
)
from lingtai_sdk import capabilities as cap
from lingtai_sdk import core_bundles as core
from lingtai_sdk import guard_bridge as gb

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


def _proposal(tool_name: str) -> ToolProposal:
    return ToolProposal(tool_name=tool_name, tool_args={})


def _manifest(name: str, tools: tuple[str, ...], danger: str) -> cap.BundleManifest:
    return cap.BundleManifest(
        name=name,
        version="0.0.1",
        surfaces=cap.CapabilitySurfaces(tools=tools),
        security=cap.SecurityPolicy(danger=danger),
        transport=cap.TransportSpec(kind=cap.TransportKind.IN_PROCESS.value),
    )


# --- policy mode surface ---------------------------------------------------


def test_policy_mode_values():
    assert gb.GuardPolicyMode.ADVISORY.value == "advisory"
    assert gb.GuardPolicyMode.BLOCKING.value == "blocking"


def test_default_policy_mode_is_blocking():
    # A destructive tool denied by default is the safe-but-fail-open posture:
    # known-destructive blocks, everything unknown still allows.
    guard = gb.tool_call_guard_from_manifests([core.system_bundle()])
    assert isinstance(guard, ToolCallGuard)
    decision = guard.evaluate(_proposal("system"))
    assert decision.allowed is False


# --- safe tool: clean pass-through -----------------------------------------


def test_safe_tool_allowed_clean_pass_through():
    safe = _manifest("safe_bundle", ("reader",), cap.SecurityDanger.SAFE.value)
    check = gb.guard_check_from_manifests([safe])
    decision = check(_proposal("reader"))
    # safe tools are an explicit no-op for the chain: a falsy/None decision so
    # the ToolCallGuard collapses to the unchanged default_allow pass-through.
    guard = ToolCallGuard([check])
    overall = guard.evaluate(_proposal("reader"))
    assert overall.allowed is True
    assert overall.approval_mode == "pass_through"
    assert overall.check_name == "default_allow"
    assert decision is None or decision.allowed is True


# --- caution/destructive core tools -----------------------------------------


def test_caution_tool_allowed_with_warning_advisory():
    guard = gb.tool_call_guard_from_manifests([core.soul_bundle()])
    decision = guard.evaluate(_proposal("soul"))
    assert decision.allowed is True
    assert decision.action == "warn"
    assert decision.severity == "warning"
    advisory = decision.advisory_metadata(_proposal("soul"))
    assert advisory is not None
    assert advisory["type"] == "tool_call_guard"
    assert advisory["metadata"]["danger"] == "caution"
    assert advisory["metadata"]["bundle"] == "soul"


# --- destructive tool: blocking vs advisory --------------------------------


def test_destructive_tool_denied_in_blocking_mode():
    guard = gb.tool_call_guard_from_manifests(
        [core.system_bundle()], mode=gb.GuardPolicyMode.BLOCKING
    )
    decision = guard.evaluate(_proposal("system"))
    assert decision.allowed is False
    assert decision.action == "deny"
    assert decision.severity == "error"
    assert decision.metadata["danger"] == "destructive"
    assert decision.metadata["bundle"] == "system"


def test_destructive_tool_warned_in_advisory_mode():
    guard = gb.tool_call_guard_from_manifests(
        [core.system_bundle()], mode=gb.GuardPolicyMode.ADVISORY
    )
    decision = guard.evaluate(_proposal("system"))
    assert decision.allowed is True
    assert decision.action == "warn"
    assert decision.severity == "warning"
    assert decision.metadata["danger"] == "destructive"


# --- Stage 21: advisory observability is source-labeled ---------------------


def test_default_core_advisory_is_source_labeled():
    """A default-core advisory exposes a flat, bundle-attributed summary.

    The Stage-21 observability guarantee: a warn-but-allow decision for the
    always-present ``system`` core surface carries a stable ``advisory_summary``
    whose ``source`` names the originating bundle and declared danger, without
    requiring a consumer to crack open the nested ``guard_decision`` payload.
    """
    guard = gb.tool_call_guard_from_manifests(
        core.core_bundle_manifests(), mode=gb.GuardPolicyMode.ADVISORY
    )
    decision = guard.evaluate(_proposal("system"))
    assert decision.allowed is True  # advisory never blocks
    summary = decision.advisory_summary()
    assert summary is not None
    assert summary["action"] == "warn"
    assert summary["severity"] == "warning"
    assert summary["allowed"] is True
    assert summary["bundle"] == "system"
    assert summary["danger"] == "destructive"
    assert summary["source"] == "bundle:system:destructive"


def test_caution_core_advisory_summary_labels_bundle():
    guard = gb.tool_call_guard_from_manifests(
        core.core_bundle_manifests(), mode=gb.GuardPolicyMode.ADVISORY
    )
    summary = guard.evaluate(_proposal("soul")).advisory_summary()
    assert summary is not None
    assert summary["bundle"] == "soul"
    assert summary["danger"] == "caution"
    assert summary["source"] == "bundle:soul:caution"


def test_safe_passthrough_has_no_advisory_summary():
    """A clean pass-through must not be mistaken for an advisory."""
    guard = gb.tool_call_guard_from_manifests(core.core_bundle_manifests())
    decision = guard.evaluate(_proposal("totally_unknown_tool"))
    assert decision.advisory_summary() is None


# --- unknown tool: fail open ------------------------------------------------


def test_unknown_tool_allowed_fail_open():
    guard = gb.tool_call_guard_from_manifests(core.core_bundle_manifests())
    decision = guard.evaluate(_proposal("totally_unknown_tool"))
    assert decision.allowed is True
    assert decision.approval_mode == "pass_through"


def test_no_manifests_allows_everything():
    guard = gb.tool_call_guard_from_manifests([])
    decision = guard.evaluate(_proposal("anything"))
    assert decision.allowed is True
    assert decision.approval_mode == "pass_through"


# --- core manifest posture maps deterministically --------------------------


def test_core_manifests_map_system_psyche_destructive_soul_caution():
    blocking = gb.tool_call_guard_from_manifests(core.core_bundle_manifests())
    # system / psyche are destructive -> denied under default BLOCKING
    for destructive_tool in ("system", "psyche"):
        decision = blocking.evaluate(_proposal(destructive_tool))
        assert decision.allowed is False
        assert decision.action == "deny"
    # soul is caution -> allowed-with-warning
    decision = blocking.evaluate(_proposal("soul"))
    assert decision.allowed is True
    assert decision.action == "warn"


def test_proof_bundle_safe_tool_is_clean():
    guard = gb.tool_call_guard_from_manifests([cap.proof_bundle()])
    decision = guard.evaluate(_proposal("echo"))
    assert decision.allowed is True
    assert decision.approval_mode == "pass_through"


# --- duplicate-tool conservatism (strongest posture wins) ------------------


def test_conflicting_danger_takes_most_dangerous():
    safe = _manifest("a", ("shared",), cap.SecurityDanger.SAFE.value)
    destructive = _manifest("b", ("shared",), cap.SecurityDanger.DESTRUCTIVE.value)
    guard = gb.tool_call_guard_from_manifests([safe, destructive])
    # the more dangerous declaration wins so a safe alias can't downgrade a
    # destructive tool.
    assert guard.evaluate(_proposal("shared")).allowed is False


# --- the bridge index helper -----------------------------------------------


def test_tool_danger_index_collects_named_tools():
    index = gb.tool_danger_index(core.core_bundle_manifests())
    assert index["system"] is cap.SecurityDanger.DESTRUCTIVE
    assert index["psyche"] is cap.SecurityDanger.DESTRUCTIVE
    assert index["soul"] is cap.SecurityDanger.CAUTION


# --- import purity ----------------------------------------------------------


def test_guard_bridge_import_is_pure_no_wrapper():
    code = (
        "import sys, lingtai_sdk.guard_bridge as gb\n"
        "from lingtai_sdk import core_bundles as core\n"
        "guard = gb.tool_call_guard_from_manifests(core.core_bundle_manifests())\n"
        "from lingtai_kernel.tool_call_guard import ToolProposal\n"
        "d = guard.evaluate(ToolProposal(tool_name='system', tool_args={}))\n"
        "assert d.allowed is False\n"
        # importing guard_bridge must NOT pull in the lingtai wrapper.
        "bad = [m for m in sys.modules if m == 'lingtai' or m.startswith('lingtai.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout


def test_kernel_does_not_import_sdk():
    # The kernel tool_call_guard module must stand alone — importing it must not
    # drag in lingtai_sdk (no import inversion).
    code = (
        "import sys\n"
        "import lingtai_kernel.tool_call_guard\n"
        "bad = [m for m in sys.modules if m == 'lingtai_sdk' "
        "or m.startswith('lingtai_sdk.')]\n"
        "assert not bad, bad\n"
        "print('OK')\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(SRC)},
    )
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout
