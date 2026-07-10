"""Smoke-check that a built ``lingtai`` wheel carries a runnable Rust sidecar.

Why this file is dependency-free
--------------------------------

cibuildwheel's built-in test phase always ``pip install``s the wheel *with its
full runtime dependency tree* before running ``test-command``, and that is not
configurable (see ``.github/workflows/wheels.yml``). For LingTai that tree pulls
``faster-whisper → av`` (PyAV), whose only distribution for the old
manylinux2014 test container is an sdist that needs FFmpeg ``pkg-config`` dev
libraries — absent in the container. So the old ``import lingtai`` smoke test
failed during *dependency installation*, never reaching the actual wheel, even
though ``auditwheel repair`` had already produced a valid wheel.

The wheel's own correctness does not depend on any of those runtime packages.
This module therefore verifies the produced wheel *directly*, with **no import
of the ``lingtai`` package** (whose ``__init__`` imports the heavy stack) and
**without installing runtime dependencies**:

1. ``pip install --no-deps <wheel>`` into a throwaway environment;
2. locate ``lingtai/bin/lingtai-search-sidecar[.exe]`` under the install prefix
   by path (never by importing the package);
3. drive the binary with its JSON stdin protocol and assert a well-formed,
   ``ok: true`` response — i.e. the native binary is present *and runnable* on
   this platform/arch.

Two entry points, one logic
---------------------------

* **CLI (used by CI):** ``python tests/test_wheel_sidecar_smoke.py <wheel.whl>``
  runs the ``--no-deps`` install + run check against an already-built wheel. The
  workflow calls this after cibuildwheel, once per host-arch wheel. Exit 0 on
  success, non-zero with a diagnostic on failure.
* **pytest (local/dev):** builds a real native wheel (skipped when ``cargo`` is
  absent) and runs the same check, so the gate is exercised on macOS hosts too.

Cross-arch wheels (e.g. the QEMU-emulated Linux ``aarch64`` wheel) cannot be
*run* on an x86_64 host; ``verify_wheel_archive_only`` checks such a wheel's
archive for the sidecar at the platlib root without executing it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

# pytest is optional: CI invokes this file as a plain script (`python
# tests/test_wheel_sidecar_smoke.py --auto <wheel>`) in an environment that has
# no pytest and — deliberately — none of lingtai's runtime deps. Only the
# collected test function needs pytest, and that path runs under pytest.
try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - exercised in the CI verify step
    pytest = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY_NAMES = ("lingtai-search-sidecar", "lingtai-search-sidecar.exe")


# ---------------------------------------------------------------------------
# Archive-only check (works for any wheel, including non-host-arch ones)
# ---------------------------------------------------------------------------


def verify_wheel_archive_only(wheel: Path) -> None:
    """Assert the wheel carries the sidecar at the platlib root (no execution).

    Raises ``AssertionError`` if the binary is missing or is buried under a
    ``*.data/{purelib,platlib}/`` scheme (the auditwheel-rejected layout).
    """
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
    sidecars = [n for n in names if Path(n).name in BINARY_NAMES]
    if not sidecars:
        raise AssertionError(f"{wheel.name}: no lingtai-search-sidecar in archive")
    for n in sidecars:
        parts = n.split("/")
        for i, seg in enumerate(parts[:-1]):
            if seg.endswith(".data") and parts[i + 1] in ("purelib", "platlib"):
                raise AssertionError(
                    f"{wheel.name}: sidecar under *.data/{parts[i + 1]} "
                    f"(auditwheel-rejected layout): {n}"
                )
    if not any(s.startswith("lingtai/bin/") for s in sidecars):
        raise AssertionError(
            f"{wheel.name}: sidecar not at platlib root lingtai/bin/: {sidecars}"
        )


# ---------------------------------------------------------------------------
# Install (--no-deps) + run check (host-arch wheels only)
# ---------------------------------------------------------------------------


def _sidecar_in_prefix(prefix_purelib: str, prefix_platlib: str) -> Path | None:
    """Find the installed sidecar under either install scheme, no import."""
    for base in (prefix_platlib, prefix_purelib):
        for name in BINARY_NAMES:
            candidate = Path(base) / "lingtai" / "bin" / name
            if candidate.is_file():
                return candidate
    return None


def _drive_sidecar(binary: Path) -> None:
    """Run the sidecar over its JSON stdin protocol and assert a good response."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "a.txt"
        target.write_text("hello native sidecar\n", encoding="utf-8")

        request = json.dumps(
            {"op": "glob", "root": str(root), "path": str(root), "pattern": "*.txt"}
        )
        proc = subprocess.run(
            [str(binary)],
            input=request,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, (
            f"sidecar exited {proc.returncode}: {proc.stdout}\n{proc.stderr}"
        )
        response = json.loads(proc.stdout)
        assert response.get("ok") is True, f"sidecar not ok: {proc.stdout}"
        assert response.get("op") == "glob", response
        got = [Path(p).resolve() for p in response.get("paths", [])]
        assert got == [target.resolve()], f"unexpected glob result: {response}"


def wheel_runnable_here(wheel: Path) -> bool:
    """True iff this wheel's tags are compatible with the running interpreter.

    Uses the same tag machinery pip uses (``packaging.tags``), so a wheel built
    for another OS/arch/ABI — e.g. the QEMU-emulated Linux ``aarch64`` wheel on
    an x86_64 runner, or a ``cp313`` wheel under a ``cp312`` interpreter — is
    correctly classified as not runnable here and gets the archive-only check
    instead of a spurious install failure. ``packaging`` is available via pip's
    vendored copy, which is always present in a pip-bearing environment.
    """
    try:
        from packaging.tags import sys_tags
        from packaging.utils import parse_wheel_filename
    except ModuleNotFoundError:  # fall back to pip's vendored copy
        from pip._vendor.packaging.tags import sys_tags  # type: ignore
        from pip._vendor.packaging.utils import parse_wheel_filename  # type: ignore
    _, _, _, tags = parse_wheel_filename(wheel.name)
    return bool(set(tags) & set(sys_tags()))


def verify_wheel_install_and_run(wheel: Path) -> None:
    """``pip install --no-deps`` the wheel and run its sidecar. No lingtai import.

    Creates a throwaway venv so the check is hermetic and never pulls the heavy
    runtime dependency tree (the manylinux2014 PyAV/FFmpeg trap this reshape
    exists to avoid).
    """
    # Archive contract first — fail loud before we even install.
    verify_wheel_archive_only(wheel)

    with tempfile.TemporaryDirectory() as tmp:
        env_dir = Path(tmp) / "venv"
        venv.EnvBuilder(with_pip=True).create(env_dir)
        bindir = "Scripts" if sys.platform == "win32" else "bin"
        py = env_dir / bindir / ("python.exe" if sys.platform == "win32" else "python")

        install = subprocess.run(
            [str(py), "-m", "pip", "install", "--no-deps", "--quiet", str(wheel)],
            capture_output=True,
            text=True,
        )
        assert install.returncode == 0, (
            f"--no-deps install failed:\n{install.stdout}\n{install.stderr}"
        )

        purelib = subprocess.run(
            [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        platlib = subprocess.run(
            [str(py), "-c", "import sysconfig; print(sysconfig.get_paths()['platlib'])"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        binary = _sidecar_in_prefix(purelib, platlib)
        assert binary is not None, (
            f"installed wheel has no lingtai/bin/ sidecar under {platlib} / {purelib}"
        )
        _drive_sidecar(binary)


# ---------------------------------------------------------------------------
# pytest entry point — build a native wheel locally and run the check
# ---------------------------------------------------------------------------


def _build_native_wheel(dest: Path) -> Path:
    import os

    env = dict(os.environ)
    env.pop("LINGTAI_SKIP_RUST_BUILD", None)
    env["LINGTAI_REQUIRE_RUST_BUILD"] = "1"  # fail loud if no binary is produced
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(dest), str(REPO_ROOT)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            "native wheel build failed:\n%s\n%s" % (result.stdout, result.stderr)
        )
    wheels = sorted(dest.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


if pytest is not None:  # only collected under pytest; CI runs this file scriptwise

    @pytest.mark.skipif(
        shutil.which("cargo") is None, reason="Rust toolchain is optional"
    )
    def test_native_wheel_ships_and_runs_sidecar(tmp_path: Path) -> None:
        wheel = _build_native_wheel(tmp_path)
        verify_wheel_install_and_run(wheel)


# ---------------------------------------------------------------------------
# CLI entry point — used by CI against an already-built wheel
# ---------------------------------------------------------------------------


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="path to the built .whl to verify")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--archive-only",
        action="store_true",
        help="only inspect the archive (for wheels whose arch differs from the host)",
    )
    mode.add_argument(
        "--auto",
        action="store_true",
        help="install+run if the wheel is compatible with this interpreter, "
        "else archive-only — the mode CI uses per produced wheel",
    )
    args = parser.parse_args(argv)

    if not args.wheel.is_file():
        print(f"error: wheel not found: {args.wheel}", file=sys.stderr)
        return 2
    try:
        archive_only = args.archive_only or (args.auto and not wheel_runnable_here(args.wheel))
        if archive_only:
            verify_wheel_archive_only(args.wheel)
            print(f"OK (archive): {args.wheel.name} carries sidecar at platlib root")
        else:
            verify_wheel_install_and_run(args.wheel)
            print(f"OK (install+run): {args.wheel.name} sidecar present and runnable")
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
