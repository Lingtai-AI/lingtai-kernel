"""SDK-01 + SDK-02 invariants — the ``lingtai_sdk`` namespace and the file-I/O peel.

These tests pin the compatibility contract of the first SDK slice:

* the capability registry resolves file tools to ``lingtai_sdk`` module paths;
* the historical ``lingtai.*`` import paths still work (shims);
* the service shims preserve *module identity* so monkeypatch-based code keeps
  operating on the real implementation module.
"""
from __future__ import annotations

import importlib

import pytest


# ---------------------------------------------------------------------------
# Registry resolves file tools to the SDK
# ---------------------------------------------------------------------------

def test_registry_file_tools_point_at_sdk():
    from lingtai.capabilities import _BUILTIN

    for name in ("read", "write", "edit", "glob", "grep"):
        assert _BUILTIN[name] == f"lingtai_sdk.capabilities.file.{name}", (
            f"{name} should resolve into lingtai_sdk, got {_BUILTIN[name]!r}"
        )


def test_file_group_unchanged():
    from lingtai.capabilities import _GROUPS

    assert _GROUPS["file"] == ["read", "write", "edit", "glob", "grep"]


def test_get_all_providers_file_resolves_via_sdk():
    from lingtai.capabilities import get_all_providers

    providers = get_all_providers()
    # "file" provider metadata must still be discoverable after the move.
    assert "file" in providers


# ---------------------------------------------------------------------------
# SDK modules are importable directly
# ---------------------------------------------------------------------------

def test_sdk_package_importable():
    import lingtai_sdk  # noqa: F401
    import lingtai_sdk.capabilities  # noqa: F401
    import lingtai_sdk.capabilities.file.read  # noqa: F401
    import lingtai_sdk.services.file_io  # noqa: F401
    import lingtai_sdk.services.file_io_sidecar  # noqa: F401


@pytest.mark.parametrize("name", ["read", "write", "edit", "glob", "grep"])
def test_sdk_file_capability_exports_setup(name):
    mod = importlib.import_module(f"lingtai_sdk.capabilities.file.{name}")
    assert callable(getattr(mod, "setup", None)), f"{name} must export setup()"


# ---------------------------------------------------------------------------
# Old import paths still work (shims)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["read", "write", "edit", "glob", "grep"])
def test_old_core_capability_paths_still_import(name):
    mod = importlib.import_module(f"lingtai.core.{name}")
    assert callable(getattr(mod, "setup", None))


def test_old_service_symbols_still_import():
    from lingtai.services.file_io import (  # noqa: F401
        FileIOBackend,
        FileIOService,
        GrepMatch,
        LocalFileIOBackend,
        LocalFileIOService,
    )
    from lingtai.services.file_io_sidecar import (  # noqa: F401
        RustFileIOBackend,
        SidecarAdapter,
        SidecarError,
        default_file_io_service,
        resolve_sidecar_binary,
    )


def test_top_level_lingtai_reexports_preserved():
    import lingtai

    for sym in (
        "FileIOService",
        "FileIOBackend",
        "LocalFileIOBackend",
        "LocalFileIOService",
        "RustFileIOBackend",
        "SidecarAdapter",
        "SidecarError",
        "default_file_io_service",
        "resolve_sidecar_binary",
        "GrepMatch",
    ):
        assert hasattr(lingtai, sym), f"lingtai.{sym} re-export lost"


# ---------------------------------------------------------------------------
# Module identity — the shims alias the SDK module, not copy it
# ---------------------------------------------------------------------------

def test_service_shim_is_same_module_object():
    import lingtai.services.file_io as old_io
    import lingtai.services.file_io_sidecar as old_sidecar
    import lingtai_sdk.services.file_io as sdk_io
    import lingtai_sdk.services.file_io_sidecar as sdk_sidecar

    assert old_io is sdk_io
    assert old_sidecar is sdk_sidecar


@pytest.mark.parametrize("name", ["read", "write", "edit", "glob", "grep"])
def test_capability_shim_is_same_module_object(name):
    old = importlib.import_module(f"lingtai.core.{name}")
    sdk = importlib.import_module(f"lingtai_sdk.capabilities.file.{name}")
    assert old is sdk


def test_monkeypatch_on_old_sidecar_name_affects_resolution(monkeypatch, tmp_path):
    """Patching the *old* dotted name must reach the resolver's real globals.

    This is the property that lets existing monkeypatch-based sidecar tests keep
    passing after the move — the shim aliases the module rather than copying it.
    """
    import lingtai.services.file_io_sidecar as sidecar_mod

    fake = tmp_path / "fake-sidecar"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)

    monkeypatch.setattr(sidecar_mod, "_packaged_binary", lambda: str(fake))
    monkeypatch.setattr(sidecar_mod, "_dev_tree_binary", lambda: None)

    resolved = sidecar_mod.resolve_sidecar_binary()
    assert resolved == str(fake)
