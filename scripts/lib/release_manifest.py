"""Compatibility loader for the shared kernel release-manifest contract.

The runtime consumer and release scripts must validate exactly the same v1
manifest semantics. This file remains the canonical scripts/lib import path for
existing tooling while loading the implementation from the source package
without importing the heavyweight ``lingtai`` package.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SHARED_PATH = Path(__file__).resolve().parents[2] / "src" / "lingtai" / "kernel" / "release_manifest.py"
_SPEC = importlib.util.spec_from_file_location("_lingtai_release_manifest_shared", _SHARED_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - repository layout failure
    raise ImportError(f"cannot load shared release-manifest contract: {_SHARED_PATH}")
_SHARED = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _SHARED
_SPEC.loader.exec_module(_SHARED)

for _name in (
    "SCHEMA",
    "Artifact",
    "ManifestError",
    "ReleaseManifest",
    "classify_artifact",
    "manifest_from_dict",
    "parse_sdist_filename",
    "parse_wheel_filename",
    "sha256_file",
    "validate_manifest_dict",
):
    globals()[_name] = getattr(_SHARED, _name)

__all__ = [
    "SCHEMA",
    "Artifact",
    "ManifestError",
    "ReleaseManifest",
    "classify_artifact",
    "manifest_from_dict",
    "parse_sdist_filename",
    "parse_wheel_filename",
    "sha256_file",
    "validate_manifest_dict",
]
