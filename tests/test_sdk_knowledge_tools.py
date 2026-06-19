"""Stage-3G proof: the ``knowledge`` catalog bundle declaration + seam.

The catalog counterpart of ``test_sdk_mcp_tools.py`` (stage 3F). These tests
assert that:

* the ``knowledge`` manifest is non-privileged + in-process (capability-carried),
  matching how the live ``setup()`` (``agent.add_tool``) path carries it, and
  declares the bounded-side-effect / configuration / catalog metadata posture;
* the manifest validates strictly and round-trips through ``load_manifest``;
* the **per-action risk table** (``KNOWLEDGE_ACTION_RISK``) covers exactly the
  declared actions (``info`` only), grades them faithfully (``info`` → CAUTION), and
  the bundle-level posture equals the strongest action's grade (CAUTION);
* the host seam hosts the surface with its correct carrier — a non-native
  ``BundleHost`` — with an injected dummy handler, and the native host refuses it;
* the **guard/audit invariant** holds: feeding the CAUTION manifest to the stage-17
  ``guard_bridge`` allows with a warning (no deny) — *without* this
  stage installing any guard.

Crucially, **no real ``knowledge`` is called or imported from the SDK**: every
handler here is a dummy, and a subprocess asserts importing ``knowledge_tools``
pulls in no ``lingtai`` wrapper module. The wrapper-side bridge (which hosts the
real handler) is tested in ``tests/test_knowledge_bundle_bridge.py``.
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
from lingtai_sdk import knowledge_tools as kt
from lingtai_sdk.errors import BundleHostError

from lingtai.kernel.tool_call_guard import ToolProposal

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


# --- manifest: identity, posture, carrier -----------------------------------


def test_knowledge_manifest_non_privileged_in_process():
    m = kt.knowledge_catalog_manifest()
    assert m.name == kt.KNOWLEDGE_TOOL_NAME == "knowledge"
    # knowledge is a wrapper capability carried in-process (add_tool), not privileged.
    assert m.roles.required is False
    assert m.roles.privileged is False
    assert m.roles.native_only is False
    assert m.roles.backend_replaceability is cap.BackendReplaceability.REPLACEABLE
    assert m.transport.kind == cap.TransportKind.IN_PROCESS.value
    # bounded legacy-migration side effect -> CAUTION bundle posture.
    assert m.security.danger == cap.SecurityDanger.CAUTION.value
    assert m.surfaces.tools == ("knowledge",)


def test_knowledge_manifest_declares_config_catalog_metadata():
    md = kt.knowledge_catalog_manifest().metadata
    assert md["config"] is True
    assert md["catalog"] is True
    assert md["read_only"] is False
    assert md["migrates_legacy_json"] is True
    assert md["agent_state_sensitive"] is True
    assert md["actions"] == ["info"]
    # a language-neutral copy of the live schema's action enum.
    assert md["schema"]["properties"]["action"]["enum"] == ["info"]


def test_manifest_validates_and_round_trips():
    original = kt.knowledge_catalog_manifest()
    original.validate()  # does not raise
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()


def test_manifest_helpers_and_names():
    assert kt.knowledge_catalog_names() == ("knowledge",)
    manifests = kt.knowledge_catalog_manifests()
    assert [m.name for m in manifests] == ["knowledge"]
    assert kt.is_knowledge_catalog_manifest(kt.knowledge_catalog_manifest()) is True


# --- the per-action risk table ----------------------------------------------


def test_knowledge_risk_table_covers_exactly_the_declared_actions():
    declared = set(kt.knowledge_catalog_manifest().metadata["actions"])
    assert set(kt.KNOWLEDGE_ACTION_RISK) == declared


def test_knowledge_risk_grades():
    R = kt.KNOWLEDGE_ACTION_RISK
    # the one catalog-view action can perform bounded legacy-migration writes.
    assert R["info"] is cap.SecurityDanger.CAUTION


def test_bundle_posture_is_strongest_action_grade():
    assert (
        kt.knowledge_catalog_manifest().security.danger
        == max(
            (d.value for d in kt.KNOWLEDGE_ACTION_RISK.values()),
            key=lambda v: {"safe": 0, "caution": 1, "destructive": 2}[v],
        )
    )
    assert (
        kt.knowledge_catalog_manifest().security.danger
        == cap.SecurityDanger.CAUTION.value
    )


def test_action_risk_helper_fails_safe_high_on_unknown():
    # an unknown action fails safe HIGH (destructive), matching the other
    # stage-3 action-risk helpers rather than silently treating it as safe.
    assert kt.knowledge_action_risk("totally-unknown") is cap.SecurityDanger.DESTRUCTIVE
    # the known action returns its graded posture.
    assert kt.knowledge_action_risk("info") is cap.SecurityDanger.CAUTION


# --- host seam: correct carrier, injected dummy handler ----------------------


def test_knowledge_host_is_non_native_in_process():
    sentinel = object()
    h = kt.knowledge_catalog_host(lambda **kw: sentinel)
    assert type(h) is host.BundleHost
    assert not isinstance(h, host.NativeBundleHost)
    assert h.manifest.name == "knowledge"
    assert h.tools == ("knowledge",)
    assert h.invoke("knowledge") is sentinel


def test_native_host_refuses_knowledge_bundle():
    # knowledge is in-process -> NativeBundleHost (native transport only) must refuse.
    m = kt.knowledge_catalog_manifest()
    with pytest.raises(BundleHostError):
        host.NativeBundleHost(
            m, {"knowledge": lambda **kw: None}, native_authority=True
        )


def test_host_requires_callable_handler():
    with pytest.raises(BundleHostError):
        kt.knowledge_catalog_host(object())


def test_knowledge_catalog_hosts_builds_with_correct_carrier():
    hosts = kt.knowledge_catalog_hosts({"knowledge": lambda **kw: {"k": True}})
    assert set(hosts) == {"knowledge"}
    assert type(hosts["knowledge"]) is host.BundleHost
    assert hosts["knowledge"].invoke("knowledge") == {"k": True}


def test_knowledge_catalog_hosts_rejects_missing_handler():
    with pytest.raises(BundleHostError):
        kt.knowledge_catalog_hosts({})


def test_knowledge_catalog_hosts_rejects_undeclared_handler():
    with pytest.raises(BundleHostError):
        kt.knowledge_catalog_hosts(
            {
                "knowledge": lambda **kw: None,
                "skills": lambda **kw: None,
            }
        )


# --- guard/audit invariant: CAUTION posture warns without blocking ------------


def test_guard_bridge_caution_surface_warns_without_blocking():
    manifests = list(kt.knowledge_catalog_manifests())
    for mode in (gb.GuardPolicyMode.BLOCKING, gb.GuardPolicyMode.ADVISORY):
        check = gb.guard_check_from_manifests(manifests, mode=mode)
        decision = check(
            ToolProposal(tool_name="knowledge", tool_args={"action": "info"})
        )
        assert decision is not None, mode
        assert decision.allowed is True
        assert decision.action == "warn"
        assert decision.severity == "warning"
        assert decision.metadata["danger"] == "caution"


def test_guard_bridge_danger_index_reflects_caution_posture():
    index = gb.tool_danger_index(list(kt.knowledge_catalog_manifests()))
    assert index["knowledge"] is cap.SecurityDanger.CAUTION


# --- import purity / no implementation migration ----------------------------


def test_knowledge_tools_import_is_pure_and_migrates_no_wrapper():
    code = (
        "import sys, lingtai_sdk.knowledge_tools as kt\n"
        "m = kt.knowledge_catalog_manifest()\n"
        "assert m.name == 'knowledge' and m.transport.kind == 'in_process'\n"
        "assert m.roles.privileged is False and m.roles.native_only is False\n"
        "assert m.security.danger == 'caution'\n"
        "h = kt.knowledge_catalog_host(lambda **kw: 'k')\n"
        "assert h.invoke('knowledge') == 'k'\n"
        "assert kt.knowledge_action_risk('info').value == 'caution'\n"
        "assert kt.knowledge_action_risk('nope').value == 'destructive'\n"
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
