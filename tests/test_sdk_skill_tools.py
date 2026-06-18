"""Stage-3G proof: the ``skills`` catalog bundle declaration + seam.

The skill-catalog counterpart of ``test_sdk_knowledge_tools.py`` (this same
stage) and ``test_sdk_mcp_tools.py`` (stage 3F). These tests assert that:

* the ``skills`` manifest is non-privileged + in-process (capability-carried),
  matching how the live ``setup()`` (``agent.add_tool``) path carries it, and
  declares the read-only / configuration / catalog metadata posture;
* the manifest validates strictly and round-trips through ``load_manifest``;
* the **per-action risk table** (``SKILLS_ACTION_RISK``) covers exactly the
  declared actions (``info`` only), grades them faithfully (``info`` → SAFE), and
  the bundle-level posture equals the strongest action's grade (SAFE);
* the host seam hosts the surface with its correct carrier — a non-native
  ``BundleHost`` — with an injected dummy handler, and the native host refuses it;
* the **guard/audit invariant** holds: feeding the SAFE manifest to the stage-17
  ``guard_bridge`` is a clean pass-through (no deny, no warn).

Crucially, **no real ``skills`` is called or imported from the SDK**: every
handler here is a dummy, and a subprocess asserts importing ``skill_tools`` pulls
in no ``lingtai`` wrapper module. The wrapper-side bridge (which hosts the real
handler) is tested in ``tests/test_skills_bundle_bridge.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lingtai_sdk import capabilities as cap
from lingtai_sdk import capability_host as host
from lingtai_sdk import guard_bridge as gb
from lingtai_sdk import skill_tools as st
from lingtai_sdk.errors import BundleHostError

from lingtai_kernel.tool_call_guard import ToolProposal

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


# --- manifest: identity, posture, carrier -----------------------------------


def test_skills_manifest_non_privileged_in_process():
    m = st.skills_catalog_manifest()
    assert m.name == st.SKILLS_TOOL_NAME == "skills"
    assert m.roles.required is False
    assert m.roles.privileged is False
    assert m.roles.native_only is False
    assert m.roles.backend_replaceability is cap.BackendReplaceability.REPLACEABLE
    assert m.transport.kind == cap.TransportKind.IN_PROCESS.value
    assert m.security.danger == cap.SecurityDanger.SAFE.value
    assert m.surfaces.tools == ("skills",)


def test_skills_manifest_declares_config_catalog_metadata():
    md = st.skills_catalog_manifest().metadata
    assert md["config"] is True
    assert md["catalog"] is True
    assert md["read_only"] is True
    assert md["agent_state_sensitive"] is True
    assert md["actions"] == ["info"]
    assert md["schema"]["properties"]["action"]["enum"] == ["info"]


def test_manifest_validates_and_round_trips():
    original = st.skills_catalog_manifest()
    original.validate()
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()


def test_manifest_helpers_and_names():
    assert st.skills_catalog_names() == ("skills",)
    manifests = st.skills_catalog_manifests()
    assert [m.name for m in manifests] == ["skills"]
    assert st.is_skills_catalog_manifest(st.skills_catalog_manifest()) is True


# --- the per-action risk table ----------------------------------------------


def test_skills_risk_table_covers_exactly_the_declared_actions():
    declared = set(st.skills_catalog_manifest().metadata["actions"])
    assert set(st.SKILLS_ACTION_RISK) == declared


def test_skills_risk_grades():
    R = st.SKILLS_ACTION_RISK
    assert R["info"] is cap.SecurityDanger.SAFE


def test_bundle_posture_is_strongest_action_grade():
    assert (
        st.skills_catalog_manifest().security.danger
        == max(
            (d.value for d in st.SKILLS_ACTION_RISK.values()),
            key=lambda v: {"safe": 0, "caution": 1, "destructive": 2}[v],
        )
    )
    assert st.skills_catalog_manifest().security.danger == cap.SecurityDanger.SAFE.value


def test_action_risk_helper_fails_safe_high_on_unknown():
    assert st.skills_action_risk("totally-unknown") is cap.SecurityDanger.DESTRUCTIVE
    assert st.skills_action_risk("info") is cap.SecurityDanger.SAFE


# --- host seam: correct carrier, injected dummy handler ----------------------


def test_skills_host_is_non_native_in_process():
    sentinel = object()
    h = st.skills_catalog_host(lambda **kw: sentinel)
    assert type(h) is host.BundleHost
    assert not isinstance(h, host.NativeBundleHost)
    assert h.manifest.name == "skills"
    assert h.tools == ("skills",)
    assert h.invoke("skills") is sentinel


def test_native_host_refuses_skills_bundle():
    m = st.skills_catalog_manifest()
    with pytest.raises(BundleHostError):
        host.NativeBundleHost(m, {"skills": lambda **kw: None}, native_authority=True)


def test_host_requires_callable_handler():
    with pytest.raises(BundleHostError):
        st.skills_catalog_host(object())


def test_skills_catalog_hosts_builds_with_correct_carrier():
    hosts = st.skills_catalog_hosts({"skills": lambda **kw: {"s": True}})
    assert set(hosts) == {"skills"}
    assert type(hosts["skills"]) is host.BundleHost
    assert hosts["skills"].invoke("skills") == {"s": True}


def test_skills_catalog_hosts_rejects_missing_handler():
    with pytest.raises(BundleHostError):
        st.skills_catalog_hosts({})


def test_skills_catalog_hosts_rejects_undeclared_handler():
    with pytest.raises(BundleHostError):
        st.skills_catalog_hosts(
            {
                "skills": lambda **kw: None,
                "knowledge": lambda **kw: None,
            }
        )


# --- guard/audit invariant: SAFE posture is a clean pass-through -------------


def test_guard_bridge_safe_surface_is_clean_pass_through():
    manifests = list(st.skills_catalog_manifests())
    for mode in (gb.GuardPolicyMode.BLOCKING, gb.GuardPolicyMode.ADVISORY):
        check = gb.guard_check_from_manifests(manifests, mode=mode)
        decision = check(
            ToolProposal(tool_name="skills", tool_args={"action": "info"})
        )
        assert decision is None, mode


def test_guard_bridge_danger_index_reflects_safe_posture():
    index = gb.tool_danger_index(list(st.skills_catalog_manifests()))
    assert index["skills"] is cap.SecurityDanger.SAFE


# --- import purity / no implementation migration ----------------------------


def test_skill_tools_import_is_pure_and_migrates_no_wrapper():
    code = (
        "import sys, lingtai_sdk.skill_tools as st\n"
        "m = st.skills_catalog_manifest()\n"
        "assert m.name == 'skills' and m.transport.kind == 'in_process'\n"
        "assert m.roles.privileged is False and m.roles.native_only is False\n"
        "assert m.security.danger == 'safe'\n"
        "h = st.skills_catalog_host(lambda **kw: 's')\n"
        "assert h.invoke('skills') == 's'\n"
        "assert st.skills_action_risk('info').value == 'safe'\n"
        "assert st.skills_action_risk('nope').value == 'destructive'\n"
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
