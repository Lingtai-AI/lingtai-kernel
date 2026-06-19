"""Stage-3J proof: the high-state ``psyche`` identity/context bundle declaration + seam.

The identity/context counterpart of ``test_sdk_lifecycle_tools.py`` (stage 3C, the
``system`` lifecycle bundle). These tests assert that:

* the ``psyche`` identity manifest is the *same* privileged, native-only,
  ``destructive`` core bundle from ``core_bundles`` (single source of truth — not a
  fork), validates strictly, and round-trips through ``load_manifest``;
* the **per-(object, action) risk table** (``PSYCHE_OBJECT_ACTION_RISK``)
  faithfully grades each pair — pure reads ``safe``, lasting-but-recoverable writes
  ``caution``, and irreversible ``name.set`` ``destructive`` — and its ``(object,
  action)`` keys cover exactly the live dispatch table
  (``intrinsics.psyche._VALID_ACTIONS``);
* the native host seam hosts the ``psyche`` bundle alone (without ``system`` /
  ``soul``) with an injected handler, as a ``NativeBundleHost`` (native authority),
  and the non-native ``BundleHost`` refuses it;
* the **guard/audit invariant** holds: feeding the ``destructive`` ``psyche``
  manifest to the stage-17 ``guard_bridge`` denies in BLOCKING and warns in
  ADVISORY — *without* this stage installing any guard.

Crucially, **no real ``psyche`` is called or imported from the SDK**: every handler
here is a dummy, and a subprocess asserts importing ``psyche_tools`` pulls in no
``lingtai`` wrapper module. The wrapper-side bridge (which hosts the real kernel
intrinsic) is tested in ``tests/test_psyche_bundle_bridge.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lingtai_sdk import capabilities as cap
from lingtai_sdk import capability_host as host
from lingtai_sdk import core_bundles as core
from lingtai_sdk import guard_bridge as gb
from lingtai_sdk import psyche_tools as pt
from lingtai_sdk.errors import BundleHostError

# The guard bridge maps a manifest's danger posture onto kernel guard primitives;
# ToolProposal is the kernel-side type the resulting check consumes.
from lingtai.kernel.tool_call_guard import ToolProposal

# The real kernel intrinsic dispatch table — the source the SDK validity map
# mirrors. Importing the kernel intrinsic here is allowed (kernel, not wrapper).
from lingtai.kernel.intrinsics.psyche import _VALID_ACTIONS as _KERNEL_VALID_ACTIONS

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


# --- the manifest is the single-source-of-truth core ``psyche`` bundle ------


def test_psyche_manifest_is_the_core_psyche_bundle():
    m = pt.psyche_identity_manifest()
    # identical to the core-bundle declaration — not a fork.
    assert m.to_dict() == core.psyche_bundle().to_dict()
    assert m.name == pt.PSYCHE_TOOL_NAME == "psyche"


def test_psyche_manifest_privileged_native_destructive():
    m = pt.psyche_identity_manifest()
    assert m.roles.required is True
    assert m.roles.privileged is True
    assert m.roles.native_only is True
    assert m.roles.backend_replaceability is cap.BackendReplaceability.NATIVE_ONLY
    assert m.transport.kind == cap.TransportKind.NATIVE.value
    # bundle-level posture is destructive because the surface includes set-once name.set.
    assert m.security.danger == cap.SecurityDanger.DESTRUCTIVE.value


def test_psyche_manifest_declares_only_psyche_tool():
    m = pt.psyche_identity_manifest()
    assert m.surfaces.tools == ("psyche",)
    assert m.surfaces.resources == ()
    assert m.surfaces.prompts == ()
    assert m.surfaces.events == ()
    assert m.surfaces.hooks == ()


def test_psyche_manifest_validates_and_round_trips():
    original = pt.psyche_identity_manifest()
    original.validate()  # does not raise
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()
    assert loaded.roles.privileged is True
    assert loaded.roles.native_only is True
    assert loaded.security.danger == cap.SecurityDanger.DESTRUCTIVE.value


def test_is_psyche_identity_manifest():
    assert pt.is_psyche_identity_manifest(pt.psyche_identity_manifest()) is True
    # the other core bundles are NOT the psyche identity bundle.
    assert pt.is_psyche_identity_manifest(core.system_bundle()) is False
    assert pt.is_psyche_identity_manifest(core.soul_bundle()) is False
    assert pt.is_psyche_identity_manifest(cap.proof_bundle()) is False


# --- the per-(object, action) risk table (the heart of stage 3J for psyche) --


def test_valid_actions_mirror_the_kernel_dispatch_table():
    # the SDK declaration must match what the kernel intrinsic dispatches, so the
    # risk table never drifts from the live (object, action) validity map.
    sdk = {obj: set(acts) for obj, acts in pt.PSYCHE_VALID_ACTIONS.items()}
    kernel = {obj: set(acts) for obj, acts in _KERNEL_VALID_ACTIONS.items()}
    assert sdk == kernel


def test_risk_table_covers_exactly_the_live_object_action_pairs():
    live_pairs = {
        (obj, action)
        for obj, actions in pt.PSYCHE_VALID_ACTIONS.items()
        for action in actions
    }
    assert set(pt.PSYCHE_OBJECT_ACTION_RISK) == live_pairs


def test_risk_grades_reads_safe():
    R = pt.PSYCHE_OBJECT_ACTION_RISK
    assert R[("lingtai", "load")] is cap.SecurityDanger.SAFE
    assert R[("pad", "load")] is cap.SecurityDanger.SAFE
    assert pt.PSYCHE_READ_PAIRS == {("lingtai", "load"), ("pad", "load")}


def test_risk_grades_writes_caution():
    R = pt.PSYCHE_OBJECT_ACTION_RISK
    for pair in (
        ("lingtai", "update"),
        ("pad", "edit"),
        ("pad", "append"),
        ("name", "nickname"),
        ("context", "molt"),
    ):
        assert R[pair] is cap.SecurityDanger.CAUTION


def test_risk_grades_immutable_name_set_destructive():
    # name.set writes the immutable true name (set-once) — the one irreversible
    # psyche pair and the reason the bundle posture is destructive.
    assert pt.PSYCHE_OBJECT_ACTION_RISK[("name", "set")] is cap.SecurityDanger.DESTRUCTIVE


def test_mutating_pairs_is_everything_but_reads():
    assert pt.PSYCHE_MUTATING_PAIRS == (
        set(pt.PSYCHE_OBJECT_ACTION_RISK) - pt.PSYCHE_READ_PAIRS
    )
    assert pt.PSYCHE_READ_PAIRS.isdisjoint(pt.PSYCHE_MUTATING_PAIRS)


def test_object_action_risk_helper_fails_safe_high_on_unknown():
    # unknown object, unknown action, or a valid object with an action it does not
    # accept all fail safe HIGH (destructive), never silently safe.
    assert pt.object_action_risk("bogus", "diff") is cap.SecurityDanger.DESTRUCTIVE
    assert pt.object_action_risk("pad", "delete") is cap.SecurityDanger.DESTRUCTIVE
    assert pt.object_action_risk("name", "load") is cap.SecurityDanger.DESTRUCTIVE
    # known pairs return their graded posture.
    assert pt.object_action_risk("pad", "load") is cap.SecurityDanger.SAFE
    assert pt.object_action_risk("context", "molt") is cap.SecurityDanger.CAUTION
    assert pt.object_action_risk("name", "set") is cap.SecurityDanger.DESTRUCTIVE


# --- non-native BundleHost refuses; native seam hosts with injected handler --


def test_bundle_host_refuses_psyche_bundle():
    m = pt.psyche_identity_manifest()
    with pytest.raises(BundleHostError):
        host.BundleHost(m, {"psyche": lambda **kw: None})


def test_psyche_identity_host_with_injected_dummy():
    sentinel = object()
    h = pt.psyche_identity_host(lambda **kw: sentinel)
    assert isinstance(h, host.NativeBundleHost)
    assert h.manifest.name == "psyche"
    assert h.tools == ("psyche",)
    # the host invokes the *injected* dummy, never a real implementation.
    assert h.invoke("psyche") is sentinel


def test_psyche_identity_host_requires_callable_handler():
    with pytest.raises(BundleHostError):
        pt.psyche_identity_host(object())  # not callable


def test_psyche_identity_host_is_native_authority_not_in_process():
    h = pt.psyche_identity_host(lambda **kw: None)
    assert type(h) is host.NativeBundleHost
    assert not isinstance(h, host.BundleHost)


def test_psyche_identity_hosts_builds_psyche_only():
    hosts = pt.psyche_identity_hosts({"psyche": lambda **kw: {"ok": True}})
    assert set(hosts) == {"psyche"}
    h = hosts["psyche"]
    assert isinstance(h, host.NativeBundleHost)
    assert h.manifest.roles.privileged is True
    assert h.manifest.transport.kind == "native"
    assert h.invoke("psyche") == {"ok": True}


def test_psyche_identity_hosts_rejects_missing_handler():
    with pytest.raises(BundleHostError):
        pt.psyche_identity_hosts({})


def test_psyche_identity_hosts_rejects_undeclared_handler():
    with pytest.raises(BundleHostError):
        pt.psyche_identity_hosts(
            {"psyche": lambda **kw: None, "soul": lambda **kw: None}
        )


def test_psyche_identity_hosts_rejects_non_callable_handler():
    with pytest.raises(BundleHostError):
        pt.psyche_identity_hosts({"psyche": object()})


# --- guard/audit invariant: destructive posture denies in blocking mode ------


def test_guard_bridge_blocks_psyche_in_blocking_mode():
    manifests = [pt.psyche_identity_manifest()]
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.BLOCKING)
    decision = check(
        ToolProposal(tool_name="psyche", tool_args={"object": "context", "action": "molt"})
    )
    # psyche is destructive at bundle level because the single tool includes name.set.
    assert decision is not None
    assert decision.allowed is False
    assert decision.action == "deny"
    assert decision.metadata.get("danger") == cap.SecurityDanger.DESTRUCTIVE.value
    assert decision.metadata.get("bundle") == "psyche"


def test_guard_bridge_warns_psyche_in_advisory_mode():
    manifests = [pt.psyche_identity_manifest()]
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.ADVISORY)
    decision = check(
        ToolProposal(tool_name="psyche", tool_args={"object": "pad", "action": "load"})
    )
    assert decision is not None
    assert decision.allowed is True
    assert decision.action == "warn"


def test_guard_bridge_danger_index_reflects_psyche_posture():
    index = gb.tool_danger_index([pt.psyche_identity_manifest()])
    assert index["psyche"] is cap.SecurityDanger.DESTRUCTIVE


# --- import purity / no implementation migration ---------------------------


def test_psyche_tools_import_is_pure_and_migrates_no_wrapper():
    code = (
        "import sys, lingtai_sdk.psyche_tools as pt\n"
        "m = pt.psyche_identity_manifest()\n"
        "assert m.name == 'psyche'\n"
        "assert m.security.danger == 'destructive'\n"
        "assert m.roles.privileged is True\n"
        "h = pt.psyche_identity_host(lambda **kw: 'dummy')\n"
        "assert h.invoke('psyche') == 'dummy'\n"
        "assert pt.object_action_risk('pad', 'load').value == 'safe'\n"
        "assert pt.object_action_risk('name', 'set').value == 'destructive'\n"
        "assert pt.object_action_risk('bogus', 'x').value == 'destructive'\n"
        # importing psyche_tools must NOT pull in the lingtai wrapper, i.e. the
        # real psyche implementation is not migrated/imported from the SDK.
        "bad = [m for m in sys.modules if m.startswith('lingtai.') and not (m == 'lingtai.kernel' or m.startswith('lingtai.kernel.') or m == 'lingtai._version')]\n"
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
