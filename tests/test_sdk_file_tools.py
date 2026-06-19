"""Stage-3A proof: the low-state file-tool bundle declarations + host seam.

The non-privileged, in-process mirror of ``test_sdk_core_bundles.py``. These
tests assert the shared non-privileged posture of the three file-tool manifests
(``read`` / ``glob`` / ``grep``), that they validate strictly and round-trip
through ``load_manifest``, that the non-native ``BundleHost`` *accepts* them
(unlike the privileged core), that the host seam wires only injected handlers,
stable ordering, and import purity.

Crucially, **no real ``read`` / ``glob`` / ``grep`` is called or imported**:
every handler here is a dummy, and a subprocess asserts importing
``file_tools`` pulls in no ``lingtai`` wrapper module (the implementation stays
in the wrapper; the wrapper-side bridge is tested in
``tests/test_file_bundle_bridge.py``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lingtai_sdk import capabilities as cap
from lingtai_sdk import capability_host as host
from lingtai_sdk import file_tools as ft
from lingtai_sdk.errors import BundleHostError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

FILE_TOOL_NAMES = ("read", "glob", "grep")
_BUILDERS = {
    "read": ft.read_bundle,
    "glob": ft.glob_bundle,
    "grep": ft.grep_bundle,
}


# --- shared non-privileged posture across all three -----------------------


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_manifest_non_privileged_in_process(name):
    m = _BUILDERS[name]()
    assert m.name == name
    assert m.roles.required is False
    assert m.roles.privileged is False
    assert m.roles.native_only is False
    assert m.roles.backend_replaceability is cap.BackendReplaceability.REPLACEABLE
    assert m.transport.kind == cap.TransportKind.IN_PROCESS.value


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_manifest_declares_only_its_public_tool(name):
    m = _BUILDERS[name]()
    assert m.surfaces.tools == (name,)
    assert m.surfaces.resources == ()
    assert m.surfaces.prompts == ()
    assert m.surfaces.events == ()
    assert m.surfaces.hooks == ()


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_manifest_validates_strictly(name):
    _BUILDERS[name]().validate()  # does not raise


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_danger_is_safe(name):
    # all three are read-only / query surfaces.
    assert _BUILDERS[name]().security.danger == cap.SecurityDanger.SAFE.value
    cap.SecurityDanger(_BUILDERS[name]().security.danger)  # allow-listed


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_metadata_carries_schema_and_role(name):
    md = _BUILDERS[name]().metadata
    assert md.get("file_tool") is True
    assert isinstance(md.get("role"), str) and md["role"]
    assert isinstance(md.get("actions"), list) and md["actions"]
    schema = md.get("schema")
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert "properties" in schema


def test_file_tool_schemas_match_the_wrapper_required_fields():
    # the embedded declaration mirrors the wrapper tool's required args, so a
    # host inspecting the manifest sees the real argument contract.
    assert ft.read_bundle().metadata["schema"]["required"] == ["file_path"]
    assert ft.glob_bundle().metadata["schema"]["required"] == ["pattern"]
    assert ft.grep_bundle().metadata["schema"]["required"] == ["pattern"]


# --- stable ordering -------------------------------------------------------


def test_file_tool_manifests_stable_order():
    manifests = ft.file_tool_manifests()
    assert tuple(m.name for m in manifests) == FILE_TOOL_NAMES
    assert tuple(m.name for m in ft.file_tool_manifests()) == FILE_TOOL_NAMES


def test_file_tool_names_matches_manifests():
    assert ft.file_tool_names() == FILE_TOOL_NAMES


def test_is_file_tool_manifest():
    for m in ft.file_tool_manifests():
        assert ft.is_file_tool_manifest(m) is True
    assert ft.is_file_tool_manifest(cap.proof_bundle()) is False


# --- load_manifest round-trip ---------------------------------------------


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_manifest_round_trips_through_loader(name):
    original = _BUILDERS[name]()
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()
    assert loaded.roles.privileged is False
    assert loaded.transport.kind == "in_process"


# --- non-native BundleHost ACCEPTS every file-tool bundle ------------------


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_bundle_host_accepts_file_tool_bundle(name):
    # unlike the privileged core, a non-native host hosts these directly.
    m = _BUILDERS[name]()
    sentinel = object()
    h = ft.file_tool_host(m, lambda **kw: sentinel)
    assert isinstance(h, host.BundleHost)
    assert h.manifest.name == name
    assert h.tools == (name,)
    assert h.invoke(name) is sentinel


@pytest.mark.parametrize("name", FILE_TOOL_NAMES)
def test_file_tool_host_requires_callable_handler(name):
    m = _BUILDERS[name]()
    with pytest.raises(BundleHostError):
        ft.file_tool_host(m, object())  # not callable


def test_file_tool_host_rejects_non_file_tool_manifest():
    proof = cap.proof_bundle()
    with pytest.raises(BundleHostError):
        ft.file_tool_host(proof, lambda **kw: None)


# --- host seam builds all three from injected handlers ---------------------


def _dummy_handlers():
    return {name: (lambda _n=name, **kw: {"name": _n}) for name in FILE_TOOL_NAMES}


def test_file_tool_hosts_builds_all_three():
    hosts = ft.file_tool_hosts(_dummy_handlers())
    assert set(hosts) == set(FILE_TOOL_NAMES)
    for name, h in hosts.items():
        assert isinstance(h, host.BundleHost)
        assert h.manifest.roles.privileged is False
        assert h.manifest.transport.kind == "in_process"
        assert h.invoke(name) == {"name": name}


def test_file_tool_hosts_rejects_missing_handler():
    handlers = _dummy_handlers()
    del handlers["grep"]
    with pytest.raises(BundleHostError):
        ft.file_tool_hosts(handlers)


def test_file_tool_hosts_rejects_undeclared_handler():
    handlers = _dummy_handlers()
    handlers["stowaway"] = lambda **kw: None
    with pytest.raises(BundleHostError):
        ft.file_tool_hosts(handlers)


def test_file_tool_hosts_rejects_non_callable_handler():
    handlers = _dummy_handlers()
    handlers["read"] = object()
    with pytest.raises(BundleHostError):
        ft.file_tool_hosts(handlers)


def test_file_tool_hosts_are_all_non_native_in_process():
    hosts = ft.file_tool_hosts(_dummy_handlers())
    for h in hosts.values():
        # the host type is the in-process non-native host, never the native one.
        assert type(h) is host.BundleHost
        assert not isinstance(h, host.NativeBundleHost)


# --- import purity / no implementation migration ---------------------------


def test_file_tools_import_is_pure_and_migrates_nothing():
    code = (
        "import sys, lingtai_sdk.file_tools as ft\n"
        "manifests = ft.file_tool_manifests()\n"
        "assert tuple(m.name for m in manifests) == ('read', 'glob', 'grep')\n"
        "h = ft.file_tool_host(ft.read_bundle(), lambda **kw: 'dummy')\n"
        "assert h.invoke('read') == 'dummy'\n"
        # importing file_tools must NOT pull in the lingtai wrapper, i.e. the
        # real read/glob/grep implementation is not migrated/imported.
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
