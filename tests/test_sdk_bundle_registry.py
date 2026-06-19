"""Stage 3K: the declared-bundle registry / dispatch-target seam.

Stages 3A–3J declared every SDK capability bundle in its own per-domain module
(``file_tools`` / ``file_mutation_tools`` / ``communication_tools`` /
``mcp_tools`` / ``knowledge_tools`` / ``skill_tools`` / ``bash_tools`` /
``avatar_tools``) plus the privileged core (``core_bundles``). Each exposes its
own ``<domain>_manifests()`` aggregator, but **nothing collected them into one
canonical, name- and tool-indexed view**. ``bundle_registry`` is that missing
connective tissue: a pure aggregation + lookup seam that

* enumerates every declared SDK bundle manifest once, in stable order, and
* indexes them by bundle name and by tool name so a consumer (the stage-17
  guard bridge, a future live runtime's tool router) can ask "which bundle
  declares tool X" without re-deriving the union itself.

It migrates **no** implementation, installs **nothing** live, and changes no
LLM turn behavior — it only *describes* the declared set. Import-pure: importing
it pulls in no ``lingtai`` wrapper module.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lingtai_sdk import bundle_registry as reg
from lingtai_sdk import (
    avatar_tools,
    bash_tools,
    communication_tools,
    core_bundles,
    file_mutation_tools,
    file_tools,
    knowledge_tools,
    mcp_tools,
    skill_tools,
)
from lingtai_sdk.capabilities import BundleManifest, SecurityDanger
from lingtai_sdk.errors import BundleLoadError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

#: The full declared set as of stage 3K, in the registry's stable order.
EXPECTED_BUNDLE_NAMES = (
    # core (privileged, native-only)
    "system",
    "psyche",
    "soul",
    # low-state file read/query
    "read",
    "glob",
    "grep",
    # side-effecting file mutation
    "write",
    "edit",
    # communication / execution
    "email",
    "daemon",
    # tool-config / catalog
    "mcp",
    "knowledge",
    "skills",
    # shell execution
    "bash",
    # peer spawn
    "avatar_spawn",
    "avatar_rules",
)


# --- all_bundle_manifests: the canonical union ----------------------------


def test_all_bundle_manifests_covers_every_declared_domain():
    manifests = reg.all_bundle_manifests()
    assert tuple(m.name for m in manifests) == EXPECTED_BUNDLE_NAMES


def test_all_bundle_manifests_is_the_union_of_the_domain_aggregators():
    """No bundle is invented or dropped relative to the per-domain modules."""
    union = (
        list(core_bundles.core_bundle_manifests())
        + list(file_tools.file_tool_manifests())
        + list(file_mutation_tools.file_mutation_tool_manifests())
        + list(communication_tools.communication_tool_manifests())
        + list(mcp_tools.mcp_config_manifests())
        + list(knowledge_tools.knowledge_catalog_manifests())
        + list(skill_tools.skills_catalog_manifests())
        + list(bash_tools.bash_exec_manifests())
        + list(avatar_tools.avatar_tool_manifests())
    )
    assert {m.name for m in reg.all_bundle_manifests()} == {m.name for m in union}


def test_all_bundle_manifests_does_not_double_count_core_reexports():
    """``lifecycle``/``psyche``/``soul`` tool modules re-export the core
    manifests; the registry must source them once (from ``core_bundles``),
    never twice."""
    names = [m.name for m in reg.all_bundle_manifests()]
    assert names.count("system") == 1
    assert names.count("psyche") == 1
    assert names.count("soul") == 1


def test_every_declared_manifest_validates():
    for m in reg.all_bundle_manifests():
        m.validate()  # raises ValueError if a declaration drifted invalid


# --- BundleRegistry: name + tool indexing ---------------------------------


def test_registry_indexes_by_bundle_name():
    r = reg.default_registry()
    assert r.names() == EXPECTED_BUNDLE_NAMES
    assert r.get("bash").surfaces.tools == ("bash",)
    assert r.get("avatar_spawn").name == "avatar_spawn"
    assert isinstance(r.get("system"), BundleManifest)


def test_registry_get_unknown_bundle_raises():
    r = reg.default_registry()
    with pytest.raises(BundleLoadError, match="no bundle"):
        r.get("nonesuch")


def test_registry_indexes_by_tool_and_dispatches():
    r = reg.default_registry()
    # Every declared bundle here has exactly one tool named after it, except the
    # avatar pair which declares its tool name verbatim.
    assert r.bundle_for_tool("write").name == "write"
    assert r.bundle_for_tool("avatar_rules").name == "avatar_rules"
    target = r.dispatch_target("mcp")
    assert target.bundle_name == "mcp"
    assert target.manifest.name == "mcp"
    assert target.danger is SecurityDanger.SAFE


def test_registry_tool_names_match_declared_tools():
    r = reg.default_registry()
    declared = {t for m in reg.all_bundle_manifests() for t in m.surfaces.tools}
    assert set(r.tool_names()) == declared


def test_registry_unknown_tool_lookup_raises():
    r = reg.default_registry()
    with pytest.raises(BundleLoadError, match="no bundle hosts tool"):
        r.bundle_for_tool("not_a_tool")
    with pytest.raises(BundleLoadError, match="no bundle hosts tool"):
        r.dispatch_target("not_a_tool")


def test_registry_dispatch_target_carries_declared_danger():
    r = reg.default_registry()
    assert r.dispatch_target("system").danger is SecurityDanger.DESTRUCTIVE
    assert r.dispatch_target("soul").danger is SecurityDanger.CAUTION
    assert r.dispatch_target("read").danger is SecurityDanger.SAFE


# --- conflict detection (the registry's invariant) ------------------------


def test_registry_rejects_duplicate_bundle_name():
    m = mcp_tools.mcp_config_manifest()
    with pytest.raises(BundleLoadError, match="duplicate bundle name"):
        reg.BundleRegistry([m, m])


def test_registry_rejects_two_bundles_declaring_the_same_tool():
    a = mcp_tools.mcp_config_manifest()
    # A second, differently-named bundle that also declares the ``mcp`` tool.
    b = BundleManifest(
        name="rogue",
        version="0.0.1",
        surfaces=type(a.surfaces)(tools=("mcp",)),
        transport=a.transport,
    )
    with pytest.raises(BundleLoadError, match="tool 'mcp'"):
        reg.BundleRegistry([a, b])


def test_registry_validates_each_manifest_on_construction():
    bad = BundleManifest(name="", version="0.0.1")
    with pytest.raises(BundleLoadError):
        reg.BundleRegistry([bad])


def test_default_registry_is_consistent_with_guard_bridge_index():
    """The registry's tool set must equal the guard bridge's danger index over
    the same manifests — they are two views of one declared union."""
    from lingtai_sdk import guard_bridge as gb

    r = reg.default_registry()
    index = gb.tool_danger_index(reg.all_bundle_manifests())
    assert set(index) == set(r.tool_names())
    for tool, danger in index.items():
        assert r.dispatch_target(tool).danger is danger


# --- import purity ---------------------------------------------------------


def test_registry_is_reachable_from_package_root_lazily_and_wrapper_free():
    code = (
        "import sys, lingtai_sdk\n"
        "# touching only kernel/eager names so far -> no wrapper loaded\n"
        "r = lingtai_sdk.default_registry()\n"
        "assert r.dispatch_target('daemon').bundle_name == 'daemon'\n"
        "assert lingtai_sdk.BundleRegistry is not None\n"
        "assert len(lingtai_sdk.all_bundle_manifests()) == 16\n"
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


def test_bundle_registry_import_is_wrapper_free():
    code = (
        "import sys\n"
        "from lingtai_sdk import bundle_registry as reg\n"
        "r = reg.default_registry()\n"
        "assert r.dispatch_target('bash').danger.value == 'destructive'\n"
        "assert len(reg.all_bundle_manifests()) == 16\n"
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
