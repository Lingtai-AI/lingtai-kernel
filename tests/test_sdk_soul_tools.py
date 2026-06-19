"""Stage-3J proof: the high-state ``soul`` inner-voice bundle declaration + seam.

The inner-voice counterpart of ``test_sdk_lifecycle_tools.py`` (stage 3C, the
``system`` lifecycle bundle) and ``test_sdk_psyche_tools.py`` (the ``psyche``
identity bundle). These tests assert that:

* the ``soul`` voice manifest is the *same* privileged, native-only, ``caution``
  core bundle from ``core_bundles`` (single source of truth — not a fork),
  validates strictly, and round-trips through ``load_manifest``;
* the **per-action risk table** (``SOUL_ACTION_RISK``) covers exactly the live
  ``soul`` action enum (``soul.get_schema``'s ``action`` enum) and grades every
  action ``caution`` (LLM consultation + persisted logs/config + notification
  dismiss — none is a pure read, none is a destructive teardown), so the
  bundle-level posture equals the strongest action;
* the native host seam hosts the ``soul`` bundle alone (without ``system`` /
  ``psyche``) with an injected handler, as a ``NativeBundleHost`` (native
  authority), and the non-native ``BundleHost`` refuses it;
* the **guard/audit invariant** holds: feeding the ``caution`` ``soul`` manifest to
  the stage-17 ``guard_bridge`` warns (never denies) in both BLOCKING and ADVISORY
  — *without* this stage installing any guard.

Crucially, **no real ``soul`` is called or imported from the SDK**: every handler
here is a dummy, and a subprocess asserts importing ``soul_tools`` pulls in no
``lingtai`` wrapper module. The wrapper-side bridge (which hosts the real kernel
intrinsic) is tested in ``tests/test_soul_bundle_bridge.py``.
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
from lingtai_sdk import soul_tools as st
from lingtai_sdk.errors import BundleHostError

# The guard bridge maps a manifest's danger posture onto kernel guard primitives;
# ToolProposal is the kernel-side type the resulting check consumes.
from lingtai.kernel.tool_call_guard import ToolProposal

# The real kernel intrinsic schema — the source the SDK action table mirrors.
# Importing the kernel intrinsic here is allowed (kernel, not wrapper).
from lingtai.kernel.intrinsics import soul as _soul

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


# --- the manifest is the single-source-of-truth core ``soul`` bundle --------


def test_soul_manifest_is_the_core_soul_bundle():
    m = st.soul_voice_manifest()
    # identical to the core-bundle declaration — not a fork.
    assert m.to_dict() == core.soul_bundle().to_dict()
    assert m.name == st.SOUL_TOOL_NAME == "soul"


def test_soul_manifest_privileged_native_caution():
    m = st.soul_voice_manifest()
    assert m.roles.required is True
    assert m.roles.privileged is True
    assert m.roles.native_only is True
    assert m.roles.backend_replaceability is cap.BackendReplaceability.NATIVE_ONLY
    assert m.transport.kind == cap.TransportKind.NATIVE.value
    # bundle-level posture is caution (inner-voice consultation + persisted config).
    assert m.security.danger == cap.SecurityDanger.CAUTION.value


def test_soul_manifest_declares_only_soul_tool():
    m = st.soul_voice_manifest()
    assert m.surfaces.tools == ("soul",)
    assert m.surfaces.resources == ()
    assert m.surfaces.prompts == ()
    assert m.surfaces.events == ()
    assert m.surfaces.hooks == ()


def test_soul_manifest_validates_and_round_trips():
    original = st.soul_voice_manifest()
    original.validate()  # does not raise
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()
    assert loaded.roles.privileged is True
    assert loaded.roles.native_only is True
    assert loaded.security.danger == cap.SecurityDanger.CAUTION.value


def test_is_soul_voice_manifest():
    assert st.is_soul_voice_manifest(st.soul_voice_manifest()) is True
    # the other core bundles are NOT the soul voice bundle.
    assert st.is_soul_voice_manifest(core.system_bundle()) is False
    assert st.is_soul_voice_manifest(core.psyche_bundle()) is False
    assert st.is_soul_voice_manifest(cap.proof_bundle()) is False


# --- the per-action risk table (the heart of stage 3J for soul) -------------


def test_action_risk_table_covers_exactly_the_live_action_enum():
    # the SDK declaration must match the live schema's action enum, so the risk
    # table never drifts from the actions the intrinsic actually dispatches.
    live_actions = set(_soul.get_schema()["properties"]["action"]["enum"])
    assert set(st.SOUL_ACTION_RISK) == live_actions


def test_action_risk_grades_all_caution():
    # every soul action is caution — none is a pure read, none a destructive teardown.
    for action, grade in st.SOUL_ACTION_RISK.items():
        assert grade is cap.SecurityDanger.CAUTION, action


def test_bundle_posture_equals_strongest_action():
    strongest = max(
        (g for g in st.SOUL_ACTION_RISK.values()),
        key=lambda g: ("safe", "caution", "destructive").index(g.value),
    )
    assert strongest is cap.SecurityDanger.CAUTION
    assert st.soul_voice_manifest().security.danger == strongest.value


def test_consultation_and_config_action_subsets():
    assert st.SOUL_CONSULTATION_ACTIONS == frozenset({"inquiry", "flow"})
    assert st.SOUL_CONFIG_ACTIONS == frozenset({"config", "voice"})
    # both subsets are real actions in the table; dismiss is in neither.
    assert st.SOUL_CONSULTATION_ACTIONS <= set(st.SOUL_ACTION_RISK)
    assert st.SOUL_CONFIG_ACTIONS <= set(st.SOUL_ACTION_RISK)
    assert "dismiss" not in (st.SOUL_CONSULTATION_ACTIONS | st.SOUL_CONFIG_ACTIONS)


def test_action_risk_helper_fails_safe_high_on_unknown():
    # an action not in the table fails safe HIGH (destructive), never silently safe.
    assert st.action_risk("totally-unknown") is cap.SecurityDanger.DESTRUCTIVE
    # known actions return their graded posture.
    assert st.action_risk("inquiry") is cap.SecurityDanger.CAUTION
    assert st.action_risk("dismiss") is cap.SecurityDanger.CAUTION


# --- non-native BundleHost refuses; native seam hosts with injected handler --


def test_bundle_host_refuses_soul_bundle():
    m = st.soul_voice_manifest()
    with pytest.raises(BundleHostError):
        host.BundleHost(m, {"soul": lambda **kw: None})


def test_soul_voice_host_with_injected_dummy():
    sentinel = object()
    h = st.soul_voice_host(lambda **kw: sentinel)
    assert isinstance(h, host.NativeBundleHost)
    assert h.manifest.name == "soul"
    assert h.tools == ("soul",)
    # the host invokes the *injected* dummy, never a real implementation.
    assert h.invoke("soul") is sentinel


def test_soul_voice_host_requires_callable_handler():
    with pytest.raises(BundleHostError):
        st.soul_voice_host(object())  # not callable


def test_soul_voice_host_is_native_authority_not_in_process():
    h = st.soul_voice_host(lambda **kw: None)
    assert type(h) is host.NativeBundleHost
    assert not isinstance(h, host.BundleHost)


def test_soul_voice_hosts_builds_soul_only():
    hosts = st.soul_voice_hosts({"soul": lambda **kw: {"ok": True}})
    assert set(hosts) == {"soul"}
    h = hosts["soul"]
    assert isinstance(h, host.NativeBundleHost)
    assert h.manifest.roles.privileged is True
    assert h.manifest.transport.kind == "native"
    assert h.invoke("soul") == {"ok": True}


def test_soul_voice_hosts_rejects_missing_handler():
    with pytest.raises(BundleHostError):
        st.soul_voice_hosts({})


def test_soul_voice_hosts_rejects_undeclared_handler():
    with pytest.raises(BundleHostError):
        st.soul_voice_hosts(
            {"soul": lambda **kw: None, "psyche": lambda **kw: None}
        )


def test_soul_voice_hosts_rejects_non_callable_handler():
    with pytest.raises(BundleHostError):
        st.soul_voice_hosts({"soul": object()})


# --- guard/audit invariant: caution posture warns (never denies) ------------


def test_guard_bridge_warns_soul_in_blocking_mode():
    manifests = [st.soul_voice_manifest()]
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.BLOCKING)
    decision = check(ToolProposal(tool_name="soul", tool_args={"action": "flow"}))
    # soul is caution -> warned, never denied, even in BLOCKING mode.
    assert decision is not None
    assert decision.allowed is True
    assert decision.action == "warn"
    assert decision.metadata.get("danger") == cap.SecurityDanger.CAUTION.value
    assert decision.metadata.get("bundle") == "soul"


def test_guard_bridge_warns_soul_in_advisory_mode():
    manifests = [st.soul_voice_manifest()]
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.ADVISORY)
    decision = check(ToolProposal(tool_name="soul", tool_args={"action": "dismiss"}))
    assert decision is not None
    assert decision.allowed is True
    assert decision.action == "warn"


def test_guard_bridge_danger_index_reflects_soul_posture():
    index = gb.tool_danger_index([st.soul_voice_manifest()])
    assert index["soul"] is cap.SecurityDanger.CAUTION


# --- import purity / no implementation migration ---------------------------


def test_soul_tools_import_is_pure_and_migrates_no_wrapper():
    code = (
        "import sys, lingtai_sdk.soul_tools as st\n"
        "m = st.soul_voice_manifest()\n"
        "assert m.name == 'soul'\n"
        "assert m.security.danger == 'caution'\n"
        "assert m.roles.privileged is True\n"
        "h = st.soul_voice_host(lambda **kw: 'dummy')\n"
        "assert h.invoke('soul') == 'dummy'\n"
        "assert st.action_risk('inquiry').value == 'caution'\n"
        "assert st.action_risk('nope').value == 'destructive'\n"
        # importing soul_tools must NOT pull in the lingtai wrapper, i.e. the real
        # soul implementation is not migrated/imported from the SDK.
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
