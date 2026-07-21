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
import contextlib
import json
import ntpath
import posixpath
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


def _strip_extended_prefix(path: str) -> str:
    """Drop a Windows extended-length / device path prefix if present.

    The sidecar canonicalizes paths with Rust's ``Path::canonicalize``, which on
    Windows yields verbatim ``\\\\?\\C:\\...`` paths, then normalizes separators to
    ``/`` (``main.rs``: ``abs.to_string_lossy().replace('\\\\', "/")``). So a real
    Windows glob hit looks like ``//?/C:/Users/.../a.txt``. That prefix is a
    spelling artifact, not part of the file's identity — strip it before
    comparison. POSIX paths are returned unchanged.
    """
    for prefix in ("\\\\?\\", "\\\\.\\", "//?/", "//./"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _canonical_key(path: str) -> str:
    """Spelling-independent identity key for a filesystem path.

    Folds away the platform-specific spelling the sidecar emits — the Windows
    extended-length prefix, ``/`` vs ``\\`` separators, and case-insensitive
    volumes — so the same key falls out of ``//?/C:/Users/.../a.txt`` and
    ``C:\\Users\\...\\A.TXT`` while a genuinely different file (other basename or
    other directory) yields a different key. On POSIX it is an ordinary
    case-sensitive normpath. This mirrors how the production integration test
    compares glob output rather than reconstructing the path with
    ``Path.resolve``, which does not equate these Windows spellings.
    """
    stripped = _strip_extended_prefix(path)
    # A drive letter (``C:``) or a backslash means Windows path semantics;
    # otherwise treat it as POSIX. ``normcase`` lowercases + unifies separators
    # on Windows and is a no-op on POSIX; ``normpath`` collapses ``.``/``..``.
    mod = ntpath if (ntpath.splitdrive(stripped)[0] or "\\" in stripped) else posixpath
    return mod.normcase(mod.normpath(stripped))


def _assert_glob_envelope_locates(response: dict, expected_key: str) -> None:
    """Assert a ``glob`` envelope canonically located exactly ``expected_key``.

    Encodes the canonical sidecar contract (``crates/lingtai-search-sidecar``):
    a ``glob`` op returns its hits in ``paths`` (``matches`` is a grep-only
    field and is legitimately empty here), each an absolute, canonicalized path
    in the sidecar's platform spelling. The comparison is robust across POSIX
    and the Windows ``//?/C:/...`` spelling but still demands the *exact* file —
    an unrelated or missing path fails loud. ``expected_key`` is a
    ``_canonical_key``; taking it (rather than a live ``Path``) lets the
    Windows response shape be exercised deterministically off-Windows.
    """
    assert response.get("ok") is True, f"sidecar not ok: {response}"
    assert response.get("backend") == "lingtai-search-sidecar", response
    assert response.get("op") == "glob", response
    # The walk must have actually traversed the tree (root + the one file).
    assert response.get("visited", 0) >= 1, f"sidecar visited nothing: {response}"
    # glob hits live in ``paths``; ``matches`` belongs to ``grep`` and is empty.
    assert not response.get("matches"), f"glob unexpectedly set matches: {response}"
    paths = response.get("paths") or []
    got = {_canonical_key(p) for p in paths}
    assert got == {expected_key}, (
        f"glob did not return exactly the expected file.\n"
        f"  expected key: {expected_key}\n"
        f"  got keys:     {sorted(got)}\n"
        f"  raw response: {response}"
    )


def _assert_glob_found_target(response: dict, target: Path) -> None:
    """Assert a ``glob`` envelope located exactly ``target`` (live filesystem)."""
    _assert_glob_envelope_locates(response, _canonical_key(str(target.resolve())))


def _drive_sidecar(binary: Path) -> None:
    """Run the sidecar over its JSON stdin protocol and assert a good response.

    Proves the native binary launches, returns ``ok: true`` with the expected
    backend/op, traverses the tree, and returns exactly the seeded test file —
    across POSIX and Windows path spellings — then lets the tempdir clean up.
    """
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
        _assert_glob_found_target(response, target)


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


def _run_wheel_build(
    dest: Path, *, extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run ``pip wheel --no-deps`` against the repo and return the raw result.

    Does not assert success — callers that expect a working native wheel use
    ``_build_native_wheel``; callers proving a build *fails* (strict-mode
    regressions) inspect ``returncode``/``stdout`` directly.
    """
    import os

    env = dict(os.environ)
    env.pop("LINGTAI_SKIP_RUST_BUILD", None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(dest), str(REPO_ROOT)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )


def _build_native_wheel(dest: Path, *, extra_env: dict[str, str] | None = None) -> Path:
    env = {"LINGTAI_REQUIRE_RUST_BUILD": "1"}  # fail loud if no binary is produced
    if extra_env:
        env.update(extra_env)
    result = _run_wheel_build(dest, extra_env=env)
    if result.returncode != 0:
        raise AssertionError(
            "native wheel build failed:\n%s\n%s" % (result.stdout, result.stderr)
        )
    wheels = sorted(dest.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return wheels[0]


def _fake_cargo_path(tmp_path: Path) -> str:
    """A ``PATH`` prefix whose ``cargo`` always exits 0 without building
    anything — makes ``_have_cargo()`` true and ``cargo build`` "succeed"
    while producing no binary, deterministically and without a real Rust
    toolchain or network access."""
    import os
    import stat

    shim_dir = tmp_path / "fake-cargo-bin"
    shim_dir.mkdir()
    cargo_shim = shim_dir / "cargo"
    cargo_shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cargo_shim.chmod(cargo_shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}"


@contextlib.contextmanager
def _sidecar_binary_hidden():
    """Temporarily rename aside a real ``target/release/`` sidecar binary
    left in this worktree by an earlier build (gitignored cargo build
    output), if any, so the fake-cargo tests below observe a clean "no
    binary produced" state without deleting anything. Restores it on exit,
    even if the test fails.

    Checks every name in ``BINARY_NAMES`` (POSIX ``lingtai-search-sidecar``
    and Windows ``lingtai-search-sidecar.exe``), not just the POSIX one —
    the platform this test happens to run on is not necessarily the platform
    whose binary is staged, and native Windows CI is exactly where a missed
    ``.exe`` would silently contaminate the fake-cargo assertions below.
    """
    release_dir = REPO_ROOT / "crates" / "lingtai-search-sidecar" / "target" / "release"
    parked: list[tuple[Path, Path]] = []
    try:
        for name in BINARY_NAMES:
            binary = release_dir / name
            if not binary.is_file():
                continue
            park_path = binary
            suffix = 0
            while park_path.exists():
                suffix += 1
                park_path = binary.with_name(f"{binary.name}.test-parked-{suffix}")
            binary.rename(park_path)
            parked.append((binary, park_path))
        yield
    finally:
        for binary, park_path in parked:
            park_path.rename(binary)


if pytest is not None:  # only collected under pytest; CI runs this file scriptwise

    @pytest.mark.skipif(
        shutil.which("cargo") is None, reason="Rust toolchain is optional"
    )
    def test_native_wheel_ships_and_runs_sidecar(tmp_path: Path) -> None:
        wheel = _build_native_wheel(tmp_path)
        verify_wheel_install_and_run(wheel)

    @pytest.mark.skipif(
        shutil.which("cargo") is None, reason="Rust toolchain is optional"
    )
    def test_native_wheel_ships_sidecar_under_ambient_cargo_target_dir(
        tmp_path: Path,
    ) -> None:
        """A caller-set CARGO_TARGET_DIR must not make setup.py silently ship
        a pure wheel: cargo honors that env var and redirects its build
        output away from the crate-local ``target/`` directory setup.py
        checks, so the build step must pin ``--target-dir`` explicitly
        rather than trust the ambient default."""
        redirected_target = tmp_path / "ambient-cargo-target"
        wheel = _build_native_wheel(
            tmp_path / "wheelout",
            extra_env={"CARGO_TARGET_DIR": str(redirected_target)},
        )
        verify_wheel_archive_only(wheel)

    # -----------------------------------------------------------------------
    # Strict-mode fail-loud vs. non-strict soft-degrade when cargo exits 0 but
    # produces no binary at the expected path (setup.py's own docstring calls
    # this "fail loud if no binary is produced" for LINGTAI_REQUIRE_RUST_BUILD;
    # a fake cargo shim makes this deterministic — no real Rust toolchain or
    # network dependency, and it never touches the real CARGO_TARGET_DIR
    # regression above).
    # -----------------------------------------------------------------------

    def test_strict_build_fails_loud_when_cargo_produces_no_binary(
        tmp_path: Path,
    ) -> None:
        with _sidecar_binary_hidden():
            result = _run_wheel_build(
                tmp_path,
                extra_env={
                    "PATH": _fake_cargo_path(tmp_path),
                    "LINGTAI_REQUIRE_RUST_BUILD": "1",
                },
            )
        assert result.returncode != 0, (
            "strict mode must fail the build, not silently ship a pure wheel:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "no binary" in result.stdout + result.stderr

    def test_non_strict_build_degrades_to_pure_wheel_when_cargo_produces_no_binary(
        tmp_path: Path,
    ) -> None:
        with _sidecar_binary_hidden():
            result = _run_wheel_build(tmp_path, extra_env={"PATH": _fake_cargo_path(tmp_path)})
        assert result.returncode == 0, (
            f"non-strict soft degrade must still succeed:\n{result.stdout}\n{result.stderr}"
        )
        wheels = sorted(tmp_path.glob("*.whl"))
        assert len(wheels) == 1, wheels
        assert wheels[0].name.endswith("-py3-none-any.whl"), wheels[0].name
        with zipfile.ZipFile(wheels[0]) as zf:
            names = zf.namelist()
        assert not [n for n in names if Path(n).name in BINARY_NAMES], names

    # -----------------------------------------------------------------------
    # Deterministic regression for the Windows glob response shape.
    #
    # These run on any host (no Windows, no Rust). They pin the exact response
    # shape from the failing Windows CI run (job 86408373017, run
    # 29106557281): a successful ``glob`` whose hit is spelled with the
    # extended-length ``//?/C:/...`` prefix and forward slashes, with an empty
    # ``matches`` array. The pre-fix assertion compared ``Path(p).resolve()``
    # against the local tempfile and failed on that spelling even though the
    # sidecar had located the exact file.
    # -----------------------------------------------------------------------

    # The literal path from windows-latest.log line 804.
    _WIN_GLOB_PATH = (
        "//?/C:/Users/runneradmin/AppData/Local/Temp/tmpduegvy1z/a.txt"
    )
    _WIN_TARGET = "C:\\Users\\runneradmin\\AppData\\Local\\Temp\\tmpduegvy1z\\a.txt"

    def _win_response(paths: list[str]) -> dict:
        # Mirrors the exact envelope fields the sidecar emitted on Windows.
        return {
            "ok": True,
            "backend": "lingtai-search-sidecar",
            "op": "glob",
            "matches": [],
            "paths": paths,
            "visited": 2,
            "files_skipped_size": 0,
            "files_skipped_binary": 0,
            "dirs_pruned": 0,
            "elapsed_ms": 4,
            "truncated_reason": None,
            "error": None,
        }

    def test_canonical_key_folds_windows_extended_length_spelling() -> None:
        # The //?/ + forward-slash spelling keys equal to the plain Windows path.
        assert _canonical_key(_WIN_GLOB_PATH) == _canonical_key(_WIN_TARGET)
        # Case-insensitive volume: an upper-cased expected path still matches.
        assert _canonical_key(_WIN_GLOB_PATH) == _canonical_key(
            "C:/USERS/runneradmin/AppData/Local/Temp/tmpduegvy1z/A.TXT"
        )

    def test_canonical_key_distinguishes_different_files() -> None:
        # Same directory, different basename — must NOT collapse.
        assert _canonical_key(_WIN_GLOB_PATH) != _canonical_key(
            "//?/C:/Users/runneradmin/AppData/Local/Temp/tmpduegvy1z/b.txt"
        )
        # Same basename, different directory — must NOT collapse.
        assert _canonical_key(_WIN_GLOB_PATH) != _canonical_key(
            "//?/C:/Users/runneradmin/AppData/Local/Temp/OTHER/a.txt"
        )
        # POSIX pair sanity: distinct files differ, identical spelling matches.
        assert _canonical_key("/tmp/x/a.txt") != _canonical_key("/tmp/x/b.txt")
        assert _canonical_key("/tmp/x/a.txt") == _canonical_key("/tmp/x/a.txt")

    def test_windows_glob_envelope_accepts_exact_file() -> None:
        # The full envelope assertion accepts the real Windows response shape.
        _assert_glob_envelope_locates(
            _win_response([_WIN_GLOB_PATH]), _canonical_key(_WIN_TARGET)
        )

    def test_windows_glob_envelope_rejects_unrelated_file() -> None:
        # A hit for a different file under the same temp dir must fail loud.
        with pytest.raises(AssertionError):
            _assert_glob_envelope_locates(
                _win_response(
                    ["//?/C:/Users/runneradmin/AppData/Local/Temp/tmpduegvy1z/b.txt"]
                ),
                _canonical_key(_WIN_TARGET),
            )

    def test_windows_glob_envelope_rejects_missing_and_extra_paths() -> None:
        # Empty paths (found nothing) fails loud …
        with pytest.raises(AssertionError):
            _assert_glob_envelope_locates(
                _win_response([]), _canonical_key(_WIN_TARGET)
            )
        # … and an extra unrelated hit alongside the right one also fails.
        with pytest.raises(AssertionError):
            _assert_glob_envelope_locates(
                _win_response(
                    [
                        _WIN_GLOB_PATH,
                        "//?/C:/Users/runneradmin/AppData/Local/Temp/tmpduegvy1z/b.txt",
                    ]
                ),
                _canonical_key(_WIN_TARGET),
            )

    def test_glob_envelope_rejects_grep_style_and_not_ok() -> None:
        # A glob envelope must not carry grep ``matches`` …
        bad = _win_response([_WIN_GLOB_PATH])
        bad["matches"] = [{"path": "a.txt", "line_number": 1, "line": "x"}]
        with pytest.raises(AssertionError):
            _assert_glob_envelope_locates(bad, _canonical_key(_WIN_TARGET))
        # … and an ``ok: false`` envelope always fails loud.
        not_ok = _win_response([_WIN_GLOB_PATH])
        not_ok["ok"] = False
        with pytest.raises(AssertionError):
            _assert_glob_envelope_locates(not_ok, _canonical_key(_WIN_TARGET))

    def test_posix_glob_envelope_accepts_forward_slash_path(tmp_path: Path) -> None:
        # The POSIX shape still works through the same assertion.
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        resp = {
            "ok": True,
            "backend": "lingtai-search-sidecar",
            "op": "glob",
            "matches": [],
            "paths": [str(target.resolve())],
            "visited": 2,
        }
        _assert_glob_found_target(resp, target)


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
