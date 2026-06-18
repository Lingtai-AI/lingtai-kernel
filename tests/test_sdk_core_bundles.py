"""Stage-8 proof: the core bundle manifests + native adapter shim.

The first deliberate contact with the privileged core surfaces (``system`` /
``psyche`` / ``soul``) — but **only** as a manifest contract plus a
stub-injection seam. These tests assert the shared privileged posture of all
three manifests, that they validate strictly (stage-7 contract), that the
non-native ``BundleHost`` refuses them, that the native adapter hosts them only
with explicit native authority and *injected dummy* handlers, stable ordering,
``load_manifest`` round-trip, and import purity.

Crucially, **no real ``system`` / ``psyche`` / ``soul`` is called or imported**:
every handler here is a dummy lambda, and a subprocess asserts importing
``core_bundles`` pulls in no ``lingtai`` wrapper module (the implementation is
not migrated).
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
from lingtai_sdk.errors import BundleHostError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

CORE_NAMES = ("system", "psyche", "soul")
_BUILDERS = {
    "system": core.system_bundle,
    "psyche": core.psyche_bundle,
    "soul": core.soul_bundle,
}


# --- shared privileged posture across all three ---------------------------


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_manifest_required_privileged_native_only(name):
    m = _BUILDERS[name]()
    assert m.name == name
    assert m.roles.required is True
    assert m.roles.privileged is True
    assert m.roles.native_only is True
    assert m.roles.backend_replaceability is cap.BackendReplaceability.NATIVE_ONLY
    assert m.transport.kind == cap.TransportKind.NATIVE.value


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_manifest_declares_only_its_public_tool(name):
    m = _BUILDERS[name]()
    # exactly the one public tool, named after the bundle; no other surfaces.
    assert m.surfaces.tools == (name,)
    assert m.surfaces.resources == ()
    assert m.surfaces.prompts == ()
    assert m.surfaces.events == ()
    assert m.surfaces.hooks == ()


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_manifest_validates_strictly(name):
    # stage-7 strict validation must pass for every core manifest.
    _BUILDERS[name]().validate()  # does not raise


def test_core_danger_postures():
    # system is highest-risk; psyche and soul both persist protected preferences/state.
    assert core.system_bundle().security.danger == cap.SecurityDanger.DESTRUCTIVE.value
    assert core.psyche_bundle().security.danger == cap.SecurityDanger.DESTRUCTIVE.value
    assert core.soul_bundle().security.danger == cap.SecurityDanger.CAUTION.value


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_danger_is_allow_listed(name):
    # every declared danger is a member of the strict stage-7 allow-list.
    cap.SecurityDanger(_BUILDERS[name]().security.danger)


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_metadata_is_helpful_and_non_secret(name):
    md = _BUILDERS[name]().metadata
    assert md.get("core") is True
    assert isinstance(md.get("role"), str) and md["role"]
    assert isinstance(md.get("actions"), list) and md["actions"]


# --- stable ordering -------------------------------------------------------


def test_core_bundle_manifests_stable_order():
    manifests = core.core_bundle_manifests()
    assert tuple(m.name for m in manifests) == CORE_NAMES
    # stable across calls
    assert tuple(m.name for m in core.core_bundle_manifests()) == CORE_NAMES


def test_core_bundle_names_matches_manifests():
    assert core.core_bundle_names() == CORE_NAMES


def test_is_core_manifest():
    for m in core.core_bundle_manifests():
        assert core.is_core_manifest(m) is True
    assert core.is_core_manifest(cap.proof_bundle()) is False


# --- load_manifest round-trip ---------------------------------------------


@pytest.mark.parametrize("name", CORE_NAMES)
def test_core_manifest_round_trips_through_loader(name):
    original = _BUILDERS[name]()
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()
    # the privileged posture survives the round trip
    assert loaded.roles.privileged is True
    assert loaded.roles.native_only is True
    assert loaded.roles.backend_replaceability is cap.BackendReplaceability.NATIVE_ONLY


# --- non-native BundleHost refuses every core bundle -----------------------


@pytest.mark.parametrize("name", CORE_NAMES)
def test_bundle_host_refuses_core_bundle(name):
    m = _BUILDERS[name]()
    with pytest.raises(BundleHostError):
        host.BundleHost(m, {name: lambda **kw: None})


# --- native adapter hosts a core bundle with injected dummy handler --------


@pytest.mark.parametrize("name", CORE_NAMES)
def test_native_core_host_with_injected_dummy(name):
    m = _BUILDERS[name]()
    sentinel = object()
    h = core.native_core_host(m, lambda **kw: sentinel)
    assert isinstance(h, host.NativeBundleHost)
    assert h.manifest.name == name
    assert h.tools == (name,)
    # the host invokes the *injected* dummy, never a real implementation.
    assert h.invoke(name) is sentinel


@pytest.mark.parametrize("name", CORE_NAMES)
def test_native_core_host_requires_callable_handler(name):
    m = _BUILDERS[name]()
    with pytest.raises(BundleHostError):
        core.native_core_host(m, object())  # not callable


def test_native_core_host_rejects_non_core_manifest():
    # a non-core (even privileged-native) manifest is refused by the adapter.
    proof = host.native_privileged_proof_bundle()
    with pytest.raises(BundleHostError):
        core.native_core_host(proof, lambda **kw: None)


# --- native adapter builds all three from injected handlers ----------------


def _dummy_handlers():
    # bind `name` per-handler (default arg) to avoid late-binding closure capture.
    return {name: (lambda _n=name, **kw: {"name": _n}) for name in CORE_NAMES}


def test_native_core_hosts_builds_all_three():
    hosts = core.native_core_hosts(_dummy_handlers())
    assert set(hosts) == set(CORE_NAMES)
    for name, h in hosts.items():
        assert isinstance(h, host.NativeBundleHost)
        assert h.manifest.roles.privileged is True
        assert h.manifest.transport.kind == "native"
        # invokes the injected dummy only
        assert h.invoke(name) == {"name": name}


def test_native_core_hosts_rejects_missing_handler():
    handlers = _dummy_handlers()
    del handlers["soul"]
    with pytest.raises(BundleHostError):
        core.native_core_hosts(handlers)


def test_native_core_hosts_rejects_undeclared_handler():
    handlers = _dummy_handlers()
    handlers["stowaway"] = lambda **kw: None
    with pytest.raises(BundleHostError):
        core.native_core_hosts(handlers)


def test_native_core_hosts_rejects_non_callable_handler():
    handlers = _dummy_handlers()
    handlers["system"] = object()
    with pytest.raises(BundleHostError):
        core.native_core_hosts(handlers)


# --- no non-native host is ever produced for a core bundle -----------------


def test_native_core_hosts_are_all_native_authority():
    hosts = core.native_core_hosts(_dummy_handlers())
    for h in hosts.values():
        # the host type is the native-authority host, never the in-process one
        # (NativeBundleHost and BundleHost are sibling subclasses, not related).
        assert type(h) is host.NativeBundleHost
        assert not isinstance(h, host.BundleHost)


# --- import purity / no implementation migration ---------------------------


def test_core_bundles_import_is_pure_and_migrates_nothing():
    code = (
        "import sys, lingtai_sdk.core_bundles as core\n"
        "manifests = core.core_bundle_manifests()\n"
        "assert tuple(m.name for m in manifests) == ('system', 'psyche', 'soul')\n"
        "h = core.native_core_host(core.system_bundle(), lambda **kw: 'dummy')\n"
        "assert h.invoke('system') == 'dummy'\n"
        # importing core_bundles must NOT pull in the lingtai wrapper, i.e. the
        # real system/psyche/soul implementation is not migrated/imported.
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
