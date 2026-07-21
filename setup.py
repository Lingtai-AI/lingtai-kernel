"""Setuptools shim — adds Rust sidecar build hooks on top of pyproject.toml.

Project metadata lives in ``pyproject.toml``. This file exists only to wire
two extra steps into the standard ``setuptools.build_meta`` flow:

1. **Bundle the Rust sidecar binary into the wheel.**
   ``BuildPyWithSidecar`` and ``BdistWheelImpure`` both call
   ``_ensure_sidecar_built()``, which runs ``cargo build --release`` on
   ``crates/lingtai-search-sidecar`` and copies the resulting binary
   into ``src/lingtai/bin/lingtai-search-sidecar[.exe]``. The function is
   idempotent — repeated invocations are cheap because cargo no-ops when
   nothing changed.

2. **Mark the wheel as platform-specific *and* platlib-compliant.** Since the
   wheel ships a native binary, ``bdist_wheel`` is overridden so the
   distribution reports ``has_ext_modules()`` *before* superclass layout
   finalization. That single predicate drives the platform tag
   (``root_is_pure=False`` → e.g.
   ``lingtai-0.10.10-cp311-cp311-macosx_14_0_arm64.whl``) *and* the install
   scheme, so every package — including the bundled binary under
   ``lingtai/bin/`` — lands at the archive root (platlib) instead of under
   ``<name>-<ver>.data/purelib/``. The latter placement is what auditwheel
   rejects on Linux; see ``BdistWheelImpure`` and
   ``tests/test_wheel_platlib_layout.py``. The sidecar-present check runs after
   ``_ensure_sidecar_built()``, so soft-fallback builds (no cargo,
   ``LINGTAI_SKIP_RUST_BUILD=1``) leave the distribution pure and produce a
   universal ``py3-none-any`` wheel.

Skip / fallback behavior:

* If ``LINGTAI_SKIP_RUST_BUILD=1`` is set, the cargo step is skipped and
  no binary is bundled. Useful for editable installs / source-only
  contributors who don't have Rust.
* If ``cargo`` is not on ``PATH``, the build emits a warning and proceeds
  without a bundled binary — the wheel will be functional but the Rust
  backend will only activate if the operator supplies a binary out-of-band
  (``LINGTAI_FILE_IO_SIDECAR=…``).
* Set ``LINGTAI_REQUIRE_RUST_BUILD=1`` to make any cargo failure abort the
  build instead of degrading gracefully.
* The Rust crate sources live under ``crates/lingtai-search-sidecar/``
  and are included in sdists via ``MANIFEST.in`` so source builds can
  rebuild from the bundled crate.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

try:
    # setuptools >= 70 ships its own integrated ``bdist_wheel`` and routes
    # ``python -m build`` / ``pip install`` through it instead of the
    # standalone ``wheel`` package — overriding the wrong one is a silent
    # no-op that ends up producing a ``py3-none-any`` wheel even when we've
    # bundled a native binary. Try the setuptools path first; fall back to
    # the standalone ``wheel`` package for older setuptools.
    from setuptools.command.bdist_wheel import bdist_wheel as _bdist_wheel
except ImportError:  # pragma: no cover - exercised only on old setuptools
    try:
        from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
    except ImportError:
        _bdist_wheel = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).parent.resolve()
SIDECAR_CRATE = REPO_ROOT / "crates" / "lingtai-search-sidecar"
PACKAGE_BIN_DIR = REPO_ROOT / "src" / "lingtai" / "bin"
BINARY_NAME = "lingtai-search-sidecar.exe" if os.name == "nt" else "lingtai-search-sidecar"

# Memoize the build step so we don't shell out twice per wheel build (once
# in ``bdist_wheel.finalize_options`` and again in ``build_py.run``).
_built_once = False


def _should_skip() -> bool:
    return os.environ.get("LINGTAI_SKIP_RUST_BUILD") == "1"


def _have_cargo() -> bool:
    return shutil.which("cargo") is not None


def _clear_staged_sidecar() -> None:
    """Remove any previously staged binary from an earlier local build."""
    for name in ("lingtai-search-sidecar", "lingtai-search-sidecar.exe"):
        try:
            (PACKAGE_BIN_DIR / name).unlink()
        except FileNotFoundError:
            pass
    # Leave __init__.py alone if present; an empty package dir is harmless and
    # avoids churn between builds that do/don't bundle the binary.


def _ensure_sidecar_built() -> Path | None:
    """Build the sidecar (idempotent) and stage it under ``src/lingtai/bin/``.

    Returns the path to the staged binary, or ``None`` on any soft failure
    (missing crate, cargo absent, build failed). Strict mode is opt-in via
    ``LINGTAI_REQUIRE_RUST_BUILD=1``, which propagates the underlying
    exception instead of degrading to a pure-Python wheel.
    """
    global _built_once
    if _built_once:
        existing = PACKAGE_BIN_DIR / BINARY_NAME
        if existing.is_file():
            return existing
        if os.environ.get("LINGTAI_REQUIRE_RUST_BUILD") == "1":
            raise RuntimeError(
                "LINGTAI_REQUIRE_RUST_BUILD=1 but no sidecar binary is staged "
                f"at {existing} on a repeat build-hook call in this process — "
                "the first call must have skipped or failed the cargo build."
            )
        return None
    _built_once = True

    if not SIDECAR_CRATE.is_dir():
        if os.environ.get("LINGTAI_REQUIRE_RUST_BUILD") == "1":
            raise RuntimeError(
                f"LINGTAI_REQUIRE_RUST_BUILD=1 but the sidecar crate directory "
                f"is missing at {SIDECAR_CRATE} — the source tree used for this "
                "build does not carry crates/lingtai-search-sidecar."
            )
        _clear_staged_sidecar()
        return None
    if _should_skip():
        print("[lingtai] LINGTAI_SKIP_RUST_BUILD=1 → skipping cargo build",
              file=sys.stderr)
        _clear_staged_sidecar()
        return None
    if not _have_cargo():
        if os.environ.get("LINGTAI_REQUIRE_RUST_BUILD") == "1":
            raise RuntimeError(
                "cargo not found on PATH but LINGTAI_REQUIRE_RUST_BUILD=1 — "
                "install Rust (https://rustup.rs) or unset the env var."
            )
        print(
            "[lingtai] cargo not found on PATH; the wheel will not include "
            "lingtai-search-sidecar. Install Rust if you want the bundled "
            "native backend.",
            file=sys.stderr,
        )
        _clear_staged_sidecar()
        return None

    print(f"[lingtai] cargo build --release --locked ({SIDECAR_CRATE})", file=sys.stderr)
    try:
        subprocess.run(
            ["cargo", "build", "--release", "--locked",
             "--manifest-path", str(SIDECAR_CRATE / "Cargo.toml"),
             # Pin the output location so the staging step below always finds
             # the binary at the same path regardless of an ambient
             # CARGO_TARGET_DIR (env var or .cargo/config.toml) the caller's
             # build environment may set — cargo honors --target-dir over
             # both, so this makes the build deterministic rather than
             # silently producing no bundle when the ambient target dir wins.
             "--target-dir", str(SIDECAR_CRATE / "target")],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        if os.environ.get("LINGTAI_REQUIRE_RUST_BUILD") == "1":
            raise RuntimeError(
                f"cargo build failed with exit code {exc.returncode} "
                "while LINGTAI_REQUIRE_RUST_BUILD=1 is set."
            ) from exc
        print(f"[lingtai] cargo build failed ({exc}); continuing without bundled sidecar.",
              file=sys.stderr)
        _clear_staged_sidecar()
        return None

    built = SIDECAR_CRATE / "target" / "release" / BINARY_NAME
    if not built.is_file():
        if os.environ.get("LINGTAI_REQUIRE_RUST_BUILD") == "1":
            raise RuntimeError(
                f"LINGTAI_REQUIRE_RUST_BUILD=1 but cargo build exited "
                f"successfully with no binary at {built} — check the crate's "
                "[[bin]] name/target-dir configuration."
            )
        print(f"[lingtai] cargo build produced no binary at {built}; skipping bundle.",
              file=sys.stderr)
        _clear_staged_sidecar()
        return None

    PACKAGE_BIN_DIR.mkdir(parents=True, exist_ok=True)
    dest = PACKAGE_BIN_DIR / BINARY_NAME
    shutil.copy2(built, dest)
    if os.name != "nt":
        os.chmod(dest, 0o755)
    # Marker module so ``importlib.resources`` can locate ``lingtai/bin/``
    # at runtime without ``lingtai.bin`` looking like a missing subpackage.
    (PACKAGE_BIN_DIR / "__init__.py").write_text(
        "# Generated by setup.py — DO NOT EDIT.\n"
        "# Holds the bundled Rust file-I/O sidecar binary for the active platform.\n",
        encoding="utf-8",
    )
    return dest


def _clear_build_lib_sidecar(build_lib: str | os.PathLike[str]) -> None:
    """Remove stale sidecar binaries from setuptools' build/lib cache."""
    bin_dir = Path(build_lib) / "lingtai" / "bin"
    for name in ("lingtai-search-sidecar", "lingtai-search-sidecar.exe"):
        try:
            (bin_dir / name).unlink()
        except FileNotFoundError:
            pass


class BuildPyWithSidecar(_build_py):
    """``build_py`` subclass that ensures the sidecar is bundled before copy."""

    def run(self) -> None:  # noqa: D401 - setuptools API
        built = _ensure_sidecar_built()
        if built is None:
            _clear_build_lib_sidecar(self.build_lib)
        super().run()
        if built is None:
            # ``build/lib`` can survive across local build invocations; make
            # sure a previous native wheel does not leak into a later
            # ``LINGTAI_SKIP_RUST_BUILD=1`` / no-cargo pure wheel.
            _clear_build_lib_sidecar(self.build_lib)


if _bdist_wheel is not None:

    class BdistWheelImpure(_bdist_wheel):
        """Force a platform-specific, platlib-compliant wheel when a native
        binary is bundled.

        The native/platlib decision must be made *before*
        ``super().finalize_options()`` runs, because setuptools finalizes the
        whole wheel layout off a single predicate — ``distribution.has_ext_modules()``:

        * ``bdist_wheel.finalize_options`` recomputes ``root_is_pure`` as
          ``not (has_ext_modules() or has_c_libraries())`` — this drives the
          platform tag (``py3-none-any`` vs ``…-macosx_14_0_arm64``).
        * ``bdist_wheel.run`` then routes the *purelib* scheme to the archive
          root only when ``root_is_pure`` is true; when false it routes the
          *platlib* scheme to the root and leaves purelib under
          ``<name>-<ver>.data/purelib/``.
        * The nested ``install`` command independently picks
          ``install_lib = install_platlib if has_ext_modules() else install_purelib``.

        Setting ``root_is_pure = False`` *after* the superclass has finalized
        (the previous approach) fixed only the tag: ``has_ext_modules()`` stayed
        false, so ``install`` still routed the pure-Python packages — and the
        bundled binary under ``lingtai/bin/`` — through *purelib*, landing the
        whole tree under ``<name>.data/purelib/``. auditwheel then rejects the
        Linux wheel because the native ``lingtai-search-sidecar`` is not at
        platlib. See ``tests/test_wheel_platlib_layout.py`` for the regression.

        The fix makes ``has_ext_modules()`` report true *before* the superclass
        reads it, so tag, run-layout, and install-routing all agree and every
        package (binary included) lands at the archive root (platlib). We have
        no ``ext_modules`` list, so ``build_ext`` still runs as a no-op — the
        native binary is a prebuilt data payload, not a compiled extension.

        ``build_py`` runs *later* than ``finalize_options``, so the cargo build
        is kicked off here too; otherwise the sidecar-present check would always
        be false on a fresh checkout and we would ship a misleadingly pure wheel.
        The pure-Python fallback (``LINGTAI_SKIP_RUST_BUILD=1`` or no cargo) is
        preserved: with no bundled binary, ``has_ext_modules()`` is left
        untouched and the superclass produces the normal ``py3-none-any`` wheel.
        """

        def finalize_options(self) -> None:  # noqa: D401 - setuptools API
            built = _ensure_sidecar_built()
            if built is not None:
                # Report an ext-module distribution *before* the superclass
                # finalizes layout, so root_is_pure, the run() install-scheme
                # routing, and install_lib selection all pick platlib and place
                # the bundled binary at the archive root — auditwheel-repairable.
                self.distribution.has_ext_modules = lambda: True
            super().finalize_options()
            if built is not None:
                # Belt-and-braces: the tag is derived from root_is_pure, which
                # the has_ext_modules() override already forces false. Assert the
                # invariant so a future setuptools refactor can't silently ship a
                # pure-tagged wheel around a native binary.
                self.root_is_pure = False

    cmdclass = {"build_py": BuildPyWithSidecar, "bdist_wheel": BdistWheelImpure}
else:  # pragma: no cover - wheel is a hard dep of modern setuptools
    cmdclass = {"build_py": BuildPyWithSidecar}


setup(cmdclass=cmdclass)
