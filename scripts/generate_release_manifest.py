#!/usr/bin/env python3
"""Aggregate built wheel/sdist artifacts into a release manifest + SHA256SUMS.

Usage (CI, after downloading all `wheels-*` and `sdist` Actions artifacts into
one flat directory):

    python scripts/generate_release_manifest.py \\
        --assets-dir ./release-assets \\
        --kernel-version 0.16.4 \\
        --kernel-tag v0.16.4 \\
        --commit "$GITHUB_SHA" \\
        --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \\
        --out-manifest ./release-assets/lingtai-kernel-release-manifest.json \\
        --out-sha256sums ./release-assets/SHA256SUMS

By default every ``*.whl`` in --assets-dir is verified with the existing
sidecar contract (tests/test_wheel_sidecar_smoke.py's --auto mode: install+run
when the wheel matches this interpreter, archive-only check otherwise) before
it is trusted into the manifest. This is what stops a plain local
`py3-none-any` fallback wheel (no Rust sidecar) from ever being published as a
platform artifact — it fails the archive check because it carries no
`lingtai/bin/lingtai-search-sidecar`. Pass --skip-sidecar-check only for
manifest-shape testing, never for a real release.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from release_manifest import (  # noqa: E402
    ReleaseManifest,
    classify_artifact,
    sha256_file,
)

SIDECAR_SMOKE_SCRIPT = REPO_ROOT / "tests" / "test_wheel_sidecar_smoke.py"


def verify_wheel_sidecar(wheel: Path) -> None:
    """Run the established sidecar validation contract against one wheel.

    Shells out to tests/test_wheel_sidecar_smoke.py --auto rather than
    importing it, so this script has the same dependency-free guarantee the
    smoke test itself documents (no lingtai runtime deps required).
    """
    result = subprocess.run(
        [sys.executable, str(SIDECAR_SMOKE_SCRIPT), "--auto", str(wheel)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"error: {wheel.name} failed the sidecar validation contract:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    print(f"  sidecar OK: {wheel.name} -> {result.stdout.strip()}")


def discover_artifacts(assets_dir: Path) -> list[Path]:
    wheels = sorted(assets_dir.glob("*.whl"))
    sdists = sorted(assets_dir.glob("*.tar.gz"))
    if not wheels:
        raise SystemExit(f"error: no *.whl files found in {assets_dir}")
    if not sdists:
        raise SystemExit(f"error: no *.tar.gz sdist found in {assets_dir}")
    if len(sdists) > 1:
        raise SystemExit(
            f"error: expected exactly one sdist in {assets_dir}, found {len(sdists)}: "
            f"{[p.name for p in sdists]}"
        )
    return wheels + sdists


def reject_plain_fallback_wheel(wheel: Path) -> None:
    """Guard against publishing the local `uv build --wheel` pure wheel.

    A pure-Python fallback wheel built without the Rust sidecar carries the
    'py3-none-any' platform/ABI/python tag combination and, more decisively,
    contains no lingtai/bin/lingtai-search-sidecar. The sidecar smoke check
    already fails such a wheel on the archive check, but this explicit early
    guard produces a clearer diagnostic for the exact known-bad case named in
    the task evidence, before spending time on a subprocess invocation.
    """
    if wheel.name.endswith("-py3-none-any.whl"):
        raise SystemExit(
            f"error: {wheel.name} looks like a plain pure-Python fallback wheel "
            "(py3-none-any) — this is not a cibuildwheel platform artifact and "
            "must never be published as one. Use the wheels-* Actions artifacts "
            "from the matrix build, not a local `uv build --wheel` / "
            "`python -m build --wheel` output."
        )


def build_manifest(
    assets_dir: Path,
    kernel_version: str,
    kernel_tag: str,
    commit: str,
    generated_at: str,
    skip_sidecar_check: bool,
) -> ReleaseManifest:
    files = discover_artifacts(assets_dir)
    artifacts = []
    sdist_filename = None

    for path in files:
        if path.suffix == ".whl":
            reject_plain_fallback_wheel(path)
            if not skip_sidecar_check:
                verify_wheel_sidecar(path)
        digest = sha256_file(path)
        artifact = classify_artifact(path.name, digest)
        if artifact.kind == "sdist":
            sdist_filename = artifact.filename
        artifacts.append(artifact)
        print(f"  {artifact.kind:5s} {artifact.filename}  sha256={digest}")

    assert sdist_filename is not None  # discover_artifacts guarantees exactly one sdist

    return ReleaseManifest(
        kernel_version=kernel_version,
        kernel_tag=kernel_tag,
        commit=commit,
        generated_at=generated_at,
        artifacts=tuple(artifacts),
        sdist_fallback=sdist_filename,
    )


def write_sha256sums(manifest: ReleaseManifest, out_path: Path) -> None:
    lines = [f"{a.sha256}  {a.filename}" for a in manifest.artifacts]
    with out_path.open("x", encoding="utf-8") as output:
        output.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--assets-dir", type=Path, required=True, help="directory containing built .whl/.tar.gz files")
    parser.add_argument("--kernel-version", required=True, help="PEP 440 version, e.g. 0.16.4")
    parser.add_argument("--kernel-tag", required=True, help="release tag, e.g. v0.16.4")
    parser.add_argument("--commit", required=True, help="full git commit SHA the release was built from")
    parser.add_argument("--generated-at", required=True, help="UTC ISO8601 timestamp, injected by the caller")
    parser.add_argument("--out-manifest", type=Path, required=True)
    parser.add_argument("--out-sha256sums", type=Path, required=True)
    parser.add_argument(
        "--skip-sidecar-check",
        action="store_true",
        help="skip the sidecar validation contract (manifest-shape testing only, never for a real release)",
    )
    args = parser.parse_args(argv)

    for output_path in (args.out_manifest, args.out_sha256sums):
        if output_path.exists():
            raise SystemExit(
                f"error: refusing to replace existing release output: {output_path}"
            )

    if not args.assets_dir.is_dir():
        raise SystemExit(f"error: --assets-dir is not a directory: {args.assets_dir}")

    print(f"Aggregating release assets from {args.assets_dir} ...")
    manifest = build_manifest(
        args.assets_dir,
        args.kernel_version,
        args.kernel_tag,
        args.commit,
        args.generated_at,
        args.skip_sidecar_check,
    )

    args.out_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.out_manifest.write_text(json.dumps(manifest.to_dict(), indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"Wrote manifest: {args.out_manifest}")

    args.out_sha256sums.parent.mkdir(parents=True, exist_ok=True)
    write_sha256sums(manifest, args.out_sha256sums)
    print(f"Wrote checksums: {args.out_sha256sums}")

    print(f"\n{len(manifest.artifacts)} artifact(s) for {manifest.kernel_tag} ({manifest.commit[:12]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
