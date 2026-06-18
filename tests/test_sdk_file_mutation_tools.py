"""Stage-3B proof: the side-effecting file-mutation bundle declarations + seam.

The side-effecting counterpart of ``test_sdk_file_tools.py``. These tests assert
the shared low-state posture of the two file-mutation manifests (``write`` /
``edit``), their **distinct danger postures** (``write`` destructive, ``edit``
caution), that they validate strictly and round-trip through ``load_manifest``,
that the non-native ``BundleHost`` accepts them (danger is a declaration, not a
host gate), that the host seam wires only injected handlers, stable ordering, and
import purity.

It also pins the **guard/audit invariant**: feeding the write/edit manifests to
the stage-17 ``guard_bridge`` derives the expected gate — ``write`` denied in
BLOCKING / warned in ADVISORY, ``edit`` always warned — *without* this stage
installing any guard. That is the side-effect posture's observable consequence.

Crucially, **no real ``write`` / ``edit`` is called or imported**: every handler
here is a dummy, and a subprocess asserts importing ``file_mutation_tools`` pulls
in no ``lingtai`` wrapper module. The wrapper-side bridge is tested in
``tests/test_file_bundle_bridge.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from lingtai_sdk import capabilities as cap
from lingtai_sdk import capability_host as host
from lingtai_sdk import file_mutation_tools as fmt
from lingtai_sdk import guard_bridge as gb
from lingtai_sdk.errors import BundleHostError

# The guard bridge maps a manifest's danger posture onto kernel guard
# primitives; ToolProposal is the kernel-side type the resulting check consumes.
from lingtai_kernel.tool_call_guard import ToolProposal

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

FILE_MUTATION_NAMES = ("write", "edit")
_BUILDERS = {
    "write": fmt.write_bundle,
    "edit": fmt.edit_bundle,
}
_EXPECTED_DANGER = {
    "write": cap.SecurityDanger.DESTRUCTIVE.value,
    "edit": cap.SecurityDanger.CAUTION.value,
}


# --- shared low-state posture across both ---------------------------------


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_manifest_non_privileged_in_process(name):
    m = _BUILDERS[name]()
    assert m.name == name
    assert m.roles.required is False
    assert m.roles.privileged is False
    assert m.roles.native_only is False
    assert m.roles.backend_replaceability is cap.BackendReplaceability.REPLACEABLE
    assert m.transport.kind == cap.TransportKind.IN_PROCESS.value


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_manifest_declares_only_its_public_tool(name):
    m = _BUILDERS[name]()
    assert m.surfaces.tools == (name,)
    assert m.surfaces.resources == ()
    assert m.surfaces.prompts == ()
    assert m.surfaces.events == ()
    assert m.surfaces.hooks == ()


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_manifest_validates_strictly(name):
    _BUILDERS[name]().validate()  # does not raise


# --- the distinct danger postures (the heart of stage 3B) ------------------


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_danger_posture(name):
    danger = _BUILDERS[name]().security.danger
    assert danger == _EXPECTED_DANGER[name]
    cap.SecurityDanger(danger)  # allow-listed


def test_write_is_destructive_and_edit_is_caution():
    # explicit, named assertion of the posture distinction.
    assert fmt.write_bundle().security.danger == cap.SecurityDanger.DESTRUCTIVE.value
    assert fmt.edit_bundle().security.danger == cap.SecurityDanger.CAUTION.value
    # and neither is SAFE (they are side-effecting, unlike read/glob/grep).
    assert fmt.write_bundle().security.danger != cap.SecurityDanger.SAFE.value
    assert fmt.edit_bundle().security.danger != cap.SecurityDanger.SAFE.value


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_metadata_marks_side_effect_and_carries_schema(name):
    md = _BUILDERS[name]().metadata
    assert md.get("file_tool") is True
    assert md.get("side_effect") is True  # distinguishes from read-only file_tools
    assert isinstance(md.get("role"), str) and md["role"]
    assert isinstance(md.get("actions"), list) and md["actions"]
    schema = md.get("schema")
    assert isinstance(schema, dict)
    assert schema.get("type") == "object"
    assert "properties" in schema


def test_file_mutation_schemas_match_the_wrapper_required_fields():
    # the embedded declaration mirrors the wrapper tool's required args.
    assert fmt.write_bundle().metadata["schema"]["required"] == ["file_path", "content"]
    assert fmt.edit_bundle().metadata["schema"]["required"] == [
        "file_path",
        "old_string",
        "new_string",
    ]


# --- stable ordering -------------------------------------------------------


def test_file_mutation_manifests_stable_order():
    manifests = fmt.file_mutation_tool_manifests()
    assert tuple(m.name for m in manifests) == FILE_MUTATION_NAMES
    assert tuple(m.name for m in fmt.file_mutation_tool_manifests()) == FILE_MUTATION_NAMES


def test_file_mutation_names_matches_manifests():
    assert fmt.file_mutation_tool_names() == FILE_MUTATION_NAMES


def test_is_file_mutation_manifest():
    for m in fmt.file_mutation_tool_manifests():
        assert fmt.is_file_mutation_manifest(m) is True
    assert fmt.is_file_mutation_manifest(cap.proof_bundle()) is False


# --- load_manifest round-trip ----------------------------------------------


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_manifest_round_trips_through_loader(name):
    original = _BUILDERS[name]()
    loaded = cap.load_manifest(original.to_dict())
    assert loaded.to_dict() == original.to_dict()
    assert loaded.roles.privileged is False
    assert loaded.transport.kind == "in_process"
    assert loaded.security.danger == _EXPECTED_DANGER[name]


# --- non-native BundleHost ACCEPTS both (danger is a declaration, not a gate) --


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_bundle_host_accepts_file_mutation_bundle(name):
    # a non-native host hosts these directly: a destructive declaration does NOT
    # make a non-privileged in_process bundle unhostable. Gating is the guard
    # bridge's job, not the host's.
    m = _BUILDERS[name]()
    sentinel = object()
    h = fmt.file_mutation_tool_host(m, lambda **kw: sentinel)
    assert isinstance(h, host.BundleHost)
    assert h.manifest.name == name
    assert h.tools == (name,)
    assert h.invoke(name) is sentinel


@pytest.mark.parametrize("name", FILE_MUTATION_NAMES)
def test_file_mutation_tool_host_requires_callable_handler(name):
    m = _BUILDERS[name]()
    with pytest.raises(BundleHostError):
        fmt.file_mutation_tool_host(m, object())  # not callable


def test_file_mutation_tool_host_rejects_non_file_mutation_manifest():
    proof = cap.proof_bundle()
    with pytest.raises(BundleHostError):
        fmt.file_mutation_tool_host(proof, lambda **kw: None)


# --- host seam builds both from injected handlers --------------------------


def _dummy_handlers():
    return {name: (lambda _n=name, **kw: {"name": _n}) for name in FILE_MUTATION_NAMES}


def test_file_mutation_tool_hosts_builds_both():
    hosts = fmt.file_mutation_tool_hosts(_dummy_handlers())
    assert set(hosts) == set(FILE_MUTATION_NAMES)
    for name, h in hosts.items():
        assert isinstance(h, host.BundleHost)
        assert h.manifest.roles.privileged is False
        assert h.manifest.transport.kind == "in_process"
        assert h.invoke(name) == {"name": name}


def test_file_mutation_tool_hosts_rejects_missing_handler():
    handlers = _dummy_handlers()
    del handlers["edit"]
    with pytest.raises(BundleHostError):
        fmt.file_mutation_tool_hosts(handlers)


def test_file_mutation_tool_hosts_rejects_undeclared_handler():
    handlers = _dummy_handlers()
    handlers["stowaway"] = lambda **kw: None
    with pytest.raises(BundleHostError):
        fmt.file_mutation_tool_hosts(handlers)


def test_file_mutation_tool_hosts_rejects_non_callable_handler():
    handlers = _dummy_handlers()
    handlers["write"] = object()
    with pytest.raises(BundleHostError):
        fmt.file_mutation_tool_hosts(handlers)


def test_file_mutation_tool_hosts_are_all_non_native_in_process():
    hosts = fmt.file_mutation_tool_hosts(_dummy_handlers())
    for h in hosts.values():
        assert type(h) is host.BundleHost
        assert not isinstance(h, host.NativeBundleHost)


# --- guard/audit invariant: posture flows through the stage-17 guard bridge --


def test_guard_bridge_blocks_write_allows_edit_in_blocking_mode():
    manifests = fmt.file_mutation_tool_manifests()
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.BLOCKING)

    write_decision = check(ToolProposal(tool_name="write", tool_args={}))
    edit_decision = check(ToolProposal(tool_name="edit", tool_args={}))

    # write is destructive -> BLOCKING denies it before dispatch.
    assert write_decision is not None
    assert write_decision.allowed is False
    assert write_decision.metadata.get("danger") == cap.SecurityDanger.DESTRUCTIVE.value
    assert write_decision.metadata.get("bundle") == "write"

    # edit is caution -> allowed but warned, in both modes.
    assert edit_decision is not None
    assert edit_decision.allowed is True
    assert edit_decision.action == "warn"
    assert edit_decision.metadata.get("danger") == cap.SecurityDanger.CAUTION.value


def test_guard_bridge_advisory_mode_warns_write_instead_of_denying():
    manifests = fmt.file_mutation_tool_manifests()
    check = gb.guard_check_from_manifests(manifests, mode=gb.GuardPolicyMode.ADVISORY)

    write_decision = check(ToolProposal(tool_name="write", tool_args={}))
    # destructive in ADVISORY mode -> allowed but warned, never denied.
    assert write_decision is not None
    assert write_decision.allowed is True
    assert write_decision.action == "warn"


def test_guard_bridge_danger_index_reflects_postures():
    index = gb.tool_danger_index(fmt.file_mutation_tool_manifests())
    assert index["write"] is cap.SecurityDanger.DESTRUCTIVE
    assert index["edit"] is cap.SecurityDanger.CAUTION


# --- import purity / no implementation migration ---------------------------


def test_file_mutation_tools_import_is_pure_and_migrates_nothing():
    code = (
        "import sys, lingtai_sdk.file_mutation_tools as fmt\n"
        "manifests = fmt.file_mutation_tool_manifests()\n"
        "assert tuple(m.name for m in manifests) == ('write', 'edit')\n"
        "assert manifests[0].security.danger == 'destructive'\n"
        "assert manifests[1].security.danger == 'caution'\n"
        "h = fmt.file_mutation_tool_host(fmt.write_bundle(), lambda **kw: 'dummy')\n"
        "assert h.invoke('write') == 'dummy'\n"
        # importing file_mutation_tools must NOT pull in the lingtai wrapper, i.e.
        # the real write/edit implementation is not migrated/imported.
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
