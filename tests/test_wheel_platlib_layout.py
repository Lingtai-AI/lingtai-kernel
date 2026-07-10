"""Regression test: a native-sidecar wheel must be platlib-compliant.

The release blocker this guards: cibuildwheel builds a Linux wheel that bundles
the native ``lingtai-search-sidecar`` binary, then ``auditwheel repair`` rejects
it because the binary (and every pure-Python package) lands under
``<name>-<ver>.data/purelib/`` instead of at the archive root (platlib). That
happened because ``setup.py`` set ``root_is_pure = False`` *after*
``bdist_wheel.finalize_options`` had already finalized the install layout off
``distribution.has_ext_modules()`` (still false) — fixing the wheel *tag* but not
the *layout*. See ``setup.py::BdistWheelImpure``.

These tests build a real wheel at the boundary pip/auditwheel actually consume —
the ``.whl`` archive — and assert the corrected contract:

* the native build (cargo present) is ``Root-Is-Purelib: false``, carries the
  sidecar binary at ``lingtai/bin/…`` at the archive root, and places *no*
  package under ``*.data/purelib`` / ``*.data/platlib``;
* the pure fallback (``LINGTAI_SKIP_RUST_BUILD=1``, Rust-independent) is
  ``Root-Is-Purelib: true``, ships no binary, and keeps packages at the root.

The native test is skipped — not silently passed — when ``cargo`` is absent, so
a Rust-less CI leg does not give a false green. The pure test always runs.

This exercises the custom ``bdist_wheel`` layout end-to-end rather than asserting
implementation internals (e.g. ``root_is_pure`` attribute state).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

BINARY_NAMES = ("lingtai-search-sidecar", "lingtai-search-sidecar.exe")


def _build_wheel(dest: Path, *, skip_rust: bool) -> Path:
    """Build one wheel into ``dest`` and return its path.

    ``skip_rust`` toggles ``LINGTAI_SKIP_RUST_BUILD``: false forces a real
    native build (cargo must be present), true forces the pure fallback. Build
    isolation lets pip pick a setuptools that understands the PEP 639 license
    expression, matching the release build.
    """
    env = dict(os.environ)
    if skip_rust:
        env["LINGTAI_SKIP_RUST_BUILD"] = "1"
    else:
        env.pop("LINGTAI_SKIP_RUST_BUILD", None)
        # Fail loud if the toolchain silently degrades to a pure wheel: a native
        # build that produced no binary would make the platlib assertions below
        # vacuously pass. Mirrors the CI env in .github/workflows/wheels.yml.
        env["LINGTAI_REQUIRE_RUST_BUILD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(dest), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            "wheel build failed (skip_rust=%s, rc=%d):\n%s\n%s"
            % (skip_rust, result.returncode, result.stdout, result.stderr)
        )
    wheels = sorted(dest.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _wheel_metadata(wheel: Path) -> dict[str, str]:
    """Return the parsed ``*.dist-info/WHEEL`` key/value fields."""
    with zipfile.ZipFile(wheel) as zf:
        wheel_meta = next(n for n in zf.namelist() if n.endswith(".dist-info/WHEEL"))
        text = zf.read(wheel_meta).decode("utf-8")
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _archive_names(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        return zf.namelist()


def _sidecar_entries(names: list[str]) -> list[str]:
    return [n for n in names if Path(n).name in BINARY_NAMES]


def _data_scheme_entries(names: list[str]) -> list[str]:
    """Entries routed through the wheel ``.data`` install schemes.

    A native, platlib-compliant wheel puts every package at the archive root, so
    nothing may sit under ``<name>-<ver>.data/purelib/`` or ``.data/platlib/`` —
    those are exactly the paths auditwheel refuses to relocate correctly.
    """
    hits = []
    for n in names:
        parts = n.split("/")
        for i, seg in enumerate(parts[:-1]):
            if seg.endswith(".data") and parts[i + 1] in ("purelib", "platlib"):
                hits.append(n)
                break
    return hits


# ---------------------------------------------------------------------------
# Native wheel — requires the Rust toolchain; skipped (not passed) without it.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("cargo") is None, reason="Rust toolchain is optional")
def test_native_wheel_is_platlib_compliant(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path, skip_rust=False)

    # 1. The sidecar must actually be present — a soft-fallback (no binary)
    #    wheel would make the platlib assertions vacuously true.
    names = _archive_names(wheel)
    sidecars = _sidecar_entries(names)
    assert sidecars, (
        "native wheel is missing lingtai-search-sidecar; the platlib checks "
        "below would be vacuous. Archive entries: %r" % names
    )

    # 2. It must be tagged as a platform wheel, not pure.
    meta = _wheel_metadata(wheel)
    assert meta.get("Root-Is-Purelib") == "false", (
        "native wheel must be Root-Is-Purelib: false, got %r" % meta.get("Root-Is-Purelib")
    )
    assert meta.get("Tag", "").endswith(("-any",)) is False, (
        "native wheel must carry a platform tag, got Tag: %r" % meta.get("Tag")
    )

    # 3. No package — the binary least of all — may live under *.data/purelib
    #    or *.data/platlib; that placement is what auditwheel rejects.
    misplaced = _data_scheme_entries(names)
    assert not misplaced, (
        "native wheel routes packages through *.data/{purelib,platlib}; "
        "auditwheel cannot repair this. Misplaced entries: %r" % misplaced
    )

    # 4. Concretely, the sidecar binary must sit at the platlib root under
    #    lingtai/bin/, exactly where importlib.resources resolves it.
    assert any(s.startswith("lingtai/bin/") for s in sidecars), (
        "sidecar not at platlib root lingtai/bin/; entries: %r" % sidecars
    )


# ---------------------------------------------------------------------------
# Pure fallback — Rust-independent, always runs.
# ---------------------------------------------------------------------------


def test_pure_wheel_has_no_native_payload_and_root_layout(tmp_path: Path) -> None:
    wheel = _build_wheel(tmp_path, skip_rust=True)

    meta = _wheel_metadata(wheel)
    assert meta.get("Root-Is-Purelib") == "true", (
        "pure fallback wheel must be Root-Is-Purelib: true, got %r"
        % meta.get("Root-Is-Purelib")
    )
    assert meta.get("Tag") == "py3-none-any", (
        "pure fallback wheel must be py3-none-any, got %r" % meta.get("Tag")
    )

    names = _archive_names(wheel)
    assert not _sidecar_entries(names), (
        "pure fallback wheel must not bundle the native sidecar binary: %r"
        % _sidecar_entries(names)
    )
    # A pure wheel keeps everything at the root too (purelib scheme → root).
    assert not _data_scheme_entries(names), (
        "pure wheel unexpectedly routes packages through *.data: %r"
        % _data_scheme_entries(names)
    )
    assert "lingtai/__init__.py" in names, (
        "pure wheel must place lingtai/ at the archive root; entries sample: %r"
        % names[:20]
    )
